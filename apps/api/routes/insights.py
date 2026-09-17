from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.insights.insight_extractor import InsightExtractor
from packages.insights.tag_manager import TagManager
from packages.persistence.db import get_session
from packages.persistence.models import (
    Conversation,
    Episode,
    EpisodeMessage,
    Insight,
    InsightClaim,
    InsightPushRecord,
    MediaAsset,
    Message,
    MessageMediaLink,
    Participant,
    Topic,
    TopicInsightLink,
)
from packages.persistence.repositories.registry import RepositoryRegistry

router = APIRouter(tags=["Insights & Human Review"])


TYPE_LABEL_MAP = {
    "issue": "🐛 产品缺陷",
    "product_issue": "🐛 产品缺陷",
    "bug": "🐛 产品缺陷",
    "feature_request": "💡 功能需求",
    "explicit_requirement": "💡 明确功能需求",
    "latent_need": "🔍 潜在需求挖掘",
    "usability_opportunity": "🎯 体验与易用性优化",
    "inquiry": "❓ 咨询求助",
    "consultation": "❓ 使用指导与咨询",
    "documentation_gap": "📖 说明与文档缺失",
    "praise": "❤️ 体验好评",
    "positive_signal": "❤️ 积极体验好评",
    "non_product": "💬 社群日常交流",
    "suggestion": "💡 体验建议",
    "general": "💬 综合交流",
}

SEVERITY_LABEL_MAP = {
    "blocker": "🚨 致命阻塞",
    "major": "⚠️ 严重故障",
    "minor": "📌 一般缺陷",
    "trivial": "🌱 轻微建议",
}

STATUS_LABEL_MAP = {
    "unresolved": "⏳ 讨论中/未解决",
    "support_acknowledged": "👨‍💻 客服已确认/记录",
    "workaround_provided": "🛠️ 已提供临时规避方案",
    "fix_confirmed": "✅ 用户确认已解决",
    "reported": "📢 用户初次上报",
}


class ExtractInsightRequest(BaseModel):
    force_mock: bool = False
    model: Optional[str] = None


class BatchExtractInsightRequest(BaseModel):
    force_mock: bool = False
    model: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)
    clean_previous: bool = Field(default=False)


class ClaimResponse(BaseModel):
    id: str
    claim_key: str
    claim_text: str
    fact_category: str
    evidence_uris: list[str]
    confidence: float
    verification_state: str


class PushHistoryRecord(BaseModel):
    record_id: str
    target_platform: str
    state: str
    status_code: Optional[int] = None
    response_body: Optional[str] = None
    error_message: Optional[str] = None
    operator: Optional[str] = None
    created_at: str


class InsightResponse(BaseModel):
    id: str
    episode_id: str
    insight_type: str
    module: str
    sub_module: Optional[str]
    severity: str
    priority: str
    summary: str
    description: str
    status_in_chat: str
    support_known_status: bool
    factual_score: float
    confidence: float
    tags: list[str] = []
    category_l1: Optional[str] = "general"
    category_l2: Optional[str] = None
    l1_tag_zh: Optional[str] = "综合交流"
    l2_tag_zh: Optional[str] = None
    type_label_zh: str = ""
    severity_label_zh: str = ""
    status_label_zh: str = ""
    confidence_level: str = "high"  # high | medium | low
    confidence_reason: str = ""
    is_clustered: bool = False
    topic_id: Optional[str] = None
    topic_title: Optional[str] = None
    state: str
    reviewed_by: Optional[str]
    rejection_reason: Optional[str]
    claims: list[ClaimResponse] = []
    push_status: str = "not_pushed"  # success | failed | not_pushed
    last_pushed_at: Optional[str] = None
    push_count: int = 0
    push_history: list[PushHistoryRecord] = []
    created_at: str


class EvidenceMessageItem(BaseModel):
    id: str
    message_id: str
    sequence: int
    sent_at: str
    sender_label: str
    sender_role: str
    raw_text: str
    media_attachments: list[dict[str, Any]] = []
    is_cited: bool = False
    cited_claims: list[str] = []


class InsightEvidenceResponse(BaseModel):
    insight_id: str
    episode_id: Optional[str] = None
    conversation_name: str
    summary: str
    description: str
    type_label_zh: str
    severity_label_zh: str
    status_label_zh: str
    confidence_level: str
    confidence_reason: str
    claims: list[ClaimResponse]
    messages: list[EvidenceMessageItem]


class ReviewInsightRequest(BaseModel):
    action: str  # approve | reject | archive
    reviewer: str = "human_operator"
    rejection_reason: Optional[str] = None
    summary_override: Optional[str] = None
    module_override: Optional[str] = None
    severity_override: Optional[str] = None


def _enrich_insight(
    ins: Insight,
    claims: list[InsightClaim],
    topic_link: Optional[TopicInsightLink] = None,
    topic: Optional[Topic] = None,
    push_status: str = "not_pushed",
    last_pushed_at: Optional[str] = None,
    push_count: int = 0,
    push_records: Optional[list[InsightPushRecord]] = None,
) -> InsightResponse:
    # 1. Tags and Breadcrumbs
    l1_k, l2_k, disp_name = TagManager.get_best_category_and_tags(
        f"{ins.summary} {ins.description} {ins.module}"
    )
    raw_tags = list(ins.tags_json or [])
    cleaned_tags: list[str] = []
    for t in raw_tags:
        zh_t = TagManager.format_tag_display(t)
        if zh_t and zh_t not in cleaned_tags:
            cleaned_tags.append(zh_t)

    if disp_name and " · " in disp_name:
        parts = [p.strip() for p in disp_name.split(" · ")]
        l1_zh = parts[0]
        l2_zh = parts[1]
    else:
        l1_zh = disp_name or "综合交流"
        l2_zh = None

    for p in [l2_zh, l1_zh]:
        if p and p not in cleaned_tags:
            cleaned_tags.insert(0, p)

    # 2. Labels & Module
    type_zh = TYPE_LABEL_MAP.get(ins.insight_type, TagManager.format_tag_display(ins.insight_type))
    sev_zh = SEVERITY_LABEL_MAP.get(ins.severity, ins.severity)
    status_zh = STATUS_LABEL_MAP.get(ins.status_in_chat, ins.status_in_chat)
    final_module = TagManager.format_tag_display(ins.module or disp_name or "综合交流")

    # 3. Confidence level & reason
    score = float(ins.factual_score if ins.factual_score is not None else ins.confidence)
    if score >= 0.85:
        conf_level = "high"
        conf_reason = f"高置信度 ({int(score * 100)}%)：核心事实由聊天原句及客服明确印证，证据充分闭环"
    elif score >= 0.60:
        conf_level = "medium"
        conf_reason = f"中置信度 ({int(score * 100)}%)：部分主张有据可查，部分背景信息包含模型推断"
    else:
        conf_level = "low"
        conf_reason = f"低置信度 ({int(score * 100)}%)：缺乏直接原句支持，可能属于社群推测或泛化描述"

    is_clustered = topic_link is not None
    top_id = topic.id if topic else (topic_link.topic_id if topic_link else None)
    top_title = topic.title if topic else None

    push_hist = [
        PushHistoryRecord(
            record_id=r.id,
            target_platform=r.target_platform,
            state=r.state,
            status_code=r.status_code,
            response_body=r.response_body,
            error_message=r.error_message,
            operator=r.operator,
            created_at=r.created_at.isoformat(),
        )
        for r in (push_records or [])
    ]

    return InsightResponse(
        id=ins.id,
        episode_id=ins.episode_id,
        insight_type=ins.insight_type,
        module=final_module,
        sub_module=ins.sub_module,
        severity=ins.severity,
        priority=ins.priority,
        summary=ins.summary,
        description=ins.description,
        status_in_chat=ins.status_in_chat,
        support_known_status=ins.support_known_status,
        factual_score=ins.factual_score,
        confidence=ins.confidence,
        tags=cleaned_tags,
        category_l1=l1_k,
        category_l2=l2_k,
        l1_tag_zh=l1_zh,
        l2_tag_zh=l2_zh,
        type_label_zh=type_zh,
        severity_label_zh=sev_zh,
        status_label_zh=status_zh,
        confidence_level=conf_level,
        confidence_reason=conf_reason,
        is_clustered=is_clustered,
        topic_id=top_id,
        topic_title=top_title,
        state=ins.state,
        reviewed_by=ins.reviewed_by,
        rejection_reason=ins.rejection_reason,
        claims=[
            ClaimResponse(
                id=c.id,
                claim_key=c.claim_key,
                claim_text=c.claim_text,
                fact_category=c.fact_category,
                evidence_uris=c.evidence_uris_json or [],
                confidence=c.confidence,
                verification_state=c.verification_state,
            )
            for c in claims
        ],
        push_status=push_status,
        last_pushed_at=last_pushed_at,
        push_count=push_count,
        push_history=push_hist,
        created_at=ins.created_at.isoformat(),
    )


def _resolve_scope_dates(
    preset: str, start_date: str | None, end_date: str | None
) -> tuple[datetime | None, datetime | None]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if preset == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0), None
    elif preset == "yesterday":
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, end
    elif preset == "last_7_days":
        return now - timedelta(days=7), None
    elif preset == "last_30_days":
        return now - timedelta(days=30), None
    elif preset == "custom":
        start = datetime.fromisoformat(start_date) if start_date else None
        end = datetime.fromisoformat(end_date) if end_date else None
        return start, end
    return None, None


@router.post("/api/v1/episodes/{id}/extract", response_model=list[InsightResponse])
async def extract_insights_from_episode(
    id: str,
    payload: ExtractInsightRequest = ExtractInsightRequest(),
    session: AsyncSession = Depends(get_session),
):
    extractor = InsightExtractor(session)
    try:
        results = await extractor.extract_insights_from_episode(
            episode_id=id,
            force_mock=payload.force_mock,
            model_override=payload.model,
        )
        await session.commit()

        repo = RepositoryRegistry(session)
        responses = []
        for ins, _ in results:
            claims = await repo.get_claims_for_insight(ins.id)
            responses.append(_enrich_insight(ins, claims))
        return responses
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Episode not found")


@router.post("/api/v1/insights/batch-extract", response_model=list[InsightResponse])
async def batch_extract_insights(
    payload: BatchExtractInsightRequest = BatchExtractInsightRequest(),
    session: AsyncSession = Depends(get_session),
):
    """Batch extracts varied, grounded insights from multiple episodes."""
    if payload.clean_previous:
        await session.execute(delete(TopicInsightLink))
        await session.execute(delete(InsightClaim))
        await session.execute(delete(Insight))
        await session.execute(delete(Topic))
        await session.commit()

    ep_stmt = select(Episode).order_by(Episode.started_at.desc()).limit(payload.limit)
    episodes = (await session.execute(ep_stmt)).scalars().all()

    extractor = InsightExtractor(session)
    repo = RepositoryRegistry(session)
    responses = []

    for ep in episodes:
        try:
            results = await extractor.extract_insights_from_episode(
                episode_id=ep.id,
                force_mock=payload.force_mock,
                model_override=payload.model,
            )
            for ins, _ in results:
                claims = await repo.get_claims_for_insight(ins.id)
                responses.append(_enrich_insight(ins, claims))
        except Exception:
            pass

    await session.commit()
    return responses


@router.get("/api/v1/insights", response_model=list[InsightResponse])
async def list_insights(
    search: Optional[str] = Query(default=None, description="全文关键字检索（标题、描述、模块）"),
    tags: Optional[str] = Query(default=None, description="多选标签，英文逗号分隔"),
    date_preset: str = Query(default="all", description="all | today | yesterday | last_7_days | last_30_days | custom"),
    start_date: Optional[str] = Query(default=None, description="自定义起始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(default=None, description="自定义结束日期 YYYY-MM-DD"),
    sort_by: str = Query(default="created_at_desc", description="created_at_desc | created_at_asc | confidence_desc | severity_desc"),
    state: Optional[str] = Query(default=None),
    insight_type: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    push_status: Optional[str] = Query(default=None, description="all | success | failed | pending | not_pushed"),
    module: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    repo = RepositoryRegistry(session)

    # Parse date range
    dt_start, dt_end = _resolve_scope_dates(date_preset, start_date, end_date)

    # Parse tag keys
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    insights = await repo.get_insights(
        state=state,
        insight_type=insight_type,
        severity=severity,
        module=module,
        search=search,
        tag_keys=tag_list,
        start_date=dt_start,
        end_date=dt_end,
        sort_by=sort_by,
        limit=limit if not push_status or push_status == "all" else max(limit * 3, 100),
        offset=offset if not push_status or push_status == "all" else 0,
    )

    if not insights:
        return []

    ins_ids = [ins.id for ins in insights]

    # Batch load push records to check push status
    push_q = (
        select(InsightPushRecord)
        .where(InsightPushRecord.insight_id.in_(ins_ids))
        .order_by(desc(InsightPushRecord.created_at))
    )
    push_recs = (await session.execute(push_q)).scalars().all()
    push_map: dict[str, list[InsightPushRecord]] = {}
    for r in push_recs:
        if r.insight_id not in push_map:
            push_map[r.insight_id] = []
        push_map[r.insight_id].append(r)

    # Batch load topic links to check clustering status
    link_stmt = (
        select(TopicInsightLink, Topic)
        .join(Topic, TopicInsightLink.topic_id == Topic.id)
        .where(TopicInsightLink.insight_id.in_(ins_ids))
    )
    link_rows = (await session.execute(link_stmt)).all()
    link_map: dict[str, tuple[TopicInsightLink, Topic]] = {
        link.insight_id: (link, topic) for link, topic in link_rows
    }

    responses = []
    for ins in insights:
        claims = await repo.get_claims_for_insight(ins.id)
        topic_pair = link_map.get(ins.id)
        t_link = topic_pair[0] if topic_pair else None
        t_obj = topic_pair[1] if topic_pair else None

        recs = push_map.get(ins.id, [])
        p_status = recs[0].state if recs else "not_pushed"
        last_push = recs[0].created_at.strftime("%Y-%m-%d %H:%M:%S") if recs else None
        p_cnt = len(recs)

        enriched = _enrich_insight(
            ins, claims, topic_link=t_link, topic=t_obj,
            push_status=p_status, last_pushed_at=last_push, push_count=p_cnt,
            push_records=recs,
        )

        # Apply push status filter if specified
        if push_status and push_status != "all":
            if push_status == "success" and enriched.push_status != "success":
                continue
            elif push_status == "failed" and enriched.push_status != "failed":
                continue
            elif push_status in ("pending", "not_pushed") and enriched.push_status in ("success", "failed"):
                continue

        responses.append(enriched)

    if push_status and push_status != "all":
        return responses[offset : offset + limit]

    return responses


@router.get("/api/v1/insights/{id}", response_model=InsightResponse)
async def get_insight(id: str, session: AsyncSession = Depends(get_session)):
    repo = RepositoryRegistry(session)
    ins = await repo.get_insight_by_id(id)
    if not ins:
        raise HTTPException(status_code=404, detail="Insight not found")

    claims = await repo.get_claims_for_insight(ins.id)

    link_stmt = (
        select(TopicInsightLink, Topic)
        .join(Topic, TopicInsightLink.topic_id == Topic.id)
        .where(TopicInsightLink.insight_id == ins.id)
    )
    link_row = (await session.execute(link_stmt)).first()
    t_link = link_row[0] if link_row else None
    t_obj = link_row[1] if link_row else None

    return _enrich_insight(ins, claims, topic_link=t_link, topic=t_obj)


@router.get("/api/v1/insights/{id}/evidence", response_model=InsightEvidenceResponse)
async def get_insight_evidence(id: str, session: AsyncSession = Depends(get_session)):
    """Fetches comprehensive 5W1H factual evidence, Claim grounding, and raw chat message stream."""
    repo = RepositoryRegistry(session)
    ins = await repo.get_insight_by_id(id)
    if not ins:
        # Fallback 1: if id is a Topic ID, resolve to representative linked insight
        link_stmt = select(TopicInsightLink).where(TopicInsightLink.topic_id == id)
        link = (await session.execute(link_stmt)).scalars().first()
        if link:
            ins = await repo.get_insight_by_id(link.insight_id)

    if not ins:
        # Fallback 2: check if id is a Message ID
        msg_target = (await session.execute(select(Message).where(Message.id == id))).scalar_one_or_none()
        if msg_target:
            ep_msg = (await session.execute(select(EpisodeMessage).where(EpisodeMessage.message_id == msg_target.id))).scalars().first()
            if ep_msg:
                ins = (await session.execute(select(Insight).where(Insight.episode_id == ep_msg.episode_id))).scalars().first()
            
            if not ins:
                conv = (await session.execute(select(Conversation).where(Conversation.id == msg_target.conversation_id))).scalar_one_or_none()
                conv_name = conv.display_name if conv else "社群交流群"
                part = (await session.execute(select(Participant).where(Participant.id == msg_target.participant_id))).scalar_one_or_none() if msg_target.participant_id else None
                sender_label = part.display_label if part else "社群用户"

                surrounding_stmt = (
                    select(Message, Participant)
                    .outerjoin(Participant, Message.participant_id == Participant.id)
                    .where(Message.conversation_id == msg_target.conversation_id)
                    .order_by(Message.sent_at.asc(), Message.source_sequence.asc())
                )
                all_conv_msgs = (await session.execute(surrounding_stmt)).all()
                
                target_idx = 0
                for i, (m, _) in enumerate(all_conv_msgs):
                    if m.id == msg_target.id:
                        target_idx = i
                        break
                start_i = max(0, target_idx - 6)
                end_i = min(len(all_conv_msgs), target_idx + 7)
                window_rows = all_conv_msgs[start_i:end_i]

                evidence_messages = []
                for m, p in window_rows:
                    is_target = (m.id == msg_target.id)
                    evidence_messages.append(
                        EvidenceMessageItem(
                            id=m.id,
                            message_id=m.id,
                            sequence=m.source_sequence,
                            sent_at=m.sent_at.isoformat() if m.sent_at else "",
                            sender_label=p.display_label if p else "群友",
                            sender_role=p.role if p else "user",
                            raw_text=m.raw_text or "",
                            is_cited=is_target,
                            cited_claims=["c_1"] if is_target else [],
                            media_items=[],
                        )
                    )

                return InsightEvidenceResponse(
                    insight_id=msg_target.id,
                    episode_id=ep_msg.episode_id if ep_msg else None,
                    conversation_name=conv_name,
                    summary=f"社群原声: {msg_target.raw_text[:40]}",
                    description=f"[用户发言原声] {msg_target.raw_text}",
                    type_label_zh="💬 社群原声",
                    severity_label_zh="📌 一般关注",
                    status_label_zh="💬 原生对话",
                    confidence_level="high",
                    confidence_reason="高置信度 (100%)：来自社群真实群聊发言原生文本记录",
                    claims=[
                        ClaimResponse(
                            id=f"claim_{msg_target.id[:8]}",
                            claim_key="c_1",
                            claim_text=f"{sender_label}：“{msg_target.raw_text}”",
                            fact_category="symptom",
                            evidence_uris=[f"chatinsight://conv/{msg_target.conversation_id}/msg_{msg_target.id}#text"],
                            confidence=1.0,
                            verification_state="supported",
                        )
                    ],
                    messages=evidence_messages,
                )

    if not ins:
        raise HTTPException(status_code=404, detail="Insight not found")

    ep = await repo.get_episode_by_id(ins.episode_id)
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found")

    conv = (await session.execute(select(Conversation).where(Conversation.id == ep.conversation_id))).scalar_one_or_none()
    conv_name = conv.display_name if conv else "未知群聊"

    claims = await repo.get_claims_for_insight(ins.id)
    claim_responses = [
        ClaimResponse(
            id=c.id,
            claim_key=c.claim_key,
            claim_text=c.claim_text,
            fact_category=c.fact_category,
            evidence_uris=c.evidence_uris_json or [],
            confidence=c.confidence,
            verification_state=c.verification_state,
        )
        for c in claims
    ]

    # Collect all cited evidence URIs
    all_cited_uris: set[str] = set()
    uri_to_claim_keys: dict[str, list[str]] = {}
    for c in claims:
        for uri in (c.evidence_uris_json or []):
            all_cited_uris.add(uri)
            if uri not in uri_to_claim_keys:
                uri_to_claim_keys[uri] = []
            uri_to_claim_keys[uri].append(c.claim_key)

    # Query all raw messages in this episode with sender details
    msg_stmt = (
        select(Message, Participant, EpisodeMessage.sequence)
        .join(EpisodeMessage, EpisodeMessage.message_id == Message.id)
        .outerjoin(Participant, Message.participant_id == Participant.id)
        .where(EpisodeMessage.episode_id == ep.id)
        .order_by(Message.sent_at.asc(), EpisodeMessage.sequence.asc())
    )
    rows = (await session.execute(msg_stmt)).all()

    msg_ids = [m.id for m, _, _ in rows]

    # Query attached media links
    media_stmt = (
        select(MessageMediaLink, MediaAsset)
        .join(MediaAsset, MessageMediaLink.media_id == MediaAsset.id)
        .where(MessageMediaLink.message_id.in_(msg_ids))
        .order_by(MessageMediaLink.media_order.asc())
    )
    media_rows = (await session.execute(media_stmt)).all()
    media_map: dict[str, list[dict[str, Any]]] = {}
    for link, asset in media_rows:
        if link.message_id not in media_map:
            media_map[link.message_id] = []
        media_map[link.message_id].append({
            "id": asset.id,
            "kind": asset.kind,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes or 0,
            "url": f"/api/v1/media/{asset.id}/content",
            "thumbnail_url": f"/api/v1/media/{asset.id}/thumbnail",
        })

    evidence_messages: list[EvidenceMessageItem] = []
    for msg, participant, seq in rows:
        display_label = participant.display_label if participant else "群友"
        role = participant.role if participant else "user"

        # Check citation match against message_id or sequence
        is_cited = False
        cited_keys: list[str] = []

        for uri, c_keys in uri_to_claim_keys.items():
            if f"msg_{msg.id}" in uri or f"msg_{seq}" in uri or msg.id in uri:
                is_cited = True
                cited_keys.extend(c_keys)

        evidence_messages.append(
            EvidenceMessageItem(
                id=msg.id,
                message_id=msg.id,
                sequence=seq,
                sent_at=msg.sent_at.isoformat() if msg.sent_at else "",
                sender_label=display_label,
                sender_role=role,
                raw_text=msg.raw_text or "",
                media_attachments=media_map.get(msg.id, []),
                is_cited=is_cited,
                cited_claims=list(set(cited_keys)),
            )
        )

    # Labels and confidence
    type_zh = TYPE_LABEL_MAP.get(ins.insight_type, TagManager.format_tag_display(ins.insight_type))
    sev_zh = SEVERITY_LABEL_MAP.get(ins.severity, ins.severity)
    status_zh = STATUS_LABEL_MAP.get(ins.status_in_chat, ins.status_in_chat)

    score = float(ins.factual_score if ins.factual_score is not None else ins.confidence)
    if score >= 0.85:
        conf_level = "high"
        conf_reason = f"高置信度 ({int(score * 100)}%)：核心事实由群聊原句及客服明确印证，证据充分闭环"
    elif score >= 0.60:
        conf_level = "medium"
        conf_reason = f"中置信度 ({int(score * 100)}%)：部分主张有据可查，部分背景包含推论"
    else:
        conf_level = "low"
        conf_reason = f"低置信度 ({int(score * 100)}%)：缺乏直接原句支持，可能属于推测或泛化描述"

    return InsightEvidenceResponse(
        insight_id=ins.id,
        episode_id=ins.episode_id,
        conversation_name=conv_name,
        summary=ins.summary,
        description=ins.description,
        type_label_zh=type_zh,
        severity_label_zh=sev_zh,
        status_label_zh=status_zh,
        confidence_level=conf_level,
        confidence_reason=conf_reason,
        claims=claim_responses,
        messages=evidence_messages,
    )


@router.post("/api/v1/insights/{id}/review", response_model=InsightResponse)
async def review_insight(
    id: str,
    payload: ReviewInsightRequest,
    session: AsyncSession = Depends(get_session),
):
    if payload.action not in ("approve", "reject", "archive"):
        raise HTTPException(status_code=400, detail="Action must be 'approve', 'reject', or 'archive'")

    target_state = "approved" if payload.action == "approve" else ("rejected" if payload.action == "reject" else "archived")

    repo = RepositoryRegistry(session)
    updated = await repo.update_insight_review(
        insight_id=id,
        state=target_state,
        reviewed_by=payload.reviewer,
        rejection_reason=payload.rejection_reason,
        summary_override=payload.summary_override,
        module_override=payload.module_override,
        severity_override=payload.severity_override,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Insight not found")

    # Record Audit Event
    ws = await repo.get_or_create_default_workspace()
    await repo.record_audit(
        workspace_id=ws.id,
        actor_id=payload.reviewer,
        action=f"insight.{payload.action}",
        object_type="insight",
        object_id=updated.id,
        metadata_json={
            "target_state": target_state,
            "rejection_reason": payload.rejection_reason,
            "summary_override": payload.summary_override,
        },
    )

    await session.commit()
    claims = await repo.get_claims_for_insight(updated.id)

    link_stmt = (
        select(TopicInsightLink, Topic)
        .join(Topic, TopicInsightLink.topic_id == Topic.id)
        .where(TopicInsightLink.insight_id == updated.id)
    )
    link_row = (await session.execute(link_stmt)).first()
    t_link = link_row[0] if link_row else None
    t_obj = link_row[1] if link_row else None

    return _enrich_insight(updated, claims, topic_link=t_link, topic=t_obj)
