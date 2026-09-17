from typing import Any, Optional
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from packages.analytics.contracts import (
    FeishuConfig,
    FeishuPushResponse,
    OperationalOverview,
    VoCReportOutput,
)
from packages.analytics.feishu_service import FeishuService
from packages.analytics.report_generator import ReportGenerator
from packages.persistence.db import get_session

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics & Reporting"])
feishu_service = FeishuService()


class GenerateReportRequest(BaseModel):
    period: str = "7d"
    period_label: Optional[str] = None
    include_ai_summary: bool = False
    force_mock: bool = False


class PushReportToFeishuRequest(BaseModel):
    period: str = "7d"
    custom_note: Optional[str] = None
    include_ai_summary: bool = False


class FeishuTestRequest(BaseModel):
    webhook_url: Optional[str] = None
    secret: Optional[str] = None


@router.get("/overview", response_model=OperationalOverview)
async def get_overview_stats(
    period: str = Query(default="7d", description="统计周期: today | 7d | 30d | all"),
    session: AsyncSession = Depends(get_session),
):
    """
    Returns real-time platform operational metrics with zero LLM token cost.
    Includes message volume, daily averages, topics, insights, research queries,
    token usage, and daily trend time-series.
    """
    generator = ReportGenerator(session)
    return await generator.get_operational_overview(period=period)


@router.post("/reports/generate", response_model=VoCReportOutput)
async def generate_voc_report(
    payload: GenerateReportRequest = GenerateReportRequest(),
    session: AsyncSession = Depends(get_session),
):
    """
    Master VoC Business Report generation:
    Combines operational dashboard metrics with high-value content synthesis
    (category dynamics & alerts, clustered topics, critical actionable insights).
    """
    generator = ReportGenerator(session)
    return await generator.generate_report(
        period=payload.period,
        period_label=payload.period_label,
        include_ai_summary=payload.include_ai_summary,
        force_mock=payload.force_mock,
    )


@router.get("/reports/export-markdown")
async def export_voc_report_markdown(
    period: str = Query(default="7d", description="统计周期: today | 7d | 30d | all"),
    period_label: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Generates and exports the VoC Business Report as a formatted Markdown file."""
    generator = ReportGenerator(session)
    report = await generator.generate_report(
        period=period,
        period_label=period_label,
        include_ai_summary=False,
        force_mock=True,
    )
    md_content = generator.render_markdown(report)
    filename = f"voc_report_{period}.md"
    return Response(
        content=md_content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# -------------------------------------------------------------
# Feishu Robot Webhook Endpoints
# -------------------------------------------------------------

@router.get("/feishu/config", response_model=FeishuConfig)
async def get_feishu_config():
    """Fetches Feishu Bot configuration with secret masked for security."""
    cfg = feishu_service.load_config()
    # Mask secret if present
    masked_sec = f"***{cfg.secret[-4:]}" if cfg.secret and len(cfg.secret) > 4 else ("***" if cfg.secret else None)
    return FeishuConfig(
        webhook_url=cfg.webhook_url,
        secret=masked_sec,
        enabled=cfg.enabled,
        auto_scheduled=cfg.auto_scheduled,
        scheduled_cron=cfg.scheduled_cron,
        report_period=cfg.report_period,
        last_push_at=cfg.last_push_at,
        last_push_status=cfg.last_push_status,
    )


@router.post("/feishu/config", response_model=FeishuConfig)
async def update_feishu_config(payload: FeishuConfig):
    """Updates and saves Feishu Bot configuration."""
    feishu_service.save_config(payload)
    return await get_feishu_config()


@router.post("/feishu/test", response_model=FeishuPushResponse)
async def test_feishu_connection(payload: FeishuTestRequest = FeishuTestRequest()):
    """Sends a verification connectivity card to the configured Feishu Bot."""
    return await feishu_service.send_test_message(
        webhook_url=payload.webhook_url,
        secret=payload.secret,
    )


@router.post("/feishu/push", response_model=FeishuPushResponse)
async def push_voc_report_to_feishu(
    payload: PushReportToFeishuRequest = PushReportToFeishuRequest(),
    request: Request = None,
    session: AsyncSession = Depends(get_session),
):
    """
    Generates the VoC Business Report for the specified period and pushes
    it as an elegant Feishu Interactive Card directly to the Feishu group.
    """
    generator = ReportGenerator(session)
    report = await generator.generate_report(
        period=payload.period,
        include_ai_summary=payload.include_ai_summary,
        force_mock=False,
    )
    base_url = str(request.base_url).rstrip("/") if request else "http://localhost:8000"
    return await feishu_service.send_voc_interactive_card(
        report=report,
        custom_note=payload.custom_note,
        base_web_url=base_url,
    )
