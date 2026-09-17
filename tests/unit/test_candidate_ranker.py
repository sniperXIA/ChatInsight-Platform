from datetime import datetime
from packages.clustering.candidate_ranker import CandidateRanker
from packages.persistence.models import Insight, Topic


def test_candidate_ranker_module_and_keyword_matching():
    ranker = CandidateRanker()

    topic1 = Topic(
        id="top_1",
        title="扩展音色卡无法识别",
        summary="用户反馈将扩展音色卡插入后设备无声音",
        module="音色/扩展卡",
    )
    topic2 = Topic(
        id="top_2",
        title="蓝牙伴奏频繁断连",
        summary="蓝牙搜索不到或者连接后无法播放伴奏",
        module="App/蓝牙连接",
    )

    insight = Insight(
        id="ins_1",
        module="音色/扩展卡",
        summary="新买的音色卡插进去没反应",
        description="请问扩展音色卡怎么使用，插进去为什么没有提示",
    )

    ranked = ranker.rank_candidate_topics(insight, [topic1, topic2])
    assert len(ranked) >= 1
    assert ranked[0][0].id == "top_1"
    assert ranked[0][1] >= 0.35
