from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.abc_sync.abc_sync_service import ABCSyncService
from packages.abc_sync.contracts import ABCSyncResult
from packages.persistence.db import get_session
from packages.persistence.models import DomainEventOutbox, Topic

router = APIRouter(prefix="/api/v1/abc", tags=["ABC User Feedback Integration"])


class SyncTopicResponse(BaseModel):
    success: bool
    topic_id: str
    abc_external_id: Optional[str]
    error: Optional[str] = None


class OutboxEventView(BaseModel):
    id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    state: str
    created_at: str


@router.post("/topics/{id}/sync", response_model=SyncTopicResponse)
async def sync_single_topic_to_abc(id: str, session: AsyncSession = Depends(get_session)):
    service = ABCSyncService(session)
    success, err = await service.sync_topic(id)
    if not success:
        raise HTTPException(status_code=400, detail=f"Sync failed: {err}")

    await session.commit()
    topic = await session.get(Topic, id)
    return SyncTopicResponse(
        success=True,
        topic_id=id,
        abc_external_id=topic.abc_external_id if topic else None,
    )


@router.post("/sync-batch", response_model=ABCSyncResult)
async def sync_all_pending_topics(session: AsyncSession = Depends(get_session)):
    service = ABCSyncService(session)
    result = await service.sync_all_pending_topics()
    await session.commit()
    return result


@router.get("/events", response_model=list[OutboxEventView])
async def list_abc_outbox_events(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(DomainEventOutbox)
        .where(DomainEventOutbox.event_type.like("abc.%"))
        .order_by(desc(DomainEventOutbox.created_at))
        .limit(limit)
    )
    res = await session.execute(stmt)
    events = res.scalars().all()
    return [
        OutboxEventView(
            id=e.id,
            event_type=e.event_type,
            aggregate_type=e.aggregate_type,
            aggregate_id=e.aggregate_id,
            payload=e.payload_json or {},
            state=e.state,
            created_at=e.created_at.isoformat(),
        )
        for e in events
    ]
