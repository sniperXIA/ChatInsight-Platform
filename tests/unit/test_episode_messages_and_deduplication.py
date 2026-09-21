from datetime import datetime, timedelta
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.insights.episode_deduplicator import DistinctTopicCluster, EpisodeDeduplicator, EpisodeItemView
from packages.persistence.db import Base, get_session
from packages.persistence.models import EpisodeMessage, Message, Participant
from packages.persistence.repositories.registry import RepositoryRegistry


def test_episode_deduplicator_clustering():
    episodes = [
        EpisodeItemView(
            id="ep1",
            conversation_id="c1",
            conversation_name="群聊A",
            title="话题探讨: 音色切换与扩展卡 - 无法加载",
            summary="咨询扩展音色卡加载失败与插拔问题",
            category_hint="sound_preset",
            started_at="2026-08-25T10:00:00",
            ended_at="2026-08-25T10:20:00",
            message_count=5,
            participants=["琴友小张", "官方客服"],
        ),
        EpisodeItemView(
            id="ep2",
            conversation_id="c2",
            conversation_name="群聊B",
            title="话题探讨: 音色与扩展卡音质表现",
            summary="讨论扩展卡音色丰富度和切换手感",
            category_hint="sound_preset",
            started_at="2026-08-25T11:00:00",
            ended_at="2026-08-25T11:30:00",
            message_count=8,
            participants=["吉他老王", "琴友小张"],
        ),
        EpisodeItemView(
            id="ep3",
            conversation_id="c1",
            conversation_name="群聊A",
            title="话题探讨: 蓝牙与无线连接异常",
            summary="反馈手机App蓝牙连接断开及无法搜索到设备",
            category_hint="software_app",
            started_at="2026-08-25T12:00:00",
            ended_at="2026-08-25T12:15:00",
            message_count=4,
            participants=["新琴友"],
        ),
    ]

    clusters = EpisodeDeduplicator.deduplicate_episodes(episodes)
    assert len(clusters) == 2

    # Verify Sound Card Cluster
    sound_cluster = next(c for c in clusters if c.category_hint in ("sound_preset", "audio_config"))
    assert sound_cluster.episode_count == 2
    assert sound_cluster.total_messages == 13
    assert "琴友小张" in sound_cluster.participants
    assert "吉他老王" in sound_cluster.participants
    assert len(sound_cluster.conversations) == 2
    assert sound_cluster.first_discussed_at == "2026-08-25T10:00:00"
    assert sound_cluster.last_discussed_at == "2026-08-25T11:30:00"

    # Verify Bluetooth Cluster
    bt_cluster = next(c for c in clusters if c.category_hint in ("software_app", "bluetooth_conn"))
    assert bt_cluster.episode_count == 1
    assert bt_cluster.total_messages == 4
    assert bt_cluster.first_discussed_at == "2026-08-25T12:00:00"
    assert bt_cluster.last_discussed_at == "2026-08-25T12:15:00"


def test_episode_deduplicator_time_filtering():
    episodes = [
        EpisodeItemView(
            id="ep1",
            conversation_id="c1",
            conversation_name="群聊A",
            title="话题探讨: 音色切换与扩展卡 - 无法加载",
            summary="咨询扩展音色卡加载失败与插拔问题",
            category_hint="sound_preset",
            started_at="2026-08-25T10:00:00",
            ended_at="2026-08-25T10:20:00",
            message_count=5,
            participants=["琴友小张"],
        ),
        EpisodeItemView(
            id="ep2",
            conversation_id="c2",
            conversation_name="群聊B",
            title="话题探讨: 音色与扩展卡音质表现",
            summary="讨论扩展卡音色丰富度和切换手感",
            category_hint="sound_preset",
            started_at="2026-08-27T11:00:00",
            ended_at="2026-08-27T11:30:00",
            message_count=8,
            participants=["吉他老王"],
        ),
        EpisodeItemView(
            id="ep3",
            conversation_id="c1",
            conversation_name="群聊A",
            title="话题探讨: 蓝牙与无线连接异常",
            summary="反馈手机App蓝牙连接断开及无法搜索到设备",
            category_hint="software_app",
            started_at="2026-08-28T12:00:00",
            ended_at="2026-08-28T12:15:00",
            message_count=4,
            participants=["新琴友"],
        ),
    ]

    # Without filter: both clusters returned
    all_clusters = EpisodeDeduplicator.deduplicate_episodes(episodes)
    assert len(all_clusters) == 2
    sound = next(c for c in all_clusters if "音色" in c.canonical_title)
    assert sound.first_discussed_at == "2026-08-25T10:00:00"
    assert sound.last_discussed_at == "2026-08-27T11:30:00"

    # Filter by first_discussed_at between 2026-08-26 and 2026-08-28
    # Sound cluster first discussed on 2026-08-25, so should be excluded
    first_filtered = EpisodeDeduplicator.deduplicate_episodes(
        episodes,
        time_filter_type="first_discussed",
        start_date="2026-08-26",
        end_date="2026-08-28",
    )
    assert len(first_filtered) == 1
    assert "蓝牙" in first_filtered[0].canonical_title

    # Filter by last_discussed_at between 2026-08-26 and 2026-08-28
    # Sound cluster last discussed on 2026-08-27, bluetooth on 2026-08-28 -> both included
    last_filtered = EpisodeDeduplicator.deduplicate_episodes(
        episodes,
        time_filter_type="last_discussed",
        start_date="2026-08-26",
        end_date="2026-08-28",
    )
    assert len(last_filtered) == 2

    # Filter by last_discussed_at with single day 2026-08-27
    day_27 = EpisodeDeduplicator.deduplicate_episodes(
        episodes,
        time_filter_type="last_discussed",
        start_date="2026-08-27",
        end_date="2026-08-27",
    )
    assert len(day_27) == 1
    assert "音色" in day_27[0].canonical_title


@pytest_asyncio.fixture
async def client(tmp_path):
    test_db_path = tmp_path / "test_ep_msg_dedup.db"
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
async def test_episode_messages_and_clusters_api(client):
    ac, session_factory = client

    now = datetime.now()
    ep_id = "test_ep_001"

    async with session_factory() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()
        root = await repo.get_or_create_source_root(ws.id, "测试源", "D:/test")
        sf = await repo.upsert_source_file(ws.id, root.id, "1.txt", "txt", 100, "hash_file_1")
        conv = await repo.upsert_conversation(ws.id, "wechat_archive", "conv_msg_test", "吉他玩家核心群")

        p1 = await repo.get_or_create_participant(ws.id, b"user_1", "晓晓", role_hint="user")
        p2 = await repo.get_or_create_participant(ws.id, b"user_supp", "官方小助手", role_hint="support")

        m1 = Message(
            id="msg_01",
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id=p1.id,
            source_file_id=sf.id,
            source_sequence=1,
            sent_at=now,
            raw_text="请问扩展音色卡怎么切换？插上没有反应",
            normalized_text="请问扩展音色卡怎么切换？插上没有反应",
            source_record_hash="hash_m1",
        )
        m2 = Message(
            id="msg_02",
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id=p2.id,
            source_file_id=sf.id,
            source_sequence=2,
            sent_at=now + timedelta(seconds=30),
            raw_text="您好，请先确保琴身处于开机状态，长按拨片右侧按键即可刷新加载。",
            normalized_text="您好，请先确保琴身处于开机状态，长按拨片右侧按键即可刷新加载。",
            quote_unresolved_text="请问扩展音色卡怎么切换？插上没有反应",
            source_record_hash="hash_m2",
        )
        session.add_all([m1, m2])
        await session.flush()

        ep = await repo.create_episode(
            workspace_id=ws.id,
            conversation_id=conv.id,
            title="话题探讨: 音色切换与扩展卡 - 插拔无反应",
            summary="咨询扩展音色卡切换无反应与官方解答",
            category_hint="sound_preset",
            started_at=now,
            ended_at=now + timedelta(minutes=10),
            message_ids=[m1.id, m2.id],
            participants=[p1.display_label, p2.display_label],
            media_ids=[],
        )
        ep_id = ep.id
        await session.commit()

    # 1. Test GET /api/v1/episodes/clusters
    clusters_res = await ac.get("/api/v1/episodes/clusters")
    assert clusters_res.status_code == 200
    clusters_data = clusters_res.json()
    assert len(clusters_data) >= 1
    assert "音色" in clusters_data[0]["canonical_title"]
    assert clusters_data[0]["episode_count"] == 1
    assert clusters_data[0]["total_messages"] == 2

    # 2. Test GET /api/v1/episodes/{id}/messages
    msg_res = await ac.get(f"/api/v1/episodes/{ep_id}/messages")
    assert msg_res.status_code == 200
    msgs = msg_res.json()
    assert len(msgs) == 2
    assert msgs[0]["sender_label"] == "晓晓"
    assert msgs[0]["sender_role"] == "user"
    assert "扩展音色卡" in msgs[0]["raw_text"]
    assert msgs[1]["sender_label"] == "官方小助手"
    assert msgs[1]["sender_role"] == "support"
    assert msgs[1]["quote_text"] == "请问扩展音色卡怎么切换？插上没有反应"

    # 3. Test GET /api/v1/episodes/{id}/detail
    detail_res = await ac.get(f"/api/v1/episodes/{ep_id}/detail")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["episode"]["id"] == ep_id
    assert len(detail["messages"]) == 2

    # 4. Test GET /api/v1/episodes with date range filtering
    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # Match today
    ep_res_today = await ac.get(f"/api/v1/episodes?start_date={today_str}&end_date={today_str}")
    assert ep_res_today.status_code == 200
    assert len(ep_res_today.json()) >= 1
    assert ep_res_today.json()[0]["id"] == ep_id

    # Miss yesterday
    ep_res_past = await ac.get(f"/api/v1/episodes?start_date={yesterday_str}&end_date={yesterday_str}")
    assert ep_res_past.status_code == 200
    assert len(ep_res_past.json()) == 0
