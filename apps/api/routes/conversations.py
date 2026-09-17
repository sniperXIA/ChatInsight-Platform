from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.persistence.db import get_session
from packages.persistence.models import (
    Conversation,
    Episode,
    Insight,
    MediaAsset,
    Message,
    MessageMediaLink,
    Participant,
)

router = APIRouter(prefix="/api/v1/conversations", tags=["Conversations & Timeline"])


class ConversationListItem(BaseModel):
    id: str
    display_name: str
    source_type: str
    message_count: int
    participant_count: int = 0
    episode_count: int = 0
    insight_count: int = 0
    media_count: int = 0
    first_message_time: Optional[str] = None
    last_message_time: Optional[str] = None
    created_at: Optional[str] = None


class MediaLinkView(BaseModel):
    link_id: str
    media_id: str
    kind: str
    mime_type: Optional[str]
    relative_path: str
    confidence: float
    state: str
    method: str


class TimelineItem(BaseModel):
    message_id: str
    source_message_id: Optional[str]
    sequence: int
    sent_at: str
    sender_label: str
    sender_role: str
    raw_text: str
    quote_unresolved: Optional[str]
    quoted_message_id: Optional[str]
    media_links: list[MediaLinkView]


@router.get("", response_model=list[ConversationListItem])
async def list_conversations(session: AsyncSession = Depends(get_session)):
    stmt = (
        select(
            Conversation.id,
            Conversation.display_name,
            Conversation.source_type,
            Conversation.created_at,
            func.count(distinct(Message.id)).label("msg_count"),
            func.count(distinct(Message.participant_id)).label("user_count"),
            func.min(Message.sent_at).label("first_sent"),
            func.max(Message.sent_at).label("last_sent"),
        )
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .group_by(Conversation.id)
        .order_by(func.max(Message.sent_at).desc().nullslast())
    )
    res = await session.execute(stmt)
    rows = res.all()

    # Episode counts per conversation
    ep_stmt = select(Episode.conversation_id, func.count(Episode.id)).group_by(Episode.conversation_id)
    ep_map = dict((await session.execute(ep_stmt)).all())

    # Insight counts per conversation (via Episode)
    ins_stmt = (
        select(Episode.conversation_id, func.count(Insight.id))
        .join(Insight, Insight.episode_id == Episode.id)
        .group_by(Episode.conversation_id)
    )
    ins_map = dict((await session.execute(ins_stmt)).all())

    # Media assets count per conversation
    media_stmt = (
        select(Message.conversation_id, func.count(MessageMediaLink.id))
        .join(MessageMediaLink, MessageMediaLink.message_id == Message.id)
        .group_by(Message.conversation_id)
    )
    media_map = dict((await session.execute(media_stmt)).all())

    return [
        ConversationListItem(
            id=r[0],
            display_name=r[1],
            source_type=r[2],
            message_count=r[4] or 0,
            participant_count=r[5] or 0,
            episode_count=ep_map.get(r[0], 0),
            insight_count=ins_map.get(r[0], 0),
            media_count=media_map.get(r[0], 0),
            first_message_time=r[6].isoformat() if r[6] else None,
            last_message_time=r[7].isoformat() if r[7] else None,
            created_at=r[3].isoformat() if r[3] else None,
        )
        for r in rows
    ]


@router.get("/{id}/timeline", response_model=list[TimelineItem])
async def get_conversation_timeline(
    id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    # Verify conversation exists
    conv_stmt = select(Conversation).where(Conversation.id == id)
    conv_res = await session.execute(conv_stmt)
    conv = conv_res.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Fetch messages with participant info
    msg_stmt = (
        select(Message, Participant)
        .outerjoin(Participant, Message.participant_id == Participant.id)
        .where(Message.conversation_id == id)
        .order_by(Message.sent_at.asc(), Message.source_sequence.asc())
        .offset(offset)
        .limit(limit)
    )
    msg_res = await session.execute(msg_stmt)
    msg_rows = msg_res.all()

    msg_ids = [m[0].id for m in msg_rows]
    links_by_msg = {mid: [] for mid in msg_ids}

    if msg_ids:
        links_stmt = (
            select(MessageMediaLink, MediaAsset)
            .join(MediaAsset, MessageMediaLink.media_id == MediaAsset.id)
            .where(MessageMediaLink.message_id.in_(msg_ids))
            .order_by(MessageMediaLink.media_order.asc())
        )
        links_res = await session.execute(links_stmt)
        for link, media in links_res.all():
            links_by_msg[link.message_id].append(
                MediaLinkView(
                    link_id=link.id,
                    media_id=media.id,
                    kind=media.kind,
                    mime_type=media.mime_type,
                    relative_path=media.derived_preview_relpath or f"/api/v1/media/{media.id}/content",
                    confidence=link.confidence,
                    state=link.state,
                    method=link.method,
                )
            )

    timeline = []
    for msg, participant in msg_rows:
        timeline.append(
            TimelineItem(
                message_id=msg.id,
                source_message_id=msg.source_message_id,
                sequence=msg.source_sequence,
                sent_at=msg.sent_at.isoformat(),
                sender_label=participant.display_label if participant else "未知用户",
                sender_role=participant.role if participant else "user",
                raw_text=msg.raw_text,
                quote_unresolved=msg.quote_unresolved_text,
                quoted_message_id=msg.quoted_message_id,
                media_links=links_by_msg.get(msg.id, []),
            )
        )
    return timeline
