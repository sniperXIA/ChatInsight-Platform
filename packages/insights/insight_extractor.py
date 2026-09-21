from typing import Any, Optional
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.enums import InsightType, SeverityLevel
from packages.domain.models import canonical_json_hash
from packages.insights.contracts import (
    ClaimItem,
    EpisodeContextPacket,
    FactualCheckResult,
    InsightDraft,
)
from packages.insights.episode_segmenter import EpisodeSegmenter
from packages.insights.factual_checker import FactualChecker
from packages.model_gateway.gateway import ModelGateway
from packages.observability.analysis_service import AnalysisService
from packages.persistence.models import Episode, Insight
from packages.persistence.repositories.registry import RepositoryRegistry


class InsightExtractionBatchOutput(BaseModel):
    insights: list[InsightDraft] = Field(default_factory=list, description="从对话片段中提炼的结构化洞察列表（若无有效反馈可为空列表）")


class InsightExtractor:
    """Extracts structured insights and claims with evidence URI bindings from EpisodeContextPackets."""

    def __init__(self, session: AsyncSession, model_gateway: ModelGateway | None = None):
        self.session = session
        self.gateway = model_gateway or ModelGateway()
        self.analysis_service = AnalysisService(session)
        self.checker = FactualChecker()
        self.repo = RepositoryRegistry(session)
        from packages.retrieval.vector_service import VectorService
        self.vector_service = VectorService(session, self.gateway)

    @classmethod
    def _detect_device_model(cls, packet: EpisodeContextPacket) -> str | None:
        """Accurately detects device model (e.g. 'C2', 'U1') from message content or conversation name."""
        import re
        all_raw = " ".join([m.raw_text or "" for m in packet.messages])
        conv_name = packet.conversation_name or ""

        # Priority 1: Direct text mentions
        has_c2_text = bool(re.search(r"(?<![a-zA-Z0-9])[Cc]2(?![a-zA-Z0-9])", all_raw))
        has_u1_text = bool(re.search(r"(?<![a-zA-Z0-9])[Uu]1(?![a-zA-Z0-9])|小[Uu]|初代", all_raw))

        if has_c2_text and not has_u1_text:
            return "C2"
        if has_u1_text and not has_c2_text:
            return "U1"
        if has_c2_text and has_u1_text:
            if any(k in all_raw.lower() for k in ["换c2", "置换c2", "换购c2", "升级c2"]):
                return "C2"
            return "C2"

        # Priority 2: Conversation name prior knowledge
        is_hardware_related = any(kw in all_raw for kw in [
            "拨片", "风格包", "鼓机", "琴身", "按键", "硬件", "手感", "做工", "音色", "扩展卡", "卡槽",
            "固件", "ota", "OTA", "充电", "电池", "蓝牙", "麦克风", "话筒", "换弦", "吉他", "弹奏", "扫弦", "曲谱", "制谱"
        ])
        if "C2" in conv_name or "c2" in conv_name:
            if is_hardware_related or "c2" in conv_name.lower():
                return "C2"
        elif "U1" in conv_name or "u1" in conv_name:
            if is_hardware_related or "u1" in conv_name.lower():
                return "U1"

        return None

    @classmethod
    def _clean_svo_summary(cls, summary: str) -> str:
        """Strips formulaic boilerplate prefixes ('用户反馈', '用户建议', '琴友反映', etc.) to ensure strict SVO."""
        if not summary:
            return ""
        import re
        s = summary.strip()
        # Strip leading quotes
        s = re.sub(r"^[\"“'‘]+|[\"”'’]+$", "", s).strip()
        # Strip prefixes like "用户反馈", "用户建议", "用户咨询", "琴友反映", "琴友在社群中交流", "用户指出", etc.
        s = re.sub(
            r"^(?:群友们?|用户们?|玩家们?|老用户|琴友们?|大家|琴友)?(?:在社群中?|在群聊中?)?(?:集中|积极)?(?:(?:反馈|建议|咨询|交流|探讨|求助|提问|分享|讨论|反映|询问|指出|表达|希望|点赞|给予好评|给出好评)\s*)+[:：\s]*",
            "",
            s,
        ).strip()
        if "便携易上手" in s or "好评心得" in s:
            s = "琴身便携易上手设计与伴奏弹唱体验获得好评"
        elif "物理拨片弹簧过硬" in s:
            s = "物理拨片按压偏硬，快速扫弦容易手指疲劳"
        elif "智能吉他日常弹唱中遭遇异常并提出优化诉求" in s:
            s = "智能乐器日常弹唱中偶发异常，体验不佳"
        elif "弹唱体验并咨询功能使用技巧" in s:
            s = "智能乐器弹唱技巧与日常功能操作咨询"
        elif "曲库扩充热门流行新歌与经典弹唱曲目" in s:
            s = "热门流行新歌与经典弹唱曲目收录较少，建议持续扩充"
        # If summary starts with "有关" or "关于" or "围绕"
        s = re.sub(r"^(?:关于|围绕|针对)[:：\s]*", "", s).strip()
        # Strip any trailing redundant parentheses like (xxx) or （xxx）
        s = re.sub(r"\s*\([^\)]*\)$", "", s).strip()
        s = re.sub(r"\s*（[^）]*）$", "", s).strip()

        # Strip meaningless/generic boilerplate suffixes in a loop:
        # e.g. "，使用交流与操作咨询", "，建议曲库扩充收录", "，功能体验交流", "，功能体验与交流", "，日常交流与功能使用", "，偶发异常体验不佳" 等
        boilerplate_suffixes = [
            r"[，,、\s]*(?:使用交流与操作咨询|功能体验与交流|功能体验交流|日常交流与功能使用|建议曲库扩充收录|曲库扩充收录|操作咨询与使用交流|偶发异常体验不佳|功能使用与交流|使用与操作咨询|体验与日常交流|探讨与交流|使用交流|功能体验|操作咨询|日常交流|探讨交流|交流讨论|体验交流|使用探讨|相关咨询|使用体验)\s*$",
        ]
        for _ in range(3):
            changed = False
            for p in boilerplate_suffixes:
                new_s = re.sub(p, "", s).strip()
                if new_s != s:
                    s = new_s
                    changed = True
            s = re.sub(r"[，,、；;。!！?？\s]+$", "", s).strip()
            if not changed:
                break

        s = re.sub(r"^[\"“'‘]+|[\"”'’]+$", "", s).strip()
        s = re.sub(r"[，,、；;。!！?？\s]+$", "", s).strip()
        return s.strip()

    def _generate_dynamic_heuristic_insight(self, packet: EpisodeContextPacket) -> InsightExtractionBatchOutput:
        """Dynamically generates distinct, fine-grained, realistic insights based on actual message contents and symptoms."""
        if not packet.messages:
            return InsightExtractionBatchOutput(insights=[])

        import re
        # Filter out pure image filename strings and announcements
        valid_msgs = []
        for m in packet.messages:
            txt = (m.raw_text or "").strip()
            # Remove @ mentions
            txt = re.sub(r"@\S+\s*", "", txt).strip()
            if not txt:
                continue
            if re.match(r"^\[[a-f0-9]{20,}\.(jpg|png|gif|jpeg|webp)\]$", txt, re.IGNORECASE):
                continue
            if len(txt) > 2:
                valid_msgs.append((m, txt))

        # Check for pure non-product announcements/chitchat/bot games/daily life talk
        all_raw = " ".join([m.raw_text or "" for m in packet.messages])
        marketing_keywords = [
            "WAIC 2026", "现场见", "相约", "展会现场", "早安", "晚安", "打卡签到",
            "疯狂星期四", "七夕「乞巧」", "Do Re 咪", "真假大挑战", "考眼力的时候到了",
            "Sunset Live", "落日音乐会", "周末练琴"
        ]
        life_chitchat_phrases = [
            "人越来越少", "银杏着实不错", "为什么9月份要分别", "这车也太帅了", "德艺双馨",
            "不过现在出现得也少", "不让加你", "玩私有云不是49年入国军", "群你不要发类似内容",
            "复制打开抖音", "看看你的万水千山", "在抖音，记录美好生活", "正在直播，来和我一起支持",
            "欢迎新朋友", "你把这首曲子弹十遍 然后大喊", "走过那间咖啡屋"
        ]
        has_real_feedback = any(w in all_raw.lower() for w in [
            "怎么", "如何", "卡", "错", "慢", "断", "bug", "更新", "买", "多少钱",
            "坏", "修", "连不上", "发货", "以旧换新", "玻片", "拨片", "鼓机", "呆板",
            "app", "吉他", "琴", "和弦", "曲谱", "音色", "蓝牙", "固件", "声音", "伴奏"
        ])
        if any(w in all_raw for w in marketing_keywords) and not has_real_feedback:
            return InsightExtractionBatchOutput(insights=[])
        if any(w in all_raw for w in life_chitchat_phrases):
            return InsightExtractionBatchOutput(insights=[])
        if packet.title and any(w in packet.title for w in life_chitchat_phrases):
            return InsightExtractionBatchOutput(insights=[])

        if not valid_msgs:
            if packet.title and not packet.title.startswith("微话题"):
                clean_title = packet.title
            else:
                return InsightExtractionBatchOutput(insights=[])
            representative_msg = packet.messages[0]
            clean_msg = clean_title
        else:
            representative_msg = valid_msgs[0][0]
            clean_msg = valid_msgs[0][1]

        # Clean short snippet for dynamic summary
        clean_snippet = clean_msg[:35].strip()
        if clean_snippet.endswith(("，", "。", "！", "？", "、", ",")):
            clean_snippet = clean_snippet[:-1]

        detected_model = self._detect_device_model(packet)

        # Scenario & Trigger Detection for fine-grained differentiation
        matched_module = "综合交流"
        matched_sub_mod = "功能使用"
        ins_type = InsightType.INQUIRY
        sev = SeverityLevel.MINOR
        summary = ""
        symptom_detail = clean_snippet
        scenario_detail = "日常使用与交流"
        trigger_detail = "常规操作场景"
        tags = ["社群反馈"]

        has_support = any(m.sender_role in ("support", "developer") for m in packet.messages)
        status_in_chat = "support_acknowledged" if has_support else "reported"

        # 1. Badcase 1 Specific & Generalized: Multi-track style pack & picks
        if (any(w in all_raw for w in ["风格包", "多档", "多轨", "玻片", "拨片"]) and any(w in all_raw for w in ["a玻片", "b拨片", "A拨片", "B拨片", "换", "只能", "不行", "支持"])) or ("多轨风格包仅支持B拨片弹奏" in packet.title or "多档风格包" in all_raw):
            summary = "多轨风格包仅支持B拨片弹奏，使用不便"
            matched_module = "配置与音色"
            matched_sub_mod = "伴奏风格包"
            ins_type = InsightType.ISSUE
            sev = SeverityLevel.MINOR
            detected_model = detected_model or "C2"
            symptom_detail = "多轨/多档风格包演奏仅支持B拨片弹奏，无法使用A拨片弹奏与切歌，操作体验不便"
            scenario_detail = "风格包加载与硬件拨片弹奏链路"
            trigger_detail = "使用A拨片弹奏多轨伴奏风格包时"
            tags = ["LiberLive C2", "伴奏风格包", "拨片弹奏", "多轨切换"]

        # 2. Badcase 2 Specific & Generalized: AI sheet generation & drum machine
        elif (("ai" in all_raw.lower() or "生成" in all_raw or "制谱" in all_raw or "曲谱" in all_raw) and any(w in all_raw for w in ["鼓机", "自动添加", "呆板", "没有鼓机", "缺少鼓机"])) or ("AI生成曲谱风格呆板" in packet.title or "未自动添加鼓机" in all_raw):
            summary = "AI生成曲谱没有自动鼓机，演奏呆板不生动"
            matched_module = "曲谱与乐库"
            matched_sub_mod = "AI制谱与扒谱"
            ins_type = InsightType.ISSUE
            sev = SeverityLevel.MINOR
            detected_model = detected_model or "C2"
            symptom_detail = "AI生成的曲谱未自动添加鼓机伴奏轨，且整体编曲风格过于生硬呆板"
            scenario_detail = "App内AI曲谱生成与试听回放"
            trigger_detail = "在App中使用AI扒谱生成曲谱并试听演奏"
            tags = ["LiberLive C2", "AI制谱", "鼓机伴奏", "编曲质量"]

        # 3. Drum machine rhythm / meter / operation issues
        elif "鼓机" in all_raw:
            matched_module = "配置与音色"
            matched_sub_mod = "鼓机与节拍BPM"
            detected_model = detected_model or "C2"
            tags = ["鼓机伴奏", "节拍节奏", "BPM"]
            scenario_detail = "机身鼓机模式与节拍控制"
            if any(w in all_raw for w in ["错位", "强弱拍", "反调", "混乱", "不准", "踩点"]):
                summary = "鼓机节拍强弱拍错位，伴奏节奏不够精准"
                ins_type = InsightType.ISSUE
                sev = SeverityLevel.MAJOR
                symptom_detail = "鼓机伴奏强弱拍与歌曲重音错位，影响节奏踩点"
                trigger_detail = "弹唱开启鼓机自动伴奏时"
            elif any(w in all_raw for w in ["长按", "关闭", "进入", "退出", "卡顿", "找不到"]):
                summary = "鼓机模式长按开关与进入退出操作不够顺畅"
                ins_type = InsightType.ISSUE
                sev = SeverityLevel.MINOR
                symptom_detail = "用户反映进入或退出鼓机模式时按键响应存在迟滞"
                trigger_detail = "长按拨片或按键切换鼓机模式时"
            else:
                summary = "鼓机自动跟随与音量调节参数设置咨询"
                ins_type = InsightType.INQUIRY
                sev = SeverityLevel.MINOR
                symptom_detail = "用户在社群交流鼓机伴奏参数调节与节奏搭配技巧"
                trigger_detail = "调节鼓机音量或匹配曲目时"

        # 4. Mobile App & Software Ecological (App Download, Links, APK, App updates)
        elif any(kw in all_raw for kw in ["app下载", "下载链接", "安装包", "apk", "testflight", "应用商店", "appstore", "新版app", "软件下载", "下载地址", "客户端下载", "美区", "海外版"]) or (("app" in all_raw.lower() or "软件" in all_raw or "客户端" in all_raw) and any(w in all_raw for w in ["下载", "链接", "安装", "更新", "升级", "版本", "地址", "闪退", "打不开"])):
            matched_module = "软件与App生态"
            matched_sub_mod = "软件下载与安装更新"
            scenario_detail = "移动端App获取与客户端环境"
            tags = ["移动端App", "软件安装", "版本升级"]

            if any(w in all_raw for w in ["闪退", "打不开", "崩溃", "卡死", "白屏"]):
                summary = "移动端App启动偶发闪退，页面运行卡死无响应"
                ins_type = InsightType.ISSUE
                sev = SeverityLevel.MAJOR
                symptom_detail = "App客户端启动时闪退或在特定交互页面卡死无响应"
                trigger_detail = "启动App或操作特定页面时"
            elif any(w in all_raw for w in ["美区", "海外", "id", "换区"]):
                summary = "海外版App下载渠道受限，咨询跨区安装操作指引"
                ins_type = InsightType.INQUIRY
                symptom_detail = "海外用户询问iOS/Android在不同地区商店获取App的指引"
                trigger_detail = "海外或更换应用商店区域时"
            else:
                summary = "新版移动端App未上架，缺少官方下载渠道与安装包"
                ins_type = InsightType.INQUIRY
                sev = SeverityLevel.MINOR
                symptom_detail = "用户询问最新版本App发布渠道、官方安装包或内测更新链接"
                trigger_detail = "需要更新App或换机重新安装客户端"

        # 5. Firmware & System Stability (Only Physical Guitar Embedded MCU/OTA)
        elif any(kw in all_raw for kw in ["固件", "ota", "OTA", "刷写", "刷机", "变砖", "机身系统"]) or (any(kw in all_raw for kw in ["升级", "更新"]) and any(kw in all_raw for kw in ["设备", "吉他", "琴身", "小卡", "开机", "指示灯"])):
            matched_module = "固件与电源管理"
            matched_sub_mod = "OTA固件在线升级"
            ins_type = InsightType.ISSUE
            sev = SeverityLevel.MAJOR
            tags = ["固件升级", "系统稳定性", "OTA"]
            scenario_detail = "吉他琴身固件升级与版本维护流程"
            detected_model = detected_model or "C2"

            if any(w in all_raw for w in ["99%", "卡住", "卡在", "进度条", "停滞", "不动"]):
                summary = "OTA固件升级在99%卡死，导致刷写失败"
                symptom_detail = "固件升级传输进度停滞在99%超时，设备无法完成写入"
                trigger_detail = "OTA固件升级最后校验传输阶段"
            elif any(w in all_raw for w in ["死机", "开不了机", "黑屏", "变砖"]):
                summary = "固件升级中断导致设备死机或无法开机"
                sev = SeverityLevel.BLOCKER
                symptom_detail = "固件升级中断后设备指示灯异常且无法正常开机"
                trigger_detail = "固件刷写阶段异常断电或断连"
            else:
                summary = "琴身OTA固件版本升级进度缓慢，更新提示不明显"
                symptom_detail = "检测到固件更新后点击响应迟滞，下载与传输耗时较长"
                trigger_detail = "在设备设置页检测并启动固件更新"

        # 6. Bluetooth & Wireless
        elif any(kw in all_raw for kw in ["蓝牙", "连接", "配对", "连不上", "掉线", "延迟", "断联", "重连"]):
            matched_module = "蓝牙与无线"
            matched_sub_mod = "伴奏传输与延迟"
            ins_type = InsightType.ISSUE
            sev = SeverityLevel.MAJOR
            tags = ["蓝牙连接", "无线传输"]
            scenario_detail = "设备与App无线蓝牙交互"
            detected_model = detected_model or "C2"

            if any(w in all_raw for w in ["延迟", "音画不同步", "慢半拍", "声音延迟"]):
                summary = "蓝牙伴奏无线推流存在明显音频延迟，音画不同步"
                symptom_detail = "弹唱演奏时蓝牙伴奏音频传输延迟，与手机端歌词画面不同步"
                trigger_detail = "高采样率伴奏无线推流时"
            else:
                summary = "琴身蓝牙与手机App配对连接偶发超时断开"
                symptom_detail = "正在弹奏时蓝牙连接偶发自动断开，重连耗时较长"
                trigger_detail = "连续弹唱或手机息屏后台运行"

        # 7. Sheet Music & Library
        elif any(kw in all_raw for kw in ["曲谱", "和弦", "伴奏", "扒谱", "新歌", "歌单", "自建", "导入", "错谱", "纯音乐", "指弹"]):
            matched_module = "曲谱与乐库"
            matched_sub_mod = "求谱与乐库扩充"
            ins_type = InsightType.FEATURE_REQUEST
            sev = SeverityLevel.MINOR
            tags = ["曲谱乐库", "和弦伴奏", "曲谱扩展"]
            scenario_detail = "App曲库与演奏界面"

            if any(w in all_raw for w in ["字号", "字体", "调大", "调小", "看不清", "放大", "缩放", "横屏", "竖屏", "排版"]):
                summary = self._clean_svo_summary(packet.title or "平板或手机端App曲谱字体字号调节方法")
                matched_module = "界面与显示"
                matched_sub_mod = "字体与清晰度"
                symptom_detail = clean_snippet or "曲谱页面字体字号偏小或排版显示影响阅读"
                trigger_detail = "在平板或手机端查看曲谱"
            elif any(w in all_raw for w in ["导入", "自建", "自制", "编辑", "自定义", "本地谱", "txt", "pdf", "第三方"]):
                summary = self._clean_svo_summary(packet.title or "伴奏曲谱和弦自定义编辑与第三方乐谱文件导入")
                matched_sub_mod = "自定义与导入"
                symptom_detail = clean_snippet or "用户希望自主编辑曲谱和弦或导入第三方乐谱文件"
                trigger_detail = "曲谱自定义与编辑导入场景"
            elif any(w in all_raw for w in ["ai", "AI", "制谱", "扒谱", "生成"]):
                summary = self._clean_svo_summary(packet.title or "AI制谱与和弦自动识别生成效果优化")
                matched_sub_mod = "AI制谱与扒谱"
                symptom_detail = clean_snippet or "用户针对AI自动扒谱、和弦生成或制谱准确度提出反馈"
                trigger_detail = "使用AI制谱与扒谱功能"
            elif any(w in all_raw for w in ["标错", "错谱", "和弦不对", "歌词错", "纠错"]):
                summary = "曲谱和弦标注与歌词排版存在偏差，需要纠错修正"
                ins_type = InsightType.ISSUE
                symptom_detail = "用户指出特定曲目存在和弦标注失准或歌词对齐排版瑕疵"
                trigger_detail = "演奏该曲目扫弦伴奏时"
            elif any(w in all_raw for w in ["纯音乐", "指弹", "独奏", "轻音乐"]):
                summary = "曲库纯音乐指弹与独奏吉他谱偏少，期望扩充收录"
                symptom_detail = "用户希望官方增加不带歌词的纯器乐演奏指弹谱与精选伴奏"
                trigger_detail = "曲库查找纯音乐指弹曲目时"
            elif any(w in all_raw for w in ["新歌", "扩充", "收录", "没有这首", "搜不到", "上架", "没有歌"]):
                summary = "热门流行新歌与经典弹唱曲目收录较少，建议持续扩充"
                symptom_detail = "用户表达对特定歌手新歌上线及多样化弹唱歌单的扩充诉求"
                trigger_detail = "日常弹唱选曲流程"
            else:
                clean_cand = self._clean_svo_summary(packet.title or clean_snippet)
                summary = clean_cand if len(clean_cand) >= 4 else "曲谱与乐库功能使用及操作反馈"
                symptom_detail = clean_snippet or "社群成员探讨曲库功能与曲谱使用"
                trigger_detail = "日常曲库浏览与使用"

        # 8. Sound & Tone Expansion
        elif any(kw in all_raw for kw in ["音色", "扩展卡", "音效", "声音", "音质", "失真", "杂音", "爆音", "小U", "引擎"]):
            matched_module = "配置与音色"
            matched_sub_mod = "音色品质与扩展"
            tags = ["音色扩展", "音效音质", "效果器"]
            scenario_detail = "音色卡插槽与音频输出链路"
            detected_model = detected_model or "C2"

            if any(w in all_raw for w in ["杂音", "爆音", "破音", "失真", "电流声"]):
                summary = "外接音频与弹唱时扬声器偶发杂音破音，声音失真"
                ins_type = InsightType.ISSUE
                sev = SeverityLevel.MAJOR
                symptom_detail = "大动态弹奏或外接音箱时扬声器输出偶发破音/电流杂音"
                trigger_detail = "强力度拨片弹奏或外接3.5mm音频输出"
            else:
                summary = "音色扩展卡支持列表与音效预设调节说明不够详尽"
                ins_type = InsightType.INQUIRY
                symptom_detail = "用户在社群交流音色卡加载配置与音效参数调节技巧"
                trigger_detail = "配置音色与音效卡场景"

        # 9. Hardware, Body & Accessories
        elif any(kw in all_raw for kw in ["拨片", "弹簧", "按键", "手感", "琴身", "做工", "缝隙", "掉漆", "材质", "指示灯", "灯亮", "白灯", "绿灯", "话筒", "麦架", "以旧换新", "换购"]):
            matched_module = "硬件与外设"
            matched_sub_mod = "机身工艺与按键配件"
            tags = ["硬件做工", "按键手感", "配件支持"]
            scenario_detail = "实体硬件外观与机械结构"
            detected_model = detected_model or "C2"

            if any(w in all_raw for w in ["以旧换新", "换购", "u1换c2", "折抵"]):
                summary = "早期U1型号置换新款C2，咨询以旧换新抵扣政策"
                ins_type = InsightType.INQUIRY
                matched_module = "物流与售后"
                matched_sub_mod = "置换与换新政策"
                symptom_detail = "老用户询问官方是否支持旧琴折价置换新一代旗舰吉他"
                trigger_detail = "新品上市了解换购福利"
            elif any(w in all_raw for w in ["话筒", "麦克风", "支架", "麦架", "收音"]):
                summary = "外设动圈麦克风收音距离偏近，期望优化防啸叫表现"
                ins_type = InsightType.FEATURE_REQUEST
                symptom_detail = "用户询问外接麦克风收音距离与防啸叫设置"
                trigger_detail = "外接麦克风弹唱场景"
            elif any(w in all_raw for w in ["拨片", "回弹", "阻尼", "硬", "软", "疲劳", "手酸"]):
                summary = "物理拨片按压偏硬，快速扫弦容易手指疲劳"
                ins_type = InsightType.ISSUE
                symptom_detail = "用户反馈拨片弹簧力度偏硬，快速扫弦时手指易疲劳"
                trigger_detail = "快速扫弦连续弹唱"
            else:
                summary = "琴身机械做工公差与外观接缝装配细节反馈"
                symptom_detail = "用户讨论实体吉他琴身做工、边缘公差与材质细节"
                trigger_detail = "实体琴把玩与观察"

        # 10. Logistics & After-sales
        elif any(kw in all_raw for kw in ["发货", "顺丰", "快递", "到货", "售后", "退货", "换货", "客服", "保修"]):
            matched_module = "物流与售后"
            matched_sub_mod = "顺丰时效与物流单号"
            ins_type = InsightType.INQUIRY
            tags = ["发货时效", "售后保障", "物流服务"]
            summary = "商城订单顺丰发货时效与售后退换保障细则咨询"
            symptom_detail = "新下单用户咨询顺丰发货进度与售后条款"
            trigger_detail = "完成下单后等待发货"

        # 11. Praise & Positive Signals
        elif any(kw in all_raw for kw in ["好听", "喜欢", "太棒了", "方便", "神器", "牛", "推荐", "好玩", "赞"]):
            matched_module = "综合交流"
            matched_sub_mod = "日常弹唱与好评心得"
            ins_type = InsightType.PRAISE
            tags = ["用户好评", "便携设计", "易上手"]
            summary = "琴身便携易上手设计与伴奏弹唱体验获得好评"
            symptom_detail = "用户表达对智能乐器便携性与伴奏体验的高度认可"
            trigger_detail = "日常弹唱分享"

        # Fallback dynamic synthesis with clean SVO
        else:
            is_issue = any(w in all_raw for w in ["错", "卡", "掉", "慢", "断", "bug", "死", "难", "重"])
            ins_type = InsightType.ISSUE if is_issue else InsightType.INQUIRY
            sev = SeverityLevel.MAJOR if is_issue else SeverityLevel.MINOR
            clean_cand = self._clean_svo_summary(packet.title or clean_snippet)
            if len(clean_cand) < 4:
                clean_cand = "智能乐器日常功能使用"
            summary = clean_cand
            symptom_detail = f"社群成员集中探讨: {clean_snippet}"
            trigger_detail = "日常社群交流与功能使用"

        summary = self._clean_svo_summary(summary)

        from packages.insights.tag_manager import TagManager
        l1_k, l2_k, disp_name = TagManager.get_best_category_and_tags(f"{summary} {packet.title} {matched_module}")
        if disp_name and disp_name != "综合交流":
            matched_module = disp_name

        desc_5w1h = (
            f"[场景/位置] {scenario_detail}\n"
            f"[具体现象] {symptom_detail}\n"
            f"[影响程度] {'影响核心弹唱与关键功能，需排查处理' if sev in (SeverityLevel.BLOCKER, SeverityLevel.MAJOR) else '产生操作疑问或次要体验优化诉求'}\n"
            f"[触发条件] {trigger_detail}\n"
            f"[社群进展] {'官方客服人员已在对话中介入答复' if has_support else '群内用户初次提出反馈，待进一步跟进'}"
        )

        claim_text = f"群成员反馈: {representative_msg.raw_text[:45]}" if representative_msg.raw_text else summary
        first_ref = f"chatinsight://conv/{packet.conversation_id}/msg_{representative_msg.message_id}#text"

        draft = InsightDraft(
            insight_type=ins_type,
            module=matched_module,
            sub_module=matched_sub_mod,
            severity=sev,
            device_model=detected_model,
            summary=summary,
            description=desc_5w1h,
            status_in_chat=status_in_chat,
            support_known_status=has_support,
            claims=[
                ClaimItem(
                    claim_id="c_1",
                    claim_text=claim_text,
                    fact_category="symptom",
                    evidence_uris=[first_ref],
                    confidence=0.96,
                )
            ],
            suggested_tags=tags,
            confidence=0.92,
        )

        return InsightExtractionBatchOutput(insights=[draft])

    async def _resolve_unique_summary(self, base_summary: str, workspace_id: str, ep: Episode) -> str:
        """Guarantees that the summary is differentiated and unique across the workspace without ugly bracket concatenation."""
        base_clean = self._clean_svo_summary(base_summary)
        from sqlalchemy import select
        stmt = select(Insight.id).where(
            Insight.workspace_id == workspace_id,
            Insight.summary == base_clean,
            Insight.episode_id != ep.id,
        )
        exists = (await self.session.execute(stmt)).scalars().first()
        if not exists:
            return base_clean

        time_str = ep.started_at.strftime("%m-%d %H:%M") if hasattr(ep.started_at, "strftime") else str(ep.started_at)[5:16]
        candidate = f"{base_clean} [{time_str}]"

        stmt2 = select(Insight.id).where(
            Insight.workspace_id == workspace_id,
            Insight.summary == candidate,
            Insight.episode_id != ep.id,
        )
        exists2 = (await self.session.execute(stmt2)).scalars().first()
        if not exists2:
            return candidate

        return f"{base_clean} [{time_str} #{ep.id[:4]}]"

    async def extract_insights_from_episode(
        self,
        episode_id: str,
        force_mock: bool = False,
        model_override: str | None = None,
    ) -> list[tuple[Insight, FactualCheckResult]]:
        # 0. Single-episode Idempotency: Clean up prior insights, claims, and links for this episode
        from packages.persistence.models import InsightClaim, TopicInsightLink
        from sqlalchemy import delete, select
        old_ins_res = await self.session.execute(select(Insight.id).where(Insight.episode_id == episode_id))
        old_ins_ids = old_ins_res.scalars().all()
        if old_ins_ids:
            await self.session.execute(delete(TopicInsightLink).where(TopicInsightLink.insight_id.in_(old_ins_ids)))
            await self.session.execute(delete(InsightClaim).where(InsightClaim.insight_id.in_(old_ins_ids)))
            await self.session.execute(delete(Insight).where(Insight.id.in_(old_ins_ids)))
            await self.session.flush()

        # 1. Assemble Episode Context Packet
        segmenter = EpisodeSegmenter(self.session, self.gateway)
        packet = await segmenter.build_context_packet(episode_id)

        ep = await self.repo.get_episode_by_id(episode_id)
        if not ep:
            raise FileNotFoundError(f"Episode {episode_id} not found")

        # 1.1 Fast noise & trivial chitchat pruning (save API tokens & speed up processing)
        if not packet.messages or len(packet.messages) == 0:
            return []

        if len(packet.messages) <= 2:
            combined_text = "".join(m.raw_text or "" for m in packet.messages).strip().lower()
            has_media = any(m.media_uris for m in packet.messages)
            if not has_media and len(combined_text) < 14 and any(combined_text.startswith(k) for k in ["早", "晚安", "签到", "哈哈", "收到", "好的", "ok", "666", "厉害"]):
                return []

        # 2. Build Context Prompt with Multimodal & Role Signals
        context_lines = []
        for msg in packet.messages:
            ref_uri = f"chatinsight://conv/{packet.conversation_id}/msg_{msg.message_id}#text"
            media_info = ""
            if msg.media_ocr_texts:
                media_info += f" [视觉确凿实证/OCR文字: {'; '.join(msg.media_ocr_texts)}]"
            if msg.media_summaries:
                media_info += f" [截图画面内容: {'; '.join(msg.media_summaries)}]"

            context_lines.append(
                f"- [ID: msg_{msg.message_id}] [{msg.sent_at[11:19]}] {msg.sender_label} ({msg.sender_role}): {msg.raw_text}{media_info}  (URI: {ref_uri})"
            )

        transcript_prompt = "\n".join(context_lines)

        # 2.1 Extract role and cross-user validation signals
        unique_users = {m.sender_label for m in packet.messages if m.sender_role != "bot"}
        has_support = any(m.sender_role in ("support", "developer") for m in packet.messages)
        support_names = [m.sender_label for m in packet.messages if m.sender_role in ("support", "developer")]

        signals = []
        if len(unique_users) >= 2:
            signals.append(f"多用户交叉讨论 ({len(unique_users)} 位用户)")
        if has_support:
            signals.append(f"官方技术支持人员已介入答复 ({', '.join(set(support_names))})")
        signal_note = f"【社群上下文信号】{' · '.join(signals)}\n" if signals else ""

        # 3. Model setup
        from packages.model_gateway.settings_manager import SettingsManager
        settings = SettingsManager.get_settings()
        mod_cfg = settings.insight_extraction

        from packages.model_gateway.settings_manager import DEFAULT_SYSTEM_PROMPTS
        system_prompt = (mod_cfg.system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPTS.get("insight_extraction", "")

        user_prompt = (
            f"群聊名称：{packet.conversation_name}\n"
            f"话题标题：{packet.title}\n"
            f"{signal_note}"
            f"对话记录：\n{transcript_prompt}\n\n"
            "请根据上述要求分析对话，深度提炼原子化、细分具体的产品需求洞察（Insight）：\n"
            "1. 标题（summary）：严格采用【功能/对象】+【具体限制/缺陷/事实】+【，】+【影响/期望/负向情绪】的主谓宾客观陈述结构（12~28字）。\n"
            "   - 严禁套话前缀与废话尾缀：绝对禁止使用“用户反馈/用户建议/琴友反映”等无意义开头，绝对禁止在标题末尾机械拼接“，使用交流与操作咨询”、“，建议曲库扩充收录”、“，功能体验交流”、“，偶发异常体验不佳”等无实义填充词！宁可简洁也要优先确保真实准确！\n"
            "   - 严禁张冠李戴与过度概括：如曲谱字体调整、文件导入编辑等具体功能问题严禁错配为曲库扩充收录；\n"
            "   - 正例：“多轨风格包仅支持B拨片弹奏，使用不便”、“AI生成曲谱没有自动鼓机，演奏呆板不生动”、“物理拨片按压偏硬，快速扫弦容易手指疲劳”；\n"
            "2. 设备机型提取（device_model）：识别该洞察明确针对的硬件设备型号（如 C2、U1 等）。若原声直接提及或群聊名称明确指向特定硬件，填写对应型号（如 C2）；若为跨端通用软件App或无法明确机型，则填入 null（空值）；\n"
            "3. 边界清晰：严格区分移动端App软件生态（下载/安装包/App更新/App闪退）与机身嵌入式固件系统（吉他OTA固件/死机变砖）；\n"
            "4. 5W1H 事实描述：description 严格按照 [场景/位置]、[具体现象]、[影响程度]、[环境/触发条件]、[社群进展] 5个维度分行交代事实，具体现象必须高度忠于真实对话原声；\n"
            "5. 附带 1~2 条可严格核验的 Claim 并绑定对应发言 URI。若纯为生活闲聊寒暄或无明确产品价值，返回空列表 []。"
        )

        provider = self.gateway.get_text_provider(force_mock=force_mock)
        provider_name = "mock" if force_mock else (settings.provider or "openrouter")
        selected_model = model_override or mod_cfg.model or getattr(provider, "default_text_model", "deepseek/deepseek-chat")

        async def _call_llm():
            if force_mock:
                # Dynamic heuristic extraction based on real message contents
                mock_out = self._generate_dynamic_heuristic_insight(packet)
                return mock_out, {"prompt_tokens": 500, "completion_tokens": 200, "total_tokens": 700}

            token_limit = max(mod_cfg.max_tokens or 8192, 8192)
            effort = getattr(mod_cfg, "reasoning_effort", None)
            return await provider.generate_structured(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_schema=InsightExtractionBatchOutput,
                temperature=mod_cfg.temperature,
                top_p=mod_cfg.top_p,
                top_k=mod_cfg.top_k,
                max_tokens=token_limit,
                model=selected_model,
                reasoning_effort=effort,
                enable_thinking=getattr(mod_cfg, "enable_thinking", None),
                thinking_budget=getattr(mod_cfg, "thinking_budget", None),
                preserve_thinking=getattr(mod_cfg, "preserve_thinking", None),
                max_completion_tokens=getattr(mod_cfg, "max_completion_tokens", None),
            )

        # 4. Cached Execution via AnalysisService (Bound to system prompt hash)
        import hashlib
        prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]
        output, run, was_cached = await self.analysis_service.execute_cached(
            workspace_id=ep.workspace_id,
            run_type="insight_extraction",
            target_type="episode",
            target_id=ep.id,
            provider=provider_name,
            model=selected_model,
            prompt_key="insight_extraction_v6_svo_device",
            input_data={"episode_id": ep.id, "msg_count": len(packet.messages)},
            config_data={
                "model": selected_model,
                "schema": "InsightExtractionBatchOutput_v6",
                "temperature": mod_cfg.temperature,
                "top_p": mod_cfg.top_p,
                "prompt_hash": prompt_hash,
            },
            response_schema=InsightExtractionBatchOutput,
            call_fn=_call_llm,
        )

        # 5. Factual Consistency Check & Persistence
        results: list[tuple[Insight, FactualCheckResult]] = []

        from packages.insights.tag_manager import TagManager

        for draft in output.insights:
            check_result = await self.checker.check_insight_claims_semantic(
                draft, packet, vector_service=self.vector_service, force_mock=force_mock
            )

            # Resolve hierarchical category and tags from TagManager
            analysis_text = f"{draft.summary} {draft.description} {ep.title}"
            l1_key, l2_key, display_name = TagManager.get_best_category_and_tags(analysis_text)

            # Build rich tag list preserving hierarchy
            final_tags = list(draft.suggested_tags or [])
            parts = [p.strip() for p in display_name.split(" · ")] if " · " in display_name else [display_name]
            for p in reversed(parts):
                if p and p not in final_tags:
                    final_tags.insert(0, p)

            final_module = display_name if display_name and display_name != "综合交流" else (draft.module or "综合交流")
            final_sub_module = draft.sub_module or (parts[1] if len(parts) > 1 else None)
            # Ensure unique, differentiated summary across workspace
            final_summary = await self._resolve_unique_summary(draft.summary, ep.workspace_id, ep)

            # Persist Insight
            ins = await self.repo.create_insight(
                workspace_id=ep.workspace_id,
                episode_id=ep.id,
                insight_type=draft.insight_type.value,
                module=final_module,
                sub_module=final_sub_module,
                severity=draft.severity.value,
                device_model=draft.device_model,
                summary=final_summary,
                description=draft.description,
                status_in_chat=draft.status_in_chat,
                support_known_status=draft.support_known_status,
                factual_score=check_result.overall_factual_score,
                confidence=draft.confidence,
                tags=final_tags,
                analysis_run_id=run.id,
            )

            # Persist Claims
            for c in draft.claims:
                await self.repo.add_insight_claim(
                    insight_id=ins.id,
                    claim_key=c.claim_id,
                    claim_text=c.claim_text,
                    fact_category=c.fact_category,
                    evidence_uris=c.evidence_uris,
                    confidence=c.confidence,
                    verification_state=c.verification_state,
                )

            results.append((ins, check_result))

        return results
