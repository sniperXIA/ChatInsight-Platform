import asyncio
import time
from datetime import datetime
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.routes.pipeline import (
    PipelineStepSummary,
    _run_abc_sync,
    _run_episode_segmentation,
    _run_insight_extraction,
    _run_multimodal_enrichment,
    _run_scan_and_import,
    _run_topic_clustering,
    resolve_scope_time_bounds,
)
from packages.analytics.report_generator import ReportGenerator
from packages.importers.chat_settings import ChatSettingsManager
from packages.model_gateway.settings_manager import SettingsManager
from packages.persistence.db import get_session
from packages.tasks.task_manager import ActiveTaskState, TaskManager

router = APIRouter(prefix="/api/v1/tasks", tags=["Task Center & Background Tasks"])


class LaunchTaskRequest(BaseModel):
    task_type: str = Field(default="full_pipeline", description="full_pipeline | scan_import | multimodal | segmentation | insights | clustering | abc_sync")
    title: Optional[str] = Field(default=None, description="自定义任务标题")
    source_path: str = Field(default_factory=lambda: ChatSettingsManager.load_settings().source_path, description="聊天记录根目录")
    force_mock: bool = Field(default=False, description="是否使用离线模式")
    import_all: bool = Field(default=True, description="是否全量扫描导入")
    limit_batches: Optional[int] = Field(default=None, description="导入批次上限")
    
    # Decoupled Analysis Scope
    conversation_ids: list[str] = Field(default_factory=list, description="目标群聊 ID 列表 (空为全部)")
    date_preset: str = Field(default="all", description="all | today | yesterday | last_7_days | last_30_days | custom")
    start_date: Optional[str] = Field(default=None, description="自定义起始日期")
    end_date: Optional[str] = Field(default=None, description="自定义结束日期")
    overwrite_existing: bool = Field(default=True, description="是否覆写该范围的历史分析结果")
    limit_episodes: Optional[int] = Field(default=None, description="提炼 Episode 数量上限")
    clean_previous_insights: bool = Field(default=False, description="是否清除全量历史测试洞察")


@router.post("/run")
async def launch_task(payload: LaunchTaskRequest):
    """
    Submit a task to the background Task Center with scoped analysis and daily overwrite.
    """
    tm = TaskManager.get_instance()
    st_time, et_time, scope_label = resolve_scope_time_bounds(
        date_preset=payload.date_preset,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )

    task_title = payload.title or ("全链路自动化分析流水线" if payload.task_type == "full_pipeline" else f"单步执行: {payload.task_type}")
    if scope_label != "全部历史时间":
        task_title += f" [{scope_label}]"

    async def full_pipeline_runner(state: ActiveTaskState, session_maker: async_sessionmaker[AsyncSession]):
        steps: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}
        settings = SettingsManager.get_settings(reload=True)

        async with session_maker() as session:
            # Stage 1: Scan & Full Import
            if state.cancel_requested:
                return
            await tm.update_progress(
                task_id=state.task_id,
                progress_pct=10,
                current_step_name="1. 扫描与全量导入",
                current_item_label=f"正在全量扫描入库: {payload.source_path}",
                steps=steps,
            )
            step1 = await _run_scan_and_import(
                session=session,
                source_path=payload.source_path,
                limit_batches=payload.limit_batches if not payload.import_all else None,
                clean_previous=payload.clean_previous_insights,
            )
            steps.append(step1.model_dump())
            summary["imported_batches"] = step1.success_count
            await tm.update_progress(task_id=state.task_id, progress_pct=25, steps=steps, summary=summary)

            # Stage 2: Multimodal Media Enrichment
            if state.cancel_requested:
                return
            await tm.update_progress(
                task_id=state.task_id,
                progress_pct=25,
                current_step_name="2. 多模态视觉解析",
                current_item_label="正在提取群聊图片 OCR 文本与界面报错...",
                steps=steps,
            )
            step2 = await _run_multimodal_enrichment(
                session=session,
                force_mock=payload.force_mock,
                limit_media=15,
            )
            steps.append(step2.model_dump())
            summary["enriched_media"] = step2.success_count
            await tm.update_progress(task_id=state.task_id, progress_pct=50, steps=steps, summary=summary)

            # Stage 3: Episode Segmentation (Scoped)
            if state.cancel_requested:
                return

            async def _on_pipeline_segmentation_progress(processed: int, total: int, label: str, log_item: Any):
                if state.cancel_requested:
                    return
                pct = 50 + int(24 * (processed / max(total, 1)))
                await tm.update_progress(
                    task_id=state.task_id,
                    progress_pct=min(pct, 74),
                    current_step_name="3. 对话与事实切分",
                    current_item_label=label,
                    steps=steps,
                )

            await tm.update_progress(
                task_id=state.task_id,
                progress_pct=50,
                current_step_name="3. 对话与事实切分",
                current_item_label=f"正在按群聊与日期范围切分话题 [{scope_label}]...",
                steps=steps,
            )
            step3 = await _run_episode_segmentation(
                session=session,
                force_mock=payload.force_mock,
                conversation_ids=payload.conversation_ids if payload.conversation_ids else None,
                start_time=st_time,
                end_time=et_time,
                overwrite_existing=payload.overwrite_existing,
                progress_callback=_on_pipeline_segmentation_progress,
            )
            steps.append(step3.model_dump())
            summary["total_episodes"] = step3.success_count
            await tm.update_progress(task_id=state.task_id, progress_pct=75, steps=steps, summary=summary)

            # Stage 4: Insight Extraction (Scoped)
            if state.cancel_requested:
                return

            async def _on_pipeline_insight_progress(processed: int, total: int, label: str, log_item: Any):
                if state.cancel_requested:
                    return
                pct = 75 + int(19 * (processed / max(total, 1)))
                await tm.update_progress(
                    task_id=state.task_id,
                    progress_pct=min(pct, 94),
                    current_step_name="4. 洞察提炼与事实核验",
                    current_item_label=label,
                    steps=steps,
                )

            await tm.update_progress(
                task_id=state.task_id,
                progress_pct=75,
                current_step_name="4. 洞察提炼与事实核验",
                current_item_label=f"正在提炼原子主张与结构化洞察 [{scope_label}]...",
                steps=steps,
            )
            step4, extracted_insights = await _run_insight_extraction(
                session=session,
                force_mock=payload.force_mock,
                conversation_ids=payload.conversation_ids if payload.conversation_ids else None,
                start_time=st_time,
                end_time=et_time,
                overwrite_existing=payload.overwrite_existing,
                limit_episodes=payload.limit_episodes,
                progress_callback=_on_pipeline_insight_progress,
            )
            steps.append(step4.model_dump())
            summary["extracted_insights"] = step4.success_count
            await tm.update_progress(task_id=state.task_id, progress_pct=95, steps=steps, summary=summary)

            # Final Report Generation
            if state.cancel_requested:
                return
            await tm.update_progress(
                task_id=state.task_id,
                progress_pct=96,
                current_step_name="实时 VoC 业务报表更新",
                current_item_label="正在聚合最新洞察并生成全景业务报表...",
                steps=steps,
            )
            report_gen = ReportGenerator(session)
            report = await report_gen.generate_report(period_label=f"全链路自动化流水线实时报告 ({scope_label})", force_mock=True)
            summary["total_feedbacks"] = report.total_feedbacks
            summary["total_topics"] = report.total_topics
            summary["scope_label"] = scope_label

            await tm.complete_task(task_id=state.task_id, steps=steps, summary=summary)

    async def single_step_runner(state: ActiveTaskState, session_maker: async_sessionmaker[AsyncSession]):
        steps: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}
        async with session_maker() as session:
            await tm.update_progress(
                task_id=state.task_id,
                progress_pct=20,
                current_step_name=f"执行单步: {payload.task_type}",
                current_item_label=f"正在处理 [{scope_label}]...",
                steps=steps,
            )
            if payload.task_type in ("import", "scan_import"):
                step_res = await _run_scan_and_import(
                    session=session,
                    source_path=payload.source_path,
                    limit_batches=payload.limit_batches if not payload.import_all else None,
                )
            elif payload.task_type == "multimodal":
                step_res = await _run_multimodal_enrichment(session=session, force_mock=payload.force_mock)
            elif payload.task_type == "segmentation":
                async def _on_single_segmentation_progress(processed: int, total: int, label: str, log_item: Any):
                    if state.cancel_requested:
                        return
                    pct = 20 + int(75 * (processed / max(total, 1)))
                    await tm.update_progress(
                        task_id=state.task_id,
                        progress_pct=min(pct, 95),
                        current_step_name="3. 对话与事实切分",
                        current_item_label=label,
                        steps=steps,
                    )

                step_res = await _run_episode_segmentation(
                    session=session,
                    force_mock=payload.force_mock,
                    conversation_ids=payload.conversation_ids if payload.conversation_ids else None,
                    start_time=st_time,
                    end_time=et_time,
                    overwrite_existing=payload.overwrite_existing,
                    progress_callback=_on_single_segmentation_progress,
                )
            elif payload.task_type in ("insights", "insight_extraction"):
                async def _on_single_insight_progress(processed: int, total: int, label: str, log_item: Any):
                    if state.cancel_requested:
                        return
                    pct = 20 + int(75 * (processed / max(total, 1)))
                    await tm.update_progress(
                        task_id=state.task_id,
                        progress_pct=min(pct, 95),
                        current_step_name="4. 洞察提炼与事实核验",
                        current_item_label=label,
                        steps=steps,
                    )

                step_res, _ = await _run_insight_extraction(
                    session=session,
                    force_mock=payload.force_mock,
                    conversation_ids=payload.conversation_ids if payload.conversation_ids else None,
                    start_time=st_time,
                    end_time=et_time,
                    overwrite_existing=payload.overwrite_existing,
                    limit_episodes=payload.limit_episodes,
                    progress_callback=_on_single_insight_progress,
                )
            elif payload.task_type in ("clustering", "topic_clustering"):
                step_res = await _run_topic_clustering(session=session, force_mock=payload.force_mock)
            elif payload.task_type == "abc_sync":
                step_res = await _run_abc_sync(session=session)
            else:
                step_res = PipelineStepSummary(
                    step_id="unknown",
                    step_name="未知步骤",
                    status="error",
                    details=f"未知步骤类型: {payload.task_type}",
                )

            steps.append(step_res.model_dump())
            summary["item_count"] = step_res.item_count
            summary["success_count"] = step_res.success_count
            summary["error_count"] = step_res.error_count
            summary["scope_label"] = scope_label
            await tm.complete_task(task_id=state.task_id, steps=steps, summary=summary)

    runner = full_pipeline_runner if payload.task_type == "full_pipeline" else single_step_runner
    state = await tm.submit_task(
        task_type=payload.task_type,
        title=task_title,
        params=payload.model_dump(),
        runner_fn=runner,
    )
    return state.to_dict()


@router.get("/active")
async def list_active_tasks():
    """List all currently active / running tasks in memory."""
    tm = TaskManager.get_instance()
    return tm.get_active_tasks()


@router.get("/history")
async def list_task_history(limit: int = Query(default=30, ge=1, le=100)):
    """List historical tasks persisted in database."""
    tm = TaskManager.get_instance()
    return await tm.get_task_history(limit=limit)


@router.get("/{task_id}")
async def get_task_detail(task_id: str):
    """Get real-time details and progress of a task."""
    tm = TaskManager.get_instance()
    task = await tm.get_task_detail(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return task


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Request cancellation of a running task."""
    tm = TaskManager.get_instance()
    success = tm.cancel_task(task_id)
    if not success:
        raise HTTPException(status_code=400, detail=f"Task {task_id} is not running or cannot be cancelled")
    return {"success": True, "message": f"Task {task_id} cancellation requested"}


@router.delete("/history")
async def clear_task_history():
    """Clear all finished task history records from database."""
    tm = TaskManager.get_instance()
    deleted_cnt = await tm.clear_history()
    return {"success": True, "deleted_count": deleted_cnt, "message": "已成功清空历史任务记录"}


class BatchDeleteTasksRequest(BaseModel):
    task_ids: list[str] = Field(default_factory=list, description="要删除的任务 ID 列表")


@router.post("/batch-delete")
async def batch_delete_tasks(payload: BatchDeleteTasksRequest):
    """Delete selected task execution records by their IDs."""
    tm = TaskManager.get_instance()
    deleted_cnt = await tm.delete_tasks_by_ids(payload.task_ids)
    return {
        "success": True,
        "deleted_count": deleted_cnt,
        "message": f"成功删除 {deleted_cnt} 条任务记录",
    }


class PurgeTasksDataRequest(BaseModel):
    steps: list[str] = Field(
        default_factory=list,
        description="需要清除数据的步骤: scan_import | multimodal | segmentation | insights | clustering | abc_sync | full_pipeline",
    )
    task_ids: list[str] = Field(
        default_factory=list,
        description="勾选需要删除的任务历史记录 ID 列表",
    )
    clear_all_task_history: bool = Field(
        default=False,
        description="是否清除全量已完成的历史任务记录",
    )
    clear_tasks_for_selected_steps: bool = Field(
        default=True,
        description="是否同时清除对应步骤的任务记录",
    )


@router.post("/purge-data")
async def purge_tasks_data(payload: PurgeTasksDataRequest):
    """
    Clear historical business data and/or execution records for selected pipeline steps or task IDs.
    """
    tm = TaskManager.get_instance()
    counts = await tm.purge_step_data_and_history(
        steps=payload.steps,
        task_ids=payload.task_ids,
        clear_all_tasks=payload.clear_all_task_history,
        clear_tasks_for_selected_steps=payload.clear_tasks_for_selected_steps,
    )
    return {
        "success": True,
        "counts": counts,
        "message": "已成功清除勾选步骤的历史数据及记录",
    }
