from datetime import datetime
from packages.abc_sync.abc_client import ABCSyncClient
from packages.persistence.models import Insight, Topic


def test_abc_sync_client_format_payload():
    client = ABCSyncClient()

    topic = Topic(
        id="topic_test_123",
        title="扩展音色卡加载无声音",
        summary="多个用户反馈扩展卡插入后无声音",
        module="音色/扩展卡",
        sub_module="硬件卡槽",
        severity="major",
        status="open",
        feedback_count=3,
        unique_users_count=2,
        first_seen_at=datetime(2026, 8, 20, 8, 0, 0),
        last_seen_at=datetime(2026, 8, 20, 9, 30, 0),
        tags_json=["LiberLive C2", "音色卡"],
    )

    ins1 = Insight(
        id="ins_1",
        summary="用户A反馈扩展音色卡无声音",
        severity="major",
        status_in_chat="unresolved",
    )
    ins2 = Insight(
        id="ins_2",
        summary="用户B反馈同样卡槽接触不良",
        severity="major",
        status_in_chat="support_acknowledged",
    )

    payload = client.format_topic_payload(topic, [ins1, ins2])

    assert payload.external_id == "topic_test_123"
    assert payload.title == "扩展音色卡加载无声音"
    assert payload.feedback_count == 3
    assert payload.unique_users_count == 2
    assert "chatinsight://insights/ins_1" in payload.evidence_urls
    assert "chatinsight://insights/ins_2" in payload.evidence_urls
    assert payload.metadata["sub_module"] == "硬件卡槽"
