from datetime import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.persistence.db import Base
from packages.persistence.models import Conversation, Insight, Message, Topic, Workspace
from packages.persistence.repositories.registry import RepositoryRegistry
from packages.search.contracts import SearchFilter
from packages.search.hybrid_search import HybridSearchEngine


@pytest.mark.asyncio
async def test_hybrid_search_scoring_and_filtering():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        # Seed Topic
        t1 = await repo.create_topic(
            workspace_id=ws.id,
            title="扩展音色卡加载异常",
            summary="用户插入扩展卡后设备无法发声",
            module="音色/扩展卡",
            severity="major",
        )
        t2 = await repo.create_topic(
            workspace_id=ws.id,
            title="蓝牙伴奏连接中断",
            summary="App蓝牙连接断开导致伴奏停止",
            module="App/蓝牙连接",
            severity="minor",
        )
        await session.commit()

        search_engine = HybridSearchEngine(session)

        # 1. Search for '音色卡'
        res1 = await search_engine.search(query="音色卡")
        assert res1.total_hits >= 1
        assert any(r.entity_id == t1.id for r in res1.results)

        # 2. Filter by module
        res2 = await search_engine.search(query="连接", filters=SearchFilter(modules=["App/蓝牙连接"]))
        assert len(res2.results) >= 1
        assert res2.results[0].entity_id == t2.id
