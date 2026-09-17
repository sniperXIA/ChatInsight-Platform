from typing import Any, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from packages.persistence.db import get_session
from packages.search.contracts import (
    HybridSearchResponse,
    ResearchAssistantResponse,
    SearchFilter,
)
from packages.search.hybrid_search import HybridSearchEngine
from packages.search.research_assistant import ResearchAssistant

router = APIRouter(tags=["Hybrid Search & Research Assistant"])


class SearchRequest(BaseModel):
    query: str
    modules: list[str] = []
    insight_types: list[str] = []
    severities: list[str] = []
    statuses: list[str] = []
    limit: int = 20


class AskAssistantRequest(BaseModel):
    question: str
    modules: list[str] = []
    force_mock: bool = False
    force_refresh: bool = False
    model: Optional[str] = None


class HistoryItemSummary(BaseModel):
    id: str
    question: str
    executive_summary: Optional[str] = None
    requirements_count: int = 0
    citations_count: int = 0
    confidence: float = 0.95
    model_used: Optional[str] = None
    duration_ms: Optional[float] = None
    created_at: str


class AssistantSettingsPayload(BaseModel):
    history_retention_days: int = 30
    history_max_records: int = 100


@router.post("/api/v1/search/hybrid", response_model=HybridSearchResponse)
async def hybrid_search(payload: SearchRequest, session: AsyncSession = Depends(get_session)):
    engine = HybridSearchEngine(session)
    filters = SearchFilter(
        modules=payload.modules,
        insight_types=payload.insight_types,
        severities=payload.severities,
        statuses=payload.statuses,
    )
    return await engine.search(query=payload.query, filters=filters, limit=payload.limit)


@router.post("/api/v1/assistant/ask", response_model=ResearchAssistantResponse)
async def ask_research_assistant(payload: AskAssistantRequest, session: AsyncSession = Depends(get_session)):
    assistant = ResearchAssistant(session)
    filters = SearchFilter(modules=payload.modules)
    return await assistant.answer_question(
        question=payload.question,
        filters=filters,
        force_mock=payload.force_mock,
        force_refresh=payload.force_refresh,
        model_override=payload.model,
    )


@router.get("/api/v1/assistant/history", response_model=list[HistoryItemSummary])
async def list_assistant_history(
    search: Optional[str] = Query(None, description="搜索问题关键字"),
    limit: int = Query(50, description="返回条数上限"),
    days: Optional[int] = Query(None, description="限制天数"),
    session: AsyncSession = Depends(get_session),
):
    from packages.search.history_service import ResearchHistoryService
    service = ResearchHistoryService(session)
    records = await service.list_history(search=search, limit=limit, days=days)
    return [
        HistoryItemSummary(
            id=r.id,
            question=r.question,
            executive_summary=r.executive_summary,
            requirements_count=len(r.requirements_json or []),
            citations_count=len(r.citations_json or []),
            confidence=r.confidence,
            model_used=r.model_used,
            duration_ms=r.duration_ms,
            created_at=r.created_at.isoformat(),
        )
        for r in records
    ]


@router.get("/api/v1/assistant/history/{record_id}", response_model=ResearchAssistantResponse)
async def get_assistant_history_detail(record_id: str, session: AsyncSession = Depends(get_session)):
    from packages.search.contracts import Citation, RequirementItem
    from packages.search.history_service import ResearchHistoryService
    service = ResearchHistoryService(session)
    rec = await service.get_record(record_id)
    if not rec:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="历史记录不存在")

    reqs = [RequirementItem.model_validate(r) for r in (rec.requirements_json or [])]
    cites = [Citation.model_validate(c) for c in (rec.citations_json or [])]

    return ResearchAssistantResponse(
        question=rec.question,
        executive_summary=rec.executive_summary,
        requirements=reqs,
        answer=rec.answer_text,
        citations=cites,
        key_findings=rec.key_findings_json or [],
        related_topic_ids=rec.related_topic_ids_json or [],
        related_insight_ids=rec.related_insight_ids_json or [],
        confidence=rec.confidence,
        from_history=True,
        history_id=rec.id,
        created_at=rec.created_at.isoformat(),
        duration_ms=rec.duration_ms,
        model_used=rec.model_used,
    )


@router.delete("/api/v1/assistant/history/{record_id}")
async def delete_assistant_history_item(record_id: str, session: AsyncSession = Depends(get_session)):
    from packages.search.history_service import ResearchHistoryService
    service = ResearchHistoryService(session)
    deleted = await service.delete_record(record_id)
    await session.commit()
    return {"success": deleted, "id": record_id}


@router.post("/api/v1/assistant/history/clear")
async def clear_all_assistant_history(session: AsyncSession = Depends(get_session)):
    from packages.search.history_service import ResearchHistoryService
    service = ResearchHistoryService(session)
    count = await service.clear_all_history()
    await session.commit()
    return {"success": True, "cleared_count": count}


@router.get("/api/v1/assistant/settings")
async def get_assistant_settings():
    from packages.model_gateway.settings_manager import SettingsManager
    cfg = SettingsManager.get_settings(reload=True)
    return {
        "history_retention_days": getattr(cfg, "history_retention_days", 30),
        "history_max_records": getattr(cfg, "history_max_records", 100),
    }


@router.post("/api/v1/assistant/settings")
async def update_assistant_settings(payload: AssistantSettingsPayload):
    from packages.model_gateway.settings_manager import SettingsManager
    cfg = SettingsManager.get_settings(reload=True)
    updated = cfg.model_copy(
        update={
            "history_retention_days": payload.history_retention_days,
            "history_max_records": payload.history_max_records,
        }
    )
    SettingsManager.save_settings(updated)
    return {
        "success": True,
        "history_retention_days": payload.history_retention_days,
        "history_max_records": payload.history_max_records,
    }
