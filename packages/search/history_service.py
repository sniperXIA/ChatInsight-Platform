from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from sqlalchemy import delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models import generate_id
from packages.persistence.models import ResearchQueryRecord


class ResearchHistoryService:
    """
    Dedicated service for persisting, querying, and managing the lifecycle
    of AI Research Assistant question-and-answer generation records.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_record(
        self,
        workspace_id: str,
        question: str,
        answer_text: str,
        executive_summary: Optional[str] = None,
        requirements: Optional[list[dict[str, Any]]] = None,
        citations: Optional[list[dict[str, Any]]] = None,
        key_findings: Optional[list[str]] = None,
        related_topic_ids: Optional[list[str]] = None,
        related_insight_ids: Optional[list[str]] = None,
        confidence: float = 0.95,
        model_used: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        duration_ms: float = 0.0,
    ) -> ResearchQueryRecord:
        """Persists a new research query outcome."""
        # Ensure workspace exists to satisfy foreign key constraints
        from packages.persistence.models import Workspace
        ws = await self.session.get(Workspace, workspace_id)
        if not ws:
            ws = Workspace(id=workspace_id, name="Default Workspace")
            self.session.add(ws)
            await self.session.flush()

        record = ResearchQueryRecord(
            id=generate_id(),
            workspace_id=workspace_id,
            question=question.strip(),
            executive_summary=executive_summary,
            answer_text=answer_text,
            requirements_json=requirements or [],
            citations_json=citations or [],
            key_findings_json=key_findings or [],
            related_topic_ids_json=related_topic_ids or [],
            related_insight_ids_json=related_insight_ids or [],
            confidence=confidence,
            model_used=model_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            duration_ms=duration_ms,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def find_exact_cached(
        self,
        question: str,
        days: int = 30,
    ) -> Optional[ResearchQueryRecord]:
        """
        Finds the most recent valid generated response for an exact question match
        within the given retention days.
        """
        clean_q = question.strip()
        if not clean_q:
            return None

        if days is not None and days <= 0:
            return None

        now_naive = datetime.now()
        cutoff = now_naive - timedelta(days=days)
        stmt = (
            select(ResearchQueryRecord)
            .where(
                ResearchQueryRecord.question == clean_q,
                ResearchQueryRecord.created_at >= cutoff,
            )
            .order_by(desc(ResearchQueryRecord.created_at))
            .limit(1)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_history(
        self,
        search: Optional[str] = None,
        limit: int = 100,
        days: Optional[int] = None,
    ) -> list[ResearchQueryRecord]:
        """Lists recent research queries, optionally filtered by keyword and days."""
        stmt = select(ResearchQueryRecord)
        if days is not None and days > 0:
            now_naive = datetime.now()
            cutoff = now_naive - timedelta(days=days)
            stmt = stmt.where(ResearchQueryRecord.created_at >= cutoff)

        if search and search.strip():
            kw1 = search.strip()
            kw2 = f"%{kw1}%"
            stmt = stmt.where(
                or_(
                    ResearchQueryRecord.question.ilike(kw2),
                    ResearchQueryRecord.executive_summary.ilike(kw2),
                    ResearchQueryRecord.answer_text.ilike(kw2),
                )
            )

        stmt = stmt.order_by(desc(ResearchQueryRecord.created_at)).limit(max(limit, 1))
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_record(self, record_id: str) -> Optional[ResearchQueryRecord]:
        """Retrieves a single research record by ID."""
        stmt = select(ResearchQueryRecord).where(ResearchQueryRecord.id == record_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def delete_record(self, record_id: str) -> bool:
        """Deletes a single research record by ID."""
        stmt = delete(ResearchQueryRecord).where(ResearchQueryRecord.id == record_id)
        res = await self.session.execute(stmt)
        return (res.rowcount or 0) > 0

    async def clear_all_history(self) -> int:
        """Clears all research query records."""
        stmt = delete(ResearchQueryRecord)
        res = await self.session.execute(stmt)
        return res.rowcount or 0

    async def prune_expired(
        self,
        retention_days: int = 30,
        max_records: int = 100,
    ) -> int:
        """
        Prunes records older than retention_days (unless bookmarked)
        and trims total count to max_records.
        Returns total number of pruned records.
        """
        pruned_count = 0
        now_naive = datetime.now()

        # 1. Prune expired records by days
        if retention_days > 0:
            cutoff = now_naive - timedelta(days=retention_days)
            del_expired = delete(ResearchQueryRecord).where(
                ResearchQueryRecord.created_at < cutoff,
                ResearchQueryRecord.is_bookmarked.is_(False),
            )
            res_expired = await self.session.execute(del_expired)
            pruned_count += res_expired.rowcount or 0

        # 2. Prune records exceeding max_records
        if max_records > 0:
            count_stmt = select(func.count()).select_from(ResearchQueryRecord)
            total_res = await self.session.execute(count_stmt)
            total_count = total_res.scalar() or 0

            if total_count > max_records:
                excess = total_count - max_records
                oldest_ids_stmt = (
                    select(ResearchQueryRecord.id)
                    .where(ResearchQueryRecord.is_bookmarked.is_(False))
                    .order_by(ResearchQueryRecord.created_at.asc())
                    .limit(excess)
                )
                ids_res = await self.session.execute(oldest_ids_stmt)
                ids_to_del = ids_res.scalars().all()

                if ids_to_del:
                    del_excess = delete(ResearchQueryRecord).where(
                        ResearchQueryRecord.id.in_(ids_to_del)
                    )
                    res_excess = await self.session.execute(del_excess)
                    pruned_count += res_excess.rowcount or 0

        return pruned_count
