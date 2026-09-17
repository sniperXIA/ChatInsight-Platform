import json
import pytest
from httpx import AsyncClient, ASGITransport
from pathlib import Path
from unittest.mock import patch

from apps.api.main import app
from packages.insights.tag_manager import TagDefinition, TagManager
from packages.insights.episode_segmenter import EpisodeSegmenter, EpisodeSummaryOutput
from packages.insights.episode_deduplicator import EpisodeDeduplicator, EpisodeItemView
from packages.persistence.db import get_session
from packages.persistence.models import Message, Participant


def test_tag_manager_crud(tmp_path):
    temp_tag_file = tmp_path / "test_tag_definitions.json"
    with patch("packages.insights.tag_manager.TAG_CONFIG_PATH", temp_tag_file):
        TagManager.clear_cache()
        tags = TagManager.get_all_tags()
        assert len(tags) >= 10
        assert any(t.key == "sound_preset" for t in tags)
        assert "音色" in TagManager.get_tag_display_name("sound_preset")

        # Add custom tag
        custom_tag = TagDefinition(
            key="custom_pedal",
            name_zh="效果器踏板",
            category="hardware",
            color="amber",
            description="关于无线踏板与外接效果器的讨论",
            is_system=False,
        )
        TagManager.upsert_tag(custom_tag.model_dump())
        
        all_tags = TagManager.get_all_tags()
        assert any(t.key == "custom_pedal" for t in all_tags)
        assert TagManager.get_tag_display_name("custom_pedal") == "效果器踏板"

        # Delete custom tag
        deleted = TagManager.delete_tag("custom_pedal")
        assert deleted is True
        assert not any(t.key == "custom_pedal" for t in TagManager.get_all_tags())

        # Cannot delete system tag
        system_deleted = TagManager.delete_tag("sound_preset")
        assert system_deleted is False


def test_svo_title_generation_and_noise_stripping():
    # Mock message cluster with U1 schedule keywords
    msg1 = Message(id="m1", raw_text="[引用] [图片] 请问U1新品吉他什么时候发售开售呢？排期定了吗？")
    p1 = Participant(id="p1", display_label="琴友阿强", metadata_json={"raw_nickname": "琴友阿强"})

    segmenter = EpisodeSegmenter(session=None)
    out = segmenter._infer_cluster_metadata([(msg1, p1)], conv_name="测试交流群")
    
    assert "U1" in out.title
    assert "发售" in out.title or "规划" in out.title
    assert "[引用]" not in out.title
    assert "[图片]" not in out.title
    assert out.category_hint == "new_product"


def test_svo_title_hardware_specs():
    msg1 = Message(id="m2", raw_text="U1吉他的琴身做工和按键拨片手感怎么样？材质有升级吗")
    p1 = Participant(id="p1", display_label="浪人吉他", metadata_json={"raw_nickname": "浪人吉他"})

    segmenter = EpisodeSegmenter(session=None)
    out = segmenter._infer_cluster_metadata([(msg1, p1)], conv_name="测试交流群")
    
    assert "硬件配置" in out.title or "做工" in out.title
    assert out.category_hint in ("hardware", "hardware_craft")


def test_distinct_clustering_deduplication_and_nicknames():
    ep1 = EpisodeItemView(
        id="ep1",
        conversation_id="c1",
        conversation_name="玩家群1",
        title="讨论U1吉他新品规划与预计发售时间",
        summary="关于U1上市发售时间的咨询",
        category_hint="new_product",
        started_at="2026-08-20T10:00:00",
        ended_at="2026-08-20T10:15:00",
        message_count=12,
        participants=["琴友阿强", "小助手", "用户 U-123456"],
    )
    ep2 = EpisodeItemView(
        id="ep2",
        conversation_id="c2",
        conversation_name="玩家群2",
        title="咨询U1发售上市排期计划",
        summary="咨询U1发售时间",
        category_hint="new_product",
        started_at="2026-08-21T11:00:00",
        ended_at="2026-08-21T11:20:00",
        message_count=8,
        participants=["海阔天空", "琴友阿强"],
    )
    ep3 = EpisodeItemView(
        id="ep3",
        conversation_id="c1",
        conversation_name="玩家群1",
        title="反馈蓝牙连接超时与掉线",
        summary="蓝牙配对搜不到",
        category_hint="software_app",
        started_at="2026-08-21T12:00:00",
        ended_at="2026-08-21T12:10:00",
        message_count=5,
        participants=["阿强"],
    )

    clusters = EpisodeDeduplicator.deduplicate_episodes([ep1, ep2, ep3])
    
    # ep1 and ep2 should merge into the same U1 schedule cluster
    u1_cluster = next((c for c in clusters if c.id == "cluster_u1_release_schedule"), None)
    assert u1_cluster is not None
    assert u1_cluster.episode_count == 2
    assert u1_cluster.total_messages == 20
    assert "琴友阿强" in u1_cluster.participants
    assert "海阔天空" in u1_cluster.participants
    assert "用户 U-123456" not in u1_cluster.participants  # Filtered out anonymous fallback
