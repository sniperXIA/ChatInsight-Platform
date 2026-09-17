import pytest
from datetime import datetime
from packages.insights.episode_deduplicator import EpisodeDeduplicator, EpisodeItemView
from packages.insights.episode_segmenter import EpisodeSegmenter
from packages.persistence.models import Message, Participant


def test_relevance_gate_drops_pure_chitchat():
    """Test that pure life chit-chat ('好幸福的工作') is dropped and returns empty []."""
    segmenter = EpisodeSegmenter(session=None)
    
    # 1. Pure chit-chat cluster
    cluster = [
        (Message(id="m1", raw_text="好幸福的工作", sent_at=datetime(2026, 8, 25, 11, 56, 38)), Participant(id="p1", display_label="一只葫卢娃")),
        (Message(id="m2", raw_text="好幸福的工作+1", sent_at=datetime(2026, 8, 25, 11, 56, 52)), Participant(id="p2", display_label="小小小酷婷")),
        (Message(id="m3", raw_text="好羡慕", sent_at=datetime(2026, 8, 25, 11, 56, 55)), Participant(id="p2", display_label="小小小酷婷")),
        (Message(id="m4", raw_text="为了陪娃[捂脸]他昨天跟着小伙伴去下山口泡温泉，我不放心下班去看看", sent_at=datetime(2026, 8, 25, 11, 58, 24)), Participant(id="p3", display_label="庄崇云")),
    ]
    
    sub_eps = segmenter._infer_cluster_sub_episodes(cluster, "LiberLive U1玩家交流群 01")
    assert sub_eps == [], "Pure life chit-chat should be dropped completely!"


def test_recording_volume_balance_svo_title():
    """Test that recording volume balance discussion produces clean SVO title without '围绕...'."""
    segmenter = EpisodeSegmenter(session=None)
    
    cluster = [
        (Message(id="m1", raw_text="[引用] 你们有没有发现一个现象，就是在室内情况下，吉他弹唱的时候，最后录下来的都是歌声很强，琴声很弱，甚至几乎录不到琴声，但是在现场时候，明明是听得到很强的琴声的。我遇到过这种情况，有没有人知道是什么原因呢？", sent_at=datetime(2026, 8, 14, 13, 35, 27)), Participant(id="p1", display_label="qq753700992")),
        (Message(id="m2", raw_text="确实是这样的。录下来效果就不好了。", sent_at=datetime(2026, 8, 14, 13, 39, 52)), Participant(id="p2", display_label="琴友A")),
        (Message(id="m3", raw_text="外录会有高声抑制，声音大小不平衡，可以试试内录", sent_at=datetime(2026, 8, 14, 13, 57, 47)), Participant(id="p3", display_label="橙颗粒")),
    ]
    
    sub_eps = segmenter._infer_cluster_sub_episodes(cluster, "LiberLive C2玩家交流群 8")
    assert len(sub_eps) == 1
    assert not sub_eps[0].title.startswith("围绕")
    assert not sub_eps[0].title.startswith("关于")
    assert "录" in sub_eps[0].title or "人声" in sub_eps[0].title or "琴声" in sub_eps[0].title
    assert sub_eps[0].category_l1 == "performance"
    assert sub_eps[0].category_l2 == "video_recording"


def test_official_event_dynamic_deduplication():
    """Test that official event registration episodes do NOT get hijacked by beauty filter / landscape recording template."""
    episodes = [
        EpisodeItemView(
            id="ep1",
            conversation_id="c1",
            title="官方发布上海sorry dog咖啡馆人宠音乐会线下活动报名通知",
            summary="群内官方人员李琳发布线下活动通知：官方与sorry dog咖啡馆联合举办人类与猫咪共同参与的音乐会，需提前扫描二维码报名，已报名的玩家可私聊小助理加入上海城市群。",
            category_hint="general:official_event:官方线下活动报名通知",
            started_at="2026-08-20 18:02:45",
            ended_at="2026-08-20 18:02:47",
            message_count=4,
            participants=["李琳"],
        ),
        EpisodeItemView(
            id="ep2",
            conversation_id="c2",
            title="官方发布深圳湾U3 PARK落日现场活动报名与出行指南",
            summary="官方在群内发布LiberLive × U3 PARK Sunset Live落日现场活动通知，用户可带LiberLive现场弹奏表演，节目报名通过飞书表单提交，并附公共交通与自驾出行指南。",
            category_hint="general:official_event:落日现场活动报名与出行指南",
            started_at="2026-07-06 16:59:38",
            ended_at="2026-07-06 16:59:39",
            message_count=3,
            participants=["小助理"],
        ),
    ]
    
    clusters = EpisodeDeduplicator.deduplicate_episodes(episodes)
    assert len(clusters) >= 1
    cluster = clusters[0]
    
    # Verify canonical title and summary are NOT hijacked by '美颜滤镜' or '横屏录制'
    assert "美颜" not in cluster.canonical_title
    assert "滤镜" not in cluster.canonical_title
    assert "横屏" not in cluster.canonical_title
    assert "官方" in cluster.canonical_title or "活动" in cluster.canonical_title or "报名" in cluster.canonical_title
    assert "sorry dog" in cluster.summary or "落日现场" in cluster.summary or "活动" in cluster.summary
