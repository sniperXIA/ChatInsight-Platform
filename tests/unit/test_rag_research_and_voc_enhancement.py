import pytest
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.analytics.report_generator import ReportGenerator
from packages.persistence.db import Base
from packages.persistence.models import (
    Conversation,
    Episode,
    Insight,
    Message,
    MessageContextChunk,
    Participant,
    SourceFile,
    SourceRoot,
    Topic,
)
from packages.persistence.repositories.registry import RepositoryRegistry
from packages.retrieval.vector_service import VectorService
from packages.search.hybrid_search import HybridSearchEngine
from packages.search.research_assistant import ResearchAssistant


@asynccontextmanager
async def create_test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_vector_service_message_chunk_indexing():
    """Verify sliding-window chunk indexing for conversation messages."""
    async with create_test_session() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        root = SourceRoot(id="r1", workspace_id=ws.id, display_name="root", root_path="/test")
        session.add(root)
        sfile = SourceFile(
            id="f1",
            workspace_id=ws.id,
            source_root_id=root.id,
            relative_path="sample.txt",
            file_kind="wechat",
            size_bytes=100,
            sha256="abc",
        )
        session.add(sfile)

        conv = Conversation(
            workspace_id=ws.id,
            source_type="wechat_archive",
            source_conversation_id="conv_test_chunk",
            display_name="C2测试群",
        )
        session.add(conv)
        await session.flush()

        p1 = Participant(
            workspace_id=ws.id,
            stable_anonymous_key="user_1",
            display_label="琴友小张",
        )
        p2 = Participant(
            workspace_id=ws.id,
            stable_anonymous_key="staff_1",
            display_label="客服小李",
            is_internal=True,
        )
        session.add_all([p1, p2])
        await session.flush()

        # Seed 4 messages
        messages = [
            Message(
                workspace_id=ws.id,
                conversation_id=conv.id,
                participant_id=p1.id,
                source_file_id=sfile.id,
                source_sequence=1,
                source_record_hash="hash_1",
                raw_text="每次打开旅行锁开机，吉他都无法加载扩展卡",
                normalized_text="每次打开旅行锁开机，吉他都无法加载扩展卡",
                sent_at=datetime(2026, 9, 1, 10, 0),
            ),
            Message(
                workspace_id=ws.id,
                conversation_id=conv.id,
                participant_id=p2.id,
                source_file_id=sfile.id,
                source_sequence=2,
                source_record_hash="hash_2",
                raw_text="收到反馈，请问您的扩展卡是指示灯闪烁还是常亮？",
                normalized_text="收到反馈，请问您的扩展卡是指示灯闪烁还是常亮？",
                sent_at=datetime(2026, 9, 1, 10, 1),
            ),
            Message(
                workspace_id=ws.id,
                conversation_id=conv.id,
                participant_id=p1.id,
                source_file_id=sfile.id,
                source_sequence=3,
                source_record_hash="hash_3",
                raw_text="常亮但是手机App里显示未插卡，必须拔掉重插才行",
                normalized_text="常亮但是手机App里显示未插卡，必须拔掉重插才行",
                sent_at=datetime(2026, 9, 1, 10, 2),
            ),
            Message(
                workspace_id=ws.id,
                conversation_id=conv.id,
                participant_id=p2.id,
                source_file_id=sfile.id,
                source_sequence=4,
                source_record_hash="hash_4",
                raw_text="已为您登记，建议先在关机状态下解锁再开机",
                normalized_text="已为您登记，建议先在关机状态下解锁再开机",
                sent_at=datetime(2026, 9, 1, 10, 3),
            ),
        ]
        session.add_all(messages)
        await session.flush()

        vs = VectorService(session)
        chunks = await vs.index_message_chunks(conversation_id=conv.id, messages=messages, window_size=3, force_mock=True)
        assert len(chunks) >= 1
        assert chunks[0].conversation_id == conv.id
        assert chunks[0].embedding is not None
        assert "琴友小张" in chunks[0].context_text
        assert "客服小李" in chunks[0].context_text


@pytest.mark.asyncio
async def test_dual_channel_hybrid_search_zero_lexical_recall():
    """
    Verify that when lexical keyword matching yields 0 hits due to synonym/colloquial phrasing,
    the dense RAG vector channel rescues the query and returns high-relevance items!
    """
    async with create_test_session() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        # Seed Topic with technical wording
        t1 = await repo.create_topic(
            workspace_id=ws.id,
            title="硬件启动时序异常导致扩展卡识别中断",
            summary="设备在特定解锁开机时序下，SPI总线枚举扩展存储卡失败。",
            module="配置与音色 · 旋律音色与音色卡",
            severity="major",
        )
        await repo.update_topic_stats(t1.id, increment_feedback=5, increment_users=3)

        search_engine = HybridSearchEngine(session)

        # User asks in colloquial words that have virtually 0 token overlap with the formal title
        query = "为什么开机拔掉重插才能认出音色卡？"

        res = await search_engine.search(query=query, limit=5)
        assert res.total_hits >= 1
        # Dense channel should have found t1
        found_ids = [r.entity_id for r in res.results]
        assert t1.id in found_ids


@pytest.mark.asyncio
async def test_research_assistant_grounded_with_rag_chunks():
    """Verify Research Assistant generates citations and answers using RAG-recalled evidence."""
    async with create_test_session() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        conv = Conversation(
            workspace_id=ws.id,
            source_type="wechat_archive",
            source_conversation_id="conv_ra",
            display_name="研发展开群",
        )
        session.add(conv)
        await session.flush()

        ep = Episode(
            workspace_id=ws.id,
            conversation_id=conv.id,
            title="扩展卡时序问题",
            summary="讨论旅行锁开机扩展卡识别失败",
            started_at=datetime(2026, 9, 1, 10, 0),
            ended_at=datetime(2026, 9, 1, 10, 30),
        )
        session.add(ep)
        await session.flush()

        # Seed sample topic and insight
        t1 = await repo.create_topic(
            workspace_id=ws.id,
            title="旅行锁开机扩展卡加载失败",
            summary="社群反馈在启用旅行锁后开机，扩展卡无法正确加载，需重新插拔。",
            module="配置与音色",
            severity="major",
        )
        ins1 = Insight(
            workspace_id=ws.id,
            episode_id=ep.id,
            summary="[BUG] 旅行锁开机扩展卡识别失败",
            description="[场景/位置] 开启旅行锁后开机 [具体现象] 扩展卡无法读取 [影响程度] 严重影响演奏 [环境] 固件v1.2",
            module="配置与音色",
            severity="major",
            insight_type="product_issue",
            confidence=0.92,
        )
        session.add(ins1)
        await session.flush()

        assistant = ResearchAssistant(session)
        response = await assistant.answer_question(
            question="社群中关于旅行锁和扩展卡有哪些问题？",
            force_mock=True,
        )
        assert response is not None
        assert len(response.citations) >= 1
        assert len(response.requirements) >= 1
        assert response.confidence > 0.5
        assert response.from_history is False


@pytest.mark.asyncio
async def test_voc_report_rag_grounded_executive_summary_and_recommendations():
    """Verify VoC report generator incorporates RAG highlights and dynamic actionable recommendations."""
    async with create_test_session() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        conv = Conversation(
            workspace_id=ws.id,
            source_type="wechat_archive",
            source_conversation_id="conv_voc",
            display_name="体验反馈群",
        )
        session.add(conv)
        await session.flush()

        now_dt = datetime.now()
        ep = Episode(
            workspace_id=ws.id,
            conversation_id=conv.id,
            title="扩展卡开机异常讨论",
            summary="社群内热议旅行锁开机后扩展卡无法读取",
            started_at=now_dt - timedelta(days=2),
            ended_at=now_dt - timedelta(days=2) + timedelta(minutes=30),
        )
        session.add(ep)
        await session.flush()

        # Seed critical topic and insight
        t1 = await repo.create_topic(
            workspace_id=ws.id,
            title="旅行锁开机扩展卡识别异常",
            summary="开启旅行锁后开机，扩展卡无法正确加载",
            module="配置与音色 · 旋律音色与音色卡",
            severity="major",
        )
        await repo.update_topic_stats(t1.id, increment_feedback=6, increment_users=4)

        ins1 = Insight(
            workspace_id=ws.id,
            episode_id=ep.id,
            summary="[BLOCKER] 启用旅行锁开机致扩展卡识别失败",
            description="[具体现象] 按住旅行锁开机后扩展卡无法读取 [建议] 研发修复时序",
            module="配置与音色",
            severity="blocker",
            insight_type="product_issue",
            priority="P0",
            confidence=0.95,
            created_at=now_dt - timedelta(days=2),
        )
        session.add(ins1)
        await session.flush()

        generator = ReportGenerator(session)
        hv = await generator.get_high_value_content(period="7d", force_mock=True)

        # 1. Check RAG highlights populated
        assert hasattr(hv, "rag_evidence_highlights")
        assert isinstance(hv.rag_evidence_highlights, list)

        # 2. Check dynamic recommendations tailored to the critical insight
        assert len(hv.key_recommendations) >= 3
        rec_text = " ".join(hv.key_recommendations)
        assert "高危排期" in rec_text
        assert "旅行锁" in rec_text or "扩展卡" in rec_text

        # 3. Check executive summary dynamically references critical insight
        assert hv.ai_executive_summary is not None
        assert "旅行锁" in hv.ai_executive_summary or "配置与音色" in hv.ai_executive_summary

        # 4. Check Markdown rendering contains RAG tracing or executive strategy
        report = await generator.generate_report(period="7d", force_mock=True)
        md = generator.render_markdown(report)
        assert "## 1. 核心指标概览" in md
        assert "扩展卡开机异常讨论" in md or "旅行锁开机扩展卡识别异常" in md
        assert "管理层战略综述与行动建议" in md
