import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Optional
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.insights.contracts import EpisodeContextItem, EpisodeContextPacket
from packages.insights.tag_manager import TagManager
from packages.model_gateway.gateway import ModelGateway
from packages.persistence.models import (
    AnalysisRun,
    Conversation,
    Episode,
    EpisodeMessage,
    Insight,
    InsightClaim,
    Message,
    MessageMediaLink,
    Participant,
    TopicInsightLink,
    generate_id,
)
from packages.persistence.repositories.registry import RepositoryRegistry

logger = logging.getLogger(__name__)



class SubEpisodeItem(BaseModel):
    title: str = Field(description="清晰的主谓宾规范标题，不超过30字，严禁包含'围绕...'等套话")
    topic_summary: str = Field(description="详细客观的内容摘要，150-200字，包含具体背景、操作行为、诉求与反馈细节")
    category_l1: str = Field(default="general", description="一级分类代码 (如 ui_ux, sheet_music, sound_preset, performance, hardware_craft, bluetooth_conn, firmware_power, logistics_srv, general)")
    category_l2: Optional[str] = Field(default=None, description="二级细分类代码 (如 sheet_request, sound_expansion_card, ui_font, video_recording 等)")
    category_l3: Optional[str] = Field(default=None, description="三级细分类目或具体功能点名称")
    matched_message_indices: list[int] = Field(default_factory=list, description="属于该微话题的消息序号列表(1-indexed)")


class ChunkSegmentationOutput(BaseModel):
    sub_episodes: list[SubEpisodeItem] = Field(default_factory=list, description="从当前时间段消息中提取出的独立微话题列表")


class EpisodeSummaryOutput(BaseModel):
    title: str = Field(description="主谓宾结构标题")
    topic_summary: str = Field(description="话题摘要")
    category_hint: str = Field(default="general", description="分类代码")


class EpisodeSegmenter:
    """
    High-accuracy, fine-grained episode segmenter with:
    1. Relevance gating (strict elimination of non-product chit-chat and emotional talk);
    2. 25-minute coarse time-gap windowing;
    3. Intra-chunk micro-topic atomic decomposition;
    4. Exact message index binding (matched_message_indices);
    5. Zero-template mandatory Subject-Verb-Object (SVO) titles;
    6. Rich 150-200 word summaries with multi-level taxonomy tags.
    """

    def __init__(self, session: AsyncSession | None = None, model_gateway: ModelGateway | None = None) -> None:
        self.session = session
        self.gateway = model_gateway or ModelGateway()
        self.repo = RepositoryRegistry(session) if session else None

    @classmethod
    def _clean_episode_title(cls, title: str) -> str:
        """
        Cleans formulaic prefixes ('用户反馈', '用户咨询', '用户交流', '用户探讨', etc.)
        and wraps, ensuring titles are direct, concise statements of the discussed topic.
        """
        if not title:
            return ""
        t = title.strip()
        # Strip leading/trailing quotes
        t = re.sub(r"^[\"“'‘]+|[\"”'’]+$", "", t).strip()
        # Strip template packaging (围绕...展开的? / 关于...)
        t = re.sub(r"^(?:围绕|关于)[\"“'‘]?(.*?)[\"”'’]?(?:展开)?(?:的)?(?:讨论|探讨|问题|交流|反馈|建议|分享|诉求)[:：\s]*$", r"\1", t).strip()
        t = re.sub(r"^(?:围绕|关于)[\"“'‘]?(.*?)[\"”'’]?(?:展开)?(?:的)?(?:讨论|探讨|问题|交流|反馈|建议|分享|诉求)[:：\s]+", r"\1", t).strip()
        t = re.sub(r"^围绕[\"“'‘]?(.*?)[\"”'’]?展开的?", r"\1", t).strip()
        t = re.sub(r"^关于", "", t).strip()
        t = re.sub(r"的$", "", t).strip()
        # Strip formulaic compound prefixes (e.g. 用户在群聊中反馈探讨, 群友反馈, 官方发布)
        t = re.sub(
            r"^(?:群友们?|用户们?|玩家们?|官方|成员|大家)?(?:在群聊中)?(?:集中)?(?:(?:反馈|咨询|交流|探讨|建议|求助|提问|分享|讨论|反映|询问|关注|评价)\s*)+[:：\s]*",
            "",
            t,
        ).strip()
        # Strip standalone "反馈" or "建议" prefix if directly followed by content
        t = re.sub(r"^(?:反馈|建议|咨询|探讨|交流)[:：\s]*", "", t).strip()
        # Strip @mentions like "@App反馈 " or "@xxx "
        clean_no_at = re.sub(r"^@\S+\s*", "", t).strip()
        if clean_no_at:
            t = clean_no_at
        else:
            t = t.lstrip("@").strip()
        # Strip redundant boilerplate endings like "相关功能与诉求", "相关问题与建议", "的反馈", "的讨论"
        t = re.sub(r"(?:相关(?:功能与)?(?:诉求|问题|建议|讨论|探讨|情况)|相关讨论)$", "", t).strip()
        t = re.sub(r"的?(?:反馈|建议|诉求|问题|讨论|探讨|分享)$", "", t).strip()
        t = re.sub(r"^[\"“'‘]+|[\"”'’]+$", "", t).strip()
        return t.strip()

    @classmethod
    def _is_noise_message(cls, raw: str) -> bool:
        """Determines if a message is pure noise (greetings, emojis, red packets, system notices)."""
        if not raw:
            return True
        t = raw.strip()
        # Red packets, system messages, transfer
        if any(rp in t for rp in ["[微信红包]", "恭喜发财", "大吉大利", "[系统消息]", "[拍了拍]", "[微信转账]"]):
            return True
        # Pure sticker/image/quote/video placeholder
        if re.fullmatch(r"\[(?:动画表情|表情|图片|语音|视频|消息类型\d+).*?\]", t):
            return True
        # Pure trivial greeting or short acknowledgment
        cleaned = re.sub(r"\[.*?\]", "", t).strip()
        cleaned = re.sub(r"[，。！？；：、“”‘’'\"~\s\d]+", "", cleaned)
        if not cleaned:
            return True
        noise_words = {
            "早", "早啊", "早安", "早上好", "大家早", "早啊大家", "大家早上好", "各位早", "群友们早",
            "晚安", "好的", "收到", "哈哈", "哈哈哈", "哈哈哈哈", "嗯", "嗯嗯", "对", "对的",
            "赞", "谢谢", "多谢", "感谢", "ok", "666", "恭喜发财", "签到", "打卡", "来了", "支持",
            "好听", "好听好听", "真好听", "太好听了", "爽飞", "帅", "牛", "厉害", "到位", "安排"
        }
        if cleaned in noise_words:
            return True
        return False

    @classmethod
    def _is_product_relevant(cls, text: str) -> bool:
        """
        Determines whether text contains substantive product value from a Product Manager (PM) perspective.
        Covers:
        1. All functional modules in TagManager (UI, sheet music, sound preset, performance, hardware, bluetooth, firmware, logistics, new product, official events);
        2. Qualitative PM dimensions:
           - User experience feedback (手感, 体验, 音质, 字号, 做工);
           - Software/Hardware issues & bugs (死机, 卡死, 闪退, 掉线, 杂音, 报错, 充不进);
           - Feature suggestions & requests (建议, 希望, 能不能, 优化, 增加, 求加歌);
           - Purchase & after-sales process (发货, 顺丰, 售后, 退换, 保修, 价格, 客服);
           - Feature confusion & operation inquiries (怎么用, 怎么关, 怎么开, 如何设置, 和弦怎么改, 教程).
        Used to strictly eliminate pure life chit-chat ('好幸福的工作', '陪娃泡温泉', '今天吃什么').
        """
        if not text:
            return False
        t = text.lower()

        # 1. Check all keywords from TagManager
        for tag in TagManager.get_all_tags():
            if any(kw.lower() in t for kw in tag.keywords if len(kw) >= 2):
                return True

        # 2. Qualitative PM Value Dimensions
        pm_dimension_keywords = [
            # 软硬件产品问题与故障
            "bug", "故障", "死机", "卡死", "掉线", "连不上", "闪退", "搜不到", "卡顿", "杂音", "爆音", "跳音", "失真",
            "充不进", "发热", "松动", "缝隙", "异响", "接触不良", "报错", "异常", "失灵", "断连", "无反应", "对不准", "错位",
            # 用户体验反馈与评价
            "手感", "体验", "音质", "太亮", "刺眼", "看不清", "字太小", "塑料感", "难用", "好用", "质感", "做工", "按键手感",
            "打品", "跑调", "音准", "回声", "啸叫", "延迟", "慢半拍", "不跟手", "重", "轻",
            # 产品改进建议与需求诉求
            "建议", "希望", "能不能", "可不可以", "优化", "增加", "支持", "加个功能", "改进", "求加", "什么时候出",
            "什么时候能", "排期", "规划", "新功能", "需求", "想要", "求更新",
            # 功能不清晰与操作疑惑咨询
            "怎么用", "怎么关", "怎么开", "怎么设置", "如何", "在哪里", "怎么调", "什么意思", "没看懂", "鼓机怎么",
            "和弦怎么", "如何导入", "怎么配对", "怎么连", "怎么充", "教程", "操作指南", "求助", "请问", "怎么切换",
            # 购买及售后过程问题
            "发货", "顺丰", "快递", "单号", "到货", "售后", "保修", "退货", "换货", "退款", "首发", "价格", "大促",
            "二手", "闲鱼", "运费", "客服", "配件", "赠品", "购买渠道", "有货", "降价", "保价",
            # 核心硬件与型号
            "吉他", "c1", "c2", "u1", "琴", "指板", "按键", "拨片", "琴身", "底座", "麦克风", "话筒", "耳机", "监听",
            # 伴奏与音频
            "音色", "风格包", "伴奏", "和弦", "鼓机", "bpm", "变调", "移调", "音量", "扩展卡", "音色卡", "合成器", "效果器",
            "导唱", "跟唱", "分轨", "多轨", "曲谱", "歌词", "乐库", "求谱", "加歌", "制谱", "扒谱", "谱面", "走带", "光标", "节拍",
            # 官方活动与生态
            "sorry dog", "落日现场", "sunset", "线下活动", "城市群", "waic"
        ]
        return any(k in t for k in pm_dimension_keywords)

    @classmethod
    def _is_pure_chitchat(cls, text: str) -> bool:
        """Identifies explicit life trivialities, emotional chatter, and non-product water chat."""
        t = text.strip()
        chitchat_patterns = [
            r"好幸福的工作",
            r"好羡慕",
            r"泡温泉",
            r"为了陪娃",
            r"吃什么",
            r"下雨了",
            r"日常开播啦",
        ]
        return any(re.search(p, t) for p in chitchat_patterns)

    @classmethod
    def _evaluate_chunk_quality(cls, cluster: list[tuple[Message, Participant | None]]) -> bool:
        """
        Active Chunk Pruning Gate:
        Evaluates whether a 25-minute message chunk contains meaningful product-related signal.
        If it only contains noise, greetings, pure life chit-chat, or ambiguous 1-2 line chatter,
        the entire chunk is pruned/dropped immediately (no LLM call, no Episode created).
        """
        if not cluster:
            return False

        # 1. Filter out pure noise messages (emojis, greetings, system notices)
        valid_entries = [(idx, m, p) for idx, (m, p) in enumerate(cluster, 1) if not cls._is_noise_message(m.raw_text)]
        if not valid_entries:
            return False

        combined_text = " ".join((m.raw_text or "") for _, m, _ in valid_entries).strip()
        if not combined_text or len(combined_text) < 4:
            return False

        # 2. Check pure life chit-chat
        if cls._is_pure_chitchat(combined_text) and not cls._is_product_relevant(combined_text):
            return False

        # 3. PM Business Relevance Check (taxonomies + qualitative dimensions)
        if not cls._is_product_relevant(combined_text):
            return False

        return True

    def _infer_cluster_metadata(self, cluster: list[tuple[Message, Participant | None]], conv_name: str = "") -> EpisodeSummaryOutput:
        """Backward-compatible helper returning a single EpisodeSummaryOutput."""
        sub_eps = self._infer_cluster_sub_episodes(cluster, conv_name)
        if sub_eps:
            return EpisodeSummaryOutput(
                title=sub_eps[0].title,
                topic_summary=sub_eps[0].topic_summary,
                category_hint=sub_eps[0].category_l1 or "general"
            )
        return EpisodeSummaryOutput(
            title="",
            topic_summary="",
            category_hint="general"
        )

    def _infer_cluster_sub_episodes(self, cluster: list[tuple[Message, Participant | None]], conv_name: str) -> list[SubEpisodeItem]:
        """
        Rule-based heuristic fine-grained decomposition when running offline or mock mode.
        Decomposes distinct topic threads inside the 25-minute window and strictly ignores pure noise & non-product chit-chat.
        """
        if not cluster or not self._evaluate_chunk_quality(cluster):
            return []

        # 1. Filter out pure noise messages and index valid messages
        valid_entries: list[tuple[int, Message, Participant | None]] = []
        for idx, (msg, part) in enumerate(cluster, 1):
            if not self._is_noise_message(msg.raw_text):
                valid_entries.append((idx, msg, part))

        if not valid_entries:
            return []

        # 2. Check if the cluster is pure life chit-chat without product relevance
        combined_all_text = " ".join((m.raw_text or "") for _, m, _ in valid_entries)
        if self._is_pure_chitchat(combined_all_text) and not self._is_product_relevant(combined_all_text):
            return []

        # 3. Group messages by semantic domain (e.g. sheet_music, sound_preset, ui_ux, etc.)
        domain_groups: dict[str, list[tuple[int, Message, Participant | None]]] = {}
        for idx, msg, part in valid_entries:
            t = (msg.raw_text or "").lower()

            # Detailed Fine-Grained Topic Signatures
            if any(k in t for k in ["求谱", "加歌", "乐库没有", "没有这首歌", "希望能出", "流行歌", "周杰伦", "求加歌"]):
                group_key = "sheet_music:sheet_request"
            elif any(k in t for k in ["ai制谱", "ai扒谱", "手动制谱", "自建谱", "导入曲谱", "改和弦", "编辑和弦", "和弦自定义", "自定义编辑", "和弦谱"]):
                group_key = "sheet_music:sheet_manual_create"
            elif any(k in t for k in ["错谱", "标错", "和弦不对", "歌词错", "谱子错", "曲谱报错"]):
                group_key = "sheet_music:sheet_report"
            elif any(k in t for k in ["歌声很强", "琴声很弱", "录不到琴声", "外录", "内录", "高声抑制", "录音机", "录音", "录歌", "录下", "录制", "录制视频", "录音频", "分屏", "悬浮", "录相", "录屏"]):
                group_key = "performance:video_recording"
            elif any(k in t for k in ["鼓机", "进鼓", "关鼓", "节奏垫", "调速", "自动鼓机"]):
                group_key = "performance:drum_machine_operation"
            elif any(k in t for k in ["音色卡", "扩展卡", "换音色", "音质", "扩展引擎"]):
                group_key = "sound_preset:sound_expansion_card"
            elif any(k in t for k in ["自定义风格包", "风格包"]):
                group_key = "sound_preset:style_pack_customization"
            elif any(k in t for k in ["看不清", "看不清楚", "字太小", "字号", "字体", "瞎了", "太小了", "放大"]):
                group_key = "ui_ux:ui_font"
            elif any(k in t for k in ["皮肤", "暗黑", "深色", "夜间", "配色", "刺眼"]):
                group_key = "ui_ux:ui_theme"
            elif any(k in t for k in ["走带", "卡顿", "跳音", "谱面卡住", "光标跳", "跟不上谱"]):
                group_key = "performance:play_track"
            elif any(k in t for k in ["蓝牙", "搜不到", "配对", "延迟", "断开"]):
                group_key = "bluetooth_conn:bt_pair"
            elif any(k in t for k in ["标配充电头", "没给配充电头", "偷走", "不配充电头", "充电线两头", "双c口", "配件"]):
                group_key = "hardware_craft:accessory_standard"
            elif any(k in t for k in ["充电器", "充电头", "功率", "40瓦", "65瓦", "20瓦", "100瓦", "5v2a", "快充", "发热", "充坏", "充不进"]):
                group_key = "firmware_power:charging_spec"
            elif any(k in t for k in ["固件升级", "固件更新", "ota", "死机", "升级失败", "最后一步", "报错截图"]):
                group_key = "firmware_power:fw_upgrade_failure"
            elif any(k in t for k in ["拨片", "按键", "做工", "手感", "缝隙", "外壳", "松动", "硬件"]):
                group_key = "hardware_craft:hw_keys"
            elif any(k in t for k in ["麦克风", "话筒", "收音距离", "啸叫", "耳机", "监听"]):
                group_key = "hardware_craft:hw_mic"
            elif any(k in t for k in ["sorry dog", "落日现场", "猫咪", "活动为定向邀约", "报名二维码", "进上海城市群", "sunset live", "节目报名", "waic", "人工智能大会"]):
                group_key = "general:official_event"
            elif any(k in t for k in ["u1", "新品吉他", "二代琴"]):
                group_key = "new_product:u1_timeline"
            elif any(k in t for k in ["发货", "顺丰", "售后", "闲鱼", "购买", "首发", "大促"]):
                group_key = "logistics_srv:log_shipping"
            else:
                continue

            if group_key not in domain_groups:
                domain_groups[group_key] = []
            domain_groups[group_key].append((idx, msg, part))

        if not domain_groups:
            if valid_entries:
                domain_groups["general:user_feedback"] = valid_entries
            else:
                return []

        # 4. Generate SVO titles and detailed summaries for each distinct group
        sub_episodes: list[SubEpisodeItem] = []
        for gkey, items in domain_groups.items():
            if not items:
                continue

            matched_indices = [item[0] for item in items]
            group_msgs = [item[1] for item in items]
            combined_group_text = " ".join(m.raw_text or "" for m in group_msgs).lower()

            # Produce direct, concise titles and rich summaries
            if "sheet_request" in gkey or any(k in combined_group_text for k in ["求谱", "加歌", "流行歌", "周杰伦"]):
                title = "官方曲谱乐库加快热门流行新歌与经典曲目扩充诉求"
                summary = "群成员集中交流官方曲谱乐库覆盖现状，强烈建议官方教研与版权团队加快扩充周杰伦等热门流行歌曲与经典伴奏资源，提升乐库曲目丰富度与搜索满意度。"
                l1, l2, l3 = "sheet_music", "sheet_request", "流行新歌版权求谱"
            elif "sheet_manual_create" in gkey or any(k in combined_group_text for k in ["和弦自定义", "改和弦", "自建谱", "编辑和弦"]):
                title = "伴奏曲谱和弦自定义编辑与第三方乐谱文件导入"
                summary = "群成员交流伴奏和弦配置与弹唱体验，提出希望App支持用户自由修改伴奏和弦走向，并支持通过第三方文件导入自定义乐谱进行个性化演奏。"
                l1, l2, l3 = "sheet_music", "sheet_manual_create", "和弦自定义编辑与导入"
            elif "video_recording" in gkey or any(k in combined_group_text for k in ["歌声很强", "琴声很弱", "录不到琴声", "外录", "内录", "录音机", "分屏"]):
                title = "室内吉他弹唱录音人声过强琴声音量偏弱"
                summary = "群成员讨论使用手机或设备进行室内弹唱录制时，遇到录音人声过大而琴声音量过弱甚至无法清晰收录的问题，群友深入交流外录高声抑制机制与内录/手机分屏操作方案。"
                l1, l2, l3 = "performance", "video_recording", "室内弹唱录音音量平衡"
            elif "drum_machine_operation" in gkey or any(k in combined_group_text for k in ["鼓机", "进鼓", "关鼓"]):
                title = "鼓机模式进入与长按关闭操作卡点"
                summary = "群成员在群聊中咨询进入鼓机模式后的关闭方法、调速与自动鼓机设置，交流操作卡点与官方指导答疑。"
                l1, l2, l3 = "performance", "drum_machine_operation", "鼓机进入与关闭操作"
            elif "style_pack_customization" in gkey or any(k in combined_group_text for k in ["自定义风格包"]):
                title = "伴奏风格包自定义配置与个性化调节"
                summary = "群成员在社群活动及日常交流中咨询如何自定义风格包，希望了解不同曲风伴奏风格的个性化设置路径与保存操作指南。"
                l1, l2, l3 = "sound_preset", "style_pack_customization", "自定义风格包设置"
            elif "sound_expansion_card" in gkey or any(k in combined_group_text for k in ["音色卡", "扩展卡", "扩展引擎"]):
                title = "扩展音色卡加载识别与风格包体验"
                summary = "群成员集中探讨伴奏风格包切换、扩展音色卡的硬件插拔识别、自定义音色加载及音质表现，交流扩展卡接触不良排查经验与购买渠道。"
                l1, l2, l3 = "sound_preset", "sound_expansion_card", "扩展音色卡加载识别"
            elif "official_event" in gkey or any(k in combined_group_text for k in ["sorry dog", "落日现场", "报名二维码", "进上海城市群", "waic"]):
                title = "线下音乐会与弹唱交流活动报名通知与出行指引"
                summary = "官方在群内发布线下音乐会、落日现场及展会活动的报名通知与出行指南，指引感兴趣的琴友通过二维码或表单报名并加入城市交流群。"
                l1, l2, l3 = "general", "official_event", "官方线下活动报名通知"
            elif "accessory_standard" in gkey or any(k in combined_group_text for k in ["标配充电头", "没给配充电头", "不配充电头", "双c口"]):
                title = "产品未标配充电头需自购及双C口线材适配体验"
                summary = "群成员反馈官方出厂未标配充电头、需用户额外自购及标配双Type-C接口线材适配不便的问题，表达对配件包装策略的诉求。"
                l1, l2, l3 = "hardware_craft", "accessory_standard", "标配充电头与配件包装策略"
            elif "charging_spec" in gkey or any(k in combined_group_text for k in ["充电器", "充电头", "功率", "40瓦", "65瓦", "20瓦"]):
                title = "C2设备充电器功率适配规格与快充协议兼容"
                summary = "群成员在群聊中咨询设备充电功率选择、不同瓦数手机充电头兼容性与快充发热风险，探讨官方充电参数要求。"
                l1, l2, l3 = "firmware_power", "charging_spec", "充电器功率与快充适配"
            elif "fw_upgrade_failure" in gkey or any(k in combined_group_text for k in ["固件升级", "固件更新", "ota", "死机", "升级失败"]):
                title = "固件OTA升级异常卡死排查及多轮重启解决"
                summary = "群成员交流设备OTA固件升级过程中偶发进度卡死在最后一步、升级报错等异常现象，分享检查网络连接、手机内存及多次重启后的解决经验。"
                l1, l2, l3 = "firmware_power", "fw_upgrade_failure", "固件升级失败与异常排查"
            elif "hw_mic" in gkey or any(k in combined_group_text for k in ["麦克风", "话筒", "收音距离", "啸叫"]):
                title = "外设动圈麦克风收音距离与防啸叫设置"
                summary = "群成员讨论官方外设动圈麦克风的收音灵敏度与有效距离，交流麦克风与琴身扬声器相对位置调整、防啸叫技巧及第三方无线麦兼容性。"
                l1, l2, l3 = "hardware_craft", "hw_mic", "麦克风收音距离与防啸叫"
            elif "hw_keys" in gkey or any(k in combined_group_text for k in ["按键", "做工", "手感", "拨片"]):
                title = "琴身装配做工公差、机械按键拨片手感与硬件细节"
                summary = "群成员讨论并反馈琴身装配做工质量、按键与拨片击弦手感反馈及外观工艺细节体验。"
                l1, l2, l3 = "hardware_craft", "hw_keys", "按键与拨片手感"
            elif "u1_timeline" in gkey or any(k in combined_group_text for k in ["u1", "新品吉他", "二代琴"]):
                title = "U1新品吉他研发规划、预计发售时间与上市排期"
                summary = "群成员集中咨询U1新品吉他的研发进展、预计上市发售时间及生产排期计划，交流对新品硬件配置与发售排期的期待。"
                l1, l2, l3 = "new_product", "u1_timeline", "U1研发与发售排期"
            elif "ui_font" in gkey or any(k in combined_group_text for k in ["看不清", "字太小", "字号", "字体"]):
                title = "App界面字号偏小与高对比度谱面显示诉求"
                summary = "群成员反馈App各功能页面文字与和弦字号过小、弱光环境下排版不易辨识，建议增加字号无级缩放与高对比度显示选项。"
                l1, l2, l3 = "ui_ux", "ui_font", "字号大小与清晰度"
            elif "ui_theme" in gkey or any(k in combined_group_text for k in ["皮肤", "暗黑", "深色", "夜间"]):
                title = "App深色暗黑模式与自定义护眼主题皮肤"
                summary = "群成员建议官方App提供夜间深色暗黑模式与自定义护眼配色切换，改善弱光环境下长时间弹唱练琴的视觉疲劳问题。"
                l1, l2, l3 = "ui_ux", "ui_theme", "深色模式与主题换肤"
            elif "play_track" in gkey or any(k in combined_group_text for k in ["走带", "卡顿", "跳音"]):
                title = "弹唱伴奏谱面光标走带卡顿、跳音与节拍同步"
                summary = "群成员反馈在伴奏弹唱过程中谱面光标前进流畅度偶发停滞卡顿、跳音及伴奏节拍不同步现象，探讨提升跟谱走带平滑度的优化建议。"
                l1, l2, l3 = "performance", "play_track", "谱面走带与节拍同步"
            elif "bt_pair" in gkey or any(k in combined_group_text for k in ["蓝牙", "延迟", "配对"]):
                title = "蓝牙伴奏音频传输延迟与手机App配对连接稳定性"
                summary = "群成员讨论通过蓝牙传输伴奏音频时的声音延迟与节拍慢半拍问题，同时反馈手机App与琴身蓝牙配对偶发超时及自动重连断开体验。"
                l1, l2, l3 = "bluetooth_conn", "bt_pair", "蓝牙配对与音频延迟"
            elif "log_shipping" in gkey or any(k in combined_group_text for k in ["发货", "顺丰", "售后", "闲鱼", "购买", "首发"]):
                title = "官方渠道发货时效、二手平台价格与售后退换政策"
                summary = "群成员交流购琴订单物流发货时效、二手渠道入手价格对比及官方售后退换保障政策，分享各平台购机体验。"
                l1, l2, l3 = "logistics_srv", "log_shipping", "购买渠道与物流售后"
            else:
                continue

            sub_episodes.append(
                SubEpisodeItem(
                    title=self._clean_episode_title(title),
                    topic_summary=summary,
                    category_l1=l1,
                    category_l2=l2,
                    category_l3=l3,
                    matched_message_indices=matched_indices,
                )
            )

        if not sub_episodes and valid_entries:
            # Resilient fallback synthesis for general product-relevant chatter
            first_msg = valid_entries[0][1]
            raw_preview = (first_msg.raw_text or "").strip()
            raw_preview = re.sub(r"[\[【].*?[\]】]", "", raw_preview).strip()
            raw_preview = re.sub(r"@\S+", "", raw_preview).strip()
            if not raw_preview or len(raw_preview) < 2:
                raw_preview = "产品功能与使用探讨"
            clean_title = self._clean_episode_title(raw_preview[:22])
            if len(clean_title) < 4:
                clean_title = f"{clean_title}使用交流"
            snippets = []
            for v in valid_entries[:5]:
                sender_label = v[2].display_label if v[2] else "用户"
                msg_snippet = re.sub(r"\s+", " ", (v[1].raw_text or "")[:40])
                snippets.append(f"{sender_label}: {msg_snippet}")
            summary = f"群成员在群聊中针对产品功能与日常使用开展交流讨论，具体反馈涉及：{'；'.join(snippets)}。建议持续跟进相关体验与用户疑问。"
            sub_episodes.append(
                SubEpisodeItem(
                    title=clean_title,
                    topic_summary=summary,
                    category_l1="general",
                    category_l2="user_feedback",
                    category_l3="群聊使用讨论",
                    matched_message_indices=[item[0] for item in valid_entries],
                )
            )

        return sub_episodes

    async def segment_conversation(
        self,
        conversation_id: str,
        time_gap_minutes: int = 15,
        force_mock: bool = False,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        overwrite_existing: bool = True,
    ) -> list[Episode]:
        """
        Segments a conversation into atomic, fine-grained coherent episodes.
        """
        # 1. Fetch conversation
        conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
        conv = (await self.session.execute(conv_stmt)).scalar_one_or_none()
        if not conv:
            raise FileNotFoundError(f"Conversation {conversation_id} not found")

        # 2. Query messages strictly within [start_time, end_time]
        msg_stmt = (
            select(Message, Participant)
            .outerjoin(Participant, Message.participant_id == Participant.id)
            .where(Message.conversation_id == conversation_id)
        )
        if start_time is not None:
            msg_stmt = msg_stmt.where(Message.sent_at >= start_time)
        if end_time is not None:
            msg_stmt = msg_stmt.where(Message.sent_at <= end_time)

        msg_stmt = msg_stmt.order_by(Message.sent_at.asc(), Message.source_sequence.asc())
        rows = (await self.session.execute(msg_stmt)).all()
        messages_with_p = [(r[0], r[1]) for r in rows]

        if not messages_with_p:
            return []

        # Overwrite Mode: Cascade delete existing episodes and insights in this scope
        if overwrite_existing:
            existing_ep_stmt = select(Episode).where(Episode.conversation_id == conversation_id)
            if start_time is not None:
                existing_ep_stmt = existing_ep_stmt.where(Episode.started_at >= start_time)
            if end_time is not None:
                existing_ep_stmt = existing_ep_stmt.where(Episode.started_at <= end_time)

            existing_episodes = (await self.session.execute(existing_ep_stmt)).scalars().all()
            if existing_episodes:
                ep_ids = [e.id for e in existing_episodes]
                
                ins_stmt = select(Insight.id).where(Insight.episode_id.in_(ep_ids))
                ins_ids = (await self.session.execute(ins_stmt)).scalars().all()

                if ins_ids:
                    from packages.persistence.models import InsightPushRecord
                    await self.session.execute(
                        delete(InsightPushRecord).where(InsightPushRecord.insight_id.in_(ins_ids))
                    )
                    await self.session.execute(
                        delete(TopicInsightLink).where(TopicInsightLink.insight_id.in_(ins_ids))
                    )
                    await self.session.execute(
                        delete(InsightClaim).where(InsightClaim.insight_id.in_(ins_ids))
                    )
                    await self.session.execute(
                        delete(Insight).where(Insight.id.in_(ins_ids))
                    )

                await self.session.execute(
                    delete(EpisodeMessage).where(EpisodeMessage.episode_id.in_(ep_ids))
                )
                await self.session.execute(
                    delete(Episode).where(Episode.id.in_(ep_ids))
                )
                await self.session.commit()

        # 3. Rule-based segmentation by 15-minute coarse time gap + max chunk size cap (25 msgs)
        clusters: list[list[tuple[Message, Participant | None]]] = []
        curr_cluster: list[tuple[Message, Participant | None]] = []

        last_time: datetime | None = None
        for msg, participant in messages_with_p:
            if last_time is not None:
                is_gap_exceeded = (msg.sent_at - last_time) > timedelta(minutes=time_gap_minutes)
                is_size_exceeded = len(curr_cluster) >= 25
                if is_gap_exceeded or is_size_exceeded:
                    if curr_cluster:
                        clusters.append(curr_cluster)
                    curr_cluster = []
            curr_cluster.append((msg, participant))
            last_time = msg.sent_at

        if curr_cluster:
            clusters.append(curr_cluster)

        # 4. Process clusters with Fine-Grained Multi-Topic Decomposition & Concurrency
        from packages.model_gateway.settings_manager import DEFAULT_SYSTEM_PROMPTS, SettingsManager
        settings = SettingsManager.get_settings()
        mod_cfg = settings.segmentation

        provider = self.gateway.get_text_provider(force_mock=force_mock)
        selected_model = mod_cfg.model or getattr(provider, "default_text_model", "deepseek/deepseek-chat")
        reasoning_effort = getattr(mod_cfg, "reasoning_effort", "none")

        semaphore = asyncio.Semaphore(3)

        async def process_single_cluster_chunk(cluster: list[tuple[Message, Participant | None]]) -> list[SubEpisodeItem]:
            # Step 1: Active Chunk Pruning Gate - drop noise/chitchat/empty chunks immediately
            if not self._evaluate_chunk_quality(cluster):
                return []

            # Step 2: Fast-Path for mock mode
            if force_mock:
                return self._infer_cluster_sub_episodes(cluster, conv.display_name)

            # Step 3: Fast-Path for ultra short cluster (1-2 messages) that passed the quality gate
            if len(cluster) <= 2:
                return self._infer_cluster_sub_episodes(cluster, conv.display_name)

            # Prepare numbered messages (chunk is already capped at 25 msgs)
            numbered_messages = []
            for idx, m in enumerate(cluster[:25], 1):
                sender = m[1].display_label if m[1] else "用户"
                numbered_messages.append(f"{idx}. [{m[0].sent_at.strftime('%H:%M:%S')}] {sender}: {m[0].raw_text}")

            cluster_text = "\n".join(numbered_messages)
            system_prompt = mod_cfg.system_prompt or DEFAULT_SYSTEM_PROMPTS.get("segmentation", "")

            user_prompt = (
                f"以下是群聊中一段连续的对话记录（群名：{conv.display_name}），每条消息前面带有序号（如 1., 2. ...）：\n\n"
                f"{cluster_text}\n\n"
                "请严格按上述原则识别提取微话题列表（注意：标题直接陈述讨论内容，严禁使用“用户反馈/咨询/交流”等任何前缀）："
            )

            t_start = datetime.now()
            async with semaphore:
                try:
                    seg_out, usage = await provider.generate_structured(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        response_schema=ChunkSegmentationOutput,
                        temperature=mod_cfg.temperature,
                        top_p=mod_cfg.top_p,
                        top_k=mod_cfg.top_k,
                        max_tokens=max(mod_cfg.max_tokens, 4096),
                        model=selected_model,
                        reasoning_effort=reasoning_effort,
                        enable_thinking=getattr(mod_cfg, "enable_thinking", None),
                        thinking_budget=getattr(mod_cfg, "thinking_budget", None),
                        preserve_thinking=getattr(mod_cfg, "preserve_thinking", None),
                        max_completion_tokens=getattr(mod_cfg, "max_completion_tokens", None),
                    )
                    run_audit = {
                        "workspace_id": conv.workspace_id,
                        "run_type": "episode",
                        "target_type": "conversation",
                        "target_id": conv.id,
                        "provider": type(provider).__name__,
                        "model": selected_model,
                        "started_at": t_start,
                        "completed_at": datetime.now(),
                        "input_tokens": usage.get("prompt_tokens") if usage else None,
                        "output_tokens": usage.get("completion_tokens") if usage else None,
                        "total_tokens": usage.get("total_tokens") if usage else None,
                        "state": "succeeded",
                    }
                    if seg_out and seg_out.sub_episodes:
                        # Clean up any residual template phrases in titles
                        valid_subs = []
                        for sub in seg_out.sub_episodes:
                            sub.title = self._clean_episode_title(sub.title)
                            # Discard generic/too short titles without product substance
                            if len(sub.title) < 4 or any(b in sub.title for b in ["日常交流", "综合交流", "社群互动", "日常操作与使用反馈"]):
                                continue
                            valid_subs.append(sub)
                        return valid_subs, run_audit
                    return self._infer_cluster_sub_episodes(cluster, conv.display_name), run_audit
                except Exception as exc:
                    logger.warning(f"Chunk LLM segmentation failed in {conv.display_name}: {exc}")
                    run_audit = {
                        "workspace_id": conv.workspace_id,
                        "run_type": "episode",
                        "target_type": "conversation",
                        "target_id": conv.id,
                        "provider": type(provider).__name__,
                        "model": selected_model,
                        "started_at": t_start,
                        "completed_at": datetime.now(),
                        "state": "failed",
                        "error_json": {"error": str(exc)},
                    }
                    return self._infer_cluster_sub_episodes(cluster, conv.display_name), run_audit

        # Run chunk decomposition tasks concurrently
        chunk_tasks = [process_single_cluster_chunk(c) for c in clusters]
        raw_chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)

        chunk_results: list[tuple[list[SubEpisodeItem], dict[str, Any] | None]] = []
        for r in raw_chunk_results:
            if isinstance(r, Exception):
                logger.error(f"Unexpected chunk decomposition error in {conv.display_name}: {r}")
                chunk_results.append(([], None))
            elif isinstance(r, tuple):
                chunk_results.append(r)
            elif isinstance(r, list):
                chunk_results.append((r, None))
            else:
                chunk_results.append(([], None))

        created_episodes: list[Episode] = []
        for cluster, (sub_eps, run_audit) in zip(clusters, chunk_results):
            if run_audit and self.session:
                try:
                    analysis_run = AnalysisRun(
                        id=generate_id(),
                        workspace_id=run_audit["workspace_id"],
                        run_type=run_audit["run_type"],
                        target_type=run_audit["target_type"],
                        target_id=run_audit["target_id"],
                        provider=run_audit["provider"],
                        model=run_audit["model"],
                        input_hash=generate_id()[:16],
                        config_hash=generate_id()[:16],
                        state=run_audit["state"],
                        started_at=run_audit["started_at"],
                        completed_at=run_audit["completed_at"],
                        input_tokens=run_audit.get("input_tokens"),
                        output_tokens=run_audit.get("output_tokens"),
                        total_tokens=run_audit.get("total_tokens"),
                        error_json=run_audit.get("error_json"),
                    )
                    self.session.add(analysis_run)
                except Exception as run_err:
                    logger.warning(f"Failed to record AnalysisRun for episode chunk: {run_err}")

            if not sub_eps:
                continue

            for sub_ep in sub_eps:
                # 1. Resolve matched messages strictly
                matched_msgs: list[tuple[Message, Participant | None]] = []
                if sub_ep.matched_message_indices:
                    for m_idx in sub_ep.matched_message_indices:
                        if 1 <= m_idx <= len(cluster):
                            matched_msgs.append(cluster[m_idx - 1])
                
                # Fallback if indices were omitted but sub_ep was returned
                if not matched_msgs:
                    matched_msgs = [m for m in cluster if not self._is_noise_message(m[0].raw_text)]
                if not matched_msgs:
                    continue

                # Sort by sent_at
                matched_msgs.sort(key=lambda m: m[0].sent_at)
                start_time_ep = matched_msgs[0][0].sent_at
                end_time_ep = matched_msgs[-1][0].sent_at
                message_ids = [m[0].id for m in matched_msgs]
                participants = list(dict.fromkeys([m[1].display_label for m in matched_msgs if m[1] and m[1].display_label]))

                # Media attachments for matched messages
                media_stmt = (
                    select(MessageMediaLink.media_id)
                    .where(MessageMediaLink.message_id.in_(message_ids))
                )
                media_ids = list(set((await self.session.execute(media_stmt)).scalars().all()))

                # Assemble tags metadata in category_hint and participants_json
                l1 = sub_ep.category_l1 or "general"
                l2 = sub_ep.category_l2 or ""
                l3 = sub_ep.category_l3 or ""
                cat_hint = f"{l1}:{l2}:{l3}" if (l2 or l3) else l1

                ep = Episode(
                    workspace_id=conv.workspace_id,
                    conversation_id=conversation_id,
                    title=sub_ep.title,
                    summary=sub_ep.topic_summary,
                    category_hint=cat_hint,
                    started_at=start_time_ep,
                    ended_at=end_time_ep,
                    message_count=len(message_ids),
                    participants_json=participants,
                    media_json=media_ids,
                    state="active",
                )
                self.session.add(ep)
                await self.session.flush()

                # Insert EpisodeMessage links
                for seq, m_id in enumerate(message_ids, 1):
                    link = EpisodeMessage(
                        episode_id=ep.id,
                        message_id=m_id,
                        sequence=seq,
                    )
                    self.session.add(link)

                created_episodes.append(ep)

        await self.session.commit()
        return created_episodes

    async def build_context_packet(self, episode_id: str) -> EpisodeContextPacket:
        """
        Builds an EpisodeContextPacket with all linked messages, participants and multimodal annotations.
        """
        ep = await self.session.get(Episode, episode_id)
        if not ep:
            raise FileNotFoundError(f"Episode {episode_id} not found")

        conv = await self.session.get(Conversation, ep.conversation_id)
        conv_name = conv.display_name if conv else "未知群聊"

        # Fetch messages linked to episode
        stmt = (
            select(Message, Participant, EpisodeMessage.sequence)
            .join(EpisodeMessage, EpisodeMessage.message_id == Message.id)
            .outerjoin(Participant, Participant.id == Message.participant_id)
            .where(EpisodeMessage.episode_id == episode_id)
            .order_by(EpisodeMessage.sequence)
        )
        res = await self.session.execute(stmt)
        rows = res.all()

        message_items: list[EpisodeContextItem] = []
        for msg, part, seq in rows:
            # Query media if any
            media_stmt = (
                select(MessageMediaLink)
                .where(MessageMediaLink.message_id == msg.id)
            )
            m_links = (await self.session.execute(media_stmt)).scalars().all()
            media_uris = [f"chatinsight://media/{link.media_id}" for link in m_links]

            role = "user"
            if part and part.metadata_json and part.metadata_json.get("role"):
                role = part.metadata_json.get("role")
            elif any(k in (part.display_label if part else "") for k in ["官方", "客服", "开发", "管理员", "Support"]):
                role = "support"

            sender_label = part.display_label if part else "未知成员"

            message_items.append(
                EpisodeContextItem(
                    message_id=msg.id,
                    sequence=seq,
                    sent_at=msg.sent_at.isoformat() if msg.sent_at else "",
                    sender_label=sender_label,
                    sender_role=role,
                    raw_text=msg.raw_text or "",
                    quote_text=None,
                    media_summaries=[],
                    media_ocr_texts=[],
                    media_uris=media_uris,
                )
            )

        return EpisodeContextPacket(
            episode_id=ep.id,
            conversation_id=ep.conversation_id,
            conversation_name=conv_name,
            title=ep.title,
            topic_summary=ep.summary,
            category_hint=ep.category_hint,
            started_at=ep.started_at.isoformat() if ep.started_at else "",
            ended_at=ep.ended_at.isoformat() if ep.ended_at else "",
            messages=message_items,
        )
