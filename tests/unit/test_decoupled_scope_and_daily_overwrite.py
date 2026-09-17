import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select

from apps.api.routes.pipeline import resolve_scope_time_bounds, _run_episode_segmentation, _run_insight_extraction
from packages.insights.episode_segmenter import EpisodeSegmenter
from packages.model_gateway.mock_adapter import MockModelAdapter
from packages.persistence.db import Base
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
)


def test_resolve_scope_time_bounds():
    # 1. All
    st, et, label = resolve_scope_time_bounds(date_preset="all")
    assert st is None
    assert et is None
    assert "全部" in label

    # 2. Today
    st, et, label = resolve_scope_time_bounds(date_preset="today")
    assert st is not None and et is not None
    assert st.hour == 0 and et.hour == 23

    # 3. Yesterday
    st, et, label = resolve_scope_time_bounds(date_preset="yesterday")
    assert st is not None and et is not None
    assert st.date() == (datetime.now() - timedelta(days=1)).date()

    # 4. Custom
    st, et, label = resolve_scope_time_bounds(date_preset="custom", start_date="2026-08-20", end_date="2026-08-25")
    assert st is not None and et is not None
    assert st.year == 2026 and st.month == 8 and st.day == 20
    assert et.year == 2026 and et.month == 8 and et.day == 25


@pytest.mark.asyncio
async def test_scoped_segmentation_and_daily_overwrite(tmp_path):
    test_db_path = tmp_path / "test_scope_overwrite.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path}", echo=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        # Create Source Root & File
        root = SourceRoot(id="r1", workspace_id="default", display_name="root", root_path="/test")
        session.add(root)
        sfile = SourceFile(id="f1", workspace_id="default", source_root_id=root.id, relative_path="chat.txt", file_kind="txt", size_bytes=100, sha256="abc")
        session.add(sfile)

        # Create two Conversations: conv1 & conv2
        conv1 = Conversation(id="conv1", workspace_id="default", display_name="吉他交流1群", source_type="wechat_archive", source_conversation_id="conv1")
        conv2 = Conversation(id="conv2", workspace_id="default", display_name="吉他交流2群", source_type="wechat_archive", source_conversation_id="conv2")
        session.add_all([conv1, conv2])

        p1 = Participant(id="p1", workspace_id="default", stable_anonymous_key="anon_p1", display_label="用户A", metadata_json={})
        session.add(p1)

        # conv1 has messages on Day 1 (2026-08-20) and Day 2 (2026-08-21)
        d1_time = datetime(2026, 8, 20, 10, 0, 0)
        d2_time = datetime(2026, 8, 21, 10, 0, 0)

        # Day 1 messages for conv1
        m1 = Message(id="m1", workspace_id="default", conversation_id=conv1.id, participant_id=p1.id, source_file_id=sfile.id, raw_text="Day 1 琴键手感偏硬", normalized_text="Day 1 琴键手感偏硬", source_record_hash="h1", sent_at=d1_time, source_sequence=1)
        # Day 2 messages for conv1
        m2 = Message(id="m2", workspace_id="default", conversation_id=conv1.id, participant_id=p1.id, source_file_id=sfile.id, raw_text="Day 2 蓝牙经常断连", normalized_text="Day 2 蓝牙经常断连", source_record_hash="h2", sent_at=d2_time, source_sequence=2)

        # conv2 message on Day 1
        m3 = Message(id="m3", workspace_id="default", conversation_id=conv2.id, participant_id=p1.id, source_file_id=sfile.id, raw_text="conv2 伴奏音量小", normalized_text="conv2 伴奏音量小", source_record_hash="h3", sent_at=d1_time, source_sequence=1)

        session.add_all([m1, m2, m3])
        await session.commit()

        mock_gateway = AsyncMock()
        mock_provider = MockModelAdapter()
        mock_gateway.get_text_provider.return_value = mock_provider
        segmenter = EpisodeSegmenter(session=session, model_gateway=mock_gateway)

        # Test 1: Run segmentation ONLY for Day 1 on conv1
        st_d1 = datetime(2026, 8, 20, 0, 0, 0)
        et_d1 = datetime(2026, 8, 20, 23, 59, 59)
        episodes_d1 = await segmenter.segment_conversation(
            conversation_id=conv1.id,
            start_time=st_d1,
            end_time=et_d1,
            overwrite_existing=True,
            force_mock=True,
        )
        assert len(episodes_d1) == 1
        assert episodes_d1[0].conversation_id == "conv1"
        assert episodes_d1[0].started_at.day == 20

        # Test 2: Overwrite Day 1 for conv1 - running again should overwrite, not duplicate!
        episodes_d1_second_run = await segmenter.segment_conversation(
            conversation_id=conv1.id,
            start_time=st_d1,
            end_time=et_d1,
            overwrite_existing=True,
            force_mock=True,
        )
        assert len(episodes_d1_second_run) == 1

        # Verify DB only has 1 episode for conv1
        db_eps = (await session.execute(select(Episode).where(Episode.conversation_id == conv1.id))).scalars().all()
        assert len(db_eps) == 1

        # Test 3: Run Day 2 for conv1 - should incrementally add Day 2 while keeping Day 1
        st_d2 = datetime(2026, 8, 21, 0, 0, 0)
        et_d2 = datetime(2026, 8, 21, 23, 59, 59)
        episodes_d2 = await segmenter.segment_conversation(
            conversation_id=conv1.id,
            start_time=st_d2,
            end_time=et_d2,
            overwrite_existing=True,
            force_mock=True,
        )
        assert len(episodes_d2) == 1

        # Verify DB now has 2 episodes for conv1 (Day 1 + Day 2 partitioned by date)
        db_eps_all = (await session.execute(select(Episode).where(Episode.conversation_id == conv1.id).order_by(Episode.started_at.asc()))).scalars().all()
        assert len(db_eps_all) == 2
        assert db_eps_all[0].started_at.day == 20
        assert db_eps_all[1].started_at.day == 21
