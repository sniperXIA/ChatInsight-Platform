import re
from collections import defaultdict
from typing import Any, Optional
from pydantic import BaseModel, Field

from packages.insights.tag_manager import TagManager


class EpisodeItemView(BaseModel):
    id: str
    conversation_id: str
    conversation_name: Optional[str] = None
    title: str
    summary: str
    category_hint: str
    category_l1: Optional[str] = "general"
    category_l2: Optional[str] = None
    category_l3: Optional[str] = None
    l1_tag_zh: Optional[str] = "综合交流"
    l2_tag_zh: Optional[str] = None
    l3_tag_zh: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    started_at: str
    ended_at: str
    message_count: int
    participants: list[str] = Field(default_factory=list)
    media_ids: list[str] = Field(default_factory=list)
    state: str = "active"


class DistinctTopicCluster(BaseModel):
    id: str
    canonical_title: str
    category_hint: str
    summary: str
    category_l1: Optional[str] = "general"
    category_l2: Optional[str] = None
    category_l3: Optional[str] = None
    l1_tag_zh: Optional[str] = "综合交流"
    l2_tag_zh: Optional[str] = None
    l3_tag_zh: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    episode_count: int
    total_messages: int
    participants: list[str]
    conversations: list[str]
    started_at: str
    ended_at: str
    episodes: list[EpisodeItemView]


class EpisodeDeduplicator:
    """
    Groups and deduplicates raw conversation episodes into distinct, fine-grained topic clusters.
    
    Key Features:
    1. Zero-template dynamic SVO synthesis: Never forcibly overwrite cluster titles with static hardcoded templates;
    2. Accurate semantic domain signatures for clustering;
    3. Multi-level taxonomy tags (L1 / L2 / L3 breadcrumbs);
    4. Strict elimination of pure non-product chit-chat;
    5. Multi-dimensional timeline sorting (latest / first_seen / frequency).
    """

    THEME_SIGNATURES = [
        # 1. UI & Visual Clarity
        (
            "ui_font_visibility",
            "ui_ux",
            "ui_font",
            "字体大小与清晰度",
            r"(看不清|看不清楚|看不见|字太小|字号|字体|瞎了|太小了|字体大小|字太密|放大|清晰度)",
            ["界面与显示", "字体与清晰度", "字号放大与清晰度"],
        ),
        (
            "ui_theme_mode",
            "ui_ux",
            "ui_theme",
            "深色模式与主题换肤",
            r"(皮肤|暗黑|深色|夜间模式|配色|刺眼|太亮|主题换肤|护眼)",
            ["界面与显示", "皮肤与暗黑模式", "自定义护眼主题"],
        ),
        (
            "ui_layout_display",
            "ui_ux",
            "ui_layout",
            "排版与按键遮挡",
            r"(遮挡|排版|布局|横屏|竖屏|界面重叠|错位|ui卡顿|显示不全)",
            ["界面与显示", "排版与界面布局", "横竖屏与元素遮挡"],
        ),

        # 2. Sheet Music & Library
        (
            "sheet_error_report",
            "sheet_music",
            "sheet_report",
            "曲谱纠错与勘误",
            r"(错谱|标错|和弦不对|歌词错|谱子错|曲谱报错|小节不对|缺字)",
            ["曲谱与乐库", "曲谱报错纠错", "和弦歌词勘误"],
        ),
        (
            "sheet_music_request",
            "sheet_music",
            "sheet_request",
            "流行新歌版权求谱",
            r"(求谱|加歌|乐库没有|没有这首歌|希望能出|求加歌|想要这首|周杰伦)",
            ["曲谱与乐库", "热门求谱与扩充", "流行新歌版权伴奏"],
        ),
        (
            "sheet_ai_and_manual_creation",
            "sheet_music",
            "sheet_manual_create",
            "和弦自定义编辑与导入",
            r"(ai制谱|ai扒谱|智能制谱|手动制谱|自建谱|导入曲谱|自制曲谱|制谱工具|编辑和弦|改和弦|和弦自定义|自定义编辑|修改和弦|和弦谱)",
            ["曲谱与乐库", "曲谱自建与导入", "和弦自定义编辑"],
        ),

        # 3. Performance & Tracking
        (
            "performance_tracking",
            "performance",
            "play_track",
            "谱面走带与节拍同步",
            r"(走带|卡顿|跳音|谱面卡住|光标跳|走带慢|走带快|跟不上谱|滚屏)",
            ["弹唱与走带", "光标走带与流畅度", "跳音与节拍同步"],
        ),
        (
            "performance_recording_and_mic",
            "performance",
            "video_recording",
            "室内弹唱录音与音量平衡",
            r"(歌声很强|琴声很弱|录不到琴声|外录|内录|录下来效果|高声抑制|录音机|录音频|分屏录制|悬浮录屏|录像|录屏|录音按钮)",
            ["弹唱与走带", "视频与音频录制", "录音人声音量平衡"],
        ),
        (
            "performance_vocal_and_multitrack",
            "performance",
            "play_vocal",
            "人声导唱与分轨调节",
            r"(旋律跟唱|导唱|人声跟唱|人声消除|伴唱|多轨|分轨|伴奏分轨|多轨道)",
            ["弹唱与走带", "人声跟唱与导唱", "多轨伴奏分轨"],
        ),
        (
            "performance_cast_and_rating",
            "performance",
            "play_cast",
            "无线投屏与打分合奏",
            r"(投屏|屏幕共享|电视投屏|大屏投屏|合奏|打分系统|评分|练习反馈|节奏准确率)",
            ["弹唱与走带", "大屏投屏与合奏", "弹唱智能打分"],
        ),

        # 3.5 Drum Machine & Rhythm Controls
        (
            "drum_machine_operation",
            "performance",
            "drum_machine_operation",
            "鼓机进入与关闭操作",
            r"(鼓机|进鼓|关鼓|长按关闭|节奏垫|自动鼓机|鼓点速度|鼓机关不掉)",
            ["弹唱与走带", "鼓机伴奏控制", "鼓机进入与关闭操作"],
        ),

        # 4. Bluetooth & Wireless
        (
            "ble_audio_latency",
            "bluetooth_conn",
            "ble_audio",
            "蓝牙音频传输延迟",
            r"(蓝牙音频|伴奏延迟|声音延迟|音频不同步|慢半拍|蓝牙伴奏卡顿|杂音卡顿)",
            ["蓝牙与无线", "蓝牙音频传输", "延迟与声音同步"],
        ),
        (
            "ble_connection_stability",
            "bluetooth_conn",
            "bt_pair",
            "蓝牙配对与连接稳定性",
            r"(蓝牙搜不到|配对失败|蓝牙断开|蓝牙掉线|连不上|无法连接|重连|蓝牙|配对)",
            ["蓝牙与无线", "蓝牙配对与搜索", "断开掉线排查"],
        ),

        # 5. Device & Compatibility
        (
            "device_pad_and_harmony_compat",
            "device_compat",
            "sys_compat",
            "平板与鸿蒙系统兼容",
            r"(平板|ipad|平板适配|平板横屏|鸿蒙|harmonyos|华为鸿蒙|ios18|安卓14|新系统|闪退)",
            ["设备与兼容", "系统兼容性", "iPad平板横屏适配"],
        ),

        # 6. Audio Config & Sound Card
        (
            "audio_style_pack_customization",
            "sound_preset",
            "style_pack_customization",
            "自定义风格包设置",
            r"(自定义风格包|风格包|伴奏风格|换风格)",
            ["音色与效果", "风格包配置", "自定义风格包设置"],
        ),
        (
            "audio_style_pack_and_soundcard",
            "sound_preset",
            "sound_expansion_card",
            "扩展音色卡与扩展引擎",
            r"(音色卡|扩展卡|旋律音色|换音色|音质|失真音色|木吉他音色|买音色卡|扩展引擎)",
            ["音色与效果", "扩展音色卡", "硬件插拔与风格包"],
        ),
        (
            "audio_chord_and_bpm_volume",
            "sound_preset",
            "audio_controls",
            "和弦映射与速度控制",
            r"(和弦指板|指板切换|和弦库|高把位|bpm|节拍速度|变调|移调|音量调节|旅行锁|防误触)",
            ["音色与效果", "演奏控制参数", "和弦映射与速度调节"],
        ),

        # 7. Hardware & Peripherals
        (
            "hardware_craft_and_peripherals",
            "hardware_craft",
            "hw_keys",
            "按键手感与琴身做工",
            r"(琴身|做工|缝隙|毛刺|材质|外壳|松动|掉漆|拨片|按键手感|回弹|按键|手感|背带|肩带)",
            ["硬件与做工", "按键与拨片手感", "装配做工与外设"],
        ),
        (
            "hardware_mic_and_c2",
            "hardware_craft",
            "hw_mic",
            "外设麦克风与啸叫排查",
            r"(麦克风|话筒|c2底座|动圈麦|收音距离|啸叫|无线麦|耳机|监听)",
            ["硬件与做工", "麦克风与外设", "收音距离与防啸叫"],
        ),
        (
            "accessory_packaging_policy",
            "hardware_craft",
            "accessory_standard",
            "标配充电头与配件包装策略",
            r"(不标配充电头|没给配充电头|不给配充电头|自购充电头|双c口|两头同口|原充电头被偷|配件包装|不配充电器)",
            ["硬件与做工", "配件包装策略", "标配充电头诉求"],
        ),

        # 8. Firmware & Power (Fine-Grained)
        (
            "fw_upgrade_failure",
            "firmware_power",
            "fw_upgrade_failure",
            "固件升级失败与异常排查",
            r"(固件升级|固件更新|ota|升级总是到最后一步|升级失败|报错代码|升级卡住|重启.*解决|死机重启)",
            ["固件与电源", "OTA固件升级", "升级失败与异常排查"],
        ),
        (
            "power_charging_spec",
            "firmware_power",
            "charging_spec",
            "充电器功率适配与快充协议",
            r"(40瓦|65瓦|20瓦|100瓦|充电器|充电头功率|涡流充电|快充|发热损坏|充不进电|电量越充越低|5v2a)",
            ["固件与电源", "充电器与功率", "快充协议与兼容性"],
        ),
        (
            "power_battery_life",
            "firmware_power",
            "fw_power",
            "电池充电与续航表现",
            r"(充不进电|电量|电池|续航|待机功耗|耗电快|自动关机)",
            ["固件与电源", "电池与功耗", "充放电与续航表现"],
        ),

        # 9. Official Events & Community
        (
            "official_events_and_offline",
            "general",
            "official_event",
            "官方线下活动与报名通知",
            r"(sorry dog|落日现场|猫咪|活动为定向邀约|报名二维码|进上海城市群|sunset live|节目报名|出行指南|waic|人工智能大会|展会)",
            ["综合与运营", "官方线下活动", "活动报名与出行指引"],
        ),

        # 10. Logistics & Purchase Channel
        (
            "logistics_and_purchase",
            "logistics_srv",
            "log_shipping",
            "购买渠道与物流售后",
            r"(顺丰|发货|单号|快递|退货|换货|退款|保修|闲鱼|二手|大促|首发|价格)",
            ["服务与保障", "物流与发货", "购买渠道与售后保障"],
        ),

        # 11. New Products & Roadmap
        (
            "cluster_u1_release_schedule",
            "new_product",
            "u1_timeline",
            "U1新品吉他研发与发售排期",
            r"(u1|新品吉他|二代琴|下一代|发售时间|上市时间|排期|规划)",
            ["新一代产品", "U1硬件研发", "发售上市排期"],
        ),
    ]

    @classmethod
    def _parse_episode_tags(cls, ep: EpisodeItemView) -> tuple[str, str, str, list[str]]:
        """Parses multi-level tags from category_hint or title/summary."""
        raw_hint = ep.category_hint or "general"
        parts = [p.strip() for p in raw_hint.split(":") if p.strip()]

        l1 = parts[0] if len(parts) > 0 else "general"
        l2 = parts[1] if len(parts) > 1 else ""
        l3 = parts[2] if len(parts) > 2 else ""

        tag_def1 = TagManager.get_tag(l1)
        l1_zh = tag_def1.name_zh if tag_def1 else TagManager.CATEGORY_NAMES_ZH.get(l1, "综合交流")

        tag_def2 = TagManager.get_tag(l2) if l2 else None
        l2_zh = tag_def2.name_zh if tag_def2 else (l2 or "常规功能")

        l3_zh = l3 if l3 else (l2_zh or "常规功能")

        tags_list = [l1_zh]
        if l2_zh and l2_zh != l1_zh:
            tags_list.append(l2_zh)
        if l3_zh and l3_zh not in tags_list:
            tags_list.append(l3_zh)

        return l1, l2, l3, tags_list

    @classmethod
    def _clean_title_text(cls, text: str) -> str:
        t = re.sub(r"^(?:话题探讨[:：]|【.*?】|\s*)+", "", text)
        t = re.sub(r"\[(?:引用|图片|表情|动画表情|语音|视频|微信红包).*?\]", "", t)
        t = re.sub(r"^围绕[\"“].*?[\"”]展开的?", "", t)
        t = re.sub(r"^关于", "", t)
        t = re.sub(r"[\.…。]+$", "", t).strip()
        t = re.sub(r"^[，。！？；：、“”‘’'\"\s]+|[，。！？；：、“”‘’'\"\s]+$", "", t)
        return t.strip()

    @classmethod
    def _pick_best_canonical_title(cls, episodes: list[EpisodeItemView], l1_zh: str, fallback_title: str) -> str:
        """Dynamically selects the best, most informative SVO title from member episodes."""
        if not episodes:
            return f"【{l1_zh}】{fallback_title}"

        # Clean all member titles
        cleaned_titles = []
        for ep in episodes:
            c = cls._clean_title_text(ep.title)
            if len(c) >= 6 and not c.startswith("围绕"):
                cleaned_titles.append(c)

        if not cleaned_titles:
            return f"【{l1_zh}】{fallback_title}"

        # Pick the most specific title (longest informative title without trailing junk)
        best_title = max(cleaned_titles, key=len)
        if len(best_title) > 36:
            best_title = best_title[:34] + "..."
        return f"【{l1_zh}】{best_title}"

    @classmethod
    def deduplicate_episodes(
        cls,
        episodes: list[EpisodeItemView],
        sort_by: str = "latest"  # latest | first_seen | frequency
    ) -> list[DistinctTopicCluster]:
        if not episodes:
            return []

        # 1. Filter out pure chit-chat / template fallback episodes
        valid_episodes: list[EpisodeItemView] = []
        for ep in episodes:
            clean_t = cls._clean_title_text(ep.title)
            if not clean_t or len(clean_t) < 4:
                continue
            # Drop if title or summary contains pure chit-chat or generic template
            if any(b in ep.title for b in ["好幸福的工作", "日常操作与使用反馈", "日常问候与社群", "日常问候与互动", "产品日常使用体验与操作反馈", "琴友日常问候与社群互动", "社群综合交流"]):
                continue
            if any(b in (ep.summary or "") for b in ["好幸福的工作"]):
                continue
            if ep.title.startswith("围绕“日常问候与社群交流”") or ep.title.startswith("围绕“社群日常问候”"):
                continue
            valid_episodes.append(ep)

        if not valid_episodes:
            return []

        # Enrich episode items with parsed multi-level tags
        for ep in valid_episodes:
            l1, l2, l3, tags_list = cls._parse_episode_tags(ep)
            ep.category_l1 = l1
            ep.category_l2 = l2
            ep.category_l3 = l3
            ep.l1_tag_zh = tags_list[0] if tags_list else "综合交流"
            ep.l2_tag_zh = tags_list[1] if len(tags_list) > 1 else None
            ep.l3_tag_zh = tags_list[2] if len(tags_list) > 2 else None
            ep.tags = tags_list

        cluster_map: dict[str, list[EpisodeItemView]] = defaultdict(list)
        cluster_meta: dict[str, dict[str, Any]] = {}

        # 2. Match against fine-grained THEME_SIGNATURES
        for ep in valid_episodes:
            full_text = f"{ep.title} {ep.summary} {ep.category_hint}".lower()
            matched = False
            for theme_id, category, l2_code, l3_name, pattern, tags_arr in cls.THEME_SIGNATURES:
                if re.search(pattern, full_text):
                    cluster_map[theme_id].append(ep)
                    if theme_id not in cluster_meta:
                        tag_def1 = TagManager.get_tag(category)
                        l1_zh = tag_def1.name_zh if tag_def1 else tags_arr[0]
                        cluster_meta[theme_id] = {
                            "category_hint": category,
                            "category_l1": category,
                            "category_l2": l2_code,
                            "category_l3": l3_name,
                            "tags": tags_arr,
                            "l1_zh": l1_zh,
                        }
                    matched = True
                    break

            if not matched:
                # 3. Match via TagManager taxonomy matcher
                l1_key, l2_key, display_name = TagManager.match_best_tag(full_text)
                if l1_key != "general":
                    theme_id = f"topic_{l1_key}_{l2_key or 'sub'}"
                    cluster_map[theme_id].append(ep)
                    if theme_id not in cluster_meta:
                        tag_def1 = TagManager.get_tag(l1_key)
                        l1_zh = tag_def1.name_zh if tag_def1 else "综合分类"
                        cluster_meta[theme_id] = {
                            "category_hint": l1_key,
                            "category_l1": l1_key,
                            "category_l2": l2_key,
                            "category_l3": display_name,
                            "tags": [l1_zh, display_name],
                            "l1_zh": l1_zh,
                        }
                    matched = True

            if not matched:
                # 4. Dynamic semantic clustering fallback
                clean_core = cls._clean_title_text(ep.title)
                if len(clean_core) > 20:
                    clean_core = clean_core[:18] + "..."
                new_cid = f"dyn_{len(cluster_map)}"
                cluster_map[new_cid].append(ep)
                cluster_meta[new_cid] = {
                    "category_hint": "general",
                    "category_l1": "general",
                    "category_l2": "discussion",
                    "category_l3": clean_core,
                    "tags": ["综合交流", "社群反馈", clean_core],
                    "l1_zh": "综合交流",
                }

        # 5. Build DistinctTopicCluster objects with DYNAMIC SVO TITLES & SUMMARIES
        result: list[DistinctTopicCluster] = []
        for cid, ep_list in cluster_map.items():
            if not ep_list:
                continue

            meta = cluster_meta.get(cid, {})
            l1_zh = meta.get("l1_zh", "综合交流")
            l3_name = meta.get("category_l3", "用户使用反馈")

            # Pick representative episode ensuring Title & Summary are strictly aligned
            best_ep = max(
                ep_list,
                key=lambda e: (
                    1 if (len(cls._clean_title_text(e.title)) >= 10 and not e.title.startswith("围绕")) else 0,
                    len(e.summary or ""),
                    e.message_count,
                ),
            )
            canonical_title = cls._pick_best_canonical_title(ep_list, l1_zh, l3_name)
            cluster_summary = best_ep.summary or f"群成员集中探讨交流关于{l3_name}的各项使用反馈与操作体验。"

            all_participants: set[str] = set()
            all_conversations: set[str] = set()
            total_messages = 0

            # Sort sub-episodes in cluster by time
            ep_list.sort(key=lambda e: e.ended_at, reverse=True)

            for e in ep_list:
                for p in e.participants:
                    if p and not p.startswith("用户 U-") and p != "未知":
                        all_participants.add(p)
                all_conversations.add(e.conversation_name or e.conversation_id)
                total_messages += e.message_count

            earliest_time = min(e.started_at for e in ep_list)
            latest_time = max(e.ended_at for e in ep_list)

            result.append(
                DistinctTopicCluster(
                    id=cid,
                    canonical_title=canonical_title,
                    category_hint=meta.get("category_hint", "general"),
                    summary=cluster_summary,
                    category_l1=meta.get("category_l1", "general"),
                    category_l2=meta.get("category_l2"),
                    category_l3=meta.get("category_l3"),
                    l1_tag_zh=l1_zh,
                    l2_tag_zh=meta.get("tags", [l1_zh])[1] if len(meta.get("tags", [])) > 1 else None,
                    l3_tag_zh=meta.get("tags", [l1_zh])[2] if len(meta.get("tags", [])) > 2 else None,
                    tags=meta.get("tags", [l1_zh]),
                    episode_count=len(ep_list),
                    total_messages=total_messages,
                    participants=list(all_participants),
                    conversations=list(all_conversations),
                    started_at=earliest_time,
                    ended_at=latest_time,
                    episodes=ep_list,
                )
            )

        # 6. Multi-dimensional Sorting Engine
        if sort_by == "frequency":
            result.sort(key=lambda c: (c.total_messages, c.episode_count, c.ended_at), reverse=True)
        elif sort_by == "first_seen":
            result.sort(key=lambda c: (c.started_at, -c.total_messages), reverse=False)
        else:  # default: 'latest'
            result.sort(key=lambda c: (c.ended_at, c.total_messages), reverse=True)

        return result
