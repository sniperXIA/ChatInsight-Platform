from datetime import datetime
from pathlib import Path
from PIL import Image
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.abc_sync.abc_sync_service import ABCSyncService
from packages.analytics.report_generator import ReportGenerator
from packages.clustering.cluster_manager import ClusterManager
from packages.clustering.contracts import JudgeDecision
from packages.insights.episode_segmenter import EpisodeSegmenter
from packages.insights.insight_extractor import InsightExtractor
from packages.media_pipeline.image_pipeline import ImageEnrichmentPipeline
from packages.persistence.db import Base
from packages.persistence.models import (
    Conversation,
    DomainEventOutbox,
    Episode,
    Insight,
    MediaAsset,
    Message,
    SourceFile,
    SourceRoot,
    Topic,
    Workspace,
)
from packages.persistence.repositories.registry import RepositoryRegistry
from packages.search.hybrid_search import HybridSearchEngine
from packages.search.research_assistant import ResearchAssistant


@pytest.mark.asyncio
async def test_full_chatinsight_end_to_end_lifecycle(tmp_path: Path):
    """Complete End-to-End Pipeline test validating all 10 scenario stories in one workflow."""
    # Create test dummy image on disk
    dummy_img_path = tmp_path / "test.jpg"
    img = Image.new("RGB", (100, 100), color=(73, 109, 137))
    img.save(dummy_img_path)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)

        # ----------------------------------------------------
        # Phase 0 & 1: Workspace, Ingestion, Messages & Media
        # ----------------------------------------------------
        ws = await repo.get_or_create_default_workspace()
        root = await repo.get_or_create_source_root(ws.id, "测试根目录", str(tmp_path))
        sf = await repo.upsert_source_file(ws.id, root.id, "test.txt", "txt", 1024, "hash_txt_001")
        sf_img = await repo.upsert_source_file(ws.id, root.id, "test.jpg", "image", 2048, "hash_img_001")

        conv = await repo.upsert_conversation(ws.id, "wechat_archive", "conv_e2e", "LiberLive 核心玩家群")
        p1 = await repo.get_or_create_participant(ws.id, b"salt123", "玩家小李", role_hint="user")
        p_support = await repo.get_or_create_participant(ws.id, b"salt123", "官方客服小凡", role_hint="support")

        media = await repo.upsert_media_asset(ws.id, sf_img.id, "image", "image/jpeg", "sha_img_e2e", 2048)

        msg1 = await repo.upsert_message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id=p1.id,
            source_file_id=sf.id,
            source_message_id="msg_1",
            sequence=1,
            sent_at=datetime(2026, 8, 20, 10, 0, 0),
            raw_text="请问我的扩展音色卡插进去为什么没声音？[图片]",
            source_record_hash="hash_m1",
        )
        await repo.upsert_media_link(msg1.id, media.id, 1, "in_line_explicit", 1.0, "confirmed")

        msg2 = await repo.upsert_message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id=p_support.id,
            source_file_id=sf.id,
            source_message_id="msg_2",
            sequence=2,
            sent_at=datetime(2026, 8, 20, 10, 2, 0),
            raw_text="您好，请先关闭琴身电源重新插拔音色卡，若仍有异常可申请售后换新。",
            source_record_hash="hash_m2",
        )
        await session.commit()

        # ----------------------------------------------------
        # Phase 2: Multimodal Media Pipeline
        # ----------------------------------------------------
        img_pipe = ImageEnrichmentPipeline(session)
        enrich_out, enrich_record, _ = await img_pipe.analyze_image_asset(media.id, force_mock=True)
        assert enrich_record.state == "available"
        assert len(enrich_out.ocr_blocks) >= 1
        await session.commit()

        # ----------------------------------------------------
        # Phase 3: Episode Segmentation
        # ----------------------------------------------------
        segmenter = EpisodeSegmenter(session)
        episodes = await segmenter.segment_conversation(conv.id, time_gap_minutes=25, force_mock=True)
        assert len(episodes) >= 1
        target_ep = episodes[0]
        assert target_ep.message_count == 2
        await session.commit()

        # ----------------------------------------------------
        # Phase 3: Insight & Claim Extraction + Review
        # ----------------------------------------------------
        extractor = InsightExtractor(session)
        insights_results = await extractor.extract_insights_from_episode(target_ep.id, force_mock=True)
        assert len(insights_results) >= 1
        insight_obj, factual_check = insights_results[0]
        assert insight_obj.factual_score >= 0.8
        assert insight_obj.state == "draft"

        # Review & Approve
        reviewed_ins = await repo.update_insight_review(
            insight_id=insight_obj.id,
            state="approved",
            reviewed_by="auditor_e2e",
            summary_override="扩展音色卡插入后接触不良无声音",
        )
        assert reviewed_ins.state == "approved"
        await session.commit()

        # ----------------------------------------------------
        # Phase 4: Two-Stage Topic Clustering & Dedup
        # ----------------------------------------------------
        cluster_mgr = ClusterManager(session)
        topic, dec, _ = await cluster_mgr.cluster_insight(reviewed_ins.id, force_mock=True)
        assert topic is not None
        assert topic.feedback_count == 1
        await session.commit()

        # ----------------------------------------------------
        # Phase 4: ABC User Feedback Sync
        # ----------------------------------------------------
        abc_service = ABCSyncService(session)
        sync_ok, err = await abc_service.sync_topic(topic.id)
        assert sync_ok is True
        assert err is None

        # Verify Outbox event recorded
        outbox_events = (await session.execute(select(DomainEventOutbox))).scalars().all()
        assert any(e.event_type == "abc.feedback.synced" for e in outbox_events)
        await session.commit()

        # ----------------------------------------------------
        # Phase 5: Hybrid Search & Research Assistant Q&A
        # ----------------------------------------------------
        search_engine = HybridSearchEngine(session)
        search_res = await search_engine.search(query="音色卡")
        assert search_res.total_hits >= 1

        assistant = ResearchAssistant(session)
        assistant_res = await assistant.answer_question(
            question="社群中有哪些关于音色卡的问题？官方是如何解决的？",
            force_mock=True,
        )
        assert len(assistant_res.citations) >= 1
        assert assistant_res.citations[0].evidence_uri.startswith("chatinsight://")

        # ----------------------------------------------------
        # Phase 5: Analytics Overview & VoC Report Export
        # ----------------------------------------------------
        report_gen = ReportGenerator(session)
        report = await report_gen.generate_report(period="all", period_label="全链路端到端验收周报", force_mock=True)
        assert report.total_feedbacks >= 1
        assert report.total_topics >= 1
        assert len(report.module_distribution) >= 1

        md_output = report_gen.render_markdown(report)
        assert "# 📊 全链路端到端验收周报" in md_output
        assert "扩展音色卡" in md_output
