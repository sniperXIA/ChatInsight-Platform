from datetime import date, datetime, timedelta
from typing import Any, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.persistence.db import get_session
from packages.persistence.models import (
    Conversation,
    MediaAsset,
    Message,
    MessageMediaLink,
    Participant,
)

router = APIRouter(prefix="/api/v1/messages", tags=["Raw Messages Explorer"])


def parse_datetime_filter(dt_str: Optional[str], is_end: bool = False) -> Optional[datetime]:
    if not dt_str or not dt_str.strip():
        return None
    cleaned = dt_str.strip()
    try:
        return datetime.fromisoformat(cleaned)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(cleaned, fmt)
            if is_end and fmt in ("%Y-%m-%d", "%Y/%m/%d"):
                dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            return dt
        except Exception:
            continue
    return None


def parse_list_param(param: Optional[str]) -> list[str]:
    if not param:
        return []
    return [p.strip() for p in param.split(",") if p.strip()]


class MediaAttachmentItem(BaseModel):
    id: str
    kind: str
    mime_type: Optional[str]
    size_bytes: int
    url: str
    thumbnail_url: str


class MessageDetailItem(BaseModel):
    id: str
    conversation_id: str
    conversation_name: str
    participant_id: Optional[str]
    sender_label: str
    sender_role: str
    sent_at: str
    raw_text: str
    quote_text: Optional[str]
    attachments: list[MediaAttachmentItem] = []


class MessageListResponse(BaseModel):
    total: int
    messages: list[MessageDetailItem]


class ParticipantOption(BaseModel):
    id: str
    display_label: str
    role: str
    message_count: int


class HourlyDistributionItem(BaseModel):
    hour: int
    count: int
    label: str
    date: str
    datetime_label: str
    is_day_start: bool = False


class TopSenderItem(BaseModel):
    participant_id: Optional[str]
    display_label: str
    role: str
    message_count: int
    percentage: float


class RoleStatItem(BaseModel):
    role: str
    label: str
    count: int
    percentage: float


class MessageStatsResponse(BaseModel):
    total_messages: int
    total_participants: int
    total_images: int
    total_videos: int
    avg_messages_per_user: float
    peak_hour: Optional[int] = None
    peak_hour_label: Optional[str] = None
    days_count: int = 1
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    granularity: str = "hour"  # "hour" when <= 3 days, "day" when > 3 days
    top_senders: list[TopSenderItem] = []
    hourly_distribution: list[HourlyDistributionItem] = []
    role_distribution: list[RoleStatItem] = []


def _apply_message_filters(
    stmt,
    conversation_id: Optional[str] = None,
    senders: Optional[str] = None,
    participant_ids: Optional[str] = None,
    role: Optional[str] = None,
    query: Optional[str] = None,
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
):
    if conversation_id:
        stmt = stmt.where(Message.conversation_id == conversation_id)
    if role:
        stmt = stmt.where(Participant.role == role)
    if query:
        stmt = stmt.where(Message.searchable_text.contains(query.strip()))

    sender_list = parse_list_param(senders)
    part_id_list = parse_list_param(participant_ids)
    if sender_list:
        stmt = stmt.where(Participant.display_label.in_(sender_list))
    if part_id_list:
        stmt = stmt.where(Message.participant_id.in_(part_id_list))

    parsed_start = parse_datetime_filter(time_start, is_end=False)
    if parsed_start:
        stmt = stmt.where(Message.sent_at >= parsed_start)

    parsed_end = parse_datetime_filter(time_end, is_end=True)
    if parsed_end:
        stmt = stmt.where(Message.sent_at <= parsed_end)

    return stmt


@router.get("", response_model=MessageListResponse)
async def list_messages(
    conversation_id: Optional[str] = Query(default=None),
    senders: Optional[str] = Query(default=None, description="逗号分隔的发送者昵称列表"),
    participant_ids: Optional[str] = Query(default=None, description="逗号分隔的参与者ID列表"),
    role: Optional[str] = Query(default=None),
    query: Optional[str] = Query(default=None),
    time_start: Optional[str] = Query(default=None),
    time_end: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    # 1. Base Query
    base_stmt = (
        select(Message, Conversation, Participant)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .outerjoin(Participant, Message.participant_id == Participant.id)
    )
    base_stmt = _apply_message_filters(
        base_stmt,
        conversation_id=conversation_id,
        senders=senders,
        participant_ids=participant_ids,
        role=role,
        query=query,
        time_start=time_start,
        time_end=time_end,
    )

    # 2. Count Total
    count_stmt = (
        select(func.count(Message.id))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .outerjoin(Participant, Message.participant_id == Participant.id)
    )
    count_stmt = _apply_message_filters(
        count_stmt,
        conversation_id=conversation_id,
        senders=senders,
        participant_ids=participant_ids,
        role=role,
        query=query,
        time_start=time_start,
        time_end=time_end,
    )
    total_count = (await session.execute(count_stmt)).scalar() or 0

    # 3. Paged Rows
    stmt = base_stmt.order_by(desc(Message.sent_at)).offset(offset).limit(limit)
    res = await session.execute(stmt)
    rows = res.all()

    # 4. Collect Media Attachments
    msg_ids = [m.id for m, c, p in rows]
    media_map: dict[str, list[MediaAttachmentItem]] = {mid: [] for mid in msg_ids}

    if msg_ids:
        links_stmt = (
            select(MessageMediaLink.message_id, MediaAsset)
            .join(MediaAsset, MessageMediaLink.media_id == MediaAsset.id)
            .where(MessageMediaLink.message_id.in_(msg_ids))
            .order_by(MessageMediaLink.media_order.asc())
        )
        links_res = await session.execute(links_stmt)
        for mid, media in links_res.all():
            media_map[mid].append(
                MediaAttachmentItem(
                    id=media.id,
                    kind=media.kind,
                    mime_type=media.mime_type,
                    size_bytes=media.size_bytes,
                    url=f"/api/v1/media/{media.id}/content",
                    thumbnail_url=f"/api/v1/media/{media.id}/content",
                )
            )

    messages = []
    for msg, conv, part in rows:
        sender_label = part.display_label if part else "匿名群友"
        sender_role = part.role if part else "user"
        messages.append(
            MessageDetailItem(
                id=msg.id,
                conversation_id=conv.id,
                conversation_name=conv.display_name,
                participant_id=msg.participant_id,
                sender_label=sender_label,
                sender_role=sender_role,
                sent_at=msg.sent_at.isoformat(),
                raw_text=msg.raw_text,
                quote_text=msg.quote_unresolved_text,
                attachments=media_map.get(msg.id, []),
            )
        )

    return MessageListResponse(total=total_count, messages=messages)


@router.get("/participants", response_model=list[ParticipantOption])
async def list_message_participants(
    conversation_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Retrieve distinct message senders with message count, sorted by activity."""
    stmt = (
        select(
            Participant.id,
            Participant.display_label,
            Participant.role,
            func.count(Message.id).label("msg_count"),
        )
        .join(Message, Message.participant_id == Participant.id)
    )
    if conversation_id:
        stmt = stmt.where(Message.conversation_id == conversation_id)
    stmt = stmt.group_by(Participant.id, Participant.display_label, Participant.role).order_by(func.count(Message.id).desc())

    res = await session.execute(stmt)
    rows = res.all()
    return [
        ParticipantOption(
            id=r[0],
            display_label=r[1] or "未知用户",
            role=r[2] or "user",
            message_count=r[3],
        )
        for r in rows
    ]


@router.get("/stats", response_model=MessageStatsResponse)
async def get_message_stats(
    conversation_id: Optional[str] = Query(default=None),
    senders: Optional[str] = Query(default=None),
    participant_ids: Optional[str] = Query(default=None),
    role: Optional[str] = Query(default=None),
    query: Optional[str] = Query(default=None),
    time_start: Optional[str] = Query(default=None),
    time_end: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Compute aggregated chat activity statistics, top senders ranking, and 24-hour distribution."""
    # 1. Fetch matching messages
    stmt = (
        select(
            Message.id,
            Message.sent_at,
            Message.participant_id,
            Participant.display_label,
            Participant.role,
        )
        .join(Conversation, Message.conversation_id == Conversation.id)
        .outerjoin(Participant, Message.participant_id == Participant.id)
    )
    stmt = _apply_message_filters(
        stmt,
        conversation_id=conversation_id,
        senders=senders,
        participant_ids=participant_ids,
        role=role,
        query=query,
        time_start=time_start,
        time_end=time_end,
    )
    res = await session.execute(stmt)
    rows = res.all()

    total_messages = len(rows)
    parsed_start = parse_datetime_filter(time_start, is_end=False)
    parsed_end = parse_datetime_filter(time_end, is_end=True)

    if total_messages == 0:
        s_date = parsed_start.date() if parsed_start else None
        e_date = parsed_end.date() if parsed_end else None
        d_cnt = (e_date - s_date).days + 1 if (s_date and e_date and e_date >= s_date) else 0
        return MessageStatsResponse(
            total_messages=0,
            total_participants=0,
            total_images=0,
            total_videos=0,
            avg_messages_per_user=0.0,
            peak_hour=None,
            peak_hour_label=None,
            days_count=d_cnt,
            start_date=str(s_date) if s_date else None,
            end_date=str(e_date) if e_date else None,
            granularity="hour" if d_cnt <= 3 else "day",
            top_senders=[],
            hourly_distribution=[],
            role_distribution=[],
        )

    # 2. Count media items for these messages
    msg_ids = [r[0] for r in rows]
    total_images = 0
    total_videos = 0

    if msg_ids:
        media_stmt = (
            select(MediaAsset.kind, func.count(MediaAsset.id))
            .join(MessageMediaLink, MessageMediaLink.media_id == MediaAsset.id)
            .where(MessageMediaLink.message_id.in_(msg_ids))
            .group_by(MediaAsset.kind)
        )
        media_res = await session.execute(media_stmt)
        for m_kind, count in media_res.all():
            if m_kind == "image":
                total_images = count
            elif m_kind == "video":
                total_videos = count

    # 3. Top senders and role distribution
    senders_map: dict[str, dict[str, Any]] = {}
    role_counts: dict[str, int] = {"user": 0, "support": 0, "developer": 0, "bot": 0}
    slot_counts: dict[tuple[str, int], int] = {}
    valid_dts: list[datetime] = []

    for msg_id, sent_at, part_id, disp_label, r_role in rows:
        label = disp_label or "匿名群友"
        user_role = r_role or "user"

        # Sender map
        key = part_id or label
        if key not in senders_map:
            senders_map[key] = {
                "participant_id": part_id,
                "display_label": label,
                "role": user_role,
                "count": 0,
            }
        senders_map[key]["count"] += 1

        # Role count
        if user_role in role_counts:
            role_counts[user_role] += 1
        else:
            role_counts[user_role] = 1

        # Hourly and date slot counting
        if sent_at:
            valid_dts.append(sent_at)
            d_str = sent_at.strftime("%Y-%m-%d")
            h = sent_at.hour
            slot_counts[(d_str, h)] = slot_counts.get((d_str, h), 0) + 1

    total_participants = len(senders_map)
    avg_per_user = round(total_messages / max(1, total_participants), 1)

    # Top Senders Ranking (Decoupled from senders filter so user can multi-select from full leaderboard)
    if senders or participant_ids:
        top_stmt = (
            select(
                Message.participant_id,
                Participant.display_label,
                Participant.role,
                func.count(Message.id).label("cnt"),
            )
            .join(Conversation, Message.conversation_id == Conversation.id)
            .outerjoin(Participant, Message.participant_id == Participant.id)
        )
        top_stmt = _apply_message_filters(
            top_stmt,
            conversation_id=conversation_id,
            senders=None,
            participant_ids=None,
            role=role,
            query=query,
            time_start=time_start,
            time_end=time_end,
        )
        top_stmt = (
            top_stmt.group_by(Message.participant_id, Participant.display_label, Participant.role)
            .order_by(func.count(Message.id).desc())
            .limit(12)
        )
        top_rows = (await session.execute(top_stmt)).all()

        scope_total_stmt = (
            select(func.count(Message.id))
            .join(Conversation, Message.conversation_id == Conversation.id)
            .outerjoin(Participant, Message.participant_id == Participant.id)
        )
        scope_total_stmt = _apply_message_filters(
            scope_total_stmt,
            conversation_id=conversation_id,
            senders=None,
            participant_ids=None,
            role=role,
            query=query,
            time_start=time_start,
            time_end=time_end,
        )
        scope_total = (await session.execute(scope_total_stmt)).scalar() or total_messages

        top_senders = [
            TopSenderItem(
                participant_id=r[0],
                display_label=r[1] or "匿名群友",
                role=r[2] or "user",
                message_count=r[3],
                percentage=round(r[3] / max(1, scope_total) * 100, 1),
            )
            for r in top_rows
        ]
    else:
        sorted_senders = sorted(senders_map.values(), key=lambda x: x["count"], reverse=True)
        top_senders = [
            TopSenderItem(
                participant_id=s["participant_id"],
                display_label=s["display_label"],
                role=s["role"],
                message_count=s["count"],
                percentage=round(s["count"] / max(1, total_messages) * 100, 1),
            )
            for s in sorted_senders[:12]
        ]

    # Continuous Hourly Distribution across selected range
    if valid_dts:
        min_dt = min(valid_dts)
        max_dt = max(valid_dts)
    else:
        min_dt = datetime.now()
        max_dt = datetime.now()

    start_date = (parsed_start or min_dt).date()
    end_date = (parsed_end or max_dt).date()
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    days_count = (end_date - start_date).days + 1

    hourly_distribution: list[HourlyDistributionItem] = []
    if days_count <= 3:
        granularity = "hour"
        curr = start_date
        while curr <= end_date:
            d_str = curr.strftime("%Y-%m-%d")
            for h in range(24):
                cnt = slot_counts.get((d_str, h), 0)
                hourly_distribution.append(
                    HourlyDistributionItem(
                        hour=h,
                        count=cnt,
                        label=f"{h:02d}:00",
                        date=d_str,
                        datetime_label=f"{d_str} {h:02d}:00",
                        is_day_start=(h == 0),
                    )
                )
            curr += timedelta(days=1)

        if hourly_distribution:
            peak_item = max(hourly_distribution, key=lambda x: x.count)
            peak_hour = peak_item.hour
            next_h = (peak_item.hour + 1) % 24
            if days_count > 1:
                peak_hour_label = f"{peak_item.date} {peak_item.hour:02d}:00 ~ {next_h:02d}:00 ({peak_item.count} 条)"
            else:
                peak_hour_label = f"{peak_item.hour:02d}:00 ~ {next_h:02d}:00 ({peak_item.count} 条)"
        else:
            peak_hour = None
            peak_hour_label = None
    else:
        granularity = "day"
        curr = start_date
        while curr <= end_date:
            d_str = curr.strftime("%Y-%m-%d")
            day_cnt = sum(slot_counts.get((d_str, h), 0) for h in range(24))
            hourly_distribution.append(
                HourlyDistributionItem(
                    hour=0,
                    count=day_cnt,
                    label=curr.strftime("%m-%d"),
                    date=d_str,
                    datetime_label=d_str,
                    is_day_start=True,
                )
            )
            curr += timedelta(days=1)

        if hourly_distribution:
            peak_item = max(hourly_distribution, key=lambda x: x.count)
            peak_hour = None
            peak_hour_label = f"{peak_item.date} (当日共 {peak_item.count} 条)"
        else:
            peak_hour = None
            peak_hour_label = None

    # Role Distribution
    role_labels = {
        "user": "普通玩家 (User)",
        "support": "官方客服 (Support)",
        "developer": "研发人员 (Developer)",
        "bot": "系统机器人 (Bot)",
    }
    role_distribution = [
        RoleStatItem(
            role=r,
            label=role_labels.get(r, r),
            count=cnt,
            percentage=round(cnt / total_messages * 100, 1),
        )
        for r, cnt in role_counts.items()
        if cnt > 0
    ]

    return MessageStatsResponse(
        total_messages=total_messages,
        total_participants=total_participants,
        total_images=total_images,
        total_videos=total_videos,
        avg_messages_per_user=avg_per_user,
        peak_hour=peak_hour,
        peak_hour_label=peak_hour_label,
        days_count=days_count,
        start_date=str(start_date),
        end_date=str(end_date),
        granularity=granularity,
        top_senders=top_senders,
        hourly_distribution=hourly_distribution,
        role_distribution=role_distribution,
    )
