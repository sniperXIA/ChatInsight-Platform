from datetime import datetime, timedelta
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.insights.episode_segmenter import EpisodeSegmenter
from packages.persistence.db import Base
from packages.persistence.models import Conversation, Message, Participant, Workspace
from packages.persistence.repositories.registry import RepositoryRegistry


@pytest.mark.asyncio
async def test_episode_segmentation_by_time_gap():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()
        conv = await repo.upsert_conversation(
            workspace_id=ws.id,
            source_type="wechat_archive",
            source_conversation_id="conv_test_seg",
            display_name="测试群聊",
        )

        sf = await repo.upsert_source_file(
            workspace_id=ws.id,
            source_root_id="dummy_root",
            relative_path="test.txt",
            file_kind="chat_txt",
            size_bytes=100,
            sha256="abc12345",
        )

        # Insert 3 messages in Cluster 1 (08:00, 08:05, 08:10)
        t0 = datetime(2026, 8, 20, 8, 0, 0)
        for i in range(3):
            await repo.upsert_message(
                workspace_id=ws.id,
                conversation_id=conv.id,
                participant_id=None,
                source_file_id=sf.id,
                source_message_id=f"msg_1_{i}",
                sequence=i + 1,
                sent_at=t0 + timedelta(minutes=i * 5),
                raw_text=f"求加周杰伦七里香曲谱伴奏 {i}",
                source_record_hash=f"hash_1_{i}",
            )

        # Insert 2 messages in Cluster 2 (09:30, 09:35 - 80 min gap)
        t1 = datetime(2026, 8, 20, 9, 30, 0)
        for i in range(2):
            await repo.upsert_message(
                workspace_id=ws.id,
                conversation_id=conv.id,
                participant_id=None,
                source_file_id=sf.id,
                source_message_id=f"msg_2_{i}",
                sequence=4 + i,
                sent_at=t1 + timedelta(minutes=i * 5),
                raw_text=f"蓝牙配对搜不到连接延迟与断开 {i}",
                source_record_hash=f"hash_2_{i}",
            )

        await session.commit()

        segmenter = EpisodeSegmenter(session)
        episodes = await segmenter.segment_conversation(
            conversation_id=conv.id,
            time_gap_minutes=25,
            force_mock=True,
        )

        assert len(episodes) == 2
        assert episodes[0].message_count == 3
        assert episodes[1].message_count == 2
        assert "曲谱" in episodes[0].title or "求谱" in episodes[0].title
        assert "蓝牙" in episodes[1].title
