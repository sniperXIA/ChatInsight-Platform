from datetime import datetime
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.clustering.cluster_manager import ClusterManager
from packages.clustering.contracts import JudgeDecision
from packages.persistence.db import Base
from packages.persistence.models import Episode, Insight, Topic, TopicInsightLink
from packages.persistence.repositories.registry import RepositoryRegistry


@pytest.mark.asyncio
async def test_two_stage_topic_clustering_and_deduplication():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()
        conv = await repo.upsert_conversation(ws.id, "wechat_archive", "c1", "交流群")
        ep = await repo.create_episode(
            ws.id, conv.id, "话题1", "摘要1", "general", datetime.now(), datetime.now(), [], [], []
        )

        # 1. Create first insight
        ins1 = await repo.create_insight(
            workspace_id=ws.id,
            episode_id=ep.id,
            insight_type="issue",
            module="音色/扩展卡",
            summary="扩展音色卡插入后无声音",
            description="用户反馈插入卡槽后没有声音输出",
        )
        await session.commit()

        # Cluster Insight 1 -> Should create Topic 1
        manager = ClusterManager(session)
        t1, dec1, _ = await manager.cluster_insight(ins1.id, force_mock=True)
        assert dec1 == JudgeDecision.DISTINCT_ISSUE
        assert t1.feedback_count == 1
        await session.commit()

        # 2. Create second matching insight (same issue)
        ins2 = await repo.create_insight(
            workspace_id=ws.id,
            episode_id=ep.id,
            insight_type="issue",
            module="音色/扩展卡",
            summary="音色卡扩展包无法发声",
            description="琴身插上音色卡后伴奏没有声音",
        )
        await session.commit()

        # Cluster Insight 2 -> Should merge into Topic 1 (feedback_count -> 2)
        t2, dec2, _ = await manager.cluster_insight(ins2.id, force_mock=True)
        assert dec2 == JudgeDecision.SAME_ISSUE
        assert t2.id == t1.id
        assert t2.feedback_count == 2
        await session.commit()

        # 3. Create third distinct insight (different module)
        ins3 = await repo.create_insight(
            workspace_id=ws.id,
            episode_id=ep.id,
            insight_type="feature_request",
            module="伴奏/曲谱",
            summary="希望支持用户自定义曲谱导入",
            description="用户建议增加自制曲谱功能",
        )
        await session.commit()

        # Cluster Insight 3 -> Should create Topic 2
        t3, dec3, _ = await manager.cluster_insight(ins3.id, force_mock=True)
        assert dec3 == JudgeDecision.DISTINCT_ISSUE
        assert t3.id != t1.id
        assert t3.module == "伴奏/曲谱"
        await session.commit()

        # 4. Verify Total Topics count in DB = 2
        topics_res = await session.execute(select(Topic))
        all_topics = topics_res.scalars().all()
        assert len(all_topics) == 2
