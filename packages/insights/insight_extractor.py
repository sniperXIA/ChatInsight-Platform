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

        # Check for pure non-product announcements/chitchat/bot games
        all_raw = " ".join([m.raw_text or "" for m in packet.messages])
        marketing_keywords = [
            "WAIC 2026", "现场见", "相约", "展会现场", "早安", "晚安", "打卡签到",
            "疯狂星期四", "七夕「乞巧」", "Do Re 咪", "真假大挑战", "考眼力的时候到了",
            "Sunset Live", "落日音乐会", "周末练琴"
        ]
        has_real_feedback = any(w in all_raw for w in ["怎么", "如何", "卡", "错", "慢", "断", "bug", "更新", "买", "多少钱", "坏", "修", "连不上", "发货", "以旧换新"])
        if any(w in all_raw for w in marketing_keywords) and not has_real_feedback:
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

        # 0. Mobile App & Software Ecological (App Download, Links, APK, App updates)
        # Prioritized BEFORE Firmware OTA to avoid classifying App update as firmware
        if any(kw in all_raw for kw in ["app下载", "下载链接", "安装包", "apk", "testflight", "应用商店", "appstore", "新版app", "软件下载", "下载地址", "客户端下载", "新版本app"]) or (("app" in all_raw.lower() or "软件" in all_raw or "客户端" in all_raw) and any(w in all_raw for w in ["下载", "链接", "安装", "更新", "升级", "版本", "地址", "闪退", "打不开"])):
            matched_module = "软件与App生态"
            matched_sub_mod = "软件下载与安装更新"
            scenario_detail = "移动端App获取与客户端环境"
            tags = ["移动端App", "软件安装", "版本升级"]

            if any(w in all_raw for w in ["闪退", "打不开", "崩溃", "卡死", "白屏"]):
                summary = "用户反馈移动端App启动闪退或运行卡死无响应"
                ins_type = InsightType.ISSUE
                sev = SeverityLevel.MAJOR
                symptom_detail = "App客户端启动时闪退或在特定交互页面卡死无响应"
                trigger_detail = "启动App或操作特定页面时"
            elif any(w in all_raw for w in ["下载", "链接", "安装包", "apk", "testflight", "商店", "地址"]):
                summary = "用户咨询新版移动端App官方下载渠道与安装包链接"
                ins_type = InsightType.INQUIRY
                sev = SeverityLevel.MINOR
                symptom_detail = "用户询问新版App最新发布动态、官方下载安装包或内测更新链接"
                trigger_detail = "需要更新App或换机重新安装客户端"
            else:
                summary = "用户咨询移动端App新功能发布与版本升级规划"
                ins_type = InsightType.INQUIRY
                sev = SeverityLevel.MINOR
                symptom_detail = "用户询问App后续功能更新安排与适配进展"
                trigger_detail = "日常使用与功能期待"

        # 1. Firmware & System Stability (Only Physical Guitar Embedded MCU/OTA)
        elif any(kw in all_raw for kw in ["固件", "ota", "OTA", "刷写", "刷机", "变砖", "机身系统"]) or (any(kw in all_raw for kw in ["升级", "更新"]) and any(kw in all_raw for kw in ["设备", "吉他", "琴身", "小卡", "开机", "指示灯"])):
            matched_module = "固件与电源管理"
            matched_sub_mod = "OTA固件在线升级"
            ins_type = InsightType.ISSUE
            sev = SeverityLevel.MAJOR
            tags = ["固件升级", "系统稳定性", "OTA"]
            scenario_detail = "吉他琴身固件升级与版本维护流程"

            if any(w in all_raw for w in ["点都没有用", "点不了", "按了没用", "点更新", "点不动", "点了没反应"]):
                summary = "用户反馈App提示有新固件但点击更新按钮无任何响应"
                symptom_detail = "检测到固件更新但点击按钮无响应，无法触发下载流程"
                trigger_detail = "在设备设置页点击立即更新固件按钮"
            elif any(w in all_raw for w in ["2.1", "购买", "小卡", "适配", "正式版", "换小卡"]):
                summary = "用户咨询后续固件2.1版本与音色小卡升级适配规则"
                ins_type = InsightType.INQUIRY
                sev = SeverityLevel.MINOR
                symptom_detail = "咨询固件大版本升级与实体音色小卡是否需要重新购买或更换"
                trigger_detail = "关注新版本发布与配件兼容性"
            elif any(w in all_raw for w in ["电量", "低电", "弹窗", "打断"]):
                summary = "用户反馈固件升级过程被设备低电量提示弹窗打断"
                symptom_detail = "升级传输阶段由于设备低电量弹窗拦截导致升级流程被迫中断"
                trigger_detail = "设备处于低电量或电量检测边缘时启动OTA升级"
            elif any(w in all_raw for w in ["99%", "卡住", "卡在", "进度条", "停滞", "不动"]):
                summary = "用户反馈OTA固件升级进度在99%停滞超时导致刷写失败"
                symptom_detail = "固件升级进度条停滞在特定百分比（如99%），无进一步提示"
                trigger_detail = "OTA固件包下载与校验传输阶段"
            elif any(w in all_raw for w in ["死机", "开不了机", "黑屏", "变砖"]):
                summary = "用户反馈固件升级中断导致设备死机或无法开机"
                sev = SeverityLevel.BLOCKER
                symptom_detail = "固件升级中断后设备指示灯异常且无法正常开机"
                trigger_detail = "固件刷写阶段异常断电或断连"
            else:
                summary = "用户反馈琴身固件升级交互体验与稳定性优化建议"
                symptom_detail = "用户在进行琴身固件升级检测与传输过程中提出体验反馈"
                trigger_detail = "固件版本升级与检测流程"

        # 2. Bluetooth & Wireless
        elif any(kw in all_raw for kw in ["蓝牙", "连接", "配对", "连不上", "掉线", "延迟", "断联", "重连"]):
            matched_module = "蓝牙与无线"
            matched_sub_mod = "伴奏传输与延迟"
            ins_type = InsightType.ISSUE
            sev = SeverityLevel.MAJOR
            tags = ["LiberLive C2", "蓝牙连接", "无线传输"]
            scenario_detail = "设备与App无线蓝牙交互"

            if any(w in all_raw for w in ["延迟", "音画不同步", "慢半拍", "声音延迟"]):
                summary = "用户反馈蓝牙伴奏无线推流存在明显音频延迟与画面不同步"
                symptom_detail = "弹唱演奏时蓝牙伴奏音频传输延迟，与手机端歌词画面不同步"
                trigger_detail = "高采样率伴奏无线推流时"
            elif any(w in all_raw for w in ["掉线", "断开", "自动断", "突然断"]):
                summary = "用户反馈设备在弹唱过程中蓝牙连接偶发自动断开"
                symptom_detail = "正在弹奏时蓝牙指示灯变暗并提示连接断开"
                trigger_detail = "连续弹唱或手机息屏后台运行"
            elif any(w in all_raw for w in ["搜索", "搜不到", "找不到", "搜慢"]):
                summary = "用户反馈App蓝牙列表搜索缓慢或无法搜索到吉他设备"
                symptom_detail = "App蓝牙搜索界面长时间转圈，无法快速发现就近开启的吉他"
                trigger_detail = "新开机首次搜索配对"
            else:
                summary = "用户反馈吉他与移动端蓝牙无线连接稳定性问题"
                symptom_detail = "社群成员集中反馈蓝牙连接与传输交互存在波动"
                trigger_detail = "日常蓝牙连接使用场景"

        # 3. Sheet Music & Library
        elif any(kw in all_raw for kw in ["曲谱", "和弦", "伴奏", "扒谱", "新歌", "歌单", "自建", "导入", "错谱", "纯音乐", "指弹"]):
            matched_module = "曲谱与乐库"
            matched_sub_mod = "求谱与乐库扩充"
            ins_type = InsightType.FEATURE_REQUEST
            sev = SeverityLevel.MINOR
            tags = ["曲谱乐库", "和弦伴奏", "曲谱扩展"]
            scenario_detail = "App曲库与演奏界面"

            if any(w in all_raw for w in ["自建", "导入", "本地", "自定义谱"]):
                summary = "用户期望支持第三方曲谱自建与本地和弦谱导入"
                symptom_detail = "现有乐库曲目有限，希望支持用户自定义上传谱面与分轨和弦"
                trigger_detail = "浏览曲库未找到目标歌曲时"
            elif any(w in all_raw for w in ["标错", "错谱", "和弦不对", "歌词错", "纠错"]):
                summary = "用户反馈曲谱和弦标错与歌词排版瑕疵并提出纠错"
                ins_type = InsightType.ISSUE
                symptom_detail = "用户指出特定曲目存在和弦标注失准或歌词对齐排版瑕疵"
                trigger_detail = "演奏该曲目扫弦伴奏时"
            elif any(w in all_raw for w in ["纯音乐", "指弹", "独奏", "轻音乐"]):
                summary = "用户建议曲库扩充纯音乐指弹与吉他独奏风格曲谱"
                symptom_detail = "用户希望官方增加不带歌词的纯器乐演奏指弹谱与精选伴奏"
                trigger_detail = "曲库查找纯音乐指弹曲目时"
            else:
                summary = "用户建议曲库扩充热门流行新歌与经典弹唱曲目"
                symptom_detail = "用户表达对特定歌手新歌上线及多样化弹唱歌单的扩充诉求"
                trigger_detail = "日常弹唱选曲流程"

        # 4. Sound & Tone Expansion
        elif any(kw in all_raw for kw in ["音色", "扩展卡", "音效", "声音", "音质", "失真", "杂音", "爆音", "小U", "引擎"]):
            matched_module = "配置与音色"
            matched_sub_mod = "音色品质与扩展"
            tags = ["音色扩展", "音效音质", "效果器"]
            scenario_detail = "音色卡插槽与音频输出链路"

            if any(w in all_raw for w in ["杂音", "爆音", "破音", "失真", "电流声"]):
                summary = "用户反馈外接音频或演奏时偶发杂音与破音失真现象"
                ins_type = InsightType.ISSUE
                sev = SeverityLevel.MAJOR
                symptom_detail = "大动态弹奏或外接音箱时扬声器输出偶发破音/电流杂音"
                trigger_detail = "强力度拨片弹奏或外接3.5mm音频输出"
            elif any(w in all_raw for w in ["小U", "u1", "区别", "引擎"]):
                summary = "用户咨询设备扩展引擎与音色包在曲谱演奏中的音质差异"
                ins_type = InsightType.INQUIRY
                symptom_detail = "用户询问C2/U1扩展引擎与音色包在具体曲目演奏中的效果差异"
                trigger_detail = "选购音色包或切换演奏风格时"
            else:
                summary = "用户咨询音色扩展卡支持列表与音效预设调节方法"
                ins_type = InsightType.INQUIRY
                symptom_detail = "用户在社群交流音色卡加载配置与音效参数调节技巧"
                trigger_detail = "配置音色与音效卡场景"

        # 5. Hardware, Body & Accessories
        elif any(kw in all_raw for kw in ["拨片", "弹簧", "按键", "手感", "琴身", "做工", "缝隙", "掉漆", "材质", "指示灯", "灯亮", "白灯", "绿灯", "话筒", "麦架", "以旧换新", "换购"]):
            matched_module = "硬件与外设"
            matched_sub_mod = "机身工艺与按键配件"
            tags = ["硬件做工", "按键手感", "配件支持"]
            scenario_detail = "实体硬件外观与机械结构"

            if any(w in all_raw for w in ["以旧换新", "换购", "u1换c2", "折抵"]):
                summary = "老用户咨询早期U1型号置换新款C2的以旧换新抵扣政策"
                ins_type = InsightType.INQUIRY
                matched_module = "物流与售后"
                matched_sub_mod = "置换与换新政策"
                symptom_detail = "老用户询问官方是否支持旧琴折价置换新一代旗舰吉他"
                trigger_detail = "新品上市了解换购福利"
            elif any(w in all_raw for w in ["话筒", "麦克风", "支架", "麦架"]):
                summary = "用户咨询吉他配件清单是否标配麦克风与外接支架"
                ins_type = InsightType.INQUIRY
                symptom_detail = "用户询问购买吉他标配清单是否包含话筒与外接支架"
                trigger_detail = "开箱或选购配件场景"
            elif any(w in all_raw for w in ["指示灯", "白灯", "绿灯", "灯亮", "红灯", "闪烁"]):
                summary = "用户咨询设备面板指示灯不同颜色状态与闪烁代表的含义"
                ins_type = InsightType.INQUIRY
                symptom_detail = "用户对琴身指示灯白灯常亮/绿灯闪烁所指示的工作状态存在疑问"
                trigger_detail = "开机或充电观察面板灯效"
            elif any(w in all_raw for w in ["拨片", "回弹", "阻尼", "硬", "软"]):
                summary = "用户反馈物理拨片弹簧过硬导致快速扫弦手指疲劳"
                ins_type = InsightType.ISSUE
                symptom_detail = "用户反馈拨片弹簧力度偏硬，快速扫弦时手指易疲劳"
                trigger_detail = "快速扫弦连续弹唱"
            else:
                summary = "用户反馈琴身机械做工细节与外观接缝装配工艺建议"
                symptom_detail = "用户讨论实体吉他琴身做工、边缘公差与材质细节"
                trigger_detail = "实体琴把玩与观察"

        # 6. Logistics & After-sales
        elif any(kw in all_raw for kw in ["发货", "顺丰", "快递", "到货", "售后", "退货", "换货", "客服", "保修"]):
            matched_module = "物流与售后"
            matched_sub_mod = "顺丰时效与物流单号"
            ins_type = InsightType.INQUIRY
            tags = ["发货时效", "售后保障", "物流服务"]
            summary = "用户咨询商城订单顺丰发货时效与售后退换保障政策"
            symptom_detail = "新下单用户咨询顺丰发货进度与售后条款"
            trigger_detail = "完成下单后等待发货"

        # 7. Praise & Positive Signals
        elif any(kw in all_raw for kw in ["好听", "喜欢", "太棒了", "方便", "神器", "牛", "推荐", "好玩", "赞"]):
            matched_module = "综合交流"
            matched_sub_mod = "日常弹唱与好评心得"
            ins_type = InsightType.PRAISE
            tags = ["用户好评", "便携设计", "易上手"]
            if any(w in all_raw for w in ["纯音乐", "指弹"]):
                summary = "用户好评点赞纯音乐曲谱演奏体验并建议持续扩充"
            else:
                summary = "琴友在社群中积极分享便携易上手弹唱体验并给予好评"
            symptom_detail = "用户表达对智能乐器便携性与伴奏体验的高度认可"
            trigger_detail = "日常弹唱分享"

        # Fallback dynamic synthesis with clean SVO
        else:
            is_issue = any(w in all_raw for w in ["错", "卡", "掉", "慢", "断", "bug", "死", "难", "重"])
            ins_type = InsightType.ISSUE if is_issue else InsightType.INQUIRY
            sev = SeverityLevel.MAJOR if is_issue else SeverityLevel.MINOR
            if is_issue:
                summary = "用户反馈智能吉他日常弹唱中遭遇异常并提出优化诉求"
            else:
                summary = "琴友在社群中交流弹唱体验并咨询功能使用技巧"
            symptom_detail = f"社群成员集中探讨: {clean_snippet}"
            trigger_detail = "日常社群交流与功能使用"

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
        """Guarantees that the summary is differentiated and unique across the workspace."""
        from sqlalchemy import select
        stmt = select(Insight.id).where(
            Insight.workspace_id == workspace_id,
            Insight.summary == base_summary,
            Insight.episode_id != ep.id,
        )
        exists = (await self.session.execute(stmt)).scalars().first()
        if not exists:
            return base_summary

        clean_ep_title = (ep.title or "").replace("微话题: ", "").replace("微话题：", "").strip()
        if clean_ep_title and not clean_ep_title.startswith("微话题"):
            candidate = f"{base_summary} ({clean_ep_title[:14]})"
        else:
            time_str = ep.started_at.strftime("%m-%d %H:%M") if hasattr(ep.started_at, "strftime") else str(ep.started_at)[5:16]
            candidate = f"{base_summary} (时段 {time_str})"

        stmt2 = select(Insight.id).where(
            Insight.workspace_id == workspace_id,
            Insight.summary == candidate,
            Insight.episode_id != ep.id,
        )
        exists2 = (await self.session.execute(stmt2)).scalars().first()
        if not exists2:
            return candidate

        time_str = ep.started_at.strftime("%m-%d %H:%M") if hasattr(ep.started_at, "strftime") else str(ep.started_at)[5:16]
        candidate_timed = f"{candidate} [时段 {time_str}]"
        stmt3 = select(Insight.id).where(
            Insight.workspace_id == workspace_id,
            Insight.summary == candidate_timed,
            Insight.episode_id != ep.id,
        )
        exists3 = (await self.session.execute(stmt3)).scalars().first()
        if not exists3:
            return candidate_timed

        return f"{base_summary} [时段 {time_str} #{ep.id[:4]}]"

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
            "1. 摘要（summary）：必须为包含【主语 + 谓语 + 宾语】的完整简洁陈述句（12~28字，严禁冒号截取拼接聊天原句，严禁空洞套话）；\n"
            "2. 边界清晰：严格区分移动端App软件生态（下载/安装包/App更新/App闪退）与机身嵌入式固件系统（吉他OTA固件/死机变砖）；\n"
            "3. 5W1H 事实描述：description 严格按照 [场景/位置]、[具体现象]、[影响程度]、[环境/触发条件]、[社群进展] 5个维度分行交代事实；\n"
            "4. 附带 1~2 条可严格核验的 Claim 并绑定对应发言 URI。若纯为日常闲聊或无明确产品价值，返回空列表 []。"
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
            prompt_key="insight_extraction_v5_prompt_bound",
            input_data={"episode_id": ep.id, "msg_count": len(packet.messages)},
            config_data={
                "model": selected_model,
                "schema": "InsightExtractionBatchOutput_v5",
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
