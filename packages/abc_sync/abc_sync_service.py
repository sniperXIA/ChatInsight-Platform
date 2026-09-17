from datetime import datetime
from typing import Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.abc_sync.abc_client import ABCSyncClient
from packages.abc_sync.contracts import ABCSyncResult
from packages.domain.models import generate_id
from packages.persistence.models import Topic
from packages.persistence.repositories.registry import RepositoryRegistry


class ABCSyncService:
    """Service orchestrating synchronization of Topics to the ABC User Feedback system."""

    def __init__(self, session: AsyncSession, client: ABCSyncClient | None = None):
        self.session = session
        self.client = client or ABCSyncClient()
        self.repo = RepositoryRegistry(session)

    async def sync_topic(self, topic_id: str) -> tuple[bool, Optional[str]]:
        topic = await self.repo.get_topic_by_id(topic_id)
        if not topic:
            return False, f"Topic {topic_id} not found"

        insights = await self.repo.get_insights_for_topic(topic_id)
        payload = self.client.format_topic_payload(topic, insights)

        success, abc_id, err = await self.client.push_payload(payload)
        if success:
            topic.abc_sync_state = "synced"
            topic.abc_external_id = abc_id
            await self.repo.record_outbox_event(
                event_type="abc.feedback.synced",
                aggregate_type="topic",
                aggregate_id=topic.id,
                payload=payload.model_dump(),
                idempotency_key=f"abc_sync_{topic.id}_{topic.feedback_count}",
            )
            await self.session.flush()
            return True, None
        else:
            topic.abc_sync_state = "sync_failed"
            await self.session.flush()
            return False, err

    async def sync_all_pending_topics(self) -> ABCSyncResult:
        batch_id = generate_id()
        stmt = select(Topic).where(Topic.abc_sync_state.in_(["not_synced", "sync_failed"]))
        res = await self.session.execute(stmt)
        topics = res.scalars().all()

        synced_count = 0
        topic_ids = []
        errors = []

        for topic in topics:
            success, err = await self.sync_topic(topic.id)
            if success:
                synced_count += 1
                topic_ids.append(topic.id)
            else:
                errors.append(f"Topic {topic.id}: {err}")

        return ABCSyncResult(
            success=len(errors) == 0,
            synced_count=synced_count,
            batch_id=batch_id,
            topic_ids=topic_ids,
            errors=errors,
            timestamp=datetime.now().isoformat(),
        )
