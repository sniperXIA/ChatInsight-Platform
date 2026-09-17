from datetime import datetime
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.abc_sync.abc_sync_service import ABCSyncService
from packages.persistence.db import Base
from packages.persistence.models import DomainEventOutbox, Topic
from packages.persistence.repositories.registry import RepositoryRegistry


@pytest.mark.asyncio
async def test_abc_sync_service_and_outbox_event():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        # Create un-synced Topic
        topic = await repo.create_topic(
            workspace_id=ws.id,
            title="扩展音色卡加载无声音",
            summary="用户反馈音色卡插入后无声音",
            module="音色/扩展卡",
            severity="major",
            tags=["LiberLive C2", "音色卡"],
        )
        await session.commit()

        # Run ABC Sync
        sync_service = ABCSyncService(session)
        success, err = await sync_service.sync_topic(topic.id)
        assert success is True
        assert err is None
        await session.commit()

        # Verify Topic State updated
        synced_topic = await repo.get_topic_by_id(topic.id)
        assert synced_topic.abc_sync_state == "synced"
        assert synced_topic.abc_external_id.startswith("abc_")

        # Verify DomainEventOutbox recorded
        outbox_res = await session.execute(
            select(DomainEventOutbox).where(DomainEventOutbox.aggregate_id == topic.id)
        )
        event = outbox_res.scalar_one_or_none()
        assert event is not None
        assert event.event_type == "abc.feedback.synced"
        assert event.payload_json["title"] == "扩展音色卡加载无声音"
