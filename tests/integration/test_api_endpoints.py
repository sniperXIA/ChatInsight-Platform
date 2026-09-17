import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.persistence.db import Base, get_session


@pytest_asyncio.fixture
async def client(tmp_path):
    test_db_path = tmp_path / "test_api.db"
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
        yield ac

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_health_routes(client):
    res_live = await client.get("/health/live")
    assert res_live.status_code == 200
    assert res_live.json() == {"status": "ok"}

    res_ready = await client.get("/health/ready")
    assert res_ready.status_code == 200
    assert res_ready.json() == {"status": "ready", "database": "connected"}


@pytest.mark.asyncio
async def test_source_root_and_import_flow(client):
    raw_root = "D:/玩家群聊天信息2"
    if not os.path.exists(raw_root):
        pytest.skip(f"Real data directory {raw_root} not found")

    # 1. Create Source Root
    create_res = await client.post(
        "/api/v1/source-roots",
        json={"display_name": "微信玩家群", "root_path": raw_root},
    )
    assert create_res.status_code == 200
    root_data = create_res.json()
    source_root_id = root_data["id"]

    # 2. Trigger Scan
    scan_res = await client.post(
        "/api/v1/imports/scan",
        json={"source_root_id": source_root_id, "target_date": "20260820"},
    )
    assert scan_res.status_code == 200
    scan_data = scan_res.json()
    assert scan_data["summary"]["conversation_count"] > 0

    # 3. Execute Import
    import_res = await client.post(
        "/api/v1/imports/execute",
        json={"source_root_id": source_root_id, "target_date": "20260820"},
    )
    assert import_res.status_code == 200
    import_stats = import_res.json()
    assert len(import_stats) > 0
    assert import_stats[0]["messages_inserted"] > 0

    # 4. List Conversations
    conv_res = await client.get("/api/v1/conversations")
    assert conv_res.status_code == 200
    conv_list = conv_res.json()
    assert len(conv_list) > 0
    first_conv_id = conv_list[0]["id"]

    # 5. Get Conversation Timeline
    tl_res = await client.get(f"/api/v1/conversations/{first_conv_id}/timeline")
    assert tl_res.status_code == 200
    timeline = tl_res.json()
    assert "sender_label" in timeline[0]
    assert len(timeline[0]["sender_label"]) > 0

    # 6. List Import Batches
    batches_res = await client.get("/api/v1/imports/batches")
    assert batches_res.status_code == 200
    batches_list = batches_res.json()
    assert len(batches_list) > 0

    # 7. Check Media links in Timeline and fetch media content
    found_media = False
    for item in timeline:
        if item["media_links"]:
            media_link = item["media_links"][0]
            media_id = media_link["media_id"]
            # Fetch media info
            media_info_res = await client.get(f"/api/v1/media/{media_id}")
            assert media_info_res.status_code == 200
            assert media_info_res.json()["id"] == media_id

            # Fetch media content
            content_res = await client.get(f"/api/v1/media/{media_id}/content")
            assert content_res.status_code == 200
            assert len(content_res.content) > 0
            found_media = True
            break


@pytest.mark.asyncio
async def test_media_link_pending_and_confirmation(client):
    raw_root = "D:/玩家群聊天信息2"
    if not os.path.exists(raw_root):
        pytest.skip(f"Real data directory {raw_root} not found")

    # Import 20260819 which has ambiguous placeholders
    import_res = await client.post(
        "/api/v1/imports/execute",
        json={"custom_path": raw_root, "target_date": "20260819"},
    )
    assert import_res.status_code == 200

    # Check pending ambiguous media links
    pending_res = await client.get("/api/v1/media-links/pending")
    assert pending_res.status_code == 200
    pending_items = pending_res.json()

    if pending_items:
        first_link_id = pending_items[0]["link_id"]
        # Confirm link
        conf_res = await client.post(f"/api/v1/media-links/{first_link_id}/confirm")
        assert conf_res.status_code == 200
        assert conf_res.json()["state"] == "confirmed"

        # Reject another link if exists
        if len(pending_items) > 1:
            second_link_id = pending_items[1]["link_id"]
            rej_res = await client.post(f"/api/v1/media-links/{second_link_id}/reject")
            assert rej_res.status_code == 200
            assert rej_res.json()["state"] == "rejected"
