import os
from datetime import datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.persistence.db import Base
from packages.persistence.models import Conversation, Episode, Insight, Message, Topic
from packages.persistence.repositories.registry import RepositoryRegistry
from packages.search.research_assistant import ResearchAssistant


@pytest.mark.asyncio
async def test_research_assistant_grounded_qa_mock():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        topic = await repo.create_topic(
            workspace_id=ws.id,
            title="扩展音色卡加载无声音",
            summary="社群用户反馈插入扩展音色卡后伴奏没有声音，客服回复需要重新插拔卡槽",
            module="音色/扩展卡",
            severity="major",
        )
        await session.commit()

        assistant = ResearchAssistant(session)
        res = await assistant.answer_question(
            question="请问扩展音色卡的主要问题和客服响应是什么？",
            force_mock=True,
        )

        assert isinstance(res.answer, str)
        assert len(res.citations) >= 1
        assert res.citations[0].evidence_uri.startswith("chatinsight://")
        assert res.confidence >= 0.9


@pytest.mark.asyncio
async def test_live_openrouter_research_assistant():
    """Live test calling OpenRouter DeepSeek/Gemini model for research assistant Q&A."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key or "test" in api_key.lower() or "mock" in api_key.lower() or "nvapi" in api_key.lower() or "fake" in api_key.lower():
        pytest.skip("Valid live OPENROUTER_API_KEY not configured")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        topic = await repo.create_topic(
            workspace_id=ws.id,
            title="扩展音色卡无法识别",
            summary="社群多名玩家反馈LiberLive C2插入扩展音色卡后无声音输出，客服已确认并提供重新插拔卡槽规避方案",
            module="音色/扩展卡",
            severity="major",
        )
        await session.commit()

        assistant = ResearchAssistant(session)
        try:
            res = await assistant.answer_question(
                question="社群中关于音色卡有什么用户反馈？官方客服如何回复的？",
                force_mock=False,
                model_override="deepseek/deepseek-chat",
            )
        except Exception as e:
            if "does not exist" in str(e) or "404" in str(e) or "model_not_found" in str(e):
                pytest.skip(f"Live model not available on endpoint: {e}")
            raise

        assert len(res.answer) > 0
        assert len(res.citations) >= 1
        print("\n[Live Assistant Q&A Test] Response:")
        print(f"  Question: {res.question}")
        print(f"  Answer: {res.answer}")
        print(f"  Citations: {[c.title for c in res.citations]}")
