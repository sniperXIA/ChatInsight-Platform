import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from packages.clustering.candidate_ranker import CandidateRanker
from packages.insights.contracts import ClaimItem, EpisodeContextItem, EpisodeContextPacket, InsightDraft
from packages.insights.factual_checker import FactualChecker
from packages.persistence.models import Conversation, Insight, Message, Topic
from packages.retrieval.vector_service import VectorService
from packages.search.contracts import SearchFilter
from packages.search.hybrid_search import HybridSearchEngine


@pytest.mark.asyncio
async def test_candidate_ranker_dense_semantic_recall():
    """
    Verifies that when an insight and topic share the same semantic meaning
    but use completely different vocabulary, dense vector ranking yields a high score.
    """
    vs = VectorService(session=None)
    ranker = CandidateRanker(vector_service=vs)

    topic = Topic(
        id="top_1",
        workspace_id="ws_1",
        title="App曲谱界面字号偏小与高对比度谱面显示",
        summary="群成员反馈文字与和弦字号过小、弱光环境下排版不易辨识，建议增加字号无级缩放与高对比度显示选项。",
        module="界面与显示",
    )

    insight = Insight(
        id="ins_1",
        workspace_id="ws_1",
        episode_id="ep_1",
        insight_type="issue",
        module="界面与显示",
        summary="弱光练琴时乐谱文字看不清且缺少深色模式",
        description="用户在暗光环境下弹唱练习时，界面字体较小导致辨识困难，希望改善排版可视度。",
        severity="minor",
    )

    unrelated_topic = Topic(
        id="top_2",
        workspace_id="ws_1",
        title="C2设备充电器功率适配规格与快充协议兼容",
        summary="群成员在群聊中咨询设备充电功率选择、不同瓦数手机充电头兼容性与快充发热风险。",
        module="固件与电源管理",
    )

    semantic_ranked = await ranker.rank_candidate_topics_async(
        insight=insight,
        candidate_topics=[topic, unrelated_topic],
        min_threshold=0.1,
        force_mock=True,
    )

    assert len(semantic_ranked) > 0
    top_matched, sem_score = semantic_ranked[0]
    assert top_matched.id == "top_1"
    assert sem_score > 0.25


@pytest.mark.asyncio
async def test_factual_checker_semantic_verification():
    """
    Tests semantic claim verification where the claim is a natural paraphrasing
    of the actual chat message.
    """
    vs = VectorService(session=None)
    checker = FactualChecker()

    msg = EpisodeContextItem(
        message_id="msg_101",
        sequence=1,
        sender_label="琴友小张",
        sender_role="user",
        sent_at="2026-09-04 14:30:00",
        raw_text="用快充头给琴充了两个小时，发现吉他发烫严重而且充不进电。",
    )
    context = EpisodeContextPacket(
        episode_id="ep_1",
        conversation_id="conv_1",
        conversation_name="LiberLive 玩家群",
        title="设备快充发热充不进电",
        topic_summary="用户反馈快充头充电发烫充不进电",
        category_hint="firmware_power",
        started_at="2026-09-04 14:00:00",
        ended_at="2026-09-04 15:00:00",
        messages=[msg],
    )

    draft = InsightDraft(
        insight_type="issue",
        module="固件与电源管理",
        severity="major",
        summary="大功率快充引发机身异常发热且电池充不进电",
        description="[具体现象] 连续充电数小时机身明显过热且电量无增长",
        confidence=0.9,
        claims=[
            ClaimItem(
                claim_id="clm_1",
                claim_text="用户使用快充充电时机身严重发热且无法充入电量",
                evidence_uris=["chatinsight://conv/conv_1/msg_msg_101#text"],
                confidence=1.0,
            )
        ],
    )

    res = await checker.check_insight_claims_semantic(draft, context, vector_service=vs, force_mock=True)
    assert res.overall_factual_score >= 0.8
    assert res.is_fully_supported is True
    assert res.action_recommendation == "auto_approve"


@pytest.mark.asyncio
async def test_hybrid_search_with_dense_rrf():
    """
    Tests that HybridSearchEngine correctly initializes with VectorService
    and computes RRF ranks without errors.
    """
    mock_topic = Topic(
        id="top_test_1",
        workspace_id="ws_1",
        title="伴奏风格包自定义配置与个性化调节",
        summary="群成员在社群活动及日常交流中咨询如何自定义风格包，希望了解不同曲风伴奏风格的个性化设置路径与保存操作指南。",
        module="配置与音色",
        severity="minor",
        status="open",
        feedback_count=5,
        tags_json=["风格包", "伴奏"],
    )

    mock_insight = Insight(
        id="ins_test_1",
        workspace_id="ws_1",
        episode_id="ep_test_1",
        insight_type="feature_request",
        summary="希望App允许用户自定义和保存个性化伴奏风格包",
        description="用户希望在弹唱时自由切换鼓点并保存到个人风格库",
        module="配置与音色",
        severity="minor",
        status_in_chat="reported",
    )

    call_count = 0

    class MockResult:
        def __init__(self, items):
            self.items = items

        def scalars(self):
            class ScalarResult:
                def __init__(self, itms):
                    self.itms = itms

                def all(self):
                    return self.itms
            return ScalarResult(self.items)

        def all(self):
            return []

    async def mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MockResult([mock_topic])
        elif call_count == 2:
            return MockResult([mock_insight])
        return MockResult([])

    session = MagicMock()
    session.execute = AsyncMock(side_effect=mock_execute)

    vs = VectorService(session=session)
    engine = HybridSearchEngine(session=session, vector_service=vs)

    res = await engine.search(query="风格包怎么自定义设置", limit=5)
    assert res.total_hits >= 1
    assert res.results[0].score > 0
