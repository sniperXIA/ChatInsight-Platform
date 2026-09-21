import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.persistence.db import Base, get_session
from packages.tasks.scheduled_task_manager import ScheduledTaskConfig, ScheduledTaskManager
from packages.tasks.task_manager import TaskManager


@pytest.mark.asyncio
async def test_scheduled_task_api_flow(tmp_path):
    # 1. Setup isolated in-memory/temp database
    test_db_path = tmp_path / "test_api_sched.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path}", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)
    
    tm = TaskManager.get_instance()
    tm.set_session_maker(session_factory)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    # Setup isolated config path
    test_config_path = tmp_path / "scheduled_tasks.json"
    stm = ScheduledTaskManager.get_instance()
    stm.config_path = test_config_path

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. GET schedule config (initially default)
        get_res = await client.get("/api/v1/tasks/schedule")
        assert get_res.status_code == 200
        cfg_data = get_res.json()
        assert "enabled" in cfg_data
        assert "selected_steps" in cfg_data

        # 2. POST update schedule config
        update_payload = {
            "enabled": True,
            "selected_steps": ["insight_extraction", "scan_import"],  # reverse order
            "schedule_type": "day",
            "schedule_interval": 1,
            "schedule_time": "09:00",
            "force_mock": True,
        }
        post_res = await client.post("/api/v1/tasks/schedule", json=update_payload)
        assert post_res.status_code == 200
        updated_data = post_res.json()
        assert updated_data["enabled"] is True
        # Enforced ordering
        assert updated_data["selected_steps"] == ["scan_import", "insight_extraction"]
        assert updated_data["cron_expression"] == "0 9 * * *"
        assert updated_data["next_run_at"] is not None

        # 3. POST trigger now
        mock_step_res = MagicMock(status="success", success_count=1, model_dump=lambda: {"step_id": "test", "status": "success"})
        with patch("apps.api.routes.tasks._run_scan_and_import", new_callable=AsyncMock) as mock_scan, \
             patch("apps.api.routes.tasks._run_insight_extraction", new_callable=AsyncMock) as mock_ins, \
             patch("apps.api.routes.tasks.ReportGenerator") as mock_rep_cls:
            
            mock_scan.return_value = mock_step_res
            mock_ins.return_value = (mock_step_res, [])
            mock_rep = MagicMock()
            mock_rep.generate_report = AsyncMock(return_value=MagicMock(total_feedbacks=5, total_topics=2))
            mock_rep_cls.return_value = mock_rep

            trigger_res = await client.post("/api/v1/tasks/schedule/trigger-now")
            assert trigger_res.status_code == 200
            task_info = trigger_res.json()
            assert task_info["task_type"] == "scheduled_pipeline"
            assert "顺序执行" in task_info["title"]
            assert task_info["id"] is not None

            # Wait a tick for execution to complete
            await asyncio.sleep(0.15)

            # Check active tasks or history
            hist_res = await client.get("/api/v1/tasks/history?limit=10")
            assert hist_res.status_code == 200
            hist = hist_res.json()
            assert any(t["task_type"] == "scheduled_pipeline" for t in hist)


@pytest.mark.asyncio
async def test_scheduler_check_and_trigger(tmp_path):
    """
    Test that _check_and_trigger fires when current time >= next_run_at,
    and updates next_run_at to a future time.
    """
    test_db_path = tmp_path / "test_tick.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path}", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)
    TaskManager.get_instance().set_session_maker(session_factory)

    test_config_path = tmp_path / "tick_scheduled_tasks.json"
    stm = ScheduledTaskManager(config_path=test_config_path)

    # Set next_run_at in the past
    past_time = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    cfg = ScheduledTaskConfig(
        enabled=True,
        selected_steps=["scan_import", "segmentation"],
        schedule_type="day",
        schedule_interval=1,
        schedule_time="09:00",
        next_run_at=past_time,
    )
    stm.save_config(cfg)

    triggered_tasks = []

    async def mock_trigger(is_scheduled=False, config=None):
        mock_state = MagicMock(task_id="mock_triggered_id")
        triggered_tasks.append(mock_state)
        return mock_state

    stm.trigger_task = mock_trigger

    await stm._check_and_trigger()

    # Verify task was triggered
    assert len(triggered_tasks) == 1
    reloaded_cfg = stm.load_config()
    assert reloaded_cfg.last_run_at is not None
    assert reloaded_cfg.next_run_at is not None
    # Next run must be in the future now
    next_dt = datetime.strptime(reloaded_cfg.next_run_at, "%Y-%m-%d %H:%M:%S")
    assert next_dt > datetime.now()
