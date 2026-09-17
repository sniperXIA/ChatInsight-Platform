import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.insights.episode_deduplicator import DistinctTopicCluster, EpisodeDeduplicator, EpisodeItemView
from packages.insights.episode_segmenter import ChunkSegmentationOutput, EpisodeSegmenter, SubEpisodeItem
from packages.persistence.models import Base, Conversation, Message, Participant, SourceFile, SourceRoot, Workspace


@pytest.mark.asyncio
async def test_fine_grained_sub_episode_extraction_and_noise_filtering(tmp_path):
    """
    Verifies that a 25-minute chunk with mixed topics (Sheet Request + Soundcard + Noise Greetings)
    is cleanly decomposed into 2 distinct micro-episodes, with noise messages completely ignored.
    """
    db_file = tmp_path / "test_fg.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        ws = Workspace(name="Test WS")
        session.add(ws)
        await session.flush()

        root = SourceRoot(workspace_id=ws.id, display_name="Test Root", root_path="/test_root")
        session.add(root)
        await session.flush()

        sf = SourceFile(
            workspace_id=ws.id,
            source_root_id=root.id,
            relative_path="test_chat.txt",
            file_kind="txt",
            size_bytes=100,
            sha256="test_sha_123",
                    )
        session.add(sf)
        await session.flush()

        conv = Conversation(
            workspace_id=ws.id,
            source_type="wechat_archive",
            source_conversation_id="conv_mix_test",
            display_name="LiberLive C2 琴友交流群 1",
        )
        session.add(conv)
        await session.flush()

        p1 = Participant(workspace_id=ws.id, stable_anonymous_key="p_1", display_label="琴友A")
        p2 = Participant(workspace_id=ws.id, stable_anonymous_key="p_2", display_label="琴友B")
        p3 = Participant(workspace_id=ws.id, stable_anonymous_key="p_3", display_label="琴友C")
        session.add_all([p1, p2, p3])
        await session.flush()

        t0 = datetime(2026, 8, 25, 14, 0, 0)

        # 1. Sheet request messages (Msg 1, 2)
        m1 = Message(
            workspace_id=ws.id, conversation_id=conv.id, participant_id=p1.id,
            source_file_id=sf.id, source_sequence=1, sent_at=t0 + timedelta(minutes=1),
            raw_text="官方乐库周杰伦的《晴天》伴奏什么时候能加啊？求加新歌！", normalized_text="周杰伦 晴天 伴奏", source_record_hash="h1"
        )
        m2 = Message(
            workspace_id=ws.id, conversation_id=conv.id, participant_id=p2.id,
            source_file_id=sf.id, source_sequence=2, sent_at=t0 + timedelta(minutes=2),
            raw_text="我也想求周杰伦的歌，流行歌太少了希望能出", normalized_text="周杰伦 流行歌", source_record_hash="h2"
        )

        # 2. Sound card messages (Msg 3, 4)
        m3 = Message(
            workspace_id=ws.id, conversation_id=conv.id, participant_id=p3.id,
            source_file_id=sf.id, source_sequence=3, sent_at=t0 + timedelta(minutes=5),
            raw_text="大家买的扩展音色卡插上去怎么没反应？指示灯不亮", normalized_text="扩展音色卡 没反应", source_record_hash="h3"
        )
        m4 = Message(
            workspace_id=ws.id, conversation_id=conv.id, participant_id=p1.id,
            source_file_id=sf.id, source_sequence=4, sent_at=t0 + timedelta(minutes=6),
            raw_text="音色卡要用力插到底，换个风格包试试音质", normalized_text="音色卡 风格包 音质", source_record_hash="h4"
        )

        # 3. Pure noise messages (Msg 5, 6)
        m5 = Message(
            workspace_id=ws.id, conversation_id=conv.id, participant_id=p2.id,
            source_file_id=sf.id, source_sequence=5, sent_at=t0 + timedelta(minutes=10),
            raw_text="早啊大家 [动画表情]", normalized_text="", source_record_hash="h5"
        )
        m6 = Message(
            workspace_id=ws.id, conversation_id=conv.id, participant_id=p3.id,
            source_file_id=sf.id, source_sequence=6, sent_at=t0 + timedelta(minutes=11),
            raw_text="[微信红包] 恭喜发财", normalized_text="", source_record_hash="h6"
        )

        session.add_all([m1, m2, m3, m4, m5, m6])
        await session.commit()

        segmenter = EpisodeSegmenter(session)
        episodes = await segmenter.segment_conversation(conversation_id=conv.id, force_mock=True)

        # Assertions:
        # Exactly 2 distinct micro-episodes should be created (Noise ignored)
        assert len(episodes) == 2, f"Expected 2 micro-episodes, got {len(episodes)}"

        titles = [e.title for e in episodes]
        assert any("曲谱" in t or "流行" in t or "新歌" in t for t in titles)
        assert any("音色卡" in t or "音色" in t for t in titles)

        # Verify message isolation:
        sheet_ep = next(e for e in episodes if "曲谱" in e.title or "流行" in e.title or "新歌" in e.title)
        assert sheet_ep.message_count == 2
        assert len(sheet_ep.summary) >= 30, "Summary should be comprehensive"

        sound_ep = next(e for e in episodes if "音色" in e.title)
        assert sound_ep.message_count == 2


@pytest.mark.asyncio
async def test_fine_grained_distinction_between_song_request_and_chord_editing():
    """
    Verifies that "求周杰伦歌曲" and "自定义修改伴奏和弦" are kept strictly separate
    as 2 distinct Topic Clusters with multi-level tags.
    """
    ep1 = EpisodeItemView(
        id="ep_1",
        conversation_id="conv_1",
        conversation_name="群聊1",
        title="建议官方曲谱乐库加快热门流行新歌与经典曲目扩充",
        summary="群成员多位用户交流官方曲谱乐库覆盖现状，强烈建议加快扩充周杰伦等热门流行歌曲与经典伴奏资源，提升官方乐库的曲目丰富度与搜索满意度。",
        category_hint="sheet_music:sheet_request:流行新歌版权求谱",
        started_at="2026-08-25T14:00:00",
        ended_at="2026-08-25T14:10:00",
        message_count=5,
        participants=["琴友A", "琴友B"],
    )

    ep2 = EpisodeItemView(
        id="ep_2",
        conversation_id="conv_2",
        conversation_name="群聊2",
        title="探讨伴奏曲谱和弦自定义编辑与第三方乐谱文件导入",
        summary="群成员交流探讨伴奏和弦配置与弹唱体验，提出希望App支持用户自由修改伴奏和弦走向，并支持通过第三方文件导入自定义乐谱进行个性化演奏。",
        category_hint="sheet_music:sheet_manual_create:和弦自定义编辑与导入",
        started_at="2026-08-26T10:00:00",
        ended_at="2026-08-26T10:15:00",
        message_count=8,
        participants=["琴友C", "琴友D"],
    )

    clusters = EpisodeDeduplicator.deduplicate_episodes([ep1, ep2], sort_by="latest")

    # They should NOT be collapsed into 1 topic! They must be 2 distinct clusters!
    assert len(clusters) == 2, "Song Request and Chord Editing must form 2 distinct topic clusters"

    cluster_titles = [c.canonical_title for c in clusters]
    assert any("热门流行新歌" in t for t in cluster_titles)
    assert any("和弦自定义编辑" in t or "自建" in t or "制谱" in t for t in cluster_titles)

    # Check multi-level tags
    c1 = next(c for c in clusters if "热门流行新歌" in c.canonical_title)
    assert "曲谱与乐库" in c1.tags
    assert len(c1.tags) >= 2


@pytest.mark.asyncio
async def test_timeline_sorting_modes():
    """
    Tests sorting by latest, first_seen, and frequency.
    """
    c_early = EpisodeItemView(
        id="ep_early",
        conversation_id="c1",
        title="反馈App界面字号偏小与暗黑模式",
        summary="反馈字体太小看不清谱面",
        category_hint="ui_ux:ui_font:字号清晰度",
        started_at="2026-08-20T10:00:00",
        ended_at="2026-08-20T10:30:00",
        message_count=20,
    )

    c_late = EpisodeItemView(
        id="ep_late",
        conversation_id="c1",
        title="探讨扩展音色卡加载识别与风格包体验",
        summary="探讨音色卡插拔识别与风格包体验",
        category_hint="sound_preset:sound_expansion_card:扩展卡",
        started_at="2026-08-26T15:00:00",
        ended_at="2026-08-26T15:40:00",
        message_count=5,
    )

    # 1. Latest sort (default): c_late first
    res_latest = EpisodeDeduplicator.deduplicate_episodes([c_early, c_late], sort_by="latest")
    assert res_latest[0].canonical_title.startswith("【配置与音色】")

    # 2. First seen sort: c_early first
    res_first = EpisodeDeduplicator.deduplicate_episodes([c_early, c_late], sort_by="first_seen")
    assert res_first[0].canonical_title.startswith("【界面与显示】")

    # 3. Frequency sort: c_early first (20 messages vs 5 messages)
    res_freq = EpisodeDeduplicator.deduplicate_episodes([c_early, c_late], sort_by="frequency")
    assert res_freq[0].canonical_title.startswith("【界面与显示】")
    assert res_freq[0].total_messages == 20
