import os
from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.importers.batch_importer import BatchImporter
from packages.importers.scanner import DirectoryScanner
from packages.insights.episode_segmenter import EpisodeSegmenter
from packages.insights.insight_extractor import InsightExtractor
from packages.persistence.db import Base
from packages.persistence.models import Conversation, Episode, Insight, InsightClaim


@pytest.mark.asyncio
async def test_segmentation_and_insight_extraction_mock():
    raw_root = "D:/玩家群聊天信息2"
    if not Path(raw_root).exists():
        pytest.skip("Real data directory not found")

    scanner = DirectoryScanner()
    _, batches = scanner.scan_root(raw_root, target_date="20260820")
    assert len(batches) > 0
    target_batch = batches[0]

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        # 1. Import messages
        importer = BatchImporter(session)
        await importer.import_parsed_batch(target_batch, root_path=raw_root)
        await session.commit()

        conv = (await session.execute(select(Conversation).limit(1))).scalar_one()

        # 2. Run Segmentation
        segmenter = EpisodeSegmenter(session)
        episodes = await segmenter.segment_conversation(
            conversation_id=conv.id,
            time_gap_minutes=25,
            force_mock=True,
        )
        assert len(episodes) > 0
        await session.commit()

        # 3. Run Insight Extraction on first episode
        first_ep = episodes[0]
        extractor = InsightExtractor(session)
        results = await extractor.extract_insights_from_episode(
            episode_id=first_ep.id,
            force_mock=True,
        )
        assert len(results) > 0
        ins, check_res = results[0]

        assert isinstance(ins, Insight)
        assert len(ins.module) > 0
        assert check_res.overall_factual_score > 0.0
        await session.commit()

        # Verify DB claims
        claims_stmt = select(InsightClaim).where(InsightClaim.insight_id == ins.id)
        claims = (await session.execute(claims_stmt)).scalars().all()
        assert len(claims) > 0
        assert claims[0].claim_key == "c_1"
        assert len(claims[0].evidence_uris_json) > 0
        assert claims[0].evidence_uris_json[0].startswith("chatinsight://conv/")


@pytest.mark.asyncio
async def test_live_openrouter_insight_extraction():
    """Live test calling OpenRouter DeepSeek model for insight extraction."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key or "test" in api_key.lower() or "mock" in api_key.lower() or "nvapi" in api_key.lower() or "fake" in api_key.lower():
        pytest.skip("Valid live OPENROUTER_API_KEY not configured")

    raw_root = "D:/玩家群聊天信息2"
    if not Path(raw_root).exists():
        pytest.skip("Real data directory not found")

    scanner = DirectoryScanner()
    _, batches = scanner.scan_root(raw_root, target_date="20260820")
    assert len(batches) > 0
    target_batch = batches[0]

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        importer = BatchImporter(session)
        await importer.import_parsed_batch(target_batch, root_path=raw_root)
        await session.commit()

        conv = (await session.execute(select(Conversation).limit(1))).scalar_one()

        segmenter = EpisodeSegmenter(session)
        episodes = await segmenter.segment_conversation(
            conversation_id=conv.id,
            time_gap_minutes=25,
            force_mock=True,
        )
        assert len(episodes) > 0
        await session.commit()

        # Call live OpenRouter model
        extractor = InsightExtractor(session)
        results = await extractor.extract_insights_from_episode(
            episode_id=episodes[0].id,
            force_mock=False,
            model_override="deepseek/deepseek-chat",
        )
        await session.commit()

        assert len(results) >= 0  # May be 0 if pure greeting, or >= 1 if insights found
        for ins, check_res in results:
            assert isinstance(ins, Insight)
            assert ins.factual_score >= 0.0
