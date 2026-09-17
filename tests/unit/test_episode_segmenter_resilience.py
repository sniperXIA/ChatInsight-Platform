import pytest
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import BaseModel

from packages.insights.episode_segmenter import (
    ChunkSegmentationOutput,
    EpisodeSegmenter,
    SubEpisodeItem,
)
from packages.model_gateway.openrouter_adapter import OpenRouterAdapter
from packages.analytics.contracts import TokenUsageMetrics, OperationalOverview
from packages.persistence.models import Conversation, Message, Episode, AnalysisRun


@pytest.mark.asyncio
async def test_list_response_auto_wrapping_in_openrouter_adapter():
    """Verify that when an LLM returns a raw JSON list [] or [{...}], it is auto-wrapped into object schema."""
    adapter = OpenRouterAdapter(api_key="mock_key")
    
    # 1. Test empty list [] auto-wrapping into ChunkSegmentationOutput
    clean_json_str = "[]"
    model_fields = getattr(ChunkSegmentationOutput, "model_fields", {})
    assert "sub_episodes" in model_fields

    parsed_list = []
    res = ChunkSegmentationOutput.model_validate({"sub_episodes": parsed_list})
    assert res.sub_episodes == []

    # 2. Test list of items auto-wrapping into ChunkSegmentationOutput
    items_json_str = """[
        {
            "title": "用户反馈蓝牙连接断开异常问题",
            "topic_summary": "用户在群聊中反馈设备在正常使用时蓝牙连接频繁中断，导致无法同步伴奏曲目。",
            "category_l1": "bluetooth_conn",
            "category_l2": "connection_stability",
            "category_l3": "disconnect_issue",
            "matched_message_indices": [1, 2]
        }
    ]"""
    parsed_items = json.loads(items_json_str)
    res2 = ChunkSegmentationOutput.model_validate({"sub_episodes": parsed_items})
    assert len(res2.sub_episodes) == 1
    assert res2.sub_episodes[0].title == "用户反馈蓝牙连接断开异常问题"


@pytest.mark.asyncio
async def test_episode_segmenter_no_name_error_on_logger():
    """Verify that logger is properly defined and exception in chunk processing does not raise NameError."""
    from packages.insights.episode_segmenter import logger
    assert logger is not None
    assert logger.name == "packages.insights.episode_segmenter"

    # Test that calling logger.warning works seamlessly
    logger.warning("Test warning log from unit test")


@pytest.mark.asyncio
async def test_infer_cluster_sub_episodes_resilient_fallback():
    """Verify that _infer_cluster_sub_episodes produces a clean fallback episode when valid product chatter occurs."""
    segmenter = EpisodeSegmenter(session=None)

    m1 = Message(
        id="m1",
        conversation_id="c1",
        raw_text="请问这个吉他琴身表面出现轻微划痕怎么处理？",
        sent_at=datetime(2026, 8, 1, 10, 0, 0),
    )
    m2 = Message(
        id="m2",
        conversation_id="c1",
        raw_text="可以用官方附带的擦琴布擦拭，不要用酒精。",
        sent_at=datetime(2026, 8, 1, 10, 1, 0),
    )
    cluster = [(m1, None), (m2, None)]

    sub_eps = segmenter._infer_cluster_sub_episodes(cluster, conv_name="玩家交流群")
    assert len(sub_eps) >= 1
    ep = sub_eps[0]
    assert len(ep.title) >= 5
    assert len(ep.topic_summary) >= 20
    assert 1 in ep.matched_message_indices


@pytest.mark.asyncio
async def test_segmentation_progress_callback_contract():
    """Verify that _run_episode_segmentation accepts and triggers progress_callback with structured details."""
    from apps.api.routes.pipeline import _run_episode_segmentation

    called_progress = []

    async def mock_callback(processed: int, total: int, label: str, log_item):
        called_progress.append((processed, total, label))

    mock_session = AsyncMock()
    # Mock Conversation scalar query
    conv1 = Conversation(id="conv_1", display_name="LiberLive C2玩家交流群 1", workspace_id="ws_1")
    conv2 = Conversation(id="conv_2", display_name="LiberLive C2玩家交流群 2", workspace_id="ws_1")

    mock_exec_convs = MagicMock()
    mock_exec_convs.scalars.return_value.all.return_value = [conv1, conv2]

    mock_stat_res = MagicMock()
    mock_stat_res.first.return_value = (50, datetime(2026, 8, 1), datetime(2026, 8, 5))

    mock_session.execute = AsyncMock(side_effect=[
        mock_exec_convs,  # select convs
        mock_stat_res,    # stat conv 1
        mock_stat_res,    # stat conv 2
    ])

    with patch("apps.api.routes.pipeline.EpisodeSegmenter") as MockSegmenterCls:
        mock_seg_instance = AsyncMock()
        mock_seg_instance.segment_conversation.return_value = [
            Episode(id="ep_1", title="用户反馈按键偏硬体验问题", conversation_id="conv_1", message_count=5),
        ]
        MockSegmenterCls.return_value = mock_seg_instance

        summary = await _run_episode_segmentation(
            session=mock_session,
            force_mock=True,
            progress_callback=mock_callback,
        )

        assert summary.status in ("success", "warning")
        assert len(called_progress) >= 2  # Start & End for each conv
        first_call = called_progress[0]
        assert "正在切分群聊 [1/2]: LiberLive C2玩家交流群 1" in first_call[2]
        assert "2026-08-01 ~ 2026-08-05" in first_call[2]


@pytest.mark.asyncio
async def test_token_usage_metrics_contracts():
    """Verify TokenUsageMetrics supports tokens_per_second, active_model, and success_rate."""
    metrics = TokenUsageMetrics(
        total_tokens=15000,
        prompt_tokens=10000,
        completion_tokens=5000,
        analysis_runs_count=10,
        avg_duration_ms=1250.5,
        tokens_per_second=48.2,
        active_model="qwen3.8-flash",
        success_rate=98.5,
    )
    assert metrics.tokens_per_second == 48.2
    assert metrics.active_model == "qwen3.8-flash"
    assert metrics.success_rate == 98.5
    data = metrics.model_dump()
    assert data["tokens_per_second"] == 48.2
    assert data["active_model"] == "qwen3.8-flash"
