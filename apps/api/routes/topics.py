from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.clustering.cluster_manager import ClusterManager
from packages.clustering.contracts import TopicView
from packages.persistence.db import get_session
from packages.persistence.models import Insight, Topic, TopicInsightLink
from packages.persistence.repositories.registry import RepositoryRegistry

router = APIRouter(tags=["Topics & Clustering"])


class ClusterInsightRequest(BaseModel):
    force_mock: bool = False
    model: Optional[str] = None


class MergeTopicsRequest(BaseModel):
    source_topic_id: str
    target_topic_id: str


class ClusterResultResponse(BaseModel):
    topic: TopicView
    decision: str
    rationale: Optional[str] = None


class TopicDetailResponse(BaseModel):
    id: str
    title: str
    summary: str
    module: str
    sub_module: Optional[str]
    severity: str
    status: str
    feedback_count: int
    unique_users_count: int
    first_seen_at: str
    last_seen_at: str
    tags: list[str]
    abc_sync_state: str
    abc_external_id: Optional[str]
    linked_insights: list[dict[str, Any]]


@router.post("/api/v1/insights/{id}/cluster", response_model=ClusterResultResponse)
async def cluster_single_insight(
    id: str,
    payload: ClusterInsightRequest = ClusterInsightRequest(),
    session: AsyncSession = Depends(get_session),
):
    manager = ClusterManager(session)
    try:
        topic, decision, judge_res = await manager.cluster_insight(
            insight_id=id,
            force_mock=payload.force_mock,
            model_override=payload.model,
        )
        await session.commit()

        return ClusterResultResponse(
            topic=TopicView(
                id=topic.id,
                title=topic.title,
                summary=topic.summary,
                module=topic.module,
                sub_module=topic.sub_module,
                severity=topic.severity,
                status=topic.status,
                feedback_count=topic.feedback_count,
                unique_users_count=topic.unique_users_count,
                first_seen_at=topic.first_seen_at.isoformat(),
                last_seen_at=topic.last_seen_at.isoformat(),
                tags=topic.tags_json or [],
                abc_sync_state=topic.abc_sync_state,
                abc_external_id=topic.abc_external_id,
            ),
            decision=decision.value,
            rationale=judge_res.rationale if judge_res else "无历史重合主题，自动创建新主题",
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Insight not found")


@router.get("/api/v1/topics", response_model=list[TopicView])
async def list_topics(
    module: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    abc_sync_state: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    repo = RepositoryRegistry(session)
    topics = await repo.get_topics(
        module=module,
        status=status,
        abc_sync_state=abc_sync_state,
        limit=limit,
        offset=offset,
    )
    return [
        TopicView(
            id=t.id,
            title=t.title,
            summary=t.summary,
            module=t.module,
            sub_module=t.sub_module,
            severity=t.severity,
            status=t.status,
            feedback_count=t.feedback_count,
            unique_users_count=t.unique_users_count,
            first_seen_at=t.first_seen_at.isoformat(),
            last_seen_at=t.last_seen_at.isoformat(),
            tags=t.tags_json or [],
            abc_sync_state=t.abc_sync_state,
            abc_external_id=t.abc_external_id,
        )
        for t in topics
    ]


@router.get("/api/v1/topics/{id}", response_model=TopicDetailResponse)
async def get_topic_detail(id: str, session: AsyncSession = Depends(get_session)):
    repo = RepositoryRegistry(session)
    topic = await repo.get_topic_by_id(id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    insights = await repo.get_insights_for_topic(id)
    return TopicDetailResponse(
        id=topic.id,
        title=topic.title,
        summary=topic.summary,
        module=topic.module,
        sub_module=topic.sub_module,
        severity=topic.severity,
        status=topic.status,
        feedback_count=topic.feedback_count,
        unique_users_count=topic.unique_users_count,
        first_seen_at=topic.first_seen_at.isoformat(),
        last_seen_at=topic.last_seen_at.isoformat(),
        tags=topic.tags_json or [],
        abc_sync_state=topic.abc_sync_state,
        abc_external_id=topic.abc_external_id,
        linked_insights=[
            {
                "id": ins.id,
                "summary": ins.summary,
                "module": ins.module,
                "severity": ins.severity,
                "status_in_chat": ins.status_in_chat,
                "created_at": ins.created_at.isoformat(),
            }
            for ins in insights
        ],
    )


@router.post("/api/v1/topics/merge", response_model=TopicView)
async def merge_topics_endpoint(
    payload: MergeTopicsRequest,
    session: AsyncSession = Depends(get_session),
):
    repo = RepositoryRegistry(session)
    merged = await repo.merge_topics(payload.source_topic_id, payload.target_topic_id)
    if not merged:
        raise HTTPException(status_code=404, detail="One or both topics not found")

    await session.commit()
    return TopicView(
        id=merged.id,
        title=merged.title,
        summary=merged.summary,
        module=merged.module,
        sub_module=merged.sub_module,
        severity=merged.severity,
        status=merged.status,
        feedback_count=merged.feedback_count,
        unique_users_count=merged.unique_users_count,
        first_seen_at=merged.first_seen_at.isoformat(),
        last_seen_at=merged.last_seen_at.isoformat(),
        tags=merged.tags_json or [],
        abc_sync_state=merged.abc_sync_state,
        abc_external_id=merged.abc_external_id,
    )
