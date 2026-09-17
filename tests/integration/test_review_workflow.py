import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.persistence.db import Base, get_session
from packages.persistence.models import Episode, Insight
from packages.persistence.repositories.registry import RepositoryRegistry


@pytest_asyncio.fixture
async def client(tmp_path):
    test_db_path = tmp_path / "test_review_api.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path}", echo=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac, session_factory

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_insight_review_approval_and_rejection(client):
    ac, session_factory = client

    # Seed an Insight into DB
    async with session_factory() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()
        conv = await repo.upsert_conversation(ws.id, "wechat_archive", "conv_seed", "测试群")
        from datetime import datetime

        ep = await repo.create_episode(
            workspace_id=ws.id,
            conversation_id=conv.id,
            title="音色卡疑问",
            summary="讨论音色卡",
            category_hint="sound_preset",
            started_at=datetime.now(),
            ended_at=datetime.now(),
            message_ids=[],
            participants=[],
            media_ids=[],
        )
        ins = await repo.create_insight(
            workspace_id=ws.id,
            episode_id=ep.id,
            insight_type="issue",
            module="音色/扩展卡",
            summary="扩展音色卡加载失败",
            description="用户反馈插入卡槽后无反应",
            status_in_chat="unresolved",
            support_known_status=False,
            severity="major",
        )
        await repo.add_insight_claim(
            insight_id=ins.id,
            claim_key="c_1",
            claim_text="插入卡槽无反应",
            fact_category="symptom",
            evidence_uris=["chatinsight://conv/seed/msg_1#text"],
        )
        await session.commit()
        insight_id = ins.id

    # 1. List Insights API
    list_res = await ac.get("/api/v1/insights?state=draft")
    assert list_res.status_code == 200
    insights = list_res.json()
    assert len(insights) >= 1

    # 2. Get Single Insight API
    get_res = await ac.get(f"/api/v1/insights/{insight_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == insight_id
    assert len(get_res.json()["claims"]) == 1

    # 3. Review & Approve with Override
    review_res = await ac.post(
        f"/api/v1/insights/{insight_id}/review",
        json={
            "action": "approve",
            "reviewer": "qa_auditor_01",
            "summary_override": "扩展音色卡插入后接触不良未能正常识别",
            "severity_override": "minor",
        },
    )
    assert review_res.status_code == 200
    approved_data = review_res.json()
    assert approved_data["state"] == "approved"
    assert approved_data["reviewed_by"] == "qa_auditor_01"
    assert approved_data["summary"] == "扩展音色卡插入后接触不良未能正常识别"
    assert approved_data["severity"] == "minor"
