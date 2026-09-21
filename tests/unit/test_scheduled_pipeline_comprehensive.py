from datetime import datetime, timedelta, time as dt_time
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from packages.tasks.scheduled_task_manager import (
    PIPELINE_STAGE_ORDER,
    ScheduledTaskConfig,
    ScheduledTaskManager,
    compute_cron_expression,
    compute_next_run,
    get_schedule_description,
    get_ordered_steps,
)
from packages.tasks.task_manager import ActiveTaskState, TaskManager
from apps.api.routes.pipeline import PipelineStepSummary


# ============================================================================
# 1. 周期与时间计算详尽测试 (Day / Week / Month Multi-interval & Boundaries)
# ============================================================================

def test_cron_expression_all_types_and_intervals():
    """Validates cron generation across Day, Week, and Month with varied intervals."""
    # Day
    assert compute_cron_expression("day", 1, time_str="09:00") == "0 9 * * *"
    assert compute_cron_expression("day", 3, time_str="23:45") == "45 23 */3 * *"
    assert compute_cron_expression("day", 1, time_str="00:00") == "0 0 * * *"

    # Week (0=Sun, 1=Mon, ..., 6=Sat)
    assert compute_cron_expression("week", 1, weekday="1", time_str="09:00") == "0 9 * * 1"
    assert compute_cron_expression("week", 1, weekday="5", time_str="18:30") == "30 18 * * 5"
    assert compute_cron_expression("week", 2, weekday="0", time_str="02:00") == "0 2 * * 0"

    # Month
    assert compute_cron_expression("month", 1, month_day="1", time_str="09:00") == "0 9 1 * *"
    assert compute_cron_expression("month", 2, month_day="15", time_str="12:15") == "15 12 15 */2 *"
    assert compute_cron_expression("month", 6, month_day="28", time_str="23:59") == "59 23 28 */6 *"


def test_compute_next_run_day_variations():
    """Tests compute_next_run for daily schedules."""
    cfg = ScheduledTaskConfig(
        schedule_type="day",
        schedule_interval=1,
        schedule_time="10:00",
    )

    # 1. Before 10:00 today -> should trigger today at 10:00
    base_dt = datetime(2026, 9, 18, 8, 30)
    next_dt = compute_next_run(cfg, base_dt)
    assert next_dt == datetime(2026, 9, 18, 10, 0)

    # 2. After 10:00 today -> should trigger tomorrow at 10:00
    base_dt2 = datetime(2026, 9, 18, 10, 30)
    next_dt2 = compute_next_run(cfg, base_dt2)
    assert next_dt2 == datetime(2026, 9, 19, 10, 0)

    # 3. Interval = 3 days
    cfg_3d = ScheduledTaskConfig(
        schedule_type="day",
        schedule_interval=3,
        schedule_time="09:00",
    )
    base_dt3 = datetime(2026, 9, 18, 14, 0)
    next_dt3 = compute_next_run(cfg_3d, base_dt3)
    assert next_dt3 == datetime(2026, 9, 21, 9, 0)


def test_compute_next_run_week_variations():
    """Tests compute_next_run for weekly schedules."""
    # Base is Friday (2026-09-18, python weekday=4)
    # Target: Monday (schedule_weekday="1", py weekday=0)
    cfg_mon = ScheduledTaskConfig(
        schedule_type="week",
        schedule_interval=1,
        schedule_weekday="1",
        schedule_time="09:00",
    )
    base_dt = datetime(2026, 9, 18, 15, 0)  # Friday afternoon
    next_dt = compute_next_run(cfg_mon, base_dt)
    # Next Monday is 2026-09-21
    assert next_dt == datetime(2026, 9, 21, 9, 0)

    # Target: Friday (schedule_weekday="5", py weekday=4)
    cfg_fri = ScheduledTaskConfig(
        schedule_type="week",
        schedule_interval=1,
        schedule_weekday="5",
        schedule_time="18:00",
    )
    # Friday 15:00 -> later today 18:00
    next_dt2 = compute_next_run(cfg_fri, base_dt)
    assert next_dt2 == datetime(2026, 9, 18, 18, 0)

    # Friday 19:00 -> next Friday 2026-09-25
    base_dt_late = datetime(2026, 9, 18, 19, 0)
    next_dt3 = compute_next_run(cfg_fri, base_dt_late)
    assert next_dt3 == datetime(2026, 9, 25, 18, 0)


def test_compute_next_run_month_variations():
    """Tests compute_next_run for monthly schedules including year boundary."""
    cfg = ScheduledTaskConfig(
        schedule_type="month",
        schedule_interval=1,
        schedule_month_day="1",
        schedule_time="09:00",
    )
    # 2026-09-18 -> next is 2026-10-01 09:00
    base_dt = datetime(2026, 9, 18, 10, 0)
    next_dt = compute_next_run(cfg, base_dt)
    assert next_dt == datetime(2026, 10, 1, 9, 0)

    # Year wrap-around: 2026-12-15 -> next is 2027-01-01 09:00
    base_dec = datetime(2026, 12, 15, 10, 0)
    next_dec = compute_next_run(cfg, base_dec)
    assert next_dec == datetime(2027, 1, 1, 9, 0)


# ============================================================================
# 2. 步骤任意乱序组合与强序列保证测试
# ============================================================================

def test_pipeline_ordering_all_permutations():
    """
    Tests that arbitrary subsets and permutations of steps are ALWAYS
    sorted strictly according to PIPELINE_STAGE_ORDER:
    scan_import -> multimodal -> segmentation -> insight_extraction
    """
    # 1. Reverse all 4
    rev_all = ["insight_extraction", "segmentation", "multimodal", "scan_import"]
    assert get_ordered_steps(rev_all) == [
        "scan_import",
        "multimodal",
        "segmentation",
        "insight_extraction",
    ]

    # 2. Out of order subset (1, 3, 4)
    subset_134 = ["insight_extraction", "scan_import", "segmentation"]
    assert get_ordered_steps(subset_134) == [
        "scan_import",
        "segmentation",
        "insight_extraction",
    ]

    # 3. Out of order subset (2, 4)
    subset_24 = ["insight_extraction", "multimodal"]
    assert get_ordered_steps(subset_24) == ["multimodal", "insight_extraction"]

    # 4. Out of order subset (1, 4)
    subset_14 = ["insight_extraction", "scan_import"]
    assert get_ordered_steps(subset_14) == ["scan_import", "insight_extraction"]

    # 5. Invalid values filtered out
    dirty = ["foo", "segmentation", "bar", "multimodal"]
    assert get_ordered_steps(dirty) == ["multimodal", "segmentation"]

    # 6. Duplicates deduped and sorted
    dups = ["insight_extraction", "scan_import", "scan_import", "segmentation"]
    assert get_ordered_steps(dups) == [
        "scan_import",
        "segmentation",
        "insight_extraction",
    ]

    # 7. Empty list
    assert get_ordered_steps([]) == []


# ============================================================================
# 3. 链式执行器多阶段流转与熔断机制测试
# ============================================================================

@pytest.mark.asyncio
async def test_chained_pipeline_runner_execution_and_progress():
    """
    Tests that chained_pipeline_runner runs selected stages sequentially
    and completes with properly allocated stage progress.
    """
    from apps.api.routes.tasks import chained_pipeline_runner

    state = ActiveTaskState(
        task_id="test_chained_comp_1",
        task_type="scheduled_pipeline",
        title="测试定期链式流水线",
        params={
            "selected_steps": ["scan_import", "segmentation"],
            "ordered_steps": ["scan_import", "segmentation"],
            "force_mock": True,
            "date_preset": "all",
        },
    )

    step1_res = PipelineStepSummary(
        step_id="scan_import",
        step_name="1. 扫描与全量导入",
        status="success",
        item_count=10,
        success_count=10,
        error_count=0,
        details="OK",
    )
    step3_res = PipelineStepSummary(
        step_id="segmentation",
        step_name="3. 对话 Episode 话题切分",
        status="success",
        item_count=5,
        success_count=5,
        error_count=0,
        details="OK",
    )

    mock_session = AsyncMock()
    mock_session_maker = MagicMock()
    mock_session_maker.return_value.__aenter__.return_value = mock_session

    tm = TaskManager.get_instance()
    tm._active_tasks[state.task_id] = state

    with patch("apps.api.routes.tasks._run_scan_and_import", new_callable=AsyncMock) as mock_s1, \
         patch("apps.api.routes.tasks._run_episode_segmentation", new_callable=AsyncMock) as mock_s3:

        mock_s1.return_value = step1_res
        mock_s3.return_value = step3_res

        await chained_pipeline_runner(state, mock_session_maker)

        # Check that both steps executed in exact order
        assert mock_s1.await_count == 1
        assert mock_s3.await_count == 1

        # Check task state
        assert state.status == "completed"
        assert len(state.steps) == 2
        assert state.steps[0]["step_id"] == "scan_import"
        assert state.steps[1]["step_id"] == "segmentation"
        assert state.summary.get("imported_batches") == 10
        assert state.summary.get("total_episodes") == 5


@pytest.mark.asyncio
async def test_chained_pipeline_circuit_breaker_aborts_subsequent_steps():
    """
    Circuit-breaker test: if Step 1 fails, Step 3 and Step 4 MUST NOT run.
    The task must be marked 'failed' immediately.
    """
    from apps.api.routes.tasks import chained_pipeline_runner

    state = ActiveTaskState(
        task_id="test_circuit_breaker_1",
        task_type="scheduled_pipeline",
        title="测试熔断机制",
        params={
            "selected_steps": ["scan_import", "segmentation", "insight_extraction"],
            "ordered_steps": ["scan_import", "segmentation", "insight_extraction"],
            "force_mock": True,
        },
    )

    # Step 1 fails with an unhandled exception
    mock_session = AsyncMock()
    mock_session_maker = MagicMock()
    mock_session_maker.return_value.__aenter__.return_value = mock_session

    with patch("apps.api.routes.tasks._run_scan_and_import", side_effect=RuntimeError("Data archive corrupted!")) as mock_s1, \
         patch("apps.api.routes.tasks._run_episode_segmentation", new_callable=AsyncMock) as mock_s3, \
         patch("apps.api.routes.tasks._run_insight_extraction", new_callable=AsyncMock) as mock_s4:

        await chained_pipeline_runner(state, mock_session_maker)

        # Step 1 attempted
        assert mock_s1.await_count == 1
        # Step 3 and 4 were circuit-broken and NEVER called
        assert mock_s3.await_count == 0
        assert mock_s4.await_count == 0

        # State reflects failure
        assert state.status == "failed"
        assert "Data archive corrupted!" in (state.error_summary or "")


# ============================================================================
# 4. 并发防重入互斥锁测试 (Re-entrancy Prevention)
# ============================================================================

@pytest.mark.asyncio
async def test_scheduled_manager_reentrancy_mutex(tmp_path: Path):
    """
    Verifies that _check_and_trigger skips execution if another
    scheduled pipeline task is actively running.
    """
    cfg_file = tmp_path / "scheduled_tasks.json"
    stm = ScheduledTaskManager(config_path=cfg_file)

    past_time = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    cfg = ScheduledTaskConfig(
        enabled=True,
        selected_steps=["scan_import"],
        next_run_at=past_time,
    )
    stm.save_config(cfg)

    # Mock TaskManager to report an active running task
    mock_tm = MagicMock()
    mock_tm.get_active_tasks.return_value = [
        {"task_type": "scheduled_pipeline", "status": "running"}
    ]

    with patch.object(TaskManager, "get_instance", return_value=mock_tm), \
         patch.object(stm, "trigger_task", new_callable=AsyncMock) as mock_trigger:

        await stm._check_and_trigger()
        # Should be skipped due to reentrancy mutex!
        mock_trigger.assert_not_called()


# ============================================================================
# 5. 配置持久化与重启还原测试 (Persistence Roundtrip)
# ============================================================================

def test_scheduled_task_config_persistence_roundtrip(tmp_path: Path):
    """Verifies that config changes are persisted to JSON and cleanly loaded back."""
    cfg_file = tmp_path / "test_config.json"
    stm = ScheduledTaskManager(config_path=cfg_file)

    original_cfg = ScheduledTaskConfig(
        enabled=True,
        selected_steps=["multimodal", "insight_extraction"],
        schedule_type="week",
        schedule_interval=2,
        schedule_weekday="3",
        schedule_time="14:30",
        cron_expression="30 14 * * 3",
        force_mock=True,
        date_preset="7d",
    )
    stm.save_config(original_cfg)

    # Read back
    loaded_cfg = stm.load_config()
    assert loaded_cfg.enabled is True
    assert loaded_cfg.selected_steps == ["multimodal", "insight_extraction"]
    assert loaded_cfg.schedule_type == "week"
    assert loaded_cfg.schedule_interval == 2
    assert loaded_cfg.schedule_weekday == "3"
    assert loaded_cfg.schedule_time == "14:30"
    assert loaded_cfg.cron_expression == "30 14 * * 3"
    assert loaded_cfg.force_mock is True
    assert loaded_cfg.date_preset == "7d"
