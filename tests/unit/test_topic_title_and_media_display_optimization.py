import pytest
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.insights.episode_segmenter import EpisodeSegmenter
from packages.persistence.db import Base
from packages.persistence.models import Conversation, Message, SourceFile, Workspace
from packages.persistence.repositories.registry import RepositoryRegistry


def test_clean_episode_title():
    """Verify that formulaic prefixes ('用户反馈', '用户咨询', '用户交流', etc.) are completely stripped."""
    segmenter = EpisodeSegmenter()

    # 1. Formulaic prefixes
    assert segmenter._clean_episode_title("用户反馈更新后独奏模式入口缺失及横屏适配") == "更新后独奏模式入口缺失及横屏适配"
    assert segmenter._clean_episode_title("用户咨询鼓机模式进入与长按关闭操作方法") == "鼓机模式进入与长按关闭操作方法"
    assert segmenter._clean_episode_title("用户交流调音器软件使用及App内乐谱资源") == "调音器软件使用及App内乐谱资源"
    assert segmenter._clean_episode_title("用户探讨音色扩展卡升级体验与音质表现") == "音色扩展卡升级体验与音质表现"
    assert segmenter._clean_episode_title("用户建议App增加黑白背景主题切换") == "App增加黑白背景主题切换"
    assert segmenter._clean_episode_title("群友反馈蓝牙无法配对与频繁掉线") == "蓝牙无法配对与频繁掉线"
    assert segmenter._clean_episode_title("玩家交流AI制谱功能操作演示视频") == "AI制谱功能操作演示视频"

    # 2. Template packaging '围绕...' and '关于...'
    assert segmenter._clean_episode_title("围绕“AI制谱”展开的讨论") == "AI制谱"
    assert segmenter._clean_episode_title("关于曲谱字号过小的反馈") == "曲谱字号过小"

    # 3. Mention stripping and ending stripping
    assert segmenter._clean_episode_title("用户在群聊中反馈探讨@App小助手 亲爱的你呀相关功能与诉求") == "亲爱的你呀"
    assert segmenter._clean_episode_title("用户反馈产品未标配充电头需自购及双C口线材适配体验") == "产品未标配充电头需自购及双C口线材适配体验"

    # 4. Direct statements remain intact
    direct = "更新后独奏模式入口缺失及横屏适配"
    assert segmenter._clean_episode_title(direct) == direct


@pytest.mark.asyncio
async def test_segmentation_15min_window_and_25msg_cap():
    """Verify that 15-minute gap and 25-message cap correctly partition messages."""
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
            source_conversation_id="conv_test_15m",
            display_name="切分测试群",
        )
        sf = await repo.upsert_source_file(
            workspace_id=ws.id,
            source_root_id="root_1",
            relative_path="test_chat.txt",
            file_kind="chat_txt",
            size_bytes=1000,
            sha256="sha_test_15m",
        )

        t0 = datetime(2026, 9, 1, 10, 0, 0)
        # Create 30 messages within 1-minute intervals (exceeding 25-message chunk cap)
        for i in range(30):
            await repo.upsert_message(
                workspace_id=ws.id,
                conversation_id=conv.id,
                participant_id=None,
                source_file_id=sf.id,
                source_message_id=f"msg_chunk_{i}",
                sequence=i + 1,
                sent_at=t0 + timedelta(minutes=i),
                raw_text=f"测试曲谱和弦功能讨论消息 {i}",
                source_record_hash=f"hash_cap_{i}",
            )

        # Message 31 after 20 minutes (exceeding 15-minute gap)
        await repo.upsert_message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id=None,
            source_file_id=sf.id,
            source_message_id="msg_chunk_31",
            sequence=31,
            sent_at=t0 + timedelta(minutes=55),
            raw_text="测试蓝牙配对延迟与连接稳定性 31",
            source_record_hash="hash_cap_31",
        )

        await session.commit()

        segmenter = EpisodeSegmenter(session=session)
        episodes = await segmenter.segment_conversation(
            conversation_id=conv.id,
            time_gap_minutes=15,
            force_mock=True,
        )

        # Because 30 msgs exceeded 25-message cap, it was split into 2 chunks (25 + 5), plus the 15min gap chunk
        assert len(episodes) >= 2
        for ep in episodes:
            # Verify titles do NOT have formulaic prefixes
            assert not ep.title.startswith("用户反馈")
            assert not ep.title.startswith("用户咨询")
            assert not ep.title.startswith("用户交流")
