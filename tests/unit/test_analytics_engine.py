from datetime import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.analytics.report_generator import ReportGenerator
from packages.persistence.db import Base
from packages.persistence.repositories.registry import RepositoryRegistry


@pytest.mark.asyncio
async def test_analytics_report_generator_and_markdown_rendering():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        # Seed 2 Topics
        t1 = await repo.create_topic(
            workspace_id=ws.id,
            title="扩展音色卡问题",
            summary="无法正常识别",
            module="音色/扩展卡",
            severity="major",
        )
        await repo.update_topic_stats(t1.id, increment_feedback=4, increment_users=3)

        t2 = await repo.create_topic(
            workspace_id=ws.id,
            title="蓝牙配对慢",
            summary="搜索耗时较长",
            module="App/蓝牙连接",
            severity="minor",
        )
        await repo.update_topic_stats(t2.id, increment_feedback=1, increment_users=1)

        await session.commit()

        generator = ReportGenerator(session)
        report = await generator.generate_report(period_label="测试周期报告", force_mock=True)

        assert report.total_feedbacks == 7  # (1+4) + (1+1)
        assert report.total_topics == 2
        assert len(report.module_distribution) == 2
        assert report.module_distribution[0].category == "音色/扩展卡"
        assert len(report.key_recommendations) > 0

        # Markdown render test
        md = generator.render_markdown(report)
        assert "# 📊 测试周期报告" in md
        assert "## 1. 核心指标概览" in md
        assert "音色/扩展卡" in md
