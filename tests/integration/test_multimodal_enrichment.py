import os
import pytest
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.importers.batch_importer import BatchImporter
from packages.importers.scanner import DirectoryScanner
from packages.media_pipeline.contracts import ImageEnrichmentOutput, VideoEnrichmentOutput
from packages.media_pipeline.image_pipeline import ImageEnrichmentPipeline
from packages.media_pipeline.video_pipeline import VideoEnrichmentPipeline
from packages.persistence.db import Base
from packages.persistence.models import MediaAsset, MediaEnrichment


@pytest.mark.asyncio
async def test_multimodal_image_and_video_pipeline_mock():
    raw_root = "D:/玩家群聊天信息2"
    if not Path(raw_root).exists():
        pytest.skip(f"Real data directory {raw_root} not found")

    scanner = DirectoryScanner()
    _, batches = scanner.scan_root(raw_root, target_date="20260820", target_group="LiberLive C2 玩家交流群1")
    assert len(batches) == 1

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        importer = BatchImporter(session)
        await importer.import_parsed_batch(batches[0], root_path=raw_root)
        await session.commit()

        # Find imported image and video
        img_stmt = select(MediaAsset).where(MediaAsset.kind == "image").limit(1)
        img_media = (await session.execute(img_stmt)).scalar_one()

        vid_stmt = select(MediaAsset).where(MediaAsset.kind == "video").limit(1)
        vid_media = (await session.execute(vid_stmt)).scalar_one()

        # 1. Test Image Pipeline (Mock mode)
        img_pipe = ImageEnrichmentPipeline(session)
        out_img, enrichment_img, was_cached = await img_pipe.analyze_image_asset(img_media.id, force_mock=True)
        assert was_cached is False
        assert isinstance(out_img, ImageEnrichmentOutput)
        assert len(out_img.ocr_blocks) > 0
        assert enrichment_img.summary is not None
        await session.commit()

        # Test Image Caching
        out_cached, _, was_cached_2 = await img_pipe.analyze_image_asset(img_media.id, force_mock=True)
        assert was_cached_2 is True
        assert out_cached.summary == out_img.summary

        # 2. Test Video Pipeline (Mock mode)
        vid_pipe = VideoEnrichmentPipeline(session)
        out_vid, enrichment_vid, was_cached_vid = await vid_pipe.analyze_video_asset(vid_media.id, force_mock=True)
        assert was_cached_vid is False
        assert isinstance(out_vid, VideoEnrichmentOutput)
        assert len(out_vid.timeline) > 0
        assert enrichment_vid.timeline_json is not None
        await session.commit()


@pytest.mark.asyncio
async def test_live_openrouter_image_analysis():
    """Live test calling OpenRouter API with Qwen-2.5-VL on real image sample."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key or "test" in api_key.lower() or "mock" in api_key.lower() or "nvapi" in api_key.lower() or "fake" in api_key.lower():
        pytest.skip("Valid live OPENROUTER_API_KEY not configured")

    raw_root = "D:/玩家群聊天信息2"
    if not Path(raw_root).exists():
        pytest.skip("Raw archive not found")

    scanner = DirectoryScanner()
    _, batches = scanner.scan_root(raw_root, target_date="20260820", target_group="LiberLive C2 玩家交流群1")
    if not batches:
        pytest.skip("Target batch not found")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        importer = BatchImporter(session)
        await importer.import_parsed_batch(batches[0], root_path=raw_root)
        await session.commit()

        img_stmt = select(MediaAsset).where(MediaAsset.kind == "image").limit(1)
        img_media = (await session.execute(img_stmt)).scalar_one_or_none()
        if not img_media:
            pytest.skip("No image media found")

        img_pipe = ImageEnrichmentPipeline(session)
        output, enrichment, was_cached = await img_pipe.analyze_image_asset(
            img_media.id,
            force_mock=False,
            model_override="qwen/qwen-2.5-vl-72b-instruct",
        )

        assert was_cached is False
        assert isinstance(output, ImageEnrichmentOutput)
        assert len(output.summary) > 0
        print(f"\n[Live VLM Test] Qwen 2.5 VL 72B Result:")
        print(f"  Summary: {output.summary}")
        print(f"  Screen/Scene: {output.screen_or_scene}")
        print(f"  OCR Blocks count: {len(output.ocr_blocks)}")
        if output.ocr_blocks:
            print(f"  OCR Sample: {[b.text for b in output.ocr_blocks[:5]]}")
        print(f"  Entities: {[e.value for e in output.entities]}")
