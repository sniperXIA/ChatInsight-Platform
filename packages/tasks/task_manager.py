import asyncio
import os
import time
import traceback
from datetime import datetime
from typing import Any, Callable, Coroutine, Optional
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.persistence.db import _engine, init_db_engine
from packages.persistence.models import TaskExecution, generate_id, utcnow


class TaskProgressUpdate(BaseModel):
    task_id: str
    progress_pct: int
    current_step_name: Optional[str] = None
    current_item_label: Optional[str] = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    error_summary: Optional[str] = None


class ActiveTaskState:
    def __init__(self, task_id: str, task_type: str, title: str, params: dict[str, Any]):
        self.task_id = task_id
        self.task_type = task_type
        self.title = title
        self.params = params
        self.status = "running"  # running | completed | failed | warning | cancelled
        self.progress_pct = 0
        self.current_step_name = "准备初始化..."
        self.current_item_label = "正在准备运行环境..."
        self.steps: list[dict[str, Any]] = []
        self.summary: dict[str, Any] = {}
        self.error_summary: Optional[str] = None
        self.error_detail: Optional[str] = None
        self.started_at = datetime.now()
        self.completed_at: Optional[datetime] = None
        self.duration_ms = 0.0
        self.cancel_requested = False
        self.asyncio_task: Optional[asyncio.Task] = None

    def to_dict(self) -> dict[str, Any]:
        elapsed = (time.perf_counter() - self._t0) * 1000 if hasattr(self, "_t0") else self.duration_ms
        return {
            "id": self.task_id,
            "task_type": self.task_type,
            "title": self.title,
            "status": self.status,
            "progress_pct": self.progress_pct,
            "current_step_name": self.current_step_name,
            "current_item_label": self.current_item_label,
            "params": self.params,
            "summary": self.summary,
            "steps": self.steps,
            "error_summary": self.error_summary,
            "error_detail": self.error_detail,
            "duration_ms": round(elapsed, 1),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class TaskManager:
    _instance: Optional["TaskManager"] = None
    _active_tasks: dict[str, ActiveTaskState] = {}
    _lock = asyncio.Lock()
    _custom_session_maker: Optional[async_sessionmaker[AsyncSession]] = None

    @classmethod
    def get_instance(cls) -> "TaskManager":
        if cls._instance is None:
            cls._instance = TaskManager()
        return cls._instance

    @classmethod
    def set_session_maker(cls, sm: Optional[async_sessionmaker[AsyncSession]]):
        cls._custom_session_maker = sm

    @classmethod
    def _get_session_maker(cls):
        if cls._custom_session_maker is not None:
            return cls._custom_session_maker
        from packages.persistence.db import _engine, _session_maker, init_db_engine
        if _session_maker is not None:
            return _session_maker
        engine = _engine or init_db_engine()
        return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    def diagnose_error(self, err_text: str) -> str:
        """Parse error message and generate clear diagnostic advice for users."""
        if not err_text:
            return ""
        low = err_text.lower()
        if "401" in low or "unauthorized" in low or "invalid api key" in low:
            return "【身份验证失败 401】当前配置的 API Key 无效或已过期。请前往【⚙️ 系统与模型设置】检查并重新配置有效的 API Key。"
        elif "429" in low or "rate limit" in low or "too many requests" in low or "quota" in low:
            return "【模型限流或额度超限 429】当前模型供应商触发并发限流或免费额度已耗尽。建议稍后重试或切换为备用模型/离线模式。"
        elif "connection error" in low or "timed out" in low or "network" in low or "econnrefused" in low:
            return "【网络连接超时】调用大模型 API 超时或网络中断。请检查网络连通性或当前 Base URL 地址是否正确。"
        elif "invalid json" in low or "json_invalid" in low or "eof while parsing" in low:
            return "【大模型返回 JSON 截断】模型输出达到 Max Tokens 长度限制或输出不合规。系统已尝试智能修复，建议适当增加 Max Tokens。"
        return f"执行异常: {err_text}"

    async def submit_task(
        self,
        task_type: str,
        title: str,
        params: dict[str, Any],
        runner_fn: Callable[[ActiveTaskState, async_sessionmaker], Coroutine[Any, Any, None]],
    ) -> ActiveTaskState:
        task_id = generate_id()
        state = ActiveTaskState(task_id=task_id, task_type=task_type, title=title, params=params)
        state._t0 = time.perf_counter()

        # 1. Record in DB
        session_maker = self._get_session_maker()
        async with session_maker() as session:
            db_task = TaskExecution(
                id=task_id,
                task_type=task_type,
                title=title,
                status="running",
                progress_pct=0,
                current_step_name="准备初始化...",
                current_item_label="正在准备运行环境...",
                params_json=params,
                summary_json={},
                steps_json=[],
                started_at=datetime.now(),
            )
            session.add(db_task)
            await session.commit()

        self._active_tasks[task_id] = state

        # 2. Launch background asyncio task
        async def _wrapper():
            try:
                await runner_fn(state, session_maker)
            except asyncio.CancelledError:
                state.status = "cancelled"
                state.current_step_name = "任务已由用户中止"
                state.current_item_label = "已取消执行"
            except Exception as exc:
                state.status = "failed"
                state.error_summary = str(exc)
                state.error_detail = traceback.format_exc()
                state.current_step_name = "执行异常中断"
            finally:
                state.completed_at = datetime.now()
                state.duration_ms = round((time.perf_counter() - state._t0) * 1000, 1)
                
                # Check overall status from steps if not already failed/cancelled
                if state.status == "running":
                    has_error = any(s.get("status") == "error" for s in state.steps)
                    has_warning = any(s.get("status") == "warning" for s in state.steps)
                    state.status = "failed" if has_error else ("warning" if has_warning else "completed")
                
                state.progress_pct = 100 if state.status in ("completed", "warning") else state.progress_pct

                # Persist final state to DB
                try:
                    async with session_maker() as session:
                        stmt = (
                            update(TaskExecution)
                            .where(TaskExecution.id == task_id)
                            .values(
                                status=state.status,
                                progress_pct=state.progress_pct,
                                current_step_name=state.current_step_name,
                                current_item_label=state.current_item_label,
                                summary_json=state.summary,
                                steps_json=state.steps,
                                error_summary=state.error_summary,
                                error_detail=state.error_detail,
                                duration_ms=state.duration_ms,
                                completed_at=state.completed_at,
                            )
                        )
                        await session.execute(stmt)
                        await session.commit()
                except Exception as db_err:
                    print(f"Failed to persist task final state to DB: {db_err}")

                # Keep in active list briefly or clear
                await asyncio.sleep(10)
                if task_id in self._active_tasks:
                    del self._active_tasks[task_id]

        state.asyncio_task = asyncio.create_task(_wrapper())
        return state

    async def update_progress(
        self,
        task_id: str,
        progress_pct: int,
        current_step_name: Optional[str] = None,
        current_item_label: Optional[str] = None,
        steps: Optional[list[dict[str, Any]]] = None,
        summary: Optional[dict[str, Any]] = None,
        error_summary: Optional[str] = None,
    ):
        state = self._active_tasks.get(task_id)
        if state:
            state.progress_pct = progress_pct
            if current_step_name:
                state.current_step_name = current_step_name
            if current_item_label:
                state.current_item_label = current_item_label
            if steps is not None:
                state.steps = steps
            if summary is not None:
                state.summary = summary
            if error_summary:
                state.error_summary = error_summary

        # Lightweight DB update for live observability
        session_maker = self._get_session_maker()
        try:
            async with session_maker() as session:
                update_vals = {"progress_pct": progress_pct}
                if current_step_name:
                    update_vals["current_step_name"] = current_step_name
                if current_item_label:
                    update_vals["current_item_label"] = current_item_label
                if steps is not None:
                    update_vals["steps_json"] = steps
                if summary is not None:
                    update_vals["summary_json"] = summary
                if error_summary:
                    update_vals["error_summary"] = error_summary
                
                stmt = update(TaskExecution).where(TaskExecution.id == task_id).values(**update_vals)
                await session.execute(stmt)
                await session.commit()
        except Exception:
            pass

    async def complete_task(
        self,
        task_id: str,
        steps: Optional[list[dict[str, Any]]] = None,
        summary: Optional[dict[str, Any]] = None,
        status: Optional[str] = None,
    ):
        """Mark task completed with steps and summary."""
        state = self._active_tasks.get(task_id)
        if state:
            if steps is not None:
                state.steps = steps
            if summary is not None:
                state.summary = summary
            if status is not None:
                state.status = status
            state.progress_pct = 100 if state.status in ("completed", "warning", "running") else state.progress_pct
            state.completed_at = datetime.now()
            state.duration_ms = round((time.perf_counter() - state._t0) * 1000, 1) if hasattr(state, "_t0") else 0.0

    def cancel_task(self, task_id: str) -> bool:
        state = self._active_tasks.get(task_id)
        if not state:
            return False
        state.cancel_requested = True
        if state.asyncio_task and not state.asyncio_task.done():
            state.asyncio_task.cancel()
        state.status = "cancelled"
        state.current_step_name = "任务已中止"
        state.current_item_label = "已取消执行"
        return True

    def get_active_tasks(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self._active_tasks.values()]

    async def get_task_history(self, limit: int = 50, status_filter: Optional[str] = None) -> list[dict[str, Any]]:
        session_maker = self._get_session_maker()
        async with session_maker() as session:
            stmt = select(TaskExecution).order_by(desc(TaskExecution.created_at)).limit(limit)
            if status_filter and status_filter != "all":
                stmt = stmt.where(TaskExecution.status == status_filter)
            records = (await session.execute(stmt)).scalars().all()
            
            res = []
            for r in records:
                res.append({
                    "id": r.id,
                    "task_type": r.task_type,
                    "title": r.title,
                    "status": r.status,
                    "progress_pct": r.progress_pct,
                    "current_step_name": r.current_step_name,
                    "current_item_label": r.current_item_label,
                    "params": r.params_json or {},
                    "summary": r.summary_json or {},
                    "steps": r.steps_json or [],
                    "error_summary": r.error_summary,
                    "error_detail": r.error_detail,
                    "duration_ms": r.duration_ms,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })
            return res

    async def get_task_detail(self, task_id: str) -> Optional[dict[str, Any]]:
        if task_id in self._active_tasks:
            return self._active_tasks[task_id].to_dict()

        session_maker = self._get_session_maker()
        async with session_maker() as session:
            stmt = select(TaskExecution).where(TaskExecution.id == task_id)
            r = (await session.execute(stmt)).scalar_one_or_none()
            if not r:
                return None
            return {
                "id": r.id,
                "task_type": r.task_type,
                "title": r.title,
                "status": r.status,
                "progress_pct": r.progress_pct,
                "current_step_name": r.current_step_name,
                "current_item_label": r.current_item_label,
                "params": r.params_json or {},
                "summary": r.summary_json or {},
                "steps": r.steps_json or [],
                "error_summary": r.error_summary,
                "error_detail": r.error_detail,
                "duration_ms": r.duration_ms,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }

    async def clear_history(self) -> int:
        session_maker = self._get_session_maker()
        async with session_maker() as session:
            # Delete completed or failed tasks, keep running
            active_ids = list(self._active_tasks.keys())
            stmt = delete(TaskExecution)
            if active_ids:
                stmt = stmt.where(~TaskExecution.id.in_(active_ids))
            res = await session.execute(stmt)
            await session.commit()
            return res.rowcount

    async def delete_tasks_by_ids(self, task_ids: list[str]) -> int:
        if not task_ids:
            return 0
        session_maker = self._get_session_maker()
        async with session_maker() as session:
            active_ids = list(self._active_tasks.keys())
            stmt = delete(TaskExecution).where(
                TaskExecution.id.in_(task_ids),
                ~TaskExecution.id.in_(active_ids),
            )
            res = await session.execute(stmt)
            await session.commit()
            return res.rowcount or 0

    async def purge_step_data_and_history(
        self,
        steps: list[str],
        task_ids: Optional[list[str]] = None,
        clear_all_tasks: bool = False,
        clear_tasks_for_selected_steps: bool = True,
    ) -> dict[str, int]:
        from packages.tasks.data_purger import DataPurgerService
        session_maker = self._get_session_maker()
        async with session_maker() as session:
            purger = DataPurgerService(session)
            return await purger.purge(
                steps=steps,
                task_ids=task_ids,
                clear_all_tasks=clear_all_tasks,
                clear_tasks_for_selected_steps=clear_tasks_for_selected_steps,
            )
