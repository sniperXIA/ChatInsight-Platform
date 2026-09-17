from datetime import datetime
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.persistence.db import Base, get_session
from packages.persistence.repositories.registry import RepositoryRegistry


@pytest_asyncio.fixture
async def client(tmp_path):
    test_db_path = tmp_path / "test_ep_api.db"
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
async def test_episodes_list_api(client):
    ac, session_factory = client

    async with session_factory() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()
        conv = await repo.upsert_conversation(ws.id, "wechat_archive", "conv_ep_test", "测试话题群")
        await repo.create_episode(
            ws.id, conv.id, "关于扩展卡的讨论", "用户讨论卡槽", "sound_preset", datetime.now(), datetime.now(), [], [], []
        )
        await session.commit()

    res = await ac.get("/api/v1/episodes")
    assert res.status_code == 200
    episodes = res.json()
    assert len(episodes) >= 1
    assert episodes[0]["title"] == "关于扩展卡的讨论"
    assert episodes[0]["category_hint"] == "sound_preset"
