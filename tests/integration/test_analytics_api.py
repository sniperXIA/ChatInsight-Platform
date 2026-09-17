import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.persistence.db import Base, get_session
from packages.persistence.repositories.registry import RepositoryRegistry


@pytest_asyncio.fixture
async def client(tmp_path):
    test_db_path = tmp_path / "test_analytics_api.db"
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
async def test_analytics_and_search_api_routes(client):
    ac, session_factory = client

    # Seed data
    async with session_factory() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()
        await repo.create_topic(
            workspace_id=ws.id,
            title="扩展音色卡问题",
            summary="无法正常发声",
            module="音色/扩展卡",
            severity="major",
        )
        await session.commit()

    # 1. Overview API
    overview_res = await ac.get("/api/v1/analytics/overview")
    assert overview_res.status_code == 200
    data = overview_res.json()
    assert data["total_topics"] >= 1

    # 2. Hybrid Search API
    search_res = await ac.post("/api/v1/search/hybrid", json={"query": "音色卡", "limit": 10})
    assert search_res.status_code == 200
    search_data = search_res.json()
    assert search_data["total_hits"] >= 1

    # 3. VoC Report API
    report_res = await ac.post("/api/v1/analytics/reports/generate", json={"force_mock": True})
    assert report_res.status_code == 200
    report_data = report_res.json()
    assert report_data["total_topics"] >= 1
    assert len(report_data["module_distribution"]) >= 1

    # 4. Export Markdown API
    export_res = await ac.get("/api/v1/analytics/reports/export-markdown")
    assert export_res.status_code == 200
    assert "text/markdown" in export_res.headers["content-type"]
    assert "# 📊" in export_res.text
