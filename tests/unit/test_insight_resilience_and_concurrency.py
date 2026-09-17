import asyncio
import json
import pytest
from datetime import datetime
from httpx import Response
from pydantic import BaseModel
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.domain.enums import InsightType, SeverityLevel
from packages.domain.models import generate_id
from packages.insights.contracts import ClaimItem, FactualCheckResult, InsightDraft
from packages.model_gateway.openrouter_adapter import ModelProviderError, OpenRouterAdapter
from packages.observability.analysis_service import AnalysisService
from packages.persistence.models import Base, Conversation, Episode, Insight, InsightClaim
from packages.insights.insight_extractor import InsightExtractor, InsightExtractionBatchOutput
from apps.api.routes.pipeline import _run_insight_extraction


class SampleSchema(BaseModel):
    title: str
    count: int


@pytest.mark.asyncio
async def test_openrouter_adapter_429_backoff_retry_success():
    adapter = OpenRouterAdapter(api_key="fake_key", base_url="https://fake.api.com/v1")

    resp_429 = Response(429, text=json.dumps({"error": "rate limited"}))
    resp_200 = Response(
        200,
        text=json.dumps({
            "choices": [{"message": {"content": json.dumps({"title": "Recovered", "count": 99})}}]
        }),
    )

    with patch("httpx.AsyncClient.post", side_effect=[resp_429, resp_200]):
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            res, usage = await adapter._call_and_parse(
                messages=[{"role": "user", "content": "test"}],
                response_schema=SampleSchema,
                model="test-model",
            )
            assert res.title == "Recovered"
            assert res.count == 99
            mock_sleep.assert_awaited()


@pytest.mark.asyncio
async def test_openrouter_adapter_429_exhausted_raises_status_code():
    adapter = OpenRouterAdapter(api_key="fake_key", base_url="https://fake.api.com/v1")
    resp_429 = Response(429, text=json.dumps({"error": "rate limited upstream"}))

    with patch("httpx.AsyncClient.post", return_value=resp_429):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ModelProviderError) as exc_info:
                await adapter._call_and_parse(
                    messages=[{"role": "user", "content": "test"}],
                    response_schema=SampleSchema,
                    model="test-model",
                )
            assert exc_info.value.status_code == 429
            assert "429" in str(exc_info.value)


@pytest.mark.asyncio
async def test_analysis_service_decouples_network_io():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        service = AnalysisService(session)

        network_called = False

        async def mock_call_fn():
            nonlocal network_called
            network_called = True
            return SampleSchema(title="Instant", count=1), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

        res_obj, run_rec, was_cached = await service.execute_cached(
            workspace_id="ws_1",
            run_type="test_run",
            target_type="episode",
            target_id="ep_1",
            provider="mock",
            model="mock_model",
            prompt_key="key",
            input_data={"test": 1},
            config_data={},
            response_schema=SampleSchema,
            call_fn=mock_call_fn,
        )

        assert network_called is True
        assert res_obj.title == "Instant"
        assert run_rec.state == "succeeded"
        assert was_cached is False


@pytest.mark.asyncio
async def test_insight_extraction_concurrency_and_progress():
    from sqlalchemy.pool import StaticPool
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    
    # Pre-seed 3 episodes
    async with session_maker() as session:
        conv = Conversation(
            id="c_test_1",
            workspace_id="ws_1",
            display_name="测试群聊",
        )
        session.add(conv)
        for i in range(3):
            ep = Episode(
                id=f"ep_test_{i}",
                workspace_id="ws_1",
                conversation_id="c_test_1",
                title=f"用户反馈蓝牙与曲谱问题 #{i}",
                summary=f"测试摘要 #{i}",
                started_at=datetime(2026, 8, 30, 10, i, 0),
                ended_at=datetime(2026, 8, 30, 10, i + 1, 0),
                message_count=2,
            )
            session.add(ep)
        await session.commit()

    async def fake_extract(episode_id, force_mock=False):
        ins = Insight(
            id=f"ins_{episode_id}",
            workspace_id="ws_1",
            episode_id=episode_id,
            insight_type="issue",
            module="蓝牙连接",
            sub_module="配对",
            severity="major",
            summary="蓝牙连接不稳定",
            description="详细描述",
            status_in_chat="reported",
            support_known_status=False,
            factual_score=0.95,
            confidence=0.9,
            tags_json=["蓝牙"],
            analysis_run_id="run_1",
        )
        return [(ins, FactualCheckResult(overall_factual_score=0.95, is_fully_supported=True, claim_results=[]))]

    progress_calls = []

    async def mock_progress_callback(processed: int, total: int, label: str, log_item: any):
        progress_calls.append((processed, total, label))

    with patch("apps.api.routes.pipeline.get_session_context", side_effect=session_maker):
        with patch.object(InsightExtractor, "extract_insights_from_episode", side_effect=fake_extract):
            async with session_maker() as session:
                summary, insights = await _run_insight_extraction(
                    session=session,
                    force_mock=True,
                    max_concurrency=2,
                    progress_callback=mock_progress_callback,
                )

                assert summary.status == "success"
                assert summary.success_count == 3
                assert summary.item_count == 3
                assert len(progress_calls) == 3
                assert progress_calls[-1][0] == 3
                assert progress_calls[-1][1] == 3


@pytest.mark.asyncio
async def test_insight_extraction_circuit_breaker_on_consecutive_429():
    from sqlalchemy.pool import StaticPool
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    
    # Pre-seed 6 episodes
    async with session_maker() as session:
        conv = Conversation(id="c_test_cb", workspace_id="ws_1", display_name="测试群聊")
        session.add(conv)
        for i in range(6):
            ep = Episode(
                id=f"ep_cb_{i}",
                workspace_id="ws_1",
                conversation_id="c_test_cb",
                title=f"限流熔断测试 #{i}",
                summary="测试",
                started_at=datetime(2026, 8, 30, 12, i, 0),
                ended_at=datetime(2026, 8, 30, 12, i + 1, 0),
                message_count=1,
            )
            session.add(ep)
        await session.commit()

    # Mock InsightExtractor to raise 429 rate limit errors
    mock_err = ModelProviderError("Model API returned error (429): rate limited", status_code=429)

    with patch("apps.api.routes.pipeline.get_session_context", side_effect=session_maker):
        with patch.object(InsightExtractor, "extract_insights_from_episode", side_effect=mock_err):
            async with session_maker() as session:
                summary, insights = await _run_insight_extraction(
                    session=session,
                    force_mock=False,
                    max_concurrency=1,
                )

                assert summary.status in ("error", "warning")
                assert "429" in (summary.error_summary or "")
                assert "熔断保护" in summary.details
                # Should have broken after 3 consecutive failures rather than running all 6!
                assert summary.error_count <= 4
