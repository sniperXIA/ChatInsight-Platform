from datetime import datetime
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.persistence.db import Base, get_session
from packages.persistence.models import Conversation, Message
from packages.persistence.repositories.registry import RepositoryRegistry


@pytest_asyncio.fixture
async def client(tmp_path):
    test_db_path = tmp_path / "test_msgs.db"
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
async def test_messages_explorer_api(client):
    ac, session_factory = client

    # Seed conversation and messages
    async with session_factory() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()
        root = await repo.get_or_create_source_root(ws.id, "测试根", "D:/test")
        sf = await repo.upsert_source_file(ws.id, root.id, "1.txt", "txt", 100, "hash1")
        conv = await repo.upsert_conversation(ws.id, "wechat_archive", "conv_msg_test", "测试消息群")
        p_user = await repo.get_or_create_participant(ws.id, b"s1", "小明", role_hint="user")

        await repo.upsert_message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id=p_user.id,
            source_file_id=sf.id,
            source_message_id="m1",
            sequence=1,
            sent_at=datetime.now(),
            raw_text="请问音色卡怎么使用？",
            source_record_hash="hash_m1_test",
        )
        await session.commit()

    # Query Messages API
    res = await ac.get("/api/v1/messages")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 1
    assert "音色卡" in data["messages"][0]["raw_text"]
    assert data["messages"][0]["sender_role"] == "user"
