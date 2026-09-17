import asyncio
from contextlib import asynccontextmanager
import os
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.feishu_bitable.feishu_bitable_service import FeishuBitableService
from packages.analytics.report_generator import ReportGenerator
from packages.clustering.cluster_manager import ClusterManager
from packages.importers.batch_importer import BatchImporter
from packages.importers.chat_settings import ChatSettingsManager
from packages.importers.scanner import DirectoryScanner
from packages.insights.episode_segmenter import EpisodeSegmenter
from packages.insights.insight_extractor import InsightExtractor
from packages.media_pipeline.image_pipeline import ImageEnrichmentPipeline
from packages.model_gateway.settings_manager import SettingsManager
from packages.persistence.db import get_session, get_session_context
from packages.persistence.models import (
    AnalysisRun,
    Conversation,
    Episode,
    EpisodeMessage,
    Insight,
    InsightClaim,
    MediaAsset,
    Message,
    Topic,
    TopicInsightLink,
)

router = APIRouter(prefix="/api/v1/pipeline", tags=["Full Automated Pipeline"])


def resolve_scope_time_bounds(
    date_preset: str = "all",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple[Optional[datetime], Optional[datetime], str]:
    """
    Resolves date_preset and optional start_date/end_date to datetime bounds.
    Returns: (start_time, end_time, human_label)
    """
    now = datetime.now()
    if date_preset == "today":
        st = datetime(now.year, now.month, now.day, 0, 0, 0)
        et = datetime(now.year, now.month, now.day, 23, 59, 59)
        return st, et, f"今日 ({now.strftime('%Y-%m-%d')})"
    elif date_preset == "yesterday":
        y = now - timedelta(days=1)
        st = datetime(y.year, y.month, y.day, 0, 0, 0)
        et = datetime(y.year, y.month, y.day, 23, 59, 59)
        return st, et, f"昨日 ({y.strftime('%Y-%m-%d')})"
    elif date_preset == "last_7_days":
        st = datetime(now.year, now.month, now.day, 0, 0, 0) - timedelta(days=6)
        et = datetime(now.year, now.month, now.day, 23, 59, 59)
        return st, et, f"近7天 ({st.strftime('%Y-%m-%d')} ~ {et.strftime('%Y-%m-%d')})"
    elif date_preset == "last_30_days":
        st = datetime(now.year, now.month, now.day, 0, 0, 0) - timedelta(days=29)
        et = datetime(now.year, now.month, now.day, 23, 59, 59)
        return st, et, f"近30天 ({st.strftime('%Y-%m-%d')} ~ {et.strftime('%Y-%m-%d')})"
    elif date_preset == "custom" and (start_date or end_date):
        st = None
        et = None
        if start_date:
            try:
                p_st = datetime.strptime(start_date.strip(), "%Y-%m-%d")
                st = datetime(p_st.year, p_st.month, p_st.day, 0, 0, 0)
            except Exception:
                pass
        if end_date:
            try:
                p_et = datetime.strptime(end_date.strip(), "%Y-%m-%d")
                et = datetime(p_et.year, p_et.month, p_et.day, 23, 59, 59)
            except Exception:
                pass
        label = f"自定义区间 ({start_date or '起始'} ~ {end_date or '至今'})"
        return st, et, label
    return None, None, "全部历史时间"


class PipelineRunRequest(BaseModel):
    source_path: str = Field(
        default_factory=lambda: ChatSettingsManager.load_settings().source_path,
        description="聊天记录根目录",
    )
    force_mock: bool = Field(default=False, description="是否使用离线模式（False表示调用真实AI大模型）")
    import_all: bool = Field(default=False, description="是否全量扫描导入所有本地批次")
    limit_batches: Optional[int] = Field(default=None, description="导入批次数量上限 (None/0 表示全量导入)")
    
    # Decoupled Analysis Scope
    conversation_ids: list[str] = Field(default_factory=list, description="目标群聊 ID 列表，空列表表示全部群聊")
    date_preset: str = Field(default="all", description="all | today | yesterday | last_7_days | last_30_days | custom")
    start_date: Optional[str] = Field(default=None, description="自定义起始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="自定义结束日期 YYYY-MM-DD")
    overwrite_existing: bool = Field(default=True, description="是否覆写该群聊与日期范围内的历史分析结果（保持最新）")
    limit_episodes: Optional[int] = Field(default=None, description="提炼洞察的 Episode 数量上限 (None 表示全量)")
    clean_previous_insights: bool = Field(default=False, description="是否清除全库所有历史测试洞察与主题")


class PipelineStepItemLog(BaseModel):
    item_id: str
    target_label: str
    status: str = Field(default="success", description="success | warning | error | skipped")
    duration_ms: float = 0.0
    model_used: Optional[str] = None
    summary_preview: Optional[str] = None
    error_detail: Optional[str] = None
    extra_info: dict[str, Any] = Field(default_factory=dict)


class PipelineStepSummary(BaseModel):
    step_id: str
    step_name: str
    status: str = Field(default="success", description="success | warning | error | skipped")
    details: str
    item_count: int = 0
    success_count: int = 0
    error_count: int = 0
    duration_ms: float = 0.0
    model_used: Optional[str] = None
    error_summary: Optional[str] = None
    logs: list[PipelineStepItemLog] = Field(default_factory=list)


class PipelineRunResponse(BaseModel):
    success: bool
    message: str
    provider_info: dict[str, Any] = Field(default_factory=dict)
    steps: list[PipelineStepSummary]
    summary: dict[str, Any]


async def _run_scan_and_import(
    session: AsyncSession,
    source_path: str,
    limit_batches: Optional[int] = None,
    clean_previous: bool = False,
) -> PipelineStepSummary:
    t0 = time.perf_counter()
    logs: list[PipelineStepItemLog] = []

    if clean_previous:
        try:
            from packages.persistence.models import InsightPushRecord
            await session.execute(delete(InsightPushRecord))
            await session.execute(delete(TopicInsightLink))
            await session.execute(delete(InsightClaim))
            await session.execute(delete(Insight))
            await session.execute(delete(Topic))
            await session.execute(delete(EpisodeMessage))
            await session.execute(delete(Episode))
            await session.commit()
        except Exception:
            await session.rollback()

    try:
        scanner = DirectoryScanner()
        report, batches = scanner.scan_root(root_path=source_path)
    except Exception as exc:
        total_dur = (time.perf_counter() - t0) * 1000
        return PipelineStepSummary(
            step_id="scan_import",
            step_name="1. 扫描与增量导入",
            status="error",
            details=f"扫描根目录失败: {str(exc)}。请检查路径是否存在。",
            item_count=0,
            success_count=0,
            error_count=1,
            duration_ms=round(total_dur, 1),
            error_summary=str(exc),
            logs=[
                PipelineStepItemLog(
                    item_id="scan_err",
                    target_label=f"扫描路径: {source_path}",
                    status="error",
                    duration_ms=round(total_dur, 1),
                    error_detail=f"扫描异常: {str(exc)}",
                )
            ],
        )

    importer = BatchImporter(session)
    imported_cnt = 0
    err_cnt = 0
    total_messages = 0
    total_media = 0

    target_batches = batches if (limit_batches is None or limit_batches <= 0) else batches[:limit_batches]

    for batch in target_batches:
        t_batch0 = time.perf_counter()
        conv_name = getattr(batch, "conversation_name", getattr(batch, "group_name", "群聊批次"))
        target_name = f"{conv_name} ({batch.date_str})"
        try:
            stats = await importer.import_parsed_batch(batch, root_path=source_path)
            batch_dur = (time.perf_counter() - t_batch0) * 1000
            imported_cnt += 1
            total_messages += stats.messages_inserted
            total_media += stats.media_links_created
            batch_id = getattr(batch, "batch_key", getattr(batch, "conversation_id", f"batch_{imported_cnt}"))
            logs.append(
                PipelineStepItemLog(
                    item_id=batch_id,
                    target_label=target_name,
                    status="success",
                    duration_ms=round(batch_dur, 1),
                    summary_preview=f"导入消息 {stats.messages_inserted} 条，关联媒体 {stats.media_links_created} 个",
                    extra_info={"messages": stats.messages_inserted, "media": stats.media_links_created},
                )
            )
        except Exception as exc:
            batch_dur = (time.perf_counter() - t_batch0) * 1000
            err_cnt += 1
            logs.append(
                PipelineStepItemLog(
                    item_id=f"batch_err_{err_cnt}",
                    target_label=target_name,
                    status="error",
                    duration_ms=round(batch_dur, 1),
                    error_detail=f"批次导入失败: {str(exc)}",
                )
            )

    try:
        await session.commit()
    except Exception:
        await session.rollback()

    total_dur = (time.perf_counter() - t0) * 1000
    overall_status = "success" if err_cnt == 0 else ("warning" if imported_cnt > 0 else "error")

    return PipelineStepSummary(
        step_id="scan_import",
        step_name="1. 扫描与增量导入",
        status=overall_status,
        details=f"全量扫描发现 {len(batches)} 个群聊批次，成功导入 {imported_cnt} 个批次（共入库 {total_messages} 条消息、{total_media} 个媒体文件）",
        item_count=len(target_batches),
        success_count=imported_cnt,
        error_count=err_cnt,
        duration_ms=round(total_dur, 1),
        logs=logs,
    )


async def _run_multimodal_enrichment(
    session: AsyncSession,
    force_mock: bool = False,
    limit_media: int = 15,
) -> PipelineStepSummary:
    t0 = time.perf_counter()
    logs: list[PipelineStepItemLog] = []
    settings = SettingsManager.get_settings()
    model_name = settings.multimodal.model

    media_res = await session.execute(select(MediaAsset).where(MediaAsset.kind == "image").limit(limit_media))
    media_list = media_res.scalars().all()

    img_pipe = ImageEnrichmentPipeline(session)
    analyzed_cnt = 0
    err_cnt = 0
    error_summary = None

    if not media_list:
        total_dur = (time.perf_counter() - t0) * 1000
        return PipelineStepSummary(
            step_id="multimodal",
            step_name="2. 多模态媒体富化",
            status="success",
            details="当前待分析范围内未发现未解析的图片/视频资产，已跳过多模态富化",
            item_count=0,
            success_count=0,
            error_count=0,
            duration_ms=round(total_dur, 1),
            model_used=model_name if not force_mock else "mock_adapter",
            logs=[],
        )

    for m in media_list:
        t_item0 = time.perf_counter()
        target_name = f"媒体资产 [ID: {m.id[:8]}...] {m.kind.upper()}"
        try:
            out, record, was_cached = await img_pipe.analyze_image_asset(m.id, force_mock=force_mock)
            dur = (time.perf_counter() - t_item0) * 1000
            analyzed_cnt += 1
            ocr_snippet = " | ".join(b.text for b in out.ocr_blocks[:3]) if out.ocr_blocks else "无文字"
            logs.append(
                PipelineStepItemLog(
                    item_id=m.id,
                    target_label=target_name,
                    status="success",
                    duration_ms=round(dur, 1),
                    model_used=model_name if not force_mock else "mock_adapter",
                    summary_preview=f"视觉摘要: {out.summary[:60]}... (OCR: {ocr_snippet[:40]})",
                    extra_info={"cached": was_cached, "ocr_count": len(out.ocr_blocks)},
                )
            )
        except Exception as exc:
            dur = (time.perf_counter() - t_item0) * 1000
            err_cnt += 1
            error_msg = str(exc)
            error_summary = error_msg
            logs.append(
                PipelineStepItemLog(
                    item_id=m.id,
                    target_label=target_name,
                    status="error",
                    duration_ms=round(dur, 1),
                    model_used=model_name if not force_mock else "mock_adapter",
                    error_detail=f"多模态模型调用失败: {error_msg}",
                )
            )

    await session.commit()
    total_dur = (time.perf_counter() - t0) * 1000
    overall_status = "success" if err_cnt == 0 else "warning"

    return PipelineStepSummary(
        step_id="multimodal",
        step_name="2. 多模态媒体富化",
        status=overall_status,
        details=f"处理 {len(media_list)} 个媒体资产：成功完成 {analyzed_cnt} 个，失败 {err_cnt} 个",
        item_count=len(media_list),
        success_count=analyzed_cnt,
        error_count=err_cnt,
        duration_ms=round(total_dur, 1),
        model_used=model_name if not force_mock else "mock_adapter",
        error_summary=error_summary,
        logs=logs,
    )


async def _run_episode_segmentation(
    session: AsyncSession,
    force_mock: bool = False,
    conversation_ids: list[str] | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    overwrite_existing: bool = True,
    limit_conversations: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int, str, PipelineStepItemLog | None], Coroutine[Any, Any, None]]] = None,
) -> PipelineStepSummary:
    t0 = time.perf_counter()
    logs: list[PipelineStepItemLog] = []
    settings = SettingsManager.get_settings()
    model_name = settings.segmentation.model

    stmt = select(Conversation)
    if conversation_ids:
        stmt = stmt.where(Conversation.id.in_(conversation_ids))
    if limit_conversations and limit_conversations > 0:
        stmt = stmt.limit(limit_conversations)

    conv_res = await session.execute(stmt)
    convs = conv_res.scalars().all()
    segmenter = EpisodeSegmenter(session)

    total_episodes = 0
    err_cnt = 0
    error_summary = None
    total_convs = len(convs)

    scope_desc = f"（时间范围: {start_time.strftime('%Y-%m-%d') if start_time else '全部'} ~ {end_time.strftime('%Y-%m-%d') if end_time else '全部'}）" if (start_time or end_time) else ""

    for idx, conv in enumerate(convs, 1):
        t_conv0 = time.perf_counter()
        target_name = f"群聊: {conv.display_name} {scope_desc}"

        # Fetch message count and date bounds for granular progress display
        msg_stat_stmt = (
            select(
                func.count(Message.id),
                func.min(Message.sent_at),
                func.max(Message.sent_at),
            )
            .where(Message.conversation_id == conv.id)
        )
        if start_time is not None:
            msg_stat_stmt = msg_stat_stmt.where(Message.sent_at >= start_time)
        if end_time is not None:
            msg_stat_stmt = msg_stat_stmt.where(Message.sent_at <= end_time)

        m_res = (await session.execute(msg_stat_stmt)).first()
        conv_msg_count = m_res[0] if m_res else 0
        min_date = m_res[1] if m_res else None
        max_date = m_res[2] if m_res else None

        date_str = (
            f"{min_date.strftime('%Y-%m-%d')} ~ {max_date.strftime('%Y-%m-%d')}"
            if (min_date and max_date)
            else "全部历史"
        )

        if progress_callback:
            start_label = (
                f"正在切分群聊 [{idx}/{total_convs}]: {conv.display_name} "
                f"({date_str} · {conv_msg_count}条消息) · 累计已提取 {total_episodes} 个话题..."
            )
            try:
                await progress_callback(idx - 1, total_convs, start_label, None)
            except Exception:
                pass

        try:
            episodes = await segmenter.segment_conversation(
                conversation_id=conv.id,
                time_gap_minutes=15,
                start_time=start_time,
                end_time=end_time,
                overwrite_existing=overwrite_existing,
                force_mock=force_mock,
            )
            dur = (time.perf_counter() - t_conv0) * 1000
            total_episodes += len(episodes)
            titles_sample = "、".join([e.title for e in episodes[:2]]) if episodes else "无片段"
            log_item = PipelineStepItemLog(
                item_id=conv.id,
                target_label=target_name,
                status="success",
                duration_ms=round(dur, 1),
                model_used=model_name if not force_mock else "mock_heuristics",
                summary_preview=f"切分出 {len(episodes)} 个话题片段 (示例: {titles_sample})",
                extra_info={"episode_count": len(episodes)},
            )
            logs.append(log_item)

            if progress_callback:
                done_label = (
                    f"已完成切分 [{idx}/{total_convs}]: {conv.display_name} "
                    f"(本群生成 {len(episodes)} 个话题) · 累计已提取 {total_episodes} 个话题"
                )
                try:
                    await progress_callback(idx, total_convs, done_label, log_item)
                except Exception:
                    pass

        except Exception as exc:
            dur = (time.perf_counter() - t_conv0) * 1000
            err_cnt += 1
            error_msg = str(exc)
            error_summary = error_msg
            log_item = PipelineStepItemLog(
                item_id=conv.id,
                target_label=target_name,
                status="error",
                duration_ms=round(dur, 1),
                model_used=model_name if not force_mock else "mock_heuristics",
                error_detail=f"话题切分失败: {error_msg}",
            )
            logs.append(log_item)

            if progress_callback:
                err_label = (
                    f"切分异常 [{idx}/{total_convs}]: {conv.display_name} "
                    f"({error_msg[:30]}) · 累计已提取 {total_episodes} 个话题"
                )
                try:
                    await progress_callback(idx, total_convs, err_label, log_item)
                except Exception:
                    pass

    await session.commit()
    total_dur = (time.perf_counter() - t0) * 1000
    overall_status = "success" if err_cnt == 0 else ("warning" if total_episodes > 0 else "error")

    return PipelineStepSummary(
        step_id="segmentation",
        step_name="3. 对话 Episode 话题切分",
        status=overall_status,
        details=f"对 {len(convs)} 个目标群聊完成切分，共生成 {total_episodes} 个话题片段{scope_desc}",
        item_count=len(convs),
        success_count=len(convs) - err_cnt,
        error_count=err_cnt,
        duration_ms=round(total_dur, 1),
        model_used=model_name if not force_mock else "mock_heuristics",
        error_summary=error_summary,
        logs=logs,
    )


async def _run_insight_extraction(
    session: AsyncSession,
    force_mock: bool = False,
    conversation_ids: list[str] | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    overwrite_existing: bool = True,
    limit_episodes: Optional[int] = None,
    max_concurrency: int = 3,
    progress_callback: Optional[Callable[[int, int, str, PipelineStepItemLog | None], Coroutine[Any, Any, None]]] = None,
) -> tuple[PipelineStepSummary, list[Insight]]:
    t0 = time.perf_counter()
    logs: list[PipelineStepItemLog] = []
    settings = SettingsManager.get_settings()
    model_name = settings.insight_extraction.model

    stmt = select(Episode)
    if conversation_ids:
        stmt = stmt.where(Episode.conversation_id.in_(conversation_ids))
    if start_time is not None:
        stmt = stmt.where(Episode.started_at >= start_time)
    if end_time is not None:
        stmt = stmt.where(Episode.started_at <= end_time)

    stmt = stmt.order_by(Episode.started_at.desc())
    if limit_episodes and limit_episodes > 0:
        stmt = stmt.limit(limit_episodes)

    ep_res = await session.execute(stmt)
    episodes = ep_res.scalars().all()

    # If overwrite_existing is True, clean up previously existing insights & cached analysis runs for these episodes
    if overwrite_existing and episodes:
        ep_ids = [e.id for e in episodes]
        old_ins_res = await session.execute(select(Insight.id).where(Insight.episode_id.in_(ep_ids)))
        old_ins_ids = old_ins_res.scalars().all()
        if old_ins_ids:
            await session.execute(delete(TopicInsightLink).where(TopicInsightLink.insight_id.in_(old_ins_ids)))
            await session.execute(delete(InsightClaim).where(InsightClaim.insight_id.in_(old_ins_ids)))
            await session.execute(delete(Insight).where(Insight.id.in_(old_ins_ids)))

        # Invalidate LLM cached analysis runs for these target episodes so fresh model inference runs
        await session.execute(
            delete(AnalysisRun).where(
                AnalysisRun.run_type.in_(["insight_extraction", "insight"]),
                AnalysisRun.target_type == "episode",
                AnalysisRun.target_id.in_(ep_ids),
            )
        )
        await session.commit()  # Release write lock immediately after cleanup

    success_ep_cnt = 0
    err_cnt = 0
    error_summary = None
    total_insights_count = 0
    processed_count = 0
    consecutive_429 = 0
    circuit_broken = False
    circuit_breaker_msg = None

    bind = getattr(session, "bind", None)
    worker_session_maker = async_sessionmaker(bind=bind, expire_on_commit=False, class_=AsyncSession) if bind else None

    @asynccontextmanager
    async def _get_worker_session():
        if worker_session_maker:
            async with worker_session_maker() as s:
                yield s
        else:
            async with get_session_context() as s:
                yield s

    async def _process_single_episode(ep_id: str, ep_title: str, ep_msg_count: int) -> tuple[bool, list[str], float, str | None, bool]:
        t_ep0 = time.perf_counter()
        async with _get_worker_session() as ep_session:
            extractor = InsightExtractor(ep_session)
            try:
                res_list = await extractor.extract_insights_from_episode(
                    episode_id=ep_id,
                    force_mock=force_mock,
                )
                for ins, _ in res_list:
                    ins.state = "approved"
                await ep_session.commit()
                dur = (time.perf_counter() - t_ep0) * 1000
                ep_insights = [f"[{ins.module}] {ins.summary}" for ins, _ in res_list]
                return True, ep_insights, dur, None, False
            except Exception as exc:
                dur = (time.perf_counter() - t_ep0) * 1000
                err_str = str(exc)
                is_429 = "429" in err_str or getattr(exc, "status_code", None) == 429
                return False, [], dur, err_str, is_429

    sem = asyncio.Semaphore(max_concurrency)
    chunk_size = max(max_concurrency * 2, 6)

    for i in range(0, len(episodes), chunk_size):
        if circuit_broken:
            break
        chunk = episodes[i : i + chunk_size]

        async def _worker(ep: Episode):
            if circuit_broken:
                return None
            async with sem:
                res = await _process_single_episode(ep.id, ep.title, ep.message_count)
                return ep, res

        tasks = [_worker(ep) for ep in chunk]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res_entry in results:
            if res_entry is None or isinstance(res_entry, Exception):
                continue
            ep, (is_success, ep_insights, dur, err_str, is_429) = res_entry
            processed_count += 1
            target_name = f"Episode [{ep.title[:20]}] ({ep.message_count}条消息)"
            if is_success:
                consecutive_429 = 0
                success_ep_cnt += 1
                total_insights_count += len(ep_insights)
                preview_text = "；".join(ep_insights[:2]) if ep_insights else "未检出明确产品缺陷/需求洞察"
                log_item = PipelineStepItemLog(
                    item_id=ep.id,
                    target_label=target_name,
                    status="success",
                    duration_ms=round(dur, 1),
                    model_used=model_name if not force_mock else "mock_heuristics",
                    summary_preview=f"提炼出 {len(ep_insights)} 条洞察: {preview_text[:70]}",
                    extra_info={"insight_count": len(ep_insights), "messages": ep.message_count},
                )
                logs.append(log_item)
            else:
                err_cnt += 1
                error_summary = err_str
                if is_429:
                    consecutive_429 += 1
                else:
                    consecutive_429 = 0
                log_item = PipelineStepItemLog(
                    item_id=ep.id,
                    target_label=target_name,
                    status="error",
                    duration_ms=round(dur, 1),
                    model_used=model_name if not force_mock else "mock_heuristics",
                    error_detail=f"洞察提炼模型调用失败: {err_str}",
                )
                logs.append(log_item)

            if progress_callback:
                status_msg = f"正在提炼洞察 [{processed_count}/{len(episodes)}] (已产出 {total_insights_count} 条)：{ep.title[:25]}"
                try:
                    await progress_callback(processed_count, len(episodes), status_msg, log_item)
                except Exception:
                    pass

            if consecutive_429 >= 3:
                circuit_broken = True
                circuit_breaker_msg = (
                    f"【触发模型服务商并发限流 429】当前配置的模型 '{model_name}' 连续 3 次触发上游并发限流，"
                    f"系统已自动熔断保护。已成功保存前 {processed_count} 个片段提炼的 {total_insights_count} 条洞察。"
                    "建议：1) 稍后重试；2) 前往【系统设置】切换主力模型；3) 将思考程度调整为 none 以降低 Token 消耗。"
                )
                error_summary = circuit_breaker_msg
                break

    # Fetch all newly generated insights for these episodes from the database
    ep_ids = [e.id for e in episodes]
    if ep_ids:
        ins_res = await session.execute(
            select(Insight).where(Insight.episode_id.in_(ep_ids))
        )
        extracted_insights = list(ins_res.scalars().all())
    else:
        extracted_insights = []

    total_dur = (time.perf_counter() - t0) * 1000
    if circuit_broken:
        overall_status = "warning" if len(extracted_insights) > 0 else "error"
    else:
        overall_status = "success" if err_cnt == 0 else ("warning" if len(extracted_insights) > 0 else "error")

    unique_modules = list(set([ins.module for ins in extracted_insights]))

    if circuit_broken:
        details_text = f"处理 {processed_count}/{len(episodes)} 个 Episode：成功提炼出 {len(extracted_insights)} 条洞察（覆盖模块: {', '.join(unique_modules) if unique_modules else '无'}），失败 {err_cnt} 个片段。{circuit_breaker_msg or ''}"
    else:
        details_text = f"处理 {len(episodes)} 个 Episode：成功提炼出 {len(extracted_insights)} 条洞察（覆盖模块: {', '.join(unique_modules) if unique_modules else '无'}），失败 {err_cnt} 个片段"

    summary_obj = PipelineStepSummary(
        step_id="insight_extraction",
        step_name="4. 洞察提炼与事实核验",
        status=overall_status,
        details=details_text,
        item_count=len(episodes),
        success_count=success_ep_cnt,
        error_count=err_cnt,
        duration_ms=round(total_dur, 1),
        model_used=model_name if not force_mock else "mock_heuristics",
        error_summary=error_summary,
        logs=logs,
    )
    return summary_obj, extracted_insights


async def _run_topic_clustering(
    session: AsyncSession,
    force_mock: bool = False,
) -> PipelineStepSummary:
    t0 = time.perf_counter()
    logs: list[PipelineStepItemLog] = []
    settings = SettingsManager.get_settings()
    model_name = settings.clustering_judge.model

    ins_res = await session.execute(select(Insight).where(Insight.state == "approved"))
    approved_insights = ins_res.scalars().all()
    cluster_mgr = ClusterManager(session)

    clustered_cnt = 0
    err_cnt = 0
    error_summary = None

    for ins in approved_insights:
        t_ins0 = time.perf_counter()
        target_name = f"洞察 [{ins.module}] {ins.summary[:25]}..."
        try:
            topic, dec, was_cached = await cluster_mgr.cluster_insight(ins.id, force_mock=force_mock)
            dur = (time.perf_counter() - t_ins0) * 1000
            clustered_cnt += 1
            logs.append(
                PipelineStepItemLog(
                    item_id=ins.id,
                    target_label=target_name,
                    status="success",
                    duration_ms=round(dur, 1),
                    model_used=model_name if not force_mock else "mock_judge",
                    summary_preview=f"判定为 [{dec.value}] -> 归属主题: 【{topic.title}】(反馈频次:{topic.feedback_count})",
                    extra_info={"decision": dec.value, "topic_id": topic.id},
                )
            )
        except Exception as exc:
            dur = (time.perf_counter() - t_ins0) * 1000
            err_cnt += 1
            error_msg = str(exc)
            error_summary = error_msg
            logs.append(
                PipelineStepItemLog(
                    item_id=ins.id,
                    target_label=target_name,
                    status="error",
                    duration_ms=round(dur, 1),
                    model_used=model_name if not force_mock else "mock_judge",
                    error_detail=f"聚类裁判调用失败: {error_msg}",
                )
            )

    await session.commit()
    total_dur = (time.perf_counter() - t0) * 1000
    overall_status = "success" if err_cnt == 0 else ("warning" if clustered_cnt > 0 else "error")

    return PipelineStepSummary(
        step_id="clustering",
        step_name="5. 两阶段聚类去重与主题沉淀",
        status=overall_status,
        details=f"完成 {clustered_cnt} 条洞察的聚类沉淀与知识库更新，失败 {err_cnt} 条",
        item_count=len(approved_insights),
        success_count=clustered_cnt,
        error_count=err_cnt,
        duration_ms=round(total_dur, 1),
        model_used=model_name if not force_mock else "mock_judge",
        error_summary=error_summary,
        logs=logs,
    )


async def _run_bitable_push(session: AsyncSession, force_mock: bool = False) -> PipelineStepSummary:
    t0 = time.perf_counter()
    logs: list[PipelineStepItemLog] = []

    bitable_service = FeishuBitableService(session)
    cfg = bitable_service.load_config()

    q = select(Insight).where(Insight.state == "approved").limit(20)
    insights = (await session.execute(q)).scalars().all()
    if not insights:
        insights = (await session.execute(select(Insight).limit(20))).scalars().all()

    success_cnt = 0
    err_cnt = 0
    for ins in insights:
        res = await bitable_service.push_single_insight(
            insight_id=ins.id,
            operator="pipeline",
            force_mock=force_mock or not bool(cfg.webhook_url),
        )
        if res.success:
            success_cnt += 1
            logs.append(
                PipelineStepItemLog(
                    item_id=ins.id,
                    target_label=f"洞察 [{ins.summary[:20]}]",
                    status="success",
                    duration_ms=10.0,
                    summary_preview="已成功推送至飞书多维表格",
                )
            )
        else:
            err_cnt += 1
            logs.append(
                PipelineStepItemLog(
                    item_id=ins.id,
                    target_label=f"洞察 [{ins.summary[:20]}]",
                    status="warning",
                    duration_ms=10.0,
                    error_detail=res.error or "推送失败",
                )
            )

    total_dur = (time.perf_counter() - t0) * 1000
    return PipelineStepSummary(
        step_id="bitable_push",
        step_name="6. 飞书多维表格需求洞察推送",
        status="success" if err_cnt == 0 else ("warning" if success_cnt > 0 else "error"),
        details=f"成功推送 {success_cnt} 条需求洞察至飞书多维表格" + (f"，{err_cnt} 条异常" if err_cnt else ""),
        item_count=len(insights),
        success_count=success_cnt,
        error_count=err_cnt,
        duration_ms=round(total_dur, 1),
        logs=logs,
    )


# Alias for backwards compatibility
_run_abc_sync = _run_bitable_push


@router.post("/run", response_model=PipelineRunResponse)
async def run_automated_pipeline(
    payload: PipelineRunRequest = PipelineRunRequest(),
    session: AsyncSession = Depends(get_session),
):
    """
    Execute full end-to-end automated pipeline with decoupled scope and daily overwrite support.
    """
    settings = SettingsManager.get_settings()
    provider_info = {
        "provider": settings.provider,
        "base_url": settings.base_url,
        "default_model": settings.default_model,
        "multimodal_model": settings.multimodal.model,
        "insight_model": settings.insight_extraction.model,
        "clustering_model": settings.clustering_judge.model,
        "force_mock": payload.force_mock,
    }

    st_time, et_time, scope_label = resolve_scope_time_bounds(
        date_preset=payload.date_preset,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )

    steps: list[PipelineStepSummary] = []

    # 1. Scan & Full Import
    effective_limit = payload.limit_batches if not payload.import_all else None
    if payload.limit_batches is not None and payload.limit_batches > 0:
        effective_limit = payload.limit_batches
    step1 = await _run_scan_and_import(
        session=session,
        source_path=payload.source_path,
        limit_batches=effective_limit,
        clean_previous=payload.clean_previous_insights,
    )
    steps.append(step1)

    # 2. Multimodal Media Enrichment
    step2 = await _run_multimodal_enrichment(
        session=session,
        force_mock=payload.force_mock,
        limit_media=15,
    )
    steps.append(step2)

    # 3. Episode Segmentation (Scoped)
    step3 = await _run_episode_segmentation(
        session=session,
        force_mock=payload.force_mock,
        conversation_ids=payload.conversation_ids if payload.conversation_ids else None,
        start_time=st_time,
        end_time=et_time,
        overwrite_existing=payload.overwrite_existing,
    )
    steps.append(step3)

    # 4. Insight Extraction (Scoped)
    step4, extracted_insights = await _run_insight_extraction(
        session=session,
        force_mock=payload.force_mock,
        conversation_ids=payload.conversation_ids if payload.conversation_ids else None,
        start_time=st_time,
        end_time=et_time,
        overwrite_existing=payload.overwrite_existing,
        limit_episodes=payload.limit_episodes,
    )
    steps.append(step4)

    # 5. Two-Stage Topic Clustering
    step5 = await _run_topic_clustering(
        session=session,
        force_mock=payload.force_mock,
    )
    steps.append(step5)

    # 6. ABC Sync
    step6 = await _run_abc_sync(session=session)
    steps.append(step6)

    # 7. Generate VoC Report
    report_gen = ReportGenerator(session)
    report = await report_gen.generate_report(period_label=f"流水线实时分析报告 ({scope_label})", force_mock=True)

    has_errors = any(s.status == "error" for s in steps)
    has_warnings = any(s.status == "warning" for s in steps)
    overall_msg = (
        f"流水线执行遇到错误 [{scope_label}]，请展开各步骤查看具体报错"
        if has_errors
        else (f"流水线执行完成（部分步骤有警告）[{scope_label}]" if has_warnings else f"自动化流水线处理完成！[{scope_label}]")
    )

    return PipelineRunResponse(
        success=not has_errors,
        message=overall_msg,
        provider_info=provider_info,
        steps=steps,
        summary={
            "imported_batches": step1.success_count,
            "total_episodes": step3.success_count,
            "extracted_insights": step4.success_count,
            "clustered_insights": step5.success_count,
            "synced_abc_topics": step6.success_count,
            "total_feedbacks": report.total_feedbacks,
            "total_topics": report.total_topics,
            "scope_label": scope_label,
        },
    )


class SingleStepRequest(BaseModel):
    source_path: str = Field(default_factory=lambda: ChatSettingsManager.load_settings().source_path)
    force_mock: bool = Field(default=False)
    import_all: bool = Field(default=True)
    limit_batches: Optional[int] = Field(default=None)
    
    # Decoupled Analysis Scope
    conversation_ids: list[str] = Field(default_factory=list)
    date_preset: str = Field(default="all")
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    overwrite_existing: bool = True
    limit_episodes: Optional[int] = None


@router.post("/step/{step_id}", response_model=PipelineStepSummary)
async def run_single_pipeline_step(
    step_id: str,
    payload: SingleStepRequest = SingleStepRequest(),
    session: AsyncSession = Depends(get_session),
):
    """
    Run an individual step with scoped targeted analysis and daily overwrite.
    step_id: import | multimodal | segmentation | insights | clustering | abc_sync
    """
    st_time, et_time, scope_label = resolve_scope_time_bounds(
        date_preset=payload.date_preset,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )

    try:
        if step_id in ("import", "scan_import"):
            return await _run_scan_and_import(
                session=session,
                source_path=payload.source_path,
                limit_batches=payload.limit_batches if not payload.import_all else None,
                clean_previous=False,
            )
        elif step_id == "multimodal":
            return await _run_multimodal_enrichment(session=session, force_mock=payload.force_mock)
        elif step_id == "segmentation":
            return await _run_episode_segmentation(
                session=session,
                force_mock=payload.force_mock,
                conversation_ids=payload.conversation_ids if payload.conversation_ids else None,
                start_time=st_time,
                end_time=et_time,
                overwrite_existing=payload.overwrite_existing,
            )
        elif step_id in ("insights", "insight_extraction"):
            summary_obj, _ = await _run_insight_extraction(
                session=session,
                force_mock=payload.force_mock,
                conversation_ids=payload.conversation_ids if payload.conversation_ids else None,
                start_time=st_time,
                end_time=et_time,
                overwrite_existing=payload.overwrite_existing,
                limit_episodes=payload.limit_episodes,
            )
            return summary_obj
        elif step_id in ("clustering", "topic_clustering"):
            return await _run_topic_clustering(session=session, force_mock=payload.force_mock)
        elif step_id in ("abc_sync", "bitable_push"):
            return await _run_bitable_push(session=session, force_mock=payload.force_mock)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown pipeline step_id: {step_id}")
    except HTTPException:
        raise
    except Exception as exc:
        return PipelineStepSummary(
            step_id=step_id,
            step_name=f"步骤 [{step_id}]",
            status="error",
            details=f"步骤执行遇到异常: {str(exc)}",
            item_count=0,
            success_count=0,
            error_count=1,
            duration_ms=0.0,
            error_summary=str(exc),
            logs=[
                PipelineStepItemLog(
                    item_id=f"{step_id}_err",
                    target_label=f"步骤 {step_id} ({scope_label})",
                    status="error",
                    duration_ms=0.0,
                    error_detail=f"执行异常: {str(exc)}",
                )
            ],
        )
