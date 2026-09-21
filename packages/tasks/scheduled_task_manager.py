import asyncio
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional
from pydantic import BaseModel, Field

from packages.tasks.task_manager import ActiveTaskState, TaskManager

logger = logging.getLogger(__name__)

# Fixed benchmark pipeline stage order
PIPELINE_STAGE_ORDER = [
    "scan_import",
    "multimodal",
    "segmentation",
    "insight_extraction",
]

STAGE_DISPLAY_NAMES = {
    "scan_import": "1. 扫描与全量导入",
    "multimodal": "2. 多模态视觉解析",
    "segmentation": "3. 对话 Episode 话题切分",
    "insight_extraction": "4. 洞察提炼与事实核验",
}

STAGE_ICONS = {
    "scan_import": "fa-solid fa-folder-open",
    "multimodal": "fa-solid fa-image",
    "segmentation": "fa-solid fa-layer-group",
    "insight_extraction": "fa-solid fa-lightbulb",
}

WEEKDAY_ZH_NAMES = {
    "1": "周一",
    "2": "周二",
    "3": "周三",
    "4": "周四",
    "5": "周五",
    "6": "周六",
    "0": "周日",
}

FEISHU_WEEKDAY_MAP = {
    "1": 0,  # Monday
    "2": 1,  # Tuesday
    "3": 2,  # Wednesday
    "4": 3,  # Thursday
    "5": 4,  # Friday
    "6": 5,  # Saturday
    "0": 6,  # Sunday
}


def normalize_step_id(step_id: str) -> Optional[str]:
    """Map legacy/alternative step keys to standard pipeline IDs."""
    if not step_id:
        return None
    s = step_id.strip()
    if s in ("import", "scan_import"):
        return "scan_import"
    elif s in ("insights", "insight_extraction"):
        return "insight_extraction"
    elif s in PIPELINE_STAGE_ORDER:
        return s
    return None


def get_ordered_steps(selected_steps: list[str]) -> list[str]:
    """
    Enforce strict benchmark pipeline ordering:
    scan_import -> multimodal -> segmentation -> insight_extraction
    Regardless of the permutation or order passed in by the user/caller.
    """
    if not selected_steps:
        return []
    normalized_set = set()
    for s in selected_steps:
        norm = normalize_step_id(s)
        if norm:
            normalized_set.add(norm)
    return [step for step in PIPELINE_STAGE_ORDER if step in normalized_set]


def compute_cron_expression(
    schedule_type: str,
    interval: int = 1,
    weekday: str = "1",
    month_day: str = "1",
    time_str: str = "09:00",
) -> str:
    """Generate standard 5-part cron expression aligned with Feishu schedule UI."""
    try:
        parts = time_str.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        h, m = 9, 0

    interval = max(1, interval)
    minute_str = str(m)
    hour_str = str(h)

    if schedule_type == "day":
        if interval == 1:
            return f"{minute_str} {hour_str} * * *"
        else:
            return f"{minute_str} {hour_str} */{interval} * *"
    elif schedule_type == "week":
        w = weekday if str(weekday) in ("0", "1", "2", "3", "4", "5", "6") else "1"
        return f"{minute_str} {hour_str} * * {w}"
    elif schedule_type == "month":
        d = month_day if month_day else "1"
        if interval == 1:
            return f"{minute_str} {hour_str} {d} * *"
        else:
            return f"{minute_str} {hour_str} {d} */{interval} *"
    return f"{minute_str} {hour_str} * * *"


def compute_next_run(config: "ScheduledTaskConfig", base_time: Optional[datetime] = None) -> datetime:
    """
    Deterministically computes the next datetime this task should run,
    relative to base_time (defaulting to now).
    """
    base = base_time or datetime.now()
    try:
        parts = (config.schedule_time or "09:00").split(":")
        target_h = int(parts[0])
        target_m = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        target_h, target_m = 9, 0

    interval = max(1, config.schedule_interval)
    target_today = base.replace(hour=target_h, minute=target_m, second=0, microsecond=0)

    if config.schedule_type == "day":
        if interval == 1:
            if target_today > base:
                return target_today
            return target_today + timedelta(days=1)
        else:
            if target_today > base:
                return target_today
            return target_today + timedelta(days=interval)

    elif config.schedule_type == "week":
        target_py_w = FEISHU_WEEKDAY_MAP.get(str(config.schedule_weekday), 0)
        base_py_w = base.weekday()
        days_ahead = (target_py_w - base_py_w) % 7

        if days_ahead == 0:
            if target_today > base:
                return target_today
            return target_today + timedelta(days=7 * interval)
        else:
            return (base + timedelta(days=days_ahead)).replace(
                hour=target_h, minute=target_m, second=0, microsecond=0
            )

    elif config.schedule_type == "month":
        try:
            target_day = min(max(1, int(config.schedule_month_day or 1)), 28)
        except Exception:
            target_day = 1

        if base.day < target_day:
            return base.replace(day=target_day, hour=target_h, minute=target_m, second=0, microsecond=0)
        elif base.day == target_day:
            if target_today > base:
                return target_today

        # Advance month by interval
        month_idx = (base.year * 12 + (base.month - 1)) + interval
        new_year = month_idx // 12
        new_month = (month_idx % 12) + 1
        return datetime(new_year, new_month, target_day, target_h, target_m, 0, 0)

    # Default fallback: tomorrow
    return target_today + timedelta(days=1)


def get_schedule_description(config: "ScheduledTaskConfig") -> str:
    """Generate friendly human-readable description for schedule rules."""
    t = config.schedule_time or "09:00"
    interval = max(1, config.schedule_interval)
    if config.schedule_type == "day":
        if interval == 1:
            return f"每天 {t} 自动触发"
        return f"每隔 {interval} 天的 {t} 自动触发"
    elif config.schedule_type == "week":
        w_zh = WEEKDAY_ZH_NAMES.get(str(config.schedule_weekday), "周一")
        if interval == 1:
            return f"每周{w_zh} {t} 自动触发"
        return f"每隔 {interval} 周的{w_zh} {t} 自动触发"
    elif config.schedule_type == "month":
        d = config.schedule_month_day or "1"
        if interval == 1:
            return f"每月 {d} 号 {t} 自动触发"
        return f"每隔 {interval} 个月的 {d} 号 {t} 自动触发"
    return f"定时任务: {t}"


class ScheduledTaskConfig(BaseModel):
    enabled: bool = Field(default=False, description="是否启用定期自动执行")
    selected_steps: list[str] = Field(
        default_factory=lambda: ["scan_import", "segmentation", "insight_extraction"],
        description="定期执行选定的任务步骤 (严格按流水线 1->2->3->4 顺序执行)",
    )
    schedule_type: str = Field(default="day", description="周期类型: day | week | month")
    schedule_interval: int = Field(default=1, description="周期倍数 (每 X 天/周/月)")
    schedule_weekday: str = Field(default="1", description="指定星期几 (0=周日, 1=周一...6=周六)")
    schedule_month_day: str = Field(default="1", description="指定每月几号 (1~28)")
    schedule_time: str = Field(default="09:00", description="每日/每周期执行具体时间 (hh:mm)")
    cron_expression: str = Field(default="0 9 * * *", description="Cron 表达式")

    # Pipeline Run Options
    force_mock: bool = Field(default=False, description="是否使用离线 Mock 模式")
    source_path: Optional[str] = Field(default=None, description="聊天记录根目录路径 (为空则使用系统默认)")
    limit_batches: Optional[int] = Field(default=None, description="导入批次上限")
    limit_episodes: Optional[int] = Field(default=None, description="切分 Episode 上限")
    clean_previous_insights: bool = Field(default=False, description="执行前是否清除历史洞察")
    overwrite_existing: bool = Field(default=True, description="是否覆写该时间范围的历史分析")
    date_preset: str = Field(default="all", description="分析时间范围 preset")

    # Status & Audit
    last_run_at: Optional[str] = Field(default=None, description="上次触发时间")
    last_status: Optional[str] = Field(default=None, description="上次执行状态: completed | failed | running")
    last_task_id: Optional[str] = Field(default=None, description="上次执行的任务 ID")
    next_run_at: Optional[str] = Field(default=None, description="下次预计触发时间")
    description: Optional[str] = Field(default=None, description="人类可读的调度规则描述")


class ScheduledTaskManager:
    """
    Core manager for Scheduled Autonomous Pipeline Tasks.
    Handles configuration persistence, schedule calculations, background
    polling timer loop, and sequential task triggering.
    """

    _instance: Optional["ScheduledTaskManager"] = None
    _scheduler_task: Optional[asyncio.Task] = None
    _runner_fn: Optional[Callable[..., Coroutine[Any, Any, None]]] = None

    def __init__(self, config_path: Path | str = Path("config/scheduled_tasks.json")):
        self.config_path = Path(config_path)

    @classmethod
    def get_instance(cls) -> "ScheduledTaskManager":
        if cls._instance is None:
            cls._instance = ScheduledTaskManager()
        return cls._instance

    @classmethod
    def set_runner_fn(cls, fn: Callable[..., Coroutine[Any, Any, None]]):
        cls._runner_fn = staticmethod(fn)

    def load_config(self) -> ScheduledTaskConfig:
        """Loads schedule configuration from disk, with defaults if missing."""
        if not self.config_path.exists():
            cfg = ScheduledTaskConfig()
            cfg.cron_expression = compute_cron_expression(
                cfg.schedule_type, cfg.schedule_interval, cfg.schedule_weekday, cfg.schedule_month_day, cfg.schedule_time
            )
            cfg.description = get_schedule_description(cfg)
            cfg.next_run_at = compute_next_run(cfg).strftime("%Y-%m-%d %H:%M:%S")
            return cfg

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = ScheduledTaskConfig(**data)
            # Ensure ordered steps
            cfg.selected_steps = get_ordered_steps(cfg.selected_steps)
            if not cfg.selected_steps:
                cfg.selected_steps = ["scan_import", "segmentation", "insight_extraction"]
            cfg.cron_expression = compute_cron_expression(
                cfg.schedule_type, cfg.schedule_interval, cfg.schedule_weekday, cfg.schedule_month_day, cfg.schedule_time
            )
            cfg.description = get_schedule_description(cfg)
            if not cfg.next_run_at:
                cfg.next_run_at = compute_next_run(cfg).strftime("%Y-%m-%d %H:%M:%S")
            return cfg
        except Exception as e:
            logger.warning(f"Failed to load scheduled task config: {e}. Falling back to default.")
            return ScheduledTaskConfig()

    def save_config(self, config: ScheduledTaskConfig) -> ScheduledTaskConfig:
        """Saves configuration to disk, normalizing steps and updating cron/next_run."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        config.selected_steps = get_ordered_steps(config.selected_steps)
        if not config.selected_steps:
            config.selected_steps = ["scan_import", "segmentation", "insight_extraction"]

        config.cron_expression = compute_cron_expression(
            config.schedule_type, config.schedule_interval, config.schedule_weekday, config.schedule_month_day, config.schedule_time
        )
        config.description = get_schedule_description(config)

        # Update next_run_at if empty or disabled
        if config.enabled:
            if not config.next_run_at:
                config.next_run_at = compute_next_run(config).strftime("%Y-%m-%d %H:%M:%S")
        else:
            config.next_run_at = None

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)

        return config

    def start_scheduler(self):
        """Starts background periodic polling task if not already running."""
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())
            logger.info("PeriodicTaskScheduler background loop started.")

    async def stop_scheduler(self):
        """Cleanly stops background scheduler loop."""
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            logger.info("PeriodicTaskScheduler background loop stopped.")

    async def _scheduler_loop(self):
        """
        Background polling loop: checks every 20 seconds whether
        a scheduled task is due to execute.
        """
        logger.info("Scheduled task manager background loop is active.")
        while True:
            try:
                await asyncio.sleep(20)
                await self._check_and_trigger()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in scheduler tick: {e}", exc_info=True)

    async def _check_and_trigger(self):
        """Evaluates whether current time has passed next_run_at and triggers task."""
        cfg = self.load_config()
        if not cfg.enabled or not cfg.selected_steps or not cfg.next_run_at:
            return

        now = datetime.now()
        try:
            target_dt = datetime.strptime(cfg.next_run_at, "%Y-%m-%d %H:%M:%S")
        except Exception:
            cfg.next_run_at = compute_next_run(cfg, now).strftime("%Y-%m-%d %H:%M:%S")
            self.save_config(cfg)
            return

        if now >= target_dt:
            # Check if there is already an active scheduled task running
            tm = TaskManager.get_instance()
            active_tasks = tm.get_active_tasks()
            for t in active_tasks:
                if t.get("task_type") == "scheduled_pipeline" and t.get("status") == "running":
                    logger.info("A scheduled pipeline task is already actively running. Skipping tick.")
                    return

            logger.info(f"Triggering scheduled autonomous pipeline at {now.isoformat()}...")
            # Compute next run in future
            next_run_dt = compute_next_run(cfg, now + timedelta(minutes=1))
            cfg.last_run_at = now.strftime("%Y-%m-%d %H:%M:%S")
            cfg.next_run_at = next_run_dt.strftime("%Y-%m-%d %H:%M:%S")
            cfg.last_status = "running"

            task_state = await self.trigger_task(is_scheduled=True, config=cfg)
            cfg.last_task_id = task_state.task_id
            self.save_config(cfg)

    async def trigger_task(
        self,
        is_scheduled: bool = False,
        config: Optional[ScheduledTaskConfig] = None,
    ) -> ActiveTaskState:
        """
        Triggers a chained pipeline task containing the selected steps in strict order.
        """
        cfg = config or self.load_config()
        ordered_steps = get_ordered_steps(cfg.selected_steps)
        if not ordered_steps:
            ordered_steps = ["scan_import", "segmentation", "insight_extraction"]

        # Build human-readable title
        step_names = [STAGE_DISPLAY_NAMES.get(s, s) for s in ordered_steps]
        tag = "【⏰ 定期自动化任务】" if is_scheduled else "【🚀 手动触发定期配置】"
        title = f"{tag} 顺序执行: {' ➔ '.join(step_names)}"

        tm = TaskManager.get_instance()
        runner = self._runner_fn
        if not runner:
            raise RuntimeError("Chained pipeline runner has not been registered yet.")

        # Prepare parameters
        params = cfg.model_dump()
        params["ordered_steps"] = ordered_steps
        params["is_scheduled"] = is_scheduled

        state = await tm.submit_task(
            task_type="scheduled_pipeline",
            title=title,
            params=params,
            runner_fn=runner,
        )
        return state
