from datetime import datetime, timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

from packages.tasks.scheduled_task_manager import (
    FEISHU_WEEKDAY_MAP,
    PIPELINE_STAGE_ORDER,
    STAGE_DISPLAY_NAMES,
    ScheduledTaskConfig,
    ScheduledTaskManager,
    compute_cron_expression,
    compute_next_run,
    get_ordered_steps,
    get_schedule_description,
    normalize_step_id,
)
from packages.tasks.task_manager import ActiveTaskState, TaskManager


# =========================================================================
# 1. Pipeline Sequence Constraint Tests
# =========================================================================

def test_step_id_normalization():
    assert normalize_step_id("import") == "scan_import"
    assert normalize_step_id("scan_import") == "scan_import"
    assert normalize_step_id("multimodal") == "multimodal"
    assert normalize_step_id("segmentation") == "segmentation"
    assert normalize_step_id("insights") == "insight_extraction"
    assert normalize_step_id("insight_extraction") == "insight_extraction"
    assert normalize_step_id("invalid_stage") is None
    assert normalize_step_id("") is None


def test_strict_ordered_steps_all_permutations():
    """
    Ensure that regardless of the order the user checked the tasks,
    the execution order strictly follows:
    1. scan_import -> 2. multimodal -> 3. segmentation -> 4. insight_extraction
    """
    # User checked 1, 3, 4 in reverse: 4, 3, 1
    reversed_1_3_4 = ["insight_extraction", "segmentation", "scan_import"]
    assert get_ordered_steps(reversed_1_3_4) == ["scan_import", "segmentation", "insight_extraction"]

    # User checked 3, 1, 4
    perm_3_1_4 = ["segmentation", "scan_import", "insight_extraction"]
    assert get_ordered_steps(perm_3_1_4) == ["scan_import", "segmentation", "insight_extraction"]

    # User checked 1, 4 only
    perm_1_4 = ["insight_extraction", "scan_import"]
    assert get_ordered_steps(perm_1_4) == ["scan_import", "insight_extraction"]

    # User checked 3, 2
    perm_3_2 = ["segmentation", "multimodal"]
    assert get_ordered_steps(perm_3_2) == ["multimodal", "segmentation"]

    # All 4 in reverse
    all_rev = ["insight_extraction", "segmentation", "multimodal", "scan_import"]
    assert get_ordered_steps(all_rev) == PIPELINE_STAGE_ORDER

    # Duplicates and legacy aliases
    with_duplicates = ["insights", "import", "scan_import", "segmentation", "insights", "bogus"]
    assert get_ordered_steps(with_duplicates) == ["scan_import", "segmentation", "insight_extraction"]

    # Empty list
    assert get_ordered_steps([]) == []
    assert get_ordered_steps(["unknown_one", "unknown_two"]) == []


# =========================================================================
# 2. Schedule & Cron Expression Generation Tests
# =========================================================================

def test_cron_expression_generation():
    # Day - Interval 1
    cron_day_1 = compute_cron_expression("day", 1, "1", "1", "09:00")
    assert cron_day_1 == "0 9 * * *"

    # Day - Interval 3 at 14:30
    cron_day_3 = compute_cron_expression("day", 3, "1", "1", "14:30")
    assert cron_day_3 == "30 14 */3 * *"

    # Week - Monday (1)
    cron_week_mon = compute_cron_expression("week", 1, "1", "1", "09:00")
    assert cron_week_mon == "0 9 * * 1"

    # Week - Sunday (0) at 18:00
    cron_week_sun = compute_cron_expression("week", 1, "0", "1", "18:00")
    assert cron_week_sun == "0 18 * * 0"

    # Month - 15th, interval 1
    cron_month_1 = compute_cron_expression("month", 1, "1", "15", "09:00")
    assert cron_month_1 == "0 9 15 * *"

    # Month - 1st, interval 2
    cron_month_2 = compute_cron_expression("month", 2, "1", "1", "09:00")
    assert cron_month_2 == "0 9 1 */2 *"


def test_schedule_human_description():
    cfg_day = ScheduledTaskConfig(schedule_type="day", schedule_interval=1, schedule_time="09:00")
    assert get_schedule_description(cfg_day) == "每天 09:00 自动触发"

    cfg_day_2 = ScheduledTaskConfig(schedule_type="day", schedule_interval=2, schedule_time="10:30")
    assert get_schedule_description(cfg_day_2) == "每隔 2 天的 10:30 自动触发"

    cfg_week = ScheduledTaskConfig(schedule_type="week", schedule_interval=1, schedule_weekday="1", schedule_time="09:00")
    assert get_schedule_description(cfg_week) == "每周周一 09:00 自动触发"

    cfg_week_2 = ScheduledTaskConfig(schedule_type="week", schedule_interval=2, schedule_weekday="5", schedule_time="18:00")
    assert get_schedule_description(cfg_week_2) == "每隔 2 周的周五 18:00 自动触发"

    cfg_month = ScheduledTaskConfig(schedule_type="month", schedule_interval=1, schedule_month_day="15", schedule_time="09:00")
    assert get_schedule_description(cfg_month) == "每月 15 号 09:00 自动触发"

    cfg_month_3 = ScheduledTaskConfig(schedule_type="month", schedule_interval=3, schedule_month_day="1", schedule_time="02:00")
    assert get_schedule_description(cfg_month_3) == "每隔 3 个月的 1 号 02:00 自动触发"


# =========================================================================
# 3. Deterministic Next Run Time Calculation Tests
# =========================================================================

def test_compute_next_run_daily():
    cfg = ScheduledTaskConfig(schedule_type="day", schedule_interval=1, schedule_time="09:00")
    # Base is 08:00 today -> should run today at 09:00
    base_before = datetime(2026, 9, 18, 8, 0, 0)
    next_dt = compute_next_run(cfg, base_time=base_before)
    assert next_dt == datetime(2026, 9, 18, 9, 0, 0)

    # Base is 09:30 today -> should run tomorrow at 09:00
    base_after = datetime(2026, 9, 18, 9, 30, 0)
    next_dt2 = compute_next_run(cfg, base_time=base_after)
    assert next_dt2 == datetime(2026, 9, 19, 9, 0, 0)

    # Interval = 2, base is 10:00 -> should run 2 days later
    cfg_int2 = ScheduledTaskConfig(schedule_type="day", schedule_interval=2, schedule_time="09:00")
    next_dt3 = compute_next_run(cfg_int2, base_time=base_after)
    assert next_dt3 == datetime(2026, 9, 20, 9, 0, 0)


def test_compute_next_run_weekly():
    # 2026-09-18 is a Friday (weekday 4 in python, 5 in Feishu)
    base_friday = datetime(2026, 9, 18, 10, 0, 0)

    # Schedule for Monday (Feishu "1", python 0)
    cfg_mon = ScheduledTaskConfig(schedule_type="week", schedule_interval=1, schedule_weekday="1", schedule_time="09:00")
    next_dt = compute_next_run(cfg_mon, base_time=base_friday)
    # Next Monday is 2026-09-21
    assert next_dt == datetime(2026, 9, 21, 9, 0, 0)

    # Schedule for Friday (Feishu "5", python 4), base is Friday morning 07:00
    cfg_fri = ScheduledTaskConfig(schedule_type="week", schedule_interval=1, schedule_weekday="5", schedule_time="09:00")
    base_fri_morning = datetime(2026, 9, 18, 7, 0, 0)
    next_dt_today = compute_next_run(cfg_fri, base_time=base_fri_morning)
    assert next_dt_today == datetime(2026, 9, 18, 9, 0, 0)

    # Schedule for Friday, base is Friday evening 12:00 -> should run next Friday
    next_dt_next_fri = compute_next_run(cfg_fri, base_time=base_friday)
    assert next_dt_next_fri == datetime(2026, 9, 25, 9, 0, 0)


def test_compute_next_run_monthly():
    # Base is 2026-09-18
    base = datetime(2026, 9, 18, 10, 0, 0)

    # Target day 25 (has not passed this month)
    cfg_25 = ScheduledTaskConfig(schedule_type="month", schedule_interval=1, schedule_month_day="25", schedule_time="09:00")
    next_dt = compute_next_run(cfg_25, base_time=base)
    assert next_dt == datetime(2026, 9, 25, 9, 0, 0)

    # Target day 10 (already passed this month)
    cfg_10 = ScheduledTaskConfig(schedule_type="month", schedule_interval=1, schedule_month_day="10", schedule_time="09:00")
    next_dt2 = compute_next_run(cfg_10, base_time=base)
    assert next_dt2 == datetime(2026, 10, 10, 9, 0, 0)

    # Interval 2 months, day 10 passed
    cfg_10_int2 = ScheduledTaskConfig(schedule_type="month", schedule_interval=2, schedule_month_day="10", schedule_time="09:00")
    next_dt3 = compute_next_run(cfg_10_int2, base_time=base)
    assert next_dt3 == datetime(2026, 11, 10, 9, 0, 0)


# =========================================================================
# 4. Config Persistence & Manager Tests
# =========================================================================

def test_scheduled_task_manager_load_save(tmp_path):
    config_file = tmp_path / "test_scheduled_tasks.json"
    stm = ScheduledTaskManager(config_path=config_file)

    # Default load when file does not exist
    cfg = stm.load_config()
    assert cfg.enabled is False
    assert get_ordered_steps(cfg.selected_steps) == ["scan_import", "segmentation", "insight_extraction"]
    assert cfg.schedule_time == "09:00"

    # Save with custom steps and ordering
    cfg.enabled = True
    cfg.selected_steps = ["insight_extraction", "scan_import"]  # User checked in reverse
    cfg.schedule_type = "week"
    cfg.schedule_weekday = "1"
    cfg.schedule_time = "10:30"

    saved = stm.save_config(cfg)
    assert saved.enabled is True
    # Guaranteed order
    assert saved.selected_steps == ["scan_import", "insight_extraction"]
    assert saved.cron_expression == "30 10 * * 1"
    assert saved.next_run_at is not None

    # Reload from disk
    stm2 = ScheduledTaskManager(config_path=config_file)
    reloaded = stm2.load_config()
    assert reloaded.enabled is True
    assert reloaded.selected_steps == ["scan_import", "insight_extraction"]
    assert reloaded.cron_expression == "30 10 * * 1"


# =========================================================================
# 5. Sequential Chained Execution Mock Test
# =========================================================================

@pytest.mark.asyncio
async def test_chained_pipeline_sequential_execution():
    """
    Test that when chained_pipeline_runner runs, steps 1, 3, 4 are called
    strictly in order, and each step completes before the next begins.
    """
    execution_order = []

    async def mock_step_1(*args, **kwargs):
        execution_order.append("scan_import")
        res = MagicMock()
        res.status = "success"
        res.success_count = 5
        res.model_dump.return_value = {"step_id": "scan_import", "status": "success"}
        return res

    async def mock_step_3(*args, **kwargs):
        execution_order.append("segmentation")
        res = MagicMock()
        res.status = "success"
        res.success_count = 10
        res.model_dump.return_value = {"step_id": "segmentation", "status": "success"}
        return res

    async def mock_step_4(*args, **kwargs):
        execution_order.append("insight_extraction")
        res = MagicMock()
        res.status = "success"
        res.success_count = 15
        res.model_dump.return_value = {"step_id": "insight_extraction", "status": "success"}
        return res, []

    from apps.api.routes.tasks import chained_pipeline_runner
    from packages.tasks.task_manager import ActiveTaskState

    state = ActiveTaskState(
        task_id="test_chained_order",
        task_type="scheduled_pipeline",
        title="测试链式执行",
        params={
            # Passed out of order intentionally
            "selected_steps": ["insight_extraction", "scan_import", "segmentation"],
            "force_mock": True,
            "source_path": "mock_path",
        }
    )

    mock_session = AsyncMock()
    mock_session_maker = MagicMock()
    mock_session_maker.return_value.__aenter__.return_value = mock_session

    with patch("apps.api.routes.tasks._run_scan_and_import", side_effect=mock_step_1), \
         patch("apps.api.routes.tasks._run_episode_segmentation", side_effect=mock_step_3), \
         patch("apps.api.routes.tasks._run_insight_extraction", side_effect=mock_step_4), \
         patch("apps.api.routes.tasks.ReportGenerator") as mock_rep_cls:
        
        mock_rep = MagicMock()
        mock_rep.generate_report = AsyncMock(return_value=MagicMock(total_feedbacks=10, total_topics=3))
        mock_rep_cls.return_value = mock_rep

        await chained_pipeline_runner(state, mock_session_maker)

    # Strictly assert the execution order was 1 -> 3 -> 4
    assert execution_order == ["scan_import", "segmentation", "insight_extraction"], f"Got order: {execution_order}"


@pytest.mark.asyncio
async def test_chained_pipeline_step_failure_aborts_subsequent():
    """
    Test that if step 1 (scan_import) fails, the subsequent steps (segmentation, insight_extraction)
    are immediately aborted to prevent data inconsistencies, and task is marked failed.
    """
    called_steps = []

    async def failing_step_1(*args, **kwargs):
        called_steps.append("scan_import")
        res = MagicMock()
        res.status = "error"
        res.details = "Directory not accessible"
        res.success_count = 0
        res.model_dump.return_value = {"step_id": "scan_import", "status": "error", "details": res.details}
        return res

    async def mock_step_3(*args, **kwargs):
        called_steps.append("segmentation")
        return MagicMock(status="success")

    from apps.api.routes.tasks import chained_pipeline_runner
    from packages.tasks.task_manager import ActiveTaskState

    state = ActiveTaskState(
        task_id="test_chained_failure",
        task_type="scheduled_pipeline",
        title="测试失败熔断",
        params={
            "selected_steps": ["scan_import", "segmentation", "insight_extraction"],
            "force_mock": True,
        }
    )

    mock_session = AsyncMock()
    mock_session_maker = MagicMock()
    mock_session_maker.return_value.__aenter__.return_value = mock_session

    with patch("apps.api.routes.tasks._run_scan_and_import", side_effect=failing_step_1), \
         patch("apps.api.routes.tasks._run_episode_segmentation", side_effect=mock_step_3):
        
        await chained_pipeline_runner(state, mock_session_maker)

    # Only step 1 was executed, subsequent steps were skipped
    assert called_steps == ["scan_import"]
    assert state.status == "failed"
    assert "失败" in (state.error_summary or "")

