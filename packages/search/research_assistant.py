import hashlib
import re
import time
from typing import Any, Optional, Union
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.insights.tag_manager import TagManager
from packages.model_gateway.gateway import ModelGateway
from packages.observability.analysis_service import AnalysisService
from packages.persistence.models import Conversation, Episode, Insight, InsightClaim, Message, Participant, Topic, TopicInsightLink
from packages.persistence.repositories.registry import RepositoryRegistry
from packages.search.contracts import Citation, RequirementItem, ResearchAssistantResponse, SearchFilter
from packages.search.history_service import ResearchHistoryService
from packages.search.hybrid_search import HybridSearchEngine


def parse_5w1h_dict(text: str) -> dict[str, str]:
    """Extracts 5w1h fact blocks from structured insight description."""
    if not text:
        return {}
    patterns = {
        "scene": r"\[场景/位置\]\s*([^\[]+)",
        "symptom": r"\[具体现象\]\s*([^\[]+)",
        "impact": r"\[影响程度\]\s*([^\[]+)",
        "condition": r"\[环境/触发条件\]\s*([^\[]+)",
        "progress": r"\[社群进展\]\s*([^\[]+)",
    }
    result = {}
    for k, p in patterns.items():
        m = re.search(p, text)
        if m:
            result[k] = m.group(1).strip().rstrip("；;。")
    return result


def format_problem_statement(text: str) -> str:
    """Ensure problem_statement has clean bullet points for the 4 dimensions."""
    if not text:
        return text
    lines = []
    for raw_line in text.splitlines():
        parts = re.split(r"(?<=[；;。，\n])\s*(?=[•\-\*]?\s*【(?:问题现象|诱发原因|用户建议|需求补充|影响范围|产生原因|关键背景)】)", raw_line)
        for p in parts:
            p = p.strip()
            if p:
                if p.startswith("【") and not p.startswith("• "):
                    p = f"• {p}"
                lines.append(p)
    return "\n".join(lines) if lines else text


def format_recommended_actions(text: str) -> str:
    """Ensure recommended_action items are line-by-line."""
    if not text:
        return text
    lines = []
    for raw_line in text.splitlines():
        sub_items = re.split(r"(?<=[；;。])\s*(?=(?:\d+[\.、\)]|[①②③④⑤]|\(\d+\))\s*)", raw_line)
        for item in sub_items:
            item = item.strip()
            if item:
                lines.append(item)
    return "\n".join(lines) if lines else text


def parse_fallback_requirements(answer: str) -> list[RequirementItem]:
    """
    Fallback heuristic parser if the LLM output does not populate the requirements list.
    Extracts numbered sections from the text response.
    """
    if not answer or len(answer.strip()) < 20:
        return []

    reqs = []
    # Split by numbered points like "1.", "### 1", etc.
    lines = answer.strip().splitlines()
    curr_title = None
    curr_desc = []
    curr_cites = []

    for line in lines:
        line_s = line.strip()
        m_head = re.match(r"^(?:#{1,4}\s*)?(?:[0-9]+[、.：:]|\([0-9]+\))\s*(.+)$", line_s)
        if m_head:
            if curr_title:
                c_ids = list(set(int(x) for x in curr_cites))
                reqs.append(
                    RequirementItem(
                        req_id=f"REQ-{len(reqs) + 1}",
                        title=curr_title[:25],
                        category="综合体验",
                        severity="medium",
                        problem_statement=" ".join(curr_desc)[:250] or curr_title,
                        user_voice=None,
                        recommended_action="建议产品与研发团队针对该反馈开展体验优化排期。",
                        citation_ids=c_ids,
                    )
                )
            curr_title = m_head.group(1).strip()
            curr_desc = []
            curr_cites = re.findall(r"\[(\d+)\]", line_s)
        else:
            if curr_title:
                curr_desc.append(line_s)
                curr_cites.extend(re.findall(r"\[(\d+)\]", line_s))

    if curr_title:
        c_ids = list(set(int(x) for x in curr_cites))
        reqs.append(
            RequirementItem(
                req_id=f"REQ-{len(reqs) + 1}",
                title=curr_title[:25],
                category="综合体验",
                severity="medium",
                problem_statement=" ".join(curr_desc)[:250] or curr_title,
                user_voice=None,
                recommended_action="建议产品与研发团队针对该反馈开展体验优化排期。",
                citation_ids=c_ids,
            )
        )

    return reqs


class AssistantLLMOutput(BaseModel):
    executive_summary: Optional[str] = Field(default=None, description="针对用户提问的高管/全局综述（1-2段深入分析）")
    requirements: list[RequirementItem] = Field(default_factory=list, description="拆解提取出的结构化优化需求列表")
    answer: Optional[str] = Field(default=None, description="客观详实的分析回答，并在关键事实处使用 [1]、[2] 等引用标记")
    confidence: Optional[Union[float, str]] = Field(default=0.95)
    key_findings: list[str] = Field(default_factory=list, description="要点清单")

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, v: Any) -> float:
        if isinstance(v, (int, float)):
            return float(max(0.0, min(1.0, float(v))))
        if isinstance(v, str):
            s = v.lower().strip()
            if "high" in s or "高" in s:
                return 0.95
            if "med" in s or "中" in s:
                return 0.8
            if "low" in s or "低" in s:
                return 0.6
            try:
                val = float(s)
                return max(0.0, min(1.0, val))
            except ValueError:
                return 0.9
        return 0.95


DEFAULT_MOCK_REQUIREMENTS = [
    RequirementItem(
        req_id="REQ-1",
        title="优化蓝牙扫描与配对连接稳定性",
        category="蓝牙与无线",
        severity="major",
        problem_statement="[场景/位置] 户外或复杂无线环境下连接设备\n[具体现象] 扫描设备耗时过长，偶现断连后无法自动回连\n[影响程度] 用户无法正常伴奏，影响弹唱连续性\n[触发条件] 蓝牙周边设备较多或电量较低时\n[社群进展] 客服已提供固件升级补丁，待发正式版",
        user_voice="“群里好几个人都反映蓝牙连不上，每次都要重启琴和手机才能搜到”",
        recommended_action="重构低功耗蓝牙广播过滤逻辑，增加断线指数退避自动重连机制。",
        citation_ids=[1],
    ),
    RequirementItem(
        req_id="REQ-2",
        title="曲谱夜间/深色模式与背景色自定义支持",
        category="界面与显示",
        severity="medium",
        problem_statement="[场景/位置] 室内暗光或舞台演出使用 App 曲谱\n[具体现象] 当前仅有浅色高亮白底背景，夜间刺眼且字体对比度不足\n[影响程度] 视觉疲劳，夜间弹唱体验不佳\n[触发条件] 夜间室内或暗光舞台演奏环境\n[社群进展] 客服已记录并反馈给 UI/UX 设计团队",
        user_voice="“晚上弹琴的时候屏幕太亮了，能不能加个黑底白字的深色模式？”",
        recommended_action="App 曲谱模块增加深色主题切换，支持高对比度白字及背景色微调。",
        citation_ids=[2],
    ),
]


class ResearchAssistant:
    """Research Assistant engine answering product & VoC questions strictly grounded on evidence citations."""

    def __init__(self, session: AsyncSession, model_gateway: ModelGateway | None = None):
        self.session = session
        self.gateway = model_gateway or ModelGateway()
        self.search_engine = HybridSearchEngine(session)
        self.analysis_service = AnalysisService(session)
        self.repo = RepositoryRegistry(session)
        self.history_service = ResearchHistoryService(session)

    async def answer_question(
        self,
        question: str,
        filters: SearchFilter = SearchFilter(),
        force_mock: bool = False,
        force_refresh: bool = False,
        model_override: str | None = None,
    ) -> ResearchAssistantResponse:
        ws = await self.repo.get_or_create_default_workspace()

        from packages.model_gateway.settings_manager import SettingsManager
        settings = SettingsManager.get_settings()
        retention_days = getattr(settings, "history_retention_days", 30)
        max_records = getattr(settings, "history_max_records", 100)

        # 0. Check Local History Cache (if not force_refresh and not force_mock)
        if not force_refresh and not force_mock:
            cached_rec = await self.history_service.find_exact_cached(
                question=question,
                days=retention_days,
            )
            if cached_rec:
                cached_reqs = [
                    RequirementItem.model_validate(r)
                    for r in (cached_rec.requirements_json or [])
                ]
                cached_cites = [
                    Citation.model_validate(c)
                    for c in (cached_rec.citations_json or [])
                ]
                return ResearchAssistantResponse(
                    question=cached_rec.question,
                    executive_summary=cached_rec.executive_summary,
                    requirements=cached_reqs,
                    answer=cached_rec.answer_text,
                    citations=cached_cites,
                    key_findings=cached_rec.key_findings_json or [],
                    related_topic_ids=cached_rec.related_topic_ids_json or [],
                    related_insight_ids=cached_rec.related_insight_ids_json or [],
                    confidence=cached_rec.confidence,
                    from_history=True,
                    history_id=cached_rec.id,
                    created_at=cached_rec.created_at.isoformat(),
                    duration_ms=cached_rec.duration_ms,
                    model_used=cached_rec.model_used,
                )

        t0 = time.perf_counter()

        # 1. Retrieve relevant evidence via Hybrid Search
        search_res = await self.search_engine.search(query=question, filters=filters, limit=8)

        # Zero-recall fast path: if knowledge base contains no relevant facts for this query (live mode only)
        if not search_res.results and not force_mock:
            duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            exec_summary = f"基于当前社群知识库的检索结果，暂未发现与“{question}”直接相关的用户反馈或改进建议。"
            honest_answer = f"基于当前社群多模态知识库的检索结果，暂未发现与【{question}】直接相关的用户反馈、明确诉求或具体改进建议。\n\n当前知识库沉淀的社群原声主要覆盖其他业务模块，建议后续结合专项问卷调查或客服日常工单进行定向补充收集。"
            saved_rec = await self.history_service.save_record(
                workspace_id=ws.id,
                question=question,
                answer_text=honest_answer,
                executive_summary=exec_summary,
                requirements=[],
                citations=[],
                key_findings=["当前知识库中暂未收录该特定业务维度的反馈事实"],
                confidence=0.4,
                model_used="system-direct",
                duration_ms=duration_ms,
            )
            await self.session.commit()
            return ResearchAssistantResponse(
                question=question,
                executive_summary=exec_summary,
                requirements=[],
                answer=honest_answer,
                citations=[],
                key_findings=["当前知识库中暂未收录该特定业务维度的反馈事实"],
                confidence=0.4,
                from_history=False,
                history_id=saved_rec.id,
                created_at=saved_rec.created_at.isoformat(),
                duration_ms=duration_ms,
                model_used="system-direct",
            )

        citations: list[Citation] = []
        evidence_prompt_lines = []
        related_topics = []
        related_insights = []

        # Pre-fetch insight and topic entities for rich 5W1H and metadata enrichment
        insight_ids = [item.entity_id for item in search_res.results if item.entity_type == "insight"]
        topic_ids = [item.entity_id for item in search_res.results if item.entity_type == "topic"]

        topic_to_insight_id: dict[str, str] = {}
        if topic_ids:
            t_link_stmt = select(TopicInsightLink).where(TopicInsightLink.topic_id.in_(topic_ids))
            t_link_res = await self.session.execute(t_link_stmt)
            for link in t_link_res.scalars().all():
                if link.topic_id not in topic_to_insight_id:
                    topic_to_insight_id[link.topic_id] = link.insight_id
                    if link.insight_id not in insight_ids:
                        insight_ids.append(link.insight_id)

        insight_map: dict[str, Insight] = {}
        if insight_ids:
            ins_stmt = select(Insight).where(Insight.id.in_(insight_ids))
            ins_res = await self.session.execute(ins_stmt)
            for i in ins_res.scalars().all():
                insight_map[i.id] = i

        topic_map: dict[str, Topic] = {}
        if topic_ids:
            top_stmt = select(Topic).where(Topic.id.in_(topic_ids))
            top_res = await self.session.execute(top_stmt)
            for t in top_res.scalars().all():
                topic_map[t.id] = t

        # Pre-fetch claims and verbatim messages for all relevant insights and raw message entities
        insight_claims_map: dict[str, list[InsightClaim]] = {}
        referenced_msg_ids: set[str] = set()

        if insight_ids:
            claim_stmt = select(InsightClaim).where(InsightClaim.insight_id.in_(insight_ids))
            claim_res = await self.session.execute(claim_stmt)
            for c in claim_res.scalars().all():
                insight_claims_map.setdefault(c.insight_id, []).append(c)
                for uri in (c.evidence_uris_json or []):
                    if "msg_" in uri:
                        m_id = uri.split("msg_")[-1].split("#")[0]
                        referenced_msg_ids.add(m_id)

        for item in search_res.results:
            if item.entity_type == "message":
                referenced_msg_ids.add(item.entity_id)

        raw_msg_map: dict[str, tuple[str, str]] = {}  # msg_id -> (sender_name, text)
        if referenced_msg_ids:
            msg_q = (
                select(Message, Participant)
                .outerjoin(Participant, Message.participant_id == Participant.id)
                .where(Message.id.in_(list(referenced_msg_ids)))
            )
            msg_res = await self.session.execute(msg_q)
            for m, p in msg_res.all():
                sender = p.display_label if p else "社群用户"
                raw_msg_map[m.id] = (sender, m.raw_text or "")

        severity_zh = {
            "blocker": "🚨 致命阻塞",
            "major": "⚠️ 严重故障",
            "minor": "📌 一般缺陷",
            "trivial": "🌱 轻微建议",
        }
        type_zh = {
            "product_issue": "🐛 产品缺陷",
            "feature_request": "💡 功能需求",
            "usability_opportunity": "🎯 体验与易用性优化",
            "documentation_gap": "📖 说明与文档缺失",
        }

        quotes_by_citation_id: dict[int, list[str]] = {}

        for idx, item in enumerate(search_res.results, start=1):
            e_dict = parse_5w1h_dict(item.snippet)
            ent_id = item.entity_id
            ent_type = item.entity_type
            module_raw = item.metadata.get("module", "综合体验")
            sever = item.metadata.get("severity", "minor")
            ins_t = item.metadata.get("insight_type", "product_issue")
            episode_id = None
            conv_name = None
            tags_list = []
            full_desc = item.snippet

            # If it's a topic with linked insight, resolve to that insight for complete evidence & drilldown
            resolved_ins_id = ent_id if ent_type == "insight" else topic_to_insight_id.get(ent_id)
            if resolved_ins_id and resolved_ins_id in insight_map:
                ins_rec = insight_map[resolved_ins_id]
                ent_id = ins_rec.id
                ent_type = "insight"
                episode_id = ins_rec.episode_id
                full_desc = ins_rec.description or item.snippet
                e_dict = parse_5w1h_dict(full_desc)
                sever = ins_rec.severity or sever
                ins_t = ins_rec.insight_type or ins_t
                module_raw = ins_rec.module or module_raw
                ins_tags = ins_rec.tags_json or []
                tags_list = [TagManager.format_tag_display(t) for t in ins_tags]

            # Resolve verbatim user quotes
            verbatim_quotes: list[str] = []
            if resolved_ins_id and resolved_ins_id in insight_claims_map:
                for c in insight_claims_map[resolved_ins_id]:
                    for uri in (c.evidence_uris_json or []):
                        if "msg_" in uri:
                            m_id = uri.split("msg_")[-1].split("#")[0]
                            if m_id in raw_msg_map:
                                s_name, m_text = raw_msg_map[m_id]
                                quote_str = f"“{m_text}”" if not m_text.startswith("“") else m_text
                                if quote_str not in verbatim_quotes:
                                    verbatim_quotes.append(quote_str)
            elif item.entity_type == "message" and item.entity_id in raw_msg_map:
                s_name, m_text = raw_msg_map[item.entity_id]
                quote_str = f"“{m_text}”" if not m_text.startswith("“") else m_text
                verbatim_quotes.append(quote_str)

            # Fallback if no raw message found: extract from 5w1h symptom
            if not verbatim_quotes and e_dict.get("symptom"):
                verbatim_quotes.append(f"“{e_dict['symptom']}”")

            quotes_by_citation_id[idx] = list(verbatim_quotes)

            quotes_formatted = "\n".join(f"      • {q}" for q in verbatim_quotes[:3]) if verbatim_quotes else "      （无原始聊天原句）"

            module_zh = TagManager.format_tag_display(module_raw)
            evi_uri = f"chatinsight://insights/{ent_id}" if ent_type == "insight" else item.evidence_uri

            citation = Citation(
                citation_id=idx,
                evidence_uri=evi_uri,
                title=item.title,
                snippet=item.snippet,
                entity_type=ent_type,
                entity_id=ent_id,
                module=module_raw,
                module_zh=module_zh,
                severity=sever,
                severity_label_zh=severity_zh.get(sever, "📌 一般缺陷"),
                insight_type=ins_t,
                type_label_zh=type_zh.get(ins_t, "🐛 产品缺陷"),
                tags=tags_list,
                episode_id=episode_id,
                conversation_name=conv_name,
                parsed_5w1h=e_dict,
                full_description=full_desc,
            )
            citations.append(citation)

            evidence_prompt_lines.append(
                f"[{idx}] [类型: {citation.type_label_zh}] [模块: {module_zh}] [严重度: {citation.severity_label_zh}] {item.title} (URI: {item.evidence_uri})\n"
                f"    【社群真实原声原文】（要求：若该证据被提炼为需求，其 user_voice 必须从以下原声中完整摘录带双引号的原句，严禁使用第三人称改写概括）：\n{quotes_formatted}\n"
                f"    【事实详情】: {full_desc}"
            )
            if item.entity_type == "topic":
                related_topics.append(item.entity_id)
            elif item.entity_type == "insight":
                related_insights.append(item.entity_id)

        if not citations and force_mock:
            citations = [
                Citation(
                    citation_id=1,
                    entity_type="topic",
                    entity_id="mock_topic_1",
                    title="社群高频反馈与共性问题",
                    snippet=f"社群用户多次讨论关于“{question}”的实际表现与客服支持进展。",
                    evidence_uri="chatinsight://topics/mock-1",
                    module="综合体验",
                    module_zh="综合体验",
                    severity="medium",
                    severity_label_zh="📌 一般缺陷",
                    insight_type="product_issue",
                    type_label_zh="🐛 产品缺陷",
                    tags=["体验优化"],
                ),
                Citation(
                    citation_id=2,
                    entity_type="insight",
                    entity_id="mock_insight_1",
                    title="用户体验与功能优化建议",
                    snippet="收集到的用户原声建议及客服初步排查指导方案。",
                    evidence_uri="chatinsight://insights/mock-1",
                    module="综合体验",
                    module_zh="综合体验",
                    severity="minor",
                    severity_label_zh="🌱 轻微建议",
                    insight_type="feature_request",
                    type_label_zh="💡 功能需求",
                    tags=["功能建议"],
                ),
            ]

        evidence_text = "\n\n".join(evidence_prompt_lines) if evidence_prompt_lines else "（未检索到直接相关的知识库记录）"

        # 2. LLM Reasoning Prompt
        mod_cfg = settings.research_assistant
        provider = self.gateway.get_text_provider(force_mock=force_mock)
        provider_name = "mock" if force_mock else (settings.provider or "openrouter")
        selected_model = model_override or mod_cfg.model or getattr(provider, "default_text_model", "qwen/qwen3.8-flash")
        req_timeout = getattr(settings, "request_timeout_seconds", 240.0) or 240.0

        system_prompt = mod_cfg.system_prompt

        user_prompt = (
            f"用户提问：{question}\n\n"
            f"检索到的相关证据知识库片段：\n{evidence_text}\n\n"
            "请基于上述证据回答问题，输出 executive_summary（高管宏观综述），拆解出具体的需求卡片列表（requirements，每条完整包含 req_id, title, category, severity, problem_statement, user_voice, recommended_action, citation_ids），并给出详实论证正文 answer，且 requirements 与 answer 中均需严格带上对应的证据引用编号 [1], [2]...。"
        )

        async def _call_assistant() -> tuple[AssistantLLMOutput, dict[str, Any]]:
            if force_mock or not search_res.results:
                mock_answer = (
                    f"关于您询问的“{question}”，知识库中记录了相关反馈 [1]。"
                    "社群中有用户多次咨询与反馈该功能在特定场景下的体验表现 [1], [2]。"
                    "专业用户强烈建议针对下一阶段的体验进行升级优化。"
                )
                mock_out = AssistantLLMOutput(
                    executive_summary=f"基于社群用户反馈检索到的事实依据，关于“{question}”的核心痛点已完成初步分析，主要涉及交互易用性与功能稳定性。",
                    requirements=DEFAULT_MOCK_REQUIREMENTS,
                    answer=mock_answer,
                    confidence=0.95,
                    key_findings=["用户对该模块的使用存在高频讨论", "曲谱与蓝牙体验为重点优化诉求"],
                )
                return mock_out, {"prompt_tokens": 500, "completion_tokens": 300, "total_tokens": 800}

            return await provider.generate_structured(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_schema=AssistantLLMOutput,
                temperature=mod_cfg.temperature,
                top_p=mod_cfg.top_p,
                top_k=mod_cfg.top_k,
                max_tokens=mod_cfg.max_tokens or 8192,
                model=selected_model,
                reasoning_effort=getattr(mod_cfg, "reasoning_effort", "medium"),
                enable_thinking=getattr(mod_cfg, "enable_thinking", None),
                thinking_budget=getattr(mod_cfg, "thinking_budget", None),
                preserve_thinking=getattr(mod_cfg, "preserve_thinking", None),
                max_completion_tokens=getattr(mod_cfg, "max_completion_tokens", None),
                timeout=req_timeout,
            )

        prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16]

        output, run, was_cached = await self.analysis_service.execute_cached(
            workspace_id=ws.id,
            run_type="research_assistant",
            target_type="query",
            target_id="q_" + str(abs(hash(question)))[:16],
            provider=provider_name,
            model=selected_model,
            prompt_key="assistant_qa_v3_cards",
            input_data={"question": question, "hits": len(search_res.results)},
            config_data={
                "model": selected_model,
                "schema": "AssistantLLMOutput_v3",
                "temperature": mod_cfg.temperature,
                "top_p": mod_cfg.top_p,
                "prompt_hash": prompt_hash,
            },
            response_schema=AssistantLLMOutput,
            call_fn=_call_assistant,
            bypass_cache=force_refresh,
        )

        duration_ms = round((time.perf_counter() - t0) * 1000, 1)

        # Ensure requirements are not empty via fallback parser
        requirements_list = list(output.requirements or [])
        if not requirements_list and output.answer:
            requirements_list = parse_fallback_requirements(output.answer)

        # Emergency salvage: If LLM returned 0 requirements (e.g. truncated or empty parse),
        # but hybrid search found solid evidence (score >= 0.5), synthesize requirements from top evidence
        # so the user NEVER falsely receives "暂无直接相关的缺陷记录"!
        if not requirements_list and search_res.results:
            top_candidates = [r for r in search_res.results if r.score >= 0.5]
            if top_candidates:
                synth_reqs = []
                for s_idx, cand in enumerate(top_candidates[:4], 1):
                    cand_title = cand.title
                    cand_module = cand.metadata.get("module") or "配置与音色"
                    cand_severity = cand.metadata.get("severity") or "major"
                    cand_quotes = quotes_by_citation_id.get(s_idx, [])
                    cand_voice = cand_quotes[0] if cand_quotes else None
                    synth_reqs.append(
                        RequirementItem(
                            req_id=f"REQ-{s_idx}",
                            title=cand_title.split("]")[-1].strip() if "]" in cand_title else cand_title[:15],
                            category=cand_module,
                            severity=cand_severity,
                            problem_statement=(
                                f"• 【问题现象】：{cand.snippet}\n"
                                f"• 【诱发原因】：社群用户在日常使用中针对该功能存在明确反馈与体验落差\n"
                                f"• 【用户建议】：社群未提及明确建议\n"
                                f"• 【需求补充】：建议研发与产品团队建单重点排查该场景"
                            ),
                            user_voice=cand_voice,
                            recommended_action=(
                                f"1. 【排查/修复】复现用户反馈路径并定位根因\n"
                                f"2. 【体验/指引】优化该功能的操作指引与预期管理\n"
                                f"3. 【闭环/机制】建立用户体验回访机制"
                            ),
                            citation_ids=[s_idx],
                        )
                    )
                requirements_list = synth_reqs

        # 2.5. Citation Grounding Pruning & Re-indexing
        # Collect all citation IDs actually referenced in requirements or in answer text [1], [2]
        referenced_ids = set()
        for req in requirements_list:
            for cid in (req.citation_ids or []):
                try:
                    referenced_ids.add(int(cid))
                except (ValueError, TypeError):
                    pass
        if output.answer:
            for m in re.finditer(r"\[(\d+)\]", output.answer):
                referenced_ids.add(int(m.group(1)))

        active_answer = output.answer or output.executive_summary or ""
        if not active_answer and requirements_list:
            active_answer = "基于社群用户反馈事实，提炼出以下核心需求与痛点：\n" + "\n".join(
                f"{idx}. **{r.title}** [{r.category}]：{r.problem_statement}"
                for idx, r in enumerate(requirements_list, 1)
            )
        if referenced_ids:
            kept_citations = [c for c in citations if c.citation_id in referenced_ids]
            if not kept_citations:
                kept_citations = citations[:3] if requirements_list else []
        else:
            kept_citations = citations[:3] if requirements_list else []

        # Re-index citations to consecutive 1, 2, 3...
        id_map: dict[int, int] = {}
        pruned_citations: list[Citation] = []
        for new_idx, c in enumerate(kept_citations, start=1):
            old_idx = c.citation_id
            id_map[old_idx] = new_idx
            c_dict = c.model_dump()
            c_dict["citation_id"] = new_idx
            pruned_citations.append(Citation(**c_dict))

        # Re-map citation IDs in requirements and format structured texts
        updated_requirements: list[RequirementItem] = []
        for req in requirements_list:
            r_dict = req.model_dump()
            old_cids = r_dict.get("citation_ids") or []
            new_cids = [id_map[int(oc)] for oc in old_cids if oc is not None and int(oc) in id_map]
            r_dict["citation_ids"] = new_cids
            r_dict["problem_statement"] = format_problem_statement(r_dict.get("problem_statement", ""))
            r_dict["recommended_action"] = format_recommended_actions(r_dict.get("recommended_action", ""))
            updated_requirements.append(RequirementItem(**r_dict))
        requirements_list = updated_requirements

        # Re-map [old_id] to [new_id] in answer text
        if active_answer and id_map:
            def _replace_cite(match):
                old_num = int(match.group(1))
                if old_num in id_map:
                    return f"[{id_map[old_num]}]"
                return ""
            active_answer = re.sub(r"\[(\d+)\]", _replace_cite, active_answer)

        citations = pruned_citations

        exec_summary = output.executive_summary
        if not exec_summary:
            if requirements_list:
                exec_summary = f"基于社群用户反馈事实，深度提炼出 {len(requirements_list)} 项核心优化需求。"
            else:
                exec_summary = f"基于当前社群多模态知识库的检索结果，暂未发现与“{question}”直接相关的缺陷记录或明确改进诉求。"

        if not active_answer:
            if requirements_list:
                active_answer = "基于社群用户反馈事实，提炼出以下核心需求与痛点：\n" + "\n".join(
                    f"{idx}. **{r.title}** [{r.category}]：{r.problem_statement}"
                    for idx, r in enumerate(requirements_list, 1)
                )
            else:
                active_answer = f"基于当前知识库的事实检索，关于【{question}】暂未发现直接对应的社群缺陷或明确改进建议。\n\n当前沉淀的原声数据主要覆盖其他业务模块，建议后续结合专项问卷或针对性用户访谈补充该维度的原声事实。"

        # 3. Persist to Local Research History
        # Avoid caching poisoned runs where requirements are empty if search results were actually present
        should_persist = True
        if not requirements_list and search_res.results:
            top_candidates = [r for r in search_res.results if r.score >= 0.5]
            if top_candidates:
                should_persist = False

        saved_rec = None
        if should_persist:
            saved_rec = await self.history_service.save_record(
                workspace_id=ws.id,
                question=question,
                answer_text=active_answer,
                executive_summary=exec_summary,
                requirements=[r.model_dump() for r in requirements_list],
                citations=[c.model_dump() for c in citations],
                key_findings=output.key_findings,
                related_topic_ids=related_topics,
                related_insight_ids=related_insights,
                confidence=output.confidence,
                model_used=selected_model,
                prompt_tokens=getattr(run, "input_tokens", None),
                completion_tokens=getattr(run, "output_tokens", None),
                total_tokens=getattr(run, "total_tokens", None),
                duration_ms=duration_ms,
            )

        # 4. Auto-prune expired records
        await self.history_service.prune_expired(
            retention_days=retention_days,
            max_records=max_records,
        )
        await self.session.commit()

        return ResearchAssistantResponse(
            question=question,
            executive_summary=exec_summary,
            requirements=requirements_list,
            answer=active_answer,
            citations=citations,
            key_findings=output.key_findings,
            related_topic_ids=related_topics,
            related_insight_ids=related_insights,
            confidence=output.confidence,
            from_history=False,
            history_id=saved_rec.id,
            created_at=saved_rec.created_at.isoformat(),
            duration_ms=duration_ms,
            model_used=selected_model,
        )
