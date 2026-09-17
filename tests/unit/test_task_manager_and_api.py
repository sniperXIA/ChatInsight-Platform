import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.persistence.db import Base, get_session
from packages.tasks.task_manager import TaskManager


def test_task_manager_error_diagnostics():
    tm = TaskManager.get_instance()
    diag_401 = tm.diagnose_error("Error 401: Unauthorized invalid api key")
    assert "身份验证失败" in diag_401
    assert "API Key" in diag_401

    diag_429 = tm.diagnose_error("Rate limit exceeded 429 too many requests")
    assert "模型限流" in diag_429

    diag_json = tm.diagnose_error("Failed to parse JSON: EOF while parsing a list at line 84")
    assert "JSON 截断" in diag_json


@pytest.mark.asyncio
async def test_task_manager_submit_and_cancel(tmp_path):
    test_db_path = tmp_path / "test_tm.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path}", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)

    tm = TaskManager.get_instance()
    tm.set_session_maker(session_factory)
    
    async def dummy_runner(state, session_maker):
        await tm.update_progress(
            task_id=state.task_id,
            progress_pct=25,
            current_step_name="Step 1 in progress",
            current_item_label="Item 1",
        )
        await asyncio.sleep(0.05)
        if state.cancel_requested:
            return
        await tm.update_progress(
            task_id=state.task_id,
            progress_pct=100,
            current_step_name="Done",
        )

    task_state = await tm.submit_task(
        task_type="test_task",
        title="测试任务",
        params={"test_param": 123},
        runner_fn=dummy_runner,
    )

    assert task_state.task_id is not None
    assert task_state.task_type == "test_task"
    assert task_state.status == "running"

    active_tasks = tm.get_active_tasks()
    assert any(t["id"] == task_state.task_id for t in active_tasks)

    # Wait for completion
    await asyncio.sleep(0.1)
    
    detail = await tm.get_task_detail(task_state.task_id)
    assert detail is not None
    assert detail["progress_pct"] == 100
    assert detail["status"] in ("completed", "running")


@pytest.mark.asyncio
async def test_tasks_api_endpoints(tmp_path):
    test_db_path = tmp_path / "test_tasks.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path}", echo=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)
    TaskManager.get_instance().set_session_maker(session_factory)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Launch a mock task
        launch_res = await client.post("/api/v1/tasks/run", json={
            "task_type": "full_pipeline",
            "title": "API测试全链路任务",
            "source_path": "D:/玩家群聊天信息2",
            "force_mock": True,
            "limit_batches": 2,
            "limit_episodes": 2,
            "clean_previous_insights": False,
        })
        assert launch_res.status_code == 200
        task_data = launch_res.json()
        task_id = task_data["id"]
        assert task_data["status"] == "running"

        # 2. Get active tasks
        active_res = await client.get("/api/v1/tasks/active")
        assert active_res.status_code == 200
        active_list = active_res.json()
        assert any(t["id"] == task_id for t in active_list)

        # 3. Get task detail
        detail_res = await client.get(f"/api/v1/tasks/{task_id}")
        assert detail_res.status_code == 200
        assert detail_res.json()["id"] == task_id

        # 4. Cancel task
        cancel_res = await client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert cancel_res.status_code == 200

        # 5. Get history
        history_res = await client.get("/api/v1/tasks/history")
        assert history_res.status_code == 200
        assert isinstance(history_res.json(), list)

        # 6. Clear history
        clear_res = await client.delete("/api/v1/tasks/history")
        assert clear_res.status_code == 200
        assert clear_res.json()["success"] is True

        # 7. Test Batch Delete
        batch_del_res = await client.post("/api/v1/tasks/batch-delete", json={"task_ids": [task_id]})
        assert batch_del_res.status_code == 200
        assert batch_del_res.json()["success"] is True

        # 8. Test Purge Data API
        purge_res = await client.post("/api/v1/tasks/purge-data", json={
            "steps": ["segmentation", "insights"],
            "clear_tasks_for_selected_steps": True,
        })
        assert purge_res.status_code == 200
        purge_body = purge_res.json()
        assert purge_body["success"] is True
        assert "counts" in purge_body
        assert "episodes" in purge_body["counts"]


@pytest.mark.asyncio
async def test_data_purger_service_selective_clearing(tmp_path):
    from datetime import datetime, timezone
    from sqlalchemy import select
    from packages.persistence.models import (
        Conversation,
        Episode,
        EpisodeMessage,
        Insight,
        InsightClaim,
        Message,
        Participant,
        SourceFile,
        SourceRoot,
        Topic,
        TopicInsightLink,
    )
    from packages.tasks.data_purger import DataPurgerService

    test_db_path = tmp_path / "test_purger.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path}", echo=False)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)

    now = datetime(2026, 8, 31, 10, 0, 0)
    async with session_factory() as session:
        # Seed test data across steps
        root = SourceRoot(id="r1", workspace_id="ws1", display_name="root", root_path="/test")
        session.add(root)
        sf = SourceFile(id="f1", workspace_id="ws1", source_root_id=root.id, relative_path="c.txt", file_kind="txt", size_bytes=100, sha256="h1")
        session.add(sf)
        conv = Conversation(id="c1", workspace_id="ws1", display_name="群聊1", source_type="wechat_archive", source_conversation_id="c1")
        session.add(conv)
        part = Participant(id="p1", workspace_id="ws1", stable_anonymous_key="k1", display_label="用户1")
        session.add(part)
        msg = Message(id="m1", workspace_id="ws1", conversation_id=conv.id, participant_id=part.id, source_file_id=sf.id, raw_text="测试消息", normalized_text="测试消息", source_record_hash="hmsg", sent_at=now, source_sequence=1)
        session.add(msg)
        ep = Episode(id="ep1", workspace_id="ws1", conversation_id=conv.id, title="话题1", summary="摘要1", category_hint="hw", started_at=now, ended_at=now, message_count=1)
        session.add(ep)
        ep_msg = EpisodeMessage(id="em1", episode_id=ep.id, message_id=msg.id, sequence=1)
        session.add(ep_msg)
        ins = Insight(id="ins1", workspace_id="ws1", episode_id=ep.id, module="hw", insight_type="issue", summary="洞察1", description="描述1")
        session.add(ins)
        claim = InsightClaim(id="cl1", insight_id=ins.id, claim_key="k1", claim_text="主张1")
        session.add(claim)
        topic = Topic(id="top1", workspace_id="ws1", title="主题1", summary="总结1", module="hw")
        session.add(topic)
        link = TopicInsightLink(id="l1", topic_id=topic.id, insight_id=ins.id)
        session.add(link)
        await session.commit()

        purger = DataPurgerService(session)

        # 1. Purge insights only -> Should clear insights, claims, links, but keep episodes and messages
        res1 = await purger.purge(steps=["insights"])
        assert res1["insights"] == 1
        assert res1["insight_claims"] == 1
        assert res1["topic_insight_links"] == 1

        # Check that episodes and messages still exist
        ep_check = (await session.execute(select(Episode))).scalars().all()
        assert len(ep_check) == 1
        msg_check = (await session.execute(select(Message))).scalars().all()
        assert len(msg_check) == 1

        # 2. Purge segmentation -> Should clear episodes
        res2 = await purger.purge(steps=["segmentation"])
        assert res2["episodes"] == 1
        ep_check2 = (await session.execute(select(Episode))).scalars().all()
        assert len(ep_check2) == 0

        # Check that messages still exist
        msg_check2 = (await session.execute(select(Message))).scalars().all()
        assert len(msg_check2) == 1

        # 3. Purge scan_import -> Clears messages, conversations, files
        res3 = await purger.purge(steps=["scan_import"])
        assert res3["messages"] == 1
        assert res3["conversations"] == 1
        msg_check3 = (await session.execute(select(Message))).scalars().all()
        assert len(msg_check3) == 0

