from datetime import datetime, timedelta
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.persistence.db import Base, get_session
from packages.persistence.repositories.registry import RepositoryRegistry


@pytest_asyncio.fixture
async def client(tmp_path):
    test_db_path = tmp_path / "test_msgs_stats.db"
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
async def test_messages_stats_and_filters_full_flow(client):
    ac, session_factory = client

    now = datetime(2026, 8, 25, 14, 30, 0)
    past_1h = now - timedelta(hours=1)
    past_2d = now - timedelta(days=2)

    # 1. Seed multiple participants and messages
    async with session_factory() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()
        root = await repo.get_or_create_source_root(ws.id, "测试根", "D:/test")
        sf = await repo.upsert_source_file(ws.id, root.id, "1.txt", "txt", 100, "hash1")
        conv = await repo.upsert_conversation(ws.id, "wechat_archive", "conv_stats_test", "测试统计群")

        p_alice = await repo.get_or_create_participant(ws.id, b"s1", "爱丽丝", role_hint="user")
        p_bob = await repo.get_or_create_participant(ws.id, b"s1", "小鲍勃", role_hint="user")
        p_support = await repo.get_or_create_participant(ws.id, b"s1", "官方客服小凡", role_hint="support")

        # Media asset
        sf_img = await repo.upsert_source_file(ws.id, root.id, "pic1.jpg", "image", 5000, "hash_img1")
        media1 = await repo.upsert_media_asset(ws.id, sf_img.id, "image", "image/jpeg", "hash_img1", 5000)

        # Alice sends 2 messages (one with media)
        m1 = await repo.upsert_message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id=p_alice.id,
            source_file_id=sf.id,
            source_message_id="m_alice_1",
            sequence=1,
            sent_at=now,
            raw_text="音色卡插上后指示灯不亮，怎么解决？[图片]",
            source_record_hash="hash_m1",
        )
        await repo.upsert_media_link(
            message_id=m1.id,
            media_id=media1.id,
            media_order=0,
            state="confirmed",
            confidence=1.0,
            method="explicit_filename",
        )

        await repo.upsert_message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id=p_alice.id,
            source_file_id=sf.id,
            source_message_id="m_alice_2",
            sequence=2,
            sent_at=past_1h,
            raw_text="补充一下，固件版本是最新的 v2.1",
            source_record_hash="hash_m2",
        )

        # Bob sends 1 message 2 days ago
        await repo.upsert_message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id=p_bob.id,
            source_file_id=sf.id,
            source_message_id="m_bob_1",
            sequence=3,
            sent_at=past_2d,
            raw_text="我也遇到了同样的问题！",
            source_record_hash="hash_m3",
        )

        # Support replies
        await repo.upsert_message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id=p_support.id,
            source_file_id=sf.id,
            source_message_id="m_support_1",
            sequence=4,
            sent_at=now,
            raw_text="您好，请尝试长按复位键5秒重新配对。",
            source_record_hash="hash_m4",
        )
        await session.commit()

    # 2. Test GET /api/v1/messages/participants
    parts_res = await ac.get("/api/v1/messages/participants")
    assert parts_res.status_code == 200
    parts_data = parts_res.json()
    assert len(parts_data) == 3
    # Top sender should be Alice with 2 messages
    assert parts_data[0]["display_label"] == "爱丽丝"
    assert parts_data[0]["message_count"] == 2

    # 3. Test GET /api/v1/messages/stats
    stats_res = await ac.get("/api/v1/messages/stats")
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert stats["total_messages"] == 4
    assert stats["total_participants"] == 3
    assert stats["total_images"] == 1
    assert len(stats["top_senders"]) == 3
    assert stats["top_senders"][0]["display_label"] == "爱丽丝"
    assert stats["top_senders"][0]["message_count"] == 2
    # Spans from 2026-08-23 to 2026-08-25 (3 days = 72 continuous hourly slots)
    assert stats["days_count"] == 3
    assert len(stats["hourly_distribution"]) == 72

    # Single-day stats check
    single_res = await ac.get(
        "/api/v1/messages/stats",
        params={"time_start": "2026-08-25 00:00:00", "time_end": "2026-08-25 23:59:59"},
    )
    assert single_res.status_code == 200
    single_stats = single_res.json()
    assert single_stats["days_count"] == 1
    assert len(single_stats["hourly_distribution"]) == 24

    # 4. Test GET /api/v1/messages with multi-user filter
    user_filter_res = await ac.get("/api/v1/messages", params={"senders": "爱丽丝,小鲍勃"})
    assert user_filter_res.status_code == 200
    user_filter_data = user_filter_res.json()
    assert user_filter_data["total"] == 3
    sender_labels = {m["sender_label"] for m in user_filter_data["messages"]}
    assert sender_labels == {"爱丽丝", "小鲍勃"}

    # 5. Test GET /api/v1/messages with time range filter
    time_filter_res = await ac.get(
        "/api/v1/messages",
        params={
            "time_start": "2026-08-25 13:00:00",
            "time_end": "2026-08-25 15:00:00",
        },
    )
    assert time_filter_res.status_code == 200
    time_filter_data = time_filter_res.json()
    # Should contain Alice m1, Alice m2 (13:30), Support m4 (14:30)
    assert time_filter_data["total"] == 3

    # Check attachments URL format
    m1_item = next(m for m in time_filter_data["messages"] if len(m["attachments"]) > 0)
    assert len(m1_item["attachments"]) == 1
    assert m1_item["attachments"][0]["url"].startswith("/api/v1/media/")
