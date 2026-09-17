import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from packages.persistence.models import Message, Participant
from packages.insights.episode_segmenter import EpisodeSegmenter, SubEpisodeItem
from packages.insights.episode_deduplicator import EpisodeDeduplicator, EpisodeItemView


def _make_msg(mid: str, text: str, sent_at: datetime) -> tuple[Message, Participant]:
    m = Message(
        id=mid,
        workspace_id="ws1",
        conversation_id="c1",
        participant_id="u1",
        source_file_id="sf1",
        source_sequence=1,
        sent_at=sent_at,
        raw_text=text,
        normalized_text=text,
        source_record_hash=f"hash_{mid}",
    )
    p = Participant(
        id="u1",
        workspace_id="ws1",
        stable_anonymous_key="key_u1",
        display_label="琴友小张",
    )
    return (m, p)


def test_chunk_pruning_gate_drops_pure_chitchat_and_greetings():
    """Verify that chunks with 1-2 pure life chitchat or greeting messages are 100% pruned/dropped."""
    now = datetime(2026, 8, 31, 10, 0, 0, tzinfo=timezone.utc)
    
    # 1. Pure greeting chunk
    greeting_chunk = [
        _make_msg("m1", "大家早上好！", now),
        _make_msg("m2", "早安", now),
    ]
    assert EpisodeSegmenter._evaluate_chunk_quality(greeting_chunk) is False
    assert EpisodeSegmenter()._infer_cluster_sub_episodes(greeting_chunk, "测试群") == []

    # 2. Pure life chitchat chunk
    life_chunk = [
        _make_msg("m3", "今天好幸福的工作，好幸福的工作+1", now),
        _make_msg("m4", "带娃去泡温泉啦，吃什么好呢", now),
    ]
    assert EpisodeSegmenter._evaluate_chunk_quality(life_chunk) is False
    assert EpisodeSegmenter()._infer_cluster_sub_episodes(life_chunk, "测试群") == []

    # 3. Emojis and single-word filler
    filler_chunk = [
        _make_msg("m5", "[表情]", now),
        _make_msg("m6", "哈哈", now),
    ]
    assert EpisodeSegmenter._evaluate_chunk_quality(filler_chunk) is False
    assert EpisodeSegmenter()._infer_cluster_sub_episodes(filler_chunk, "测试群") == []


def test_chunk_pruning_gate_keeps_high_confidence_product_questions():
    """Verify that short chunks (1-2 messages) containing explicit product questions or requests are preserved."""
    now = datetime(2026, 8, 31, 10, 0, 0, tzinfo=timezone.utc)

    # 1. Charging compatibility question
    charging_chunk = [
        _make_msg("m1", "请问C2可以用手机40瓦充电头充电吗？会充坏琴吗？", now),
    ]
    assert EpisodeSegmenter._evaluate_chunk_quality(charging_chunk) is True
    subs = EpisodeSegmenter()._infer_cluster_sub_episodes(charging_chunk, "测试群")
    assert len(subs) == 1
    assert "充电" in subs[0].title
    assert subs[0].category_l1 == "firmware_power"

    # 2. Song request
    song_chunk = [
        _make_msg("m2", "求加一首周杰伦的七里香伴奏曲谱！", now),
    ]
    assert EpisodeSegmenter._evaluate_chunk_quality(song_chunk) is True
    subs = EpisodeSegmenter()._infer_cluster_sub_episodes(song_chunk, "测试群")
    assert len(subs) == 1
    assert "曲谱" in subs[0].title or "求谱" in subs[0].title or "周杰伦" in subs[0].summary
    assert subs[0].category_l1 == "sheet_music"


def test_distinct_clustering_separates_firmware_charging_and_accessories():
    """
    Verify Case 4:
    Firmware upgrade failure vs Charging power compatibility vs Charging accessory policy
    must form 3 distinct clusters, avoiding title/summary mismatches.
    """
    now_str = datetime(2026, 8, 31, 10, 0, 0, tzinfo=timezone.utc).isoformat()

    ep_charging_spec = EpisodeItemView(
        id="ep-1",
        conversation_id="c1",
        title="用户咨询C2充电器功率规格与40瓦充电头兼容性",
        summary="用户在群内询问设备充电器接口端是否需要固定使用65瓦充电头，40瓦充电头能否用于给设备充电，群友探讨20W与65W快充发热与适配要求。",
        category_hint="firmware_power:charging_spec:充电器功率适配",
        message_count=9,
        started_at=now_str,
        ended_at=now_str,
        participants=["小张", "Ling宵"],
    )

    ep_accessory_policy = EpisodeItemView(
        id="ep-2",
        conversation_id="c1",
        title="用户反馈产品不标配充电头需自购且原充电头被偷后用手机充电器充电",
        summary="用户反馈官方不标配充电头需自购，吐槽标配双C口线材适配不便，表达对配件包装策略的不满。",
        category_hint="hardware_craft:accessory_standard:充电头标配",
        message_count=5,
        started_at=now_str,
        ended_at=now_str,
        participants=["苏子和"],
    )

    ep_fw_upgrade_failure = EpisodeItemView(
        id="ep-3",
        conversation_id="c1",
        title="用户反馈固件升级反复失败并求助排查最终重启解决",
        summary="用户宁反馈C2琴固件升级总是到最后一步失败，官方小木跟进，群友指导排查蜂窝网络、手机内存与电量，重启2-3次后成功解决。",
        category_hint="firmware_power:fw_upgrade_failure:固件升级失败排查",
        message_count=27,
        started_at=now_str,
        ended_at=now_str,
        participants=["宁", "藍ღ喵", "官方小木"],
    )

    ep_drum_machine = EpisodeItemView(
        id="ep-4",
        conversation_id="c1",
        title="用户咨询鼓机模式进入与长按关闭操作方法",
        summary="用户咨询鼓机模式进入后如何退出，官方指导长按关闭与速度调节。",
        category_hint="performance:drum_machine_operation:鼓机进入与关闭操作",
        message_count=6,
        started_at=now_str,
        ended_at=now_str,
        participants=["琴友李四"],
    )

    clusters = EpisodeDeduplicator.deduplicate_episodes(
        [ep_charging_spec, ep_accessory_policy, ep_fw_upgrade_failure, ep_drum_machine]
    )

    # Must be partitioned into 4 distinct clusters!
    assert len(clusters) == 4

    # Find the firmware upgrade cluster
    fw_cluster = next(c for c in clusters if "固件" in c.canonical_title or "升级" in c.canonical_title)
    assert "固件" in fw_cluster.canonical_title or "升级" in fw_cluster.canonical_title
    assert "最后一步失败" in fw_cluster.summary
    assert "充电头被偷" not in fw_cluster.canonical_title  # No title mismatch!

    # Find the charging accessory cluster
    acc_cluster = next(c for c in clusters if "配件" in c.canonical_title or "标配" in c.canonical_title or "充电头" in c.canonical_title and "功率" not in c.canonical_title)
    assert "标配充电头" in acc_cluster.canonical_title or "充电头" in acc_cluster.canonical_title
    assert "不标配充电头" in acc_cluster.summary or "自购" in acc_cluster.summary
    assert "固件升级" not in acc_cluster.summary  # No summary mismatch!

    # Find charging power cluster
    power_cluster = next(c for c in clusters if "功率" in c.canonical_title or "充电器" in c.canonical_title)
    assert "功率" in power_cluster.canonical_title or "40瓦" in power_cluster.canonical_title
    assert "40瓦" in power_cluster.summary or "65瓦" in power_cluster.summary

    # Find drum machine cluster
    drum_cluster = next(c for c in clusters if "鼓机" in c.canonical_title)
    assert "鼓机" in drum_cluster.canonical_title
    assert "长按关闭" in drum_cluster.summary


def test_deduplicator_filters_legacy_generic_fallback_episodes():
    """Verify that legacy placeholder episodes like '用户交流社群日常操作与使用反馈' are discarded during deduplication."""
    now_str = datetime(2026, 8, 31, 10, 0, 0, tzinfo=timezone.utc).isoformat()

    generic_ep = EpisodeItemView(
        id="ep-gen",
        conversation_id="c1",
        title="用户交流社群日常操作与使用反馈",
        summary="群成员在群聊中就日常使用体验、操作反馈与社群互动展开交流探讨。",
        category_hint="general:discussion:社群综合交流",
        message_count=1,
        started_at=now_str,
        ended_at=now_str,
        participants=["用户A"],
    )

    valid_ep = EpisodeItemView(
        id="ep-valid",
        conversation_id="c1",
        title="反馈App界面字号偏小与高对比度谱面显示诉求",
        summary="群成员反馈App各功能页面文字与和弦字号过小，建议增加字号缩放功能。",
        category_hint="ui_ux:ui_font:字号大小与清晰度",
        message_count=4,
        started_at=now_str,
        ended_at=now_str,
        participants=["用户B"],
    )

    clusters = EpisodeDeduplicator.deduplicate_episodes([generic_ep, valid_ep])
    assert len(clusters) == 1
    assert "字号" in clusters[0].canonical_title
    assert "用户交流社群日常操作与使用反馈" not in [c.canonical_title for c in clusters]
