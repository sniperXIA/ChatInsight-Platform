from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from packages.feishu_bitable.contracts import (
    BatchPushResponse,
    BitablePushResponse,
    BitablePushStats,
    FeishuBitableConfig,
    PushedInsightItem,
)
from packages.feishu_bitable.feishu_bitable_service import FeishuBitableService
from packages.persistence.db import get_session

router = APIRouter(prefix="/api/v1/bitable", tags=["Feishu Bitable Integration"])


class TestWebhookRequest(BaseModel):
    webhook_url: str = Field(description="待测试的飞书多维表格 Webhook 地址")
    enable_token_auth: bool = False
    bearer_token: Optional[str] = Field(default=None, description="凭证校验 Bearer Token")


class TestWebhookResponse(BaseModel):
    success: bool
    status_code: int
    message: str


class PushInsightRequest(BaseModel):
    operator: str = "human_operator"
    force_mock: bool = False


class BatchPushRequest(BaseModel):
    insight_ids: list[str] = Field(default_factory=list)
    operator: str = "human_operator"
    force_mock: bool = False


class PushedInsightsListResponse(BaseModel):
    total: int
    items: list[PushedInsightItem]


@router.get("/config", response_model=FeishuBitableConfig)
async def get_bitable_config(session: AsyncSession = Depends(get_session)):
    service = FeishuBitableService(session)
    return service.load_config()


@router.post("/config", response_model=FeishuBitableConfig)
async def save_bitable_config(
    config: FeishuBitableConfig,
    session: AsyncSession = Depends(get_session),
):
    service = FeishuBitableService(session)
    service.save_config(config)
    return service.load_config()


@router.post("/test", response_model=TestWebhookResponse)
async def test_bitable_webhook(
    payload: TestWebhookRequest,
    session: AsyncSession = Depends(get_session),
):
    service = FeishuBitableService(session)
    token = payload.bearer_token if (payload.enable_token_auth or payload.bearer_token) else None
    success, code, msg = await service.test_webhook(payload.webhook_url, bearer_token=token)
    return TestWebhookResponse(success=success, status_code=code, message=msg)


@router.post("/insights/{id}/push", response_model=BitablePushResponse)
async def push_single_insight(
    id: str,
    payload: PushInsightRequest = PushInsightRequest(),
    session: AsyncSession = Depends(get_session),
):
    service = FeishuBitableService(session)
    res = await service.push_single_insight(
        insight_id=id,
        operator=payload.operator,
        force_mock=payload.force_mock,
    )
    if not res.success and res.status_code == 404:
        raise HTTPException(status_code=404, detail=res.error)
    elif not res.success and res.status_code == 400:
        raise HTTPException(status_code=400, detail=res.error)
    return res


@router.post("/push-batch", response_model=BatchPushResponse)
async def push_batch_insights(
    payload: BatchPushRequest,
    session: AsyncSession = Depends(get_session),
):
    service = FeishuBitableService(session)
    res = await service.push_batch_insights(
        insight_ids=payload.insight_ids,
        operator=payload.operator,
        force_mock=payload.force_mock,
    )
    if not res.success and res.total == 0:
        raise HTTPException(
            status_code=400,
            detail="飞书多维表格 Webhook 地址未配置，请先在右上角【飞书多维表格 Webhook 设置】中配置有效地址",
        )
    return res


class PurgeResponse(BaseModel):
    success: bool
    purged_count: int
    message: str


@router.post("/purge", response_model=PurgeResponse)
async def purge_bitable_records(session: AsyncSession = Depends(get_session)):
    """清空所有飞书多维表格推送记录，将所有洞察重置为待推送初始状态以供纯净测试。"""
    service = FeishuBitableService(session)
    count = await service.purge_push_records()
    return PurgeResponse(
        success=True,
        purged_count=count,
        message=f"已成功清除 {count} 条飞书多维表格推送记录！所有洞察已恢复为待推送初始状态。",
    )


@router.get("/insights", response_model=PushedInsightsListResponse)
async def list_pushed_insights(
    date_preset: Optional[str] = Query(default=None, description="all | today | 7d | 30d | custom"),
    time_range: Optional[str] = Query(default=None, description="别名兼容"),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None, description="all | success | failed | pending | not_pushed"),
    module: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    service = FeishuBitableService(session)
    effective_preset = time_range or date_preset or "all"
    items, total = await service.get_pushed_insights_list(
        date_preset=effective_preset,
        start_date=start_date,
        end_date=end_date,
        status_filter=status,
        module_filter=module,
        search_query=search,
        limit=limit,
        offset=offset,
    )
    return PushedInsightsListResponse(total=total, items=items)


@router.get("/stats", response_model=BitablePushStats)
async def get_bitable_push_stats(
    date_preset: Optional[str] = Query(default=None),
    time_range: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    service = FeishuBitableService(session)
    effective_preset = time_range or date_preset or "all"
    return await service.get_push_stats(
        date_preset=effective_preset,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/recent", response_model=list[PushedInsightItem])
async def get_recent_pushed_insights(
    date_preset: Optional[str] = Query(default=None, description="all | today | 7d | 30d | custom"),
    time_range: Optional[str] = Query(default=None, description="别名兼容"),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    limit: int = Query(default=8, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
):
    service = FeishuBitableService(session)
    effective_preset = time_range or date_preset or "all"
    return await service.get_recent_pushed_insights(
        date_preset=effective_preset,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )

