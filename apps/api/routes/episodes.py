from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.insights.contracts import EpisodeContextPacket
from packages.insights.episode_deduplicator import DistinctTopicCluster, EpisodeDeduplicator, EpisodeItemView
from packages.insights.episode_segmenter import EpisodeSegmenter
from packages.insights.tag_manager import TagDefinition, TagManager
from packages.persistence.db import get_session
from packages.persistence.models import (
    Conversation,
    Episode,
    EpisodeMessage,
    MediaAsset,
    Message,
    MessageMediaLink,
    Participant,
)
from packages.persistence.repositories.registry import RepositoryRegistry

router = APIRouter(tags=["Episodes & Segmentation"])


# ----------------------------------------------------
# Topic Tag Management Endpoints
# ----------------------------------------------------
@router.get("/api/v1/tags", response_model=list[TagDefinition])
async def list_topic_tags(session: AsyncSession = Depends(get_session)):
    """Lists all topic tags with dynamic usage statistics."""
    tags = TagManager.get_all_tags()
    
    # Calculate usage from episodes table
    stmt = select(Episode.category_hint)
    rows = (await session.execute(stmt)).scalars().all()
    cat_counts: dict[str, int] = {}
    for cat in rows:
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    for t in tags:
        t.episode_count = cat_counts.get(t.key, 0)
        # Approximate topic count based on episodes
        t.topic_count = max(1 if t.episode_count > 0 else 0, t.episode_count // 3)

    return tags


@router.post("/api/v1/tags", response_model=TagDefinition)
async def create_or_update_topic_tag(tag: TagDefinition):
    """Creates or updates a topic tag definition."""
    saved = TagManager.upsert_tag(tag.model_dump())
    return saved


@router.delete("/api/v1/tags/{key}")
async def delete_topic_tag(key: str):
    """Deletes a custom topic tag definition (system tags cannot be deleted)."""
    success = TagManager.delete_tag(key)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot delete tag (built-in system tag or tag not found)")
    return {"success": True, "message": f"Tag {key} deleted successfully"}


# ----------------------------------------------------
# Episode & Cluster Endpoints
# ----------------------------------------------------
class SegmentRequest(BaseModel):
    time_gap_minutes: int = 15
    force_mock: bool = False


class MediaAttachmentItem(BaseModel):
    id: str
    kind: str
    mime_type: Optional[str] = None
    size_bytes: int = 0
    url: str
    thumbnail_url: str


class EpisodeMessageDetail(BaseModel):
    id: str
    conversation_id: str
    conversation_name: str
    participant_id: Optional[str] = None
    sender_label: str
    sender_role: str
    sent_at: str
    raw_text: str
    quote_text: Optional[str] = None
    attachments: list[MediaAttachmentItem] = Field(default_factory=list)


class EpisodeResponse(BaseModel):
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
    participants: list[str]
    media_ids: list[str]
    state: str


class EpisodeDetailResponse(BaseModel):
    episode: EpisodeResponse
    messages: list[EpisodeMessageDetail]


async def _build_nickname_resolver(session: AsyncSession) -> dict[str, str]:
    """Builds a lookup map from anonymous keys or display labels to real WeChat nicknames."""
    part_stmt = select(Participant)
    participants_db = (await session.execute(part_stmt)).scalars().all()
    anon_to_real_map: dict[str, str] = {}
    for p in participants_db:
        raw = (p.metadata_json or {}).get("raw_nickname") or p.display_label
        if raw and not raw.startswith("用户 U-"):
            anon_to_real_map[p.display_label] = raw
            anon_to_real_map[p.stable_anonymous_key] = raw
            anon_to_real_map[p.id] = raw
    return anon_to_real_map


def _resolve_participants(raw_parts: list[str], nick_map: dict[str, str]) -> list[str]:
    resolved = []
    for p_name in raw_parts:
        if not p_name:
            continue
        if p_name in nick_map:
            resolved.append(nick_map[p_name])
        elif not p_name.startswith("用户 U-"):
            resolved.append(p_name)
    if not resolved and raw_parts:
        return [p for p in raw_parts if p]
    return list(dict.fromkeys(resolved))


@router.get("/api/v1/episodes", response_model=list[EpisodeResponse])
async def list_all_episodes(
    conversation_id: Optional[str] = Query(default=None),
    sort_by: str = Query(default="latest", description="latest | first_seen | frequency | messages"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Episode, Conversation).join(Conversation, Episode.conversation_id == Conversation.id)
    if conversation_id and isinstance(conversation_id, str):
        stmt = stmt.where(Episode.conversation_id == conversation_id)
    
    lim = int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else 100
    off = int(offset) if isinstance(offset, (int, str)) and str(offset).isdigit() else 0
    
    if sort_by == "first_seen":
        stmt = stmt.order_by(Episode.started_at.asc())
    elif sort_by in ("frequency", "messages"):
        stmt = stmt.order_by(desc(Episode.message_count), desc(Episode.started_at))
    else:  # latest
        stmt = stmt.order_by(desc(Episode.started_at))
        
    stmt = stmt.offset(off).limit(lim)
    res = await session.execute(stmt)
    rows = res.all()

    nick_map = await _build_nickname_resolver(session)

    results = []
    for ep, conv in rows:
        dummy_item = EpisodeItemView(
            id=ep.id,
            conversation_id=ep.conversation_id,
            conversation_name=conv.display_name,
            title=ep.title,
            summary=ep.summary,
            category_hint=ep.category_hint,
            started_at=ep.started_at.isoformat(),
            ended_at=ep.ended_at.isoformat(),
            message_count=ep.message_count,
            participants=_resolve_participants(ep.participants_json or [], nick_map),
            media_ids=ep.media_json or [],
            state=ep.state,
        )
        l1, l2, l3, tags_list = EpisodeDeduplicator._parse_episode_tags(dummy_item)
        results.append(
            EpisodeResponse(
                id=ep.id,
                conversation_id=ep.conversation_id,
                conversation_name=conv.display_name,
                title=ep.title,
                summary=ep.summary,
                category_hint=ep.category_hint,
                category_l1=l1,
                category_l2=l2,
                category_l3=l3,
                l1_tag_zh=tags_list[0] if tags_list else "综合交流",
                l2_tag_zh=tags_list[1] if len(tags_list) > 1 else None,
                l3_tag_zh=tags_list[2] if len(tags_list) > 2 else None,
                tags=tags_list,
                started_at=ep.started_at.isoformat(),
                ended_at=ep.ended_at.isoformat(),
                message_count=ep.message_count,
                participants=_resolve_participants(ep.participants_json or [], nick_map),
                media_ids=ep.media_json or [],
                state=ep.state,
            )
        )
    return results


@router.get("/api/v1/episodes/clusters", response_model=list[DistinctTopicCluster])
async def list_distinct_topic_clusters(
    conversation_id: Optional[str] = Query(default=None),
    sort_by: str = Query(default="latest", description="latest | first_seen | frequency"),
    limit: int = Query(default=300, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """Returns deduplicated, distinct topic clusters aggregating similar episodes with multi-level tags and sorting."""
    stmt = select(Episode, Conversation).join(Conversation, Episode.conversation_id == Conversation.id)
    if conversation_id and isinstance(conversation_id, str):
        stmt = stmt.where(Episode.conversation_id == conversation_id)
    
    lim = int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else 300
    stmt = stmt.order_by(desc(Episode.started_at)).limit(lim)
    res = await session.execute(stmt)
    rows = res.all()

    nick_map = await _build_nickname_resolver(session)

    episode_views = [
        EpisodeItemView(
            id=ep.id,
            conversation_id=ep.conversation_id,
            conversation_name=conv.display_name,
            title=ep.title,
            summary=ep.summary,
            category_hint=ep.category_hint,
            started_at=ep.started_at.isoformat(),
            ended_at=ep.ended_at.isoformat(),
            message_count=ep.message_count,
            participants=_resolve_participants(ep.participants_json or [], nick_map),
            media_ids=ep.media_json or [],
            state=ep.state,
        )
        for ep, conv in rows
    ]

    return EpisodeDeduplicator.deduplicate_episodes(episode_views, sort_by=sort_by)


@router.get("/api/v1/episodes/{id}/messages", response_model=list[EpisodeMessageDetail])
async def get_episode_messages(id: str, session: AsyncSession = Depends(get_session)):
    """Fetches all raw chat messages belonging to a specific episode with sender details and media attachments."""
    ep = (await session.execute(select(Episode).where(Episode.id == id))).scalar_one_or_none()
    if not ep:
        raise HTTPException(status_code=404, detail="Episode not found")

    conv = (await session.execute(select(Conversation).where(Conversation.id == ep.conversation_id))).scalar_one_or_none()
    conv_name = conv.display_name if conv else "未知群聊"

    # Query messages linked to this episode
    stmt = (
        select(Message, Participant, EpisodeMessage.sequence)
        .join(EpisodeMessage, EpisodeMessage.message_id == Message.id)
        .outerjoin(Participant, Message.participant_id == Participant.id)
        .where(EpisodeMessage.episode_id == id)
        .order_by(Message.sent_at.asc(), EpisodeMessage.sequence.asc())
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []

    msg_ids = [m.id for m, _, _ in rows]

    # Query attached media links
    media_stmt = (
        select(MessageMediaLink, MediaAsset)
        .join(MediaAsset, MessageMediaLink.media_id == MediaAsset.id)
        .where(MessageMediaLink.message_id.in_(msg_ids))
        .order_by(MessageMediaLink.media_order.asc())
    )
    media_rows = (await session.execute(media_stmt)).all()

    media_map: dict[str, list[MediaAttachmentItem]] = {}
    for link, asset in media_rows:
        if link.message_id not in media_map:
            media_map[link.message_id] = []
        media_map[link.message_id].append(
            MediaAttachmentItem(
                id=asset.id,
                kind=asset.kind,
                mime_type=asset.mime_type,
                size_bytes=asset.size_bytes or 0,
                url=f"/api/v1/media/{asset.id}/content",
                thumbnail_url=f"/api/v1/media/{asset.id}/thumbnail",
            )
        )

    results: list[EpisodeMessageDetail] = []
    for msg, participant, _ in rows:
        display_label = participant.display_label if participant else "用户"
        role = participant.role if participant else "user"
        results.append(
            EpisodeMessageDetail(
                id=msg.id,
                conversation_id=msg.conversation_id,
                conversation_name=conv_name,
                participant_id=msg.participant_id,
                sender_label=display_label,
                sender_role=role,
                sent_at=msg.sent_at.isoformat(),
                raw_text=msg.raw_text,
                quote_text=msg.quote_unresolved_text,
                attachments=media_map.get(msg.id, []),
            )
        )

    return results


@router.get("/api/v1/episodes/{id}/detail", response_model=EpisodeDetailResponse)
async def get_episode_detail(id: str, session: AsyncSession = Depends(get_session)):
    """Fetches episode metadata and its corresponding raw messages."""
    ep_row = (
        await session.execute(
            select(Episode, Conversation)
            .join(Conversation, Episode.conversation_id == Conversation.id)
            .where(Episode.id == id)
        )
    ).first()
    if not ep_row:
        raise HTTPException(status_code=404, detail="Episode not found")

    ep, conv = ep_row
    ep_resp = EpisodeResponse(
        id=ep.id,
        conversation_id=ep.conversation_id,
        conversation_name=conv.display_name,
        title=ep.title,
        summary=ep.summary,
        category_hint=ep.category_hint,
        started_at=ep.started_at.isoformat(),
        ended_at=ep.ended_at.isoformat(),
        message_count=ep.message_count,
        participants=ep.participants_json or [],
        media_ids=ep.media_json or [],
        state=ep.state,
    )

    messages = await get_episode_messages(id, session)
    return EpisodeDetailResponse(episode=ep_resp, messages=messages)


@router.post("/api/v1/conversations/{id}/segment", response_model=list[EpisodeResponse])
async def segment_conversation_episodes(
    id: str,
    payload: SegmentRequest = SegmentRequest(),
    session: AsyncSession = Depends(get_session),
):
    segmenter = EpisodeSegmenter(session)
    try:
        episodes = await segmenter.segment_conversation(
            conversation_id=id,
            time_gap_minutes=payload.time_gap_minutes,
            force_mock=payload.force_mock,
        )
        await session.commit()
        return [
            EpisodeResponse(
                id=ep.id,
                conversation_id=ep.conversation_id,
                title=ep.title,
                summary=ep.summary,
                category_hint=ep.category_hint,
                started_at=ep.started_at.isoformat(),
                ended_at=ep.ended_at.isoformat(),
                message_count=ep.message_count,
                participants=ep.participants_json or [],
                media_ids=ep.media_json or [],
                state=ep.state,
            )
            for ep in episodes
        ]
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.get("/api/v1/conversations/{id}/episodes", response_model=list[EpisodeResponse])
async def list_conversation_episodes(id: str, session: AsyncSession = Depends(get_session)):
    repo = RepositoryRegistry(session)
    episodes = await repo.get_episodes_by_conversation(id)
    return [
        EpisodeResponse(
            id=ep.id,
            conversation_id=ep.conversation_id,
            title=ep.title,
            summary=ep.summary,
            category_hint=ep.category_hint,
            started_at=ep.started_at.isoformat(),
            ended_at=ep.ended_at.isoformat(),
            message_count=ep.message_count,
            participants=ep.participants_json or [],
            media_ids=ep.media_json or [],
            state=ep.state,
        )
        for ep in episodes
    ]


@router.get("/api/v1/episodes/{id}/context-packet", response_model=EpisodeContextPacket)
async def get_episode_context_packet(id: str, session: AsyncSession = Depends(get_session)):
    segmenter = EpisodeSegmenter(session)
    try:
        return await segmenter.build_context_packet(id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Episode not found")

