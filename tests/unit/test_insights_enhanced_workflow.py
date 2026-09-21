import asyncio
import pytest
from datetime import datetime, timedelta
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from packages.domain.enums import InsightType, SeverityLevel
from packages.domain.models import generate_id
from packages.persistence.db import get_session
from packages.persistence.models import (
    Base,
    Conversation,
    Episode,
    EpisodeMessage,
    Insight,
    InsightClaim,
    InsightPushRecord,
    Message,
    Participant,
    Topic,
    TopicInsightLink,
)


@pytest.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session


@pytest.mark.asyncio
async def test_insights_enhanced_filters_and_evidence():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Seed test conversation, participant, episode and messages
    async with session_maker() as session:
        conv = Conversation(id="conv_1", workspace_id="ws_1", display_name="吉他玩家交流群")
        session.add(conv)

        part_u1 = Participant(id="part_1", workspace_id="ws_1", stable_anonymous_key="key_u1", display_label="吉他手小明", role="user")
        part_sup = Participant(id="part_sup", workspace_id="ws_1", stable_anonymous_key="key_sup", display_label="官方技术小李", role="support")
        session.add_all([part_u1, part_sup])

        ep1 = Episode(
            id="ep_1",
            workspace_id="ws_1",
            conversation_id="conv_1",
            title="蓝牙配对掉线问题",
            summary="讨论iOS 17.4下蓝牙搜不到设备",
            started_at=datetime(2026, 8, 30, 10, 0, 0),
            ended_at=datetime(2026, 8, 30, 10, 20, 0),
            message_count=2,
        )
        ep2 = Episode(
            id="ep_2",
            workspace_id="ws_1",
            conversation_id="conv_1",
            title="乐库曲谱报错",
            summary="反馈晴天和弦标错",
            started_at=datetime(2026, 8, 31, 14, 0, 0),
            ended_at=datetime(2026, 8, 31, 14, 15, 0),
            message_count=1,
        )
        session.add_all([ep1, ep2])

        # Messages for ep1
        m1 = Message(
            id="m_1",
            workspace_id="ws_1",
            conversation_id="conv_1",
            participant_id="part_1",
            source_file_id="f_1",
            source_message_id="raw_1",
            source_sequence=1,
            sent_at=datetime(2026, 8, 30, 10, 2, 0),
            raw_text="iOS 17.4 打开App蓝牙一直转圈搜不到琴",
            normalized_text="iOS 17.4 打开App蓝牙一直转圈搜不到琴",
            source_record_hash="hash_1",
        )
        m2 = Message(
            id="m_2",
            workspace_id="ws_1",
            conversation_id="conv_1",
            participant_id="part_sup",
            source_file_id="f_1",
            source_message_id="raw_2",
            source_sequence=2,
            sent_at=datetime(2026, 8, 30, 10, 5, 0),
            raw_text="您好，已知新版本iOS蓝牙权限适配问题，正在紧急发布补丁",
            normalized_text="您好，已知新版本iOS蓝牙权限适配问题，正在紧急发布补丁",
            source_record_hash="hash_2",
        )
        session.add_all([m1, m2])

        em1 = EpisodeMessage(id="em_1", episode_id="ep_1", message_id="m_1", sequence=1)
        em2 = EpisodeMessage(id="em_2", episode_id="ep_1", message_id="m_2", sequence=2)
        session.add_all([em1, em2])

        # Insights
        ins1 = Insight(
            id="ins_ble",
            workspace_id="ws_1",
            episode_id="ep_1",
            insight_type="issue",
            module="蓝牙与无线 · 吉他配对与掉线",
            sub_module="吉他配对与掉线",
            severity="major",
            summary="iOS 17.4 下蓝牙搜索延迟或无法发现设备",
            description="[场景/位置] App配对连接界面\n[具体现象] 蓝牙一直转圈搜不到琴\n[影响程度] 无法连琴弹奏\n[环境/触发条件] iOS 17.4\n[社群进展] 官方客服已确认并在紧急发布补丁",
            status_in_chat="support_acknowledged",
            support_known_status=True,
            factual_score=0.96,
            confidence=0.95,
            tags_json=["蓝牙与无线", "吉他配对与掉线"],
            state="approved",
            created_at=datetime(2026, 8, 30, 10, 25, 0),
        )
        ins2 = Insight(
            id="ins_sheet",
            workspace_id="ws_1",
            episode_id="ep_2",
            insight_type="feature_request",
            module="曲谱与乐库 · 曲谱报错纠错",
            sub_module="曲谱报错纠错",
            severity="minor",
            summary="曲谱晴天副歌部分和弦有误",
            description="[场景/位置] 乐库曲谱详情页\n[具体现象] 第3小节和弦标成Em而不是C\n[影响程度] 弹唱伴奏不协调\n[环境/触发条件] 官方内置晴天曲谱\n[社群进展] 用户初次上报",
            status_in_chat="reported",
            support_known_status=False,
            factual_score=0.72,
            confidence=0.70,
            tags_json=["曲谱与乐库", "曲谱报错纠错"],
            state="draft",
            created_at=datetime(2026, 8, 31, 14, 20, 0),
        )
        session.add_all([ins1, ins2])

        # Claims for ins1
        claim1 = InsightClaim(
            id="claim_1",
            insight_id="ins_ble",
            claim_key="c_1",
            claim_text="iOS 17.4 用户反馈蓝牙转圈搜不到琴",
            fact_category="symptom",
            evidence_uris_json=["chatinsight://conv/conv_1/msg_m_1#text"],
            confidence=0.98,
            verification_state="supported",
        )
        claim2 = InsightClaim(
            id="claim_2",
            insight_id="ins_ble",
            claim_key="c_2",
            claim_text="官方技术小李答复已知适配问题并排期补丁",
            fact_category="support_response",
            evidence_uris_json=["chatinsight://conv/conv_1/msg_m_2#text"],
            confidence=0.95,
            verification_state="supported",
        )
        session.add_all([claim1, claim2])

        # Topic link for ins1 (already clustered)
        top1 = Topic(
            id="top_ble",
            workspace_id="ws_1",
            title="iOS 系统蓝牙外设配对异常",
            summary="多名用户反馈iOS升级后蓝牙搜不到设备",
            module="蓝牙与无线",
            severity="major",
        )
        session.add(top1)
        link1 = TopicInsightLink(id="link_1", topic_id="top_ble", insight_id="ins_ble", relation_type="instance")
        session.add(link1)

        await session.commit()

    async def override_get_session():
        async with session_maker() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Test search filter
        res_search = await client.get("/api/v1/insights?search=蓝牙")
        assert res_search.status_code == 200
        data_search = res_search.json()
        assert len(data_search) == 1
        assert data_search[0]["id"] == "ins_ble"
        assert data_search[0]["type_label_zh"] == "🐛 产品缺陷"
        assert data_search[0]["severity_label_zh"] == "⚠️ 严重故障"
        assert data_search[0]["status_label_zh"] == "👨‍💻 客服已确认/记录"
        assert data_search[0]["confidence_level"] == "high"
        assert "高置信度" in data_search[0]["confidence_reason"]
        assert data_search[0]["is_clustered"] is True
        assert data_search[0]["topic_id"] == "top_ble"

        # 2. Test tag multi-select filter
        res_tags = await client.get("/api/v1/insights?tags=曲谱与乐库")
        assert res_tags.status_code == 200
        data_tags = res_tags.json()
        assert len(data_tags) == 1
        assert data_tags[0]["id"] == "ins_sheet"
        assert data_tags[0]["type_label_zh"] == "💡 功能需求"
        assert data_tags[0]["confidence_level"] == "medium"
        assert data_tags[0]["is_clustered"] is False

        # 3. Test multi-dimensional sorting (severity_desc)
        res_sort_sev = await client.get("/api/v1/insights?sort_by=severity_desc")
        assert res_sort_sev.status_code == 200
        data_sort_sev = res_sort_sev.json()
        assert len(data_sort_sev) == 2
        # major should come before minor
        assert data_sort_sev[0]["id"] == "ins_ble"
        assert data_sort_sev[1]["id"] == "ins_sheet"

        # 4. Test date range
        res_date = await client.get("/api/v1/insights?date_preset=custom&start_date=2026-08-31&end_date=2026-09-01")
        assert res_date.status_code == 200
        data_date = res_date.json()
        assert len(data_date) == 1
        assert data_date[0]["id"] == "ins_sheet"

        # 5. Test evidence下钻 endpoint
        res_ev = await client.get("/api/v1/insights/ins_ble/evidence")
        assert res_ev.status_code == 200
        ev_data = res_ev.json()
        assert ev_data["insight_id"] == "ins_ble"
        assert ev_data["conversation_name"] == "吉他玩家交流群"
        assert len(ev_data["claims"]) == 2
        assert len(ev_data["messages"]) == 2
        # Verify message citation match
        assert ev_data["messages"][0]["is_cited"] is True
        assert "c_1" in ev_data["messages"][0]["cited_claims"]
        assert ev_data["messages"][1]["is_cited"] is True
        assert "c_2" in ev_data["messages"][1]["cited_claims"]
        assert ev_data.get("created_at") is not None

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_insights_stats_aggregation_accuracy():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with session_maker() as session:
        # Seed insights with various severities and dates
        i_blocker = Insight(
            id="ins_b1",
            workspace_id="ws_1",
            episode_id="ep_1",
            insight_type="issue",
            module="固件与系统",
            severity="blocker",
            priority="P0",
            summary="固件升级黑屏变砖",
            description="升级到v2.1后吉他无法开机",
            status_in_chat="unresolved",
            support_known_status=False,
            factual_score=0.95,
            confidence=0.95,
            created_at=datetime(2026, 9, 1, 10, 0, 0),
        )
        i_major1 = Insight(
            id="ins_m1",
            workspace_id="ws_1",
            episode_id="ep_1",
            insight_type="issue",
            module="蓝牙与无线",
            severity="major",
            priority="P1",
            summary="蓝牙经常断连",
            description="蓝牙播放伴奏频繁卡顿",
            status_in_chat="unresolved",
            support_known_status=False,
            factual_score=0.90,
            confidence=0.90,
            created_at=datetime(2026, 9, 2, 10, 0, 0),
        )
        i_major2 = Insight(
            id="ins_m2",
            workspace_id="ws_1",
            episode_id="ep_1",
            insight_type="issue",
            module="界面与显示",
            severity="major",
            priority="P1",
            summary="谱面白屏闪退",
            description="进入曲谱页偶发闪退",
            status_in_chat="unresolved",
            support_known_status=False,
            factual_score=0.88,
            confidence=0.88,
            created_at=datetime(2026, 9, 3, 10, 0, 0),
        )
        i_minor1 = Insight(
            id="ins_min1",
            workspace_id="ws_1",
            episode_id="ep_1",
            insight_type="feature_request",
            module="曲谱与乐库",
            severity="minor",
            priority="P2",
            summary="希望支持吉他六线谱切换",
            description="目前只有简谱",
            status_in_chat="unresolved",
            support_known_status=False,
            factual_score=0.85,
            confidence=0.85,
            created_at=datetime(2026, 9, 4, 10, 0, 0),
        )
        i_triv1 = Insight(
            id="ins_tr1",
            workspace_id="ws_1",
            episode_id="ep_1",
            insight_type="usability_opportunity",
            module="界面与显示",
            severity="trivial",
            priority="P3",
            summary="按键提示文字偏小",
            description="暗色模式下对比度可以更高",
            status_in_chat="unresolved",
            support_known_status=False,
            factual_score=0.75,
            confidence=0.75,
            created_at=datetime(2026, 9, 5, 10, 0, 0),
        )
        session.add_all([i_blocker, i_major1, i_major2, i_minor1, i_triv1])

        # Seed push records:
        # ins_b1: success
        rec_b1 = InsightPushRecord(
            id="rec_1",
            workspace_id="ws_1",
            insight_id="ins_b1",
            target_platform="feishu_bitable",
            webhook_url="https://feishu.cn/webhook/1",
            state="success",
            status_code=200,
            created_at=datetime(2026, 9, 6, 10, 0, 0),
        )
        # ins_m1: failed
        rec_m1 = InsightPushRecord(
            id="rec_2",
            workspace_id="ws_1",
            insight_id="ins_m1",
            target_platform="feishu_bitable",
            webhook_url="https://feishu.cn/webhook/1",
            state="failed",
            status_code=500,
            error_message="Gateway timeout",
            created_at=datetime(2026, 9, 6, 11, 0, 0),
        )
        # Orphan push record: points to non-existent insight
        rec_orphan = InsightPushRecord(
            id="rec_orphan",
            workspace_id="ws_1",
            insight_id="ins_non_existent",
            target_platform="feishu_bitable",
            webhook_url="https://feishu.cn/webhook/1",
            state="success",
            status_code=200,
            created_at=datetime(2026, 9, 6, 12, 0, 0),
        )
        session.add_all([rec_b1, rec_m1, rec_orphan])
        await session.commit()

    async def override_get_session():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Test overall stats aggregation
        res = await client.get("/api/v1/insights/stats")
        assert res.status_code == 200
        stats = res.json()

        assert stats["total_insights"] == 5
        assert stats["blocker_major_count"] == 3
        assert stats["blocker_count"] == 1
        assert stats["major_count"] == 2
        assert stats["minor_count"] == 1
        assert stats["trivial_count"] == 1

        # Orphan record should NOT be counted in pushed_success_count
        assert stats["pushed_success_count"] == 1
        assert stats["pushed_failed_count"] == 1
        assert stats["pending_push_count"] == 4  # 5 total - 1 success = 4
        assert stats["push_success_rate"] == 20.0  # 1 / 5 * 100

        # Global baselines
        assert stats["global_total_insights"] == 5
        assert stats["global_blocker_major_count"] == 3
        assert stats["global_pending_push_count"] == 4
        assert stats["global_pushed_success_count"] == 1

        # 2. Test filtered stats by severity
        res_sev = await client.get("/api/v1/insights/stats?severity=blocker")
        assert res_sev.status_code == 200
        sev_stats = res_sev.json()
        assert sev_stats["total_insights"] == 1
        assert sev_stats["blocker_major_count"] == 1
        assert sev_stats["blocker_count"] == 1
        assert sev_stats["major_count"] == 0
        assert sev_stats["pushed_success_count"] == 1
        assert sev_stats["pending_push_count"] == 0
        assert sev_stats["global_total_insights"] == 5

        # 3. Test filtered stats by push_status=success
        res_push = await client.get("/api/v1/insights/stats?push_status=success")
        assert res_push.status_code == 200
        push_stats = res_push.json()
        assert push_stats["total_insights"] == 1
        assert push_stats["pushed_success_count"] == 1
        assert push_stats["global_total_insights"] == 5

        # 4. Test list_insights with limit up to 500
        res_list = await client.get("/api/v1/insights?limit=300")
        assert res_list.status_code == 200
        items = res_list.json()
        assert len(items) == 5

    app.dependency_overrides.clear()
