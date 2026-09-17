from packages.domain.enums import InsightType, SeverityLevel
from packages.insights.contracts import (
    ClaimItem,
    EpisodeContextItem,
    EpisodeContextPacket,
    InsightDraft,
)
from packages.insights.factual_checker import FactualChecker


def test_factual_checker_supported_claim():
    checker = FactualChecker()

    context = EpisodeContextPacket(
        episode_id="ep_1",
        conversation_id="conv_1",
        conversation_name="LiberLive C2 玩家交流群1",
        title="扩展音色卡使用疑问",
        topic_summary="用户询问关于音色卡支持情况",
        category_hint="sound_preset",
        started_at="2026-08-20T08:00:00",
        ended_at="2026-08-20T08:05:00",
        messages=[
            EpisodeContextItem(
                message_id="msg_101",
                sequence=1,
                sent_at="2026-08-20T08:00:00",
                sender_label="用户 U-1234",
                sender_role="user",
                raw_text="亲，请问支持扩展卡吗？扩展音色卡怎么使用？",
            ),
            EpisodeContextItem(
                message_id="msg_102",
                sequence=2,
                sent_at="2026-08-20T08:01:00",
                sender_label="客服小助手",
                sender_role="support",
                raw_text="支持的，把扩展音色卡插入侧边卡槽即可直接加载演奏。",
            ),
        ],
    )

    draft = InsightDraft(
        insight_type=InsightType.INQUIRY,
        module="音色/扩展卡",
        severity=SeverityLevel.TRIVIAL,
        summary="用户咨询扩展音色卡是否支持及插入使用方法",
        description="用户询问设备对扩展音色卡的支持情况",
        status_in_chat="support_acknowledged",
        support_known_status=True,
        claims=[
            ClaimItem(
                claim_id="c_1",
                claim_text="用户询问设备是否支持扩展音色卡",
                fact_category="symptom",
                evidence_uris=["chatinsight://conv/conv_1/msg_msg_101#text"],
                confidence=1.0,
            ),
            ClaimItem(
                claim_id="c_2",
                claim_text="客服回复把扩展音色卡插入侧边卡槽即可直接加载演奏",
                fact_category="support_response",
                evidence_uris=["chatinsight://conv/conv_1/msg_msg_102#text"],
                confidence=1.0,
            ),
        ],
    )

    res = checker.check_insight_claims(draft, context)
    assert res.overall_factual_score == 1.0
    assert res.is_fully_supported is True
    assert len(res.unsupported_claims) == 0
    assert res.action_recommendation == "auto_approve"


def test_factual_checker_hallucinated_claim():
    checker = FactualChecker()

    context = EpisodeContextPacket(
        episode_id="ep_2",
        conversation_id="conv_1",
        conversation_name="LiberLive C2 玩家交流群1",
        title="闲聊",
        topic_summary="用户打招呼",
        category_hint="general",
        started_at="2026-08-20T08:00:00",
        ended_at="2026-08-20T08:01:00",
        messages=[
            EpisodeContextItem(
                message_id="msg_201",
                sequence=1,
                sent_at="2026-08-20T08:00:00",
                sender_label="用户 U-1234",
                sender_role="user",
                raw_text="早上好大家",
            )
        ],
    )

    draft = InsightDraft(
        insight_type=InsightType.ISSUE,
        module="硬件/主板",
        severity=SeverityLevel.BLOCKER,
        summary="主板芯片烧毁导致黑屏无法开机",
        description="脑补的主板硬件故障",
        status_in_chat="unresolved",
        support_known_status=False,
        claims=[
            ClaimItem(
                claim_id="c_fake",
                claim_text="主板硬件芯片发热严重发生短路烧毁故障",
                fact_category="symptom",
                evidence_uris=[],  # No evidence URI
                confidence=0.9,
            )
        ],
    )

    res = checker.check_insight_claims(draft, context)
    assert res.overall_factual_score < 0.5
    assert res.is_fully_supported is False
    assert "c_fake" in res.unsupported_claims
    assert res.action_recommendation == "reject"
