from datetime import datetime, timedelta
import json
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.analytics.contracts import FeishuConfig, VoCReportOutput
from packages.analytics.feishu_service import FeishuService
from packages.analytics.report_generator import ReportGenerator
from packages.persistence.db import Base
from packages.persistence.models import Conversation, Episode, EpisodeMessage, Insight, InsightClaim, Message
from packages.persistence.repositories.registry import RepositoryRegistry


@pytest.mark.asyncio
async def test_operational_overview_and_high_value_synthesis():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        # Seed sample Topic and Insight
        t1 = await repo.create_topic(
            workspace_id=ws.id,
            title="旅行锁开机扩展卡识别异常",
            summary="开启旅行锁后开机，扩展卡无法正确加载",
            module="配置与音色 · 旋律音色与音色卡",
            severity="major",
        )
        await repo.update_topic_stats(t1.id, increment_feedback=3, increment_users=2)

        generator = ReportGenerator(session)

        # 1. Test Operational Overview
        overview = await generator.get_operational_overview(period="7d")
        assert overview.period == "7d"
        assert overview.days_count == 7
        assert len(overview.daily_trends) == 7
        assert "平台在统计周期" in overview.brief_summary

        # 2. Test High Value Content
        hv = await generator.get_high_value_content(period="7d", force_mock=True)
        assert len(hv.high_value_topics) >= 1
        assert hv.high_value_topics[0].title == "旅行锁开机扩展卡识别异常"
        assert len(hv.key_recommendations) >= 3
        assert hv.ai_executive_summary is not None

        # 3. Test Master Report Generation
        report = await generator.generate_report(period="7d", force_mock=True)
        assert report.period == "7d"
        assert report.operational_overview.days_count == 7
        assert len(report.high_value_content.high_value_topics) >= 1

        # 4. Test Markdown rendering
        md = generator.render_markdown(report)
        assert "# 📊" in md
        assert "## 1. 核心指标概览" in md
        assert "旅行锁开机扩展卡识别异常" in md


@pytest.mark.asyncio
async def test_critical_insights_full_title_and_authentic_quote():
    from packages.persistence.models import Conversation, Episode, EpisodeMessage, Insight, InsightClaim, Message, Participant
    from datetime import datetime

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        conv = Conversation(
            workspace_id=ws.id,
            source_type="wechat_archive",
            source_conversation_id="conv_harmony",
            display_name="鸿蒙交流群",
        )
        session.add(conv)
        await session.flush()

        part_user = Participant(
            workspace_id=ws.id,
            stable_anonymous_key="user_harmony_1",
            display_label="鸿蒙吉他手",
            role="user",
            is_internal=False,
        )
        session.add(part_user)
        await session.flush()

        # User's authentic complaint message
        msg = Message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id=part_user.id,
            source_file_id="dummy_file",
            source_sequence=1,
            sent_at=datetime(2026, 9, 1, 10, 0, 0),
            raw_text="鸿蒙系统要尽快适配啊，接下来搞定超级多鸿蒙的设备啊@LiberLive小匣子",
            normalized_text="鸿蒙系统要尽快适配啊，接下来搞定超级多鸿蒙的设备啊",
            source_record_hash="hash_1",
        )
        session.add(msg)
        await session.flush()

        ep = Episode(
            workspace_id=ws.id,
            conversation_id=conv.id,
            title="鸿蒙适配诉求",
            summary="社群用户强烈要求适配鸿蒙系统",
            started_at=datetime(2026, 9, 1, 10, 0, 0),
            ended_at=datetime(2026, 9, 1, 10, 5, 0),
            message_count=1,
        )
        session.add(ep)
        await session.flush()

        ep_msg = EpisodeMessage(episode_id=ep.id, message_id=msg.id, sequence=1)
        session.add(ep_msg)

        # Long title > 24 characters
        long_title = "用户强烈建议App尽快原生适配鸿蒙系统，目前仅能通过卓易通变通下载"
        ins = Insight(
            workspace_id=ws.id,
            episode_id=ep.id,
            insight_type="issue",
            module="设备与兼容 · 鸿蒙HarmonyOS兼容",
            severity="major",
            priority="P1",
            summary=long_title,
            description="用户反馈鸿蒙系统无法直接使用App，需通过卓易通",
            state="approved",
        )
        session.add(ins)
        await session.flush()

        # Claim text is AI summary, while evidence points to the real msg
        clm = InsightClaim(
            insight_id=ins.id,
            claim_key="c_1",
            claim_text="用户请求App尽快适配鸿蒙系统，指出鸿蒙设备数量庞大。",
            evidence_uris_json=[f"chatinsight://conv/{conv.id}/msg_{msg.id}#text"],
        )
        session.add(clm)
        await session.commit()

        generator = ReportGenerator(session)
        hv = await generator.get_high_value_content(period="all")

        assert len(hv.critical_insights) >= 1
        crit = next(c for c in hv.critical_insights if c.insight_id == ins.id)

        # 1. Title must NOT be truncated at 24 chars, should be full
        assert crit.title == long_title
        assert len(crit.title) > 24

        # 2. Verbatim quote must be the real user message, NOT the AI claim text
        assert crit.verbatim_quote == "鸿蒙系统要尽快适配啊，接下来搞定超级多鸿蒙的设备啊"
        assert crit.verbatim_quote != clm.claim_text
        assert "@LiberLive小匣子" not in crit.verbatim_quote


@pytest.mark.asyncio
async def test_feishu_service_signature_and_card_construction():
    service = FeishuService(config_path="config/test_feishu_settings.json")
    
    # Signature calculation
    sig = service._generate_sign("secret_key_123", "1700000000")
    assert isinstance(sig, str)
    assert len(sig) > 10

    # Build sample report
    sample_report = VoCReportOutput(
        period_label="2026年第36周 VoC 业务周报",
        period="7d",
    )
    sample_report.operational_overview.total_messages = 1527
    sample_report.operational_overview.daily_avg_messages = 218.1
    sample_report.operational_overview.total_episodes = 162
    sample_report.operational_overview.total_insights = 41
    sample_report.operational_overview.brief_summary = "本周期消息吞吐良好，运转稳定。"

    # Save mock config
    service.save_config(FeishuConfig(
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/mock_token",
        secret="test_secret",
        enabled=True,
    ))

    # Test mocked post payload
    with patch.object(service, "_post_payload", new_callable=AsyncMock) as mock_post:
        mock_post.return_value.success = True
        mock_post.return_value.status_code = 200
        mock_post.return_value.message = "推送成功"

        resp = await service.send_voc_interactive_card(sample_report, custom_note="请各团队注意重点缺陷")
        assert resp.success is True
        assert mock_post.called
        call_args = mock_post.call_args[0]
        url, payload = call_args[0], call_args[1]
        assert "https://open.feishu.cn" in url
        assert payload["msg_type"] == "interactive"
        card = payload["card"]
        assert "2026年第36周" in card["header"]["title"]["content"]
        assert any("1,527" in str(el) for el in card["elements"])

    # Clean up test config
    if service.config_path.exists():
        service.config_path.unlink()


def test_analytics_api_routes_integration():
    client = TestClient(app)

    # 1. Overview API
    resp1 = client.get("/api/v1/analytics/overview?period=7d")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert "total_messages" in data1
    assert "daily_trends" in data1
    assert "token_usage" in data1
    assert "comparison" in data1

    # 2. Report Generation API
    resp2 = client.post("/api/v1/analytics/reports/generate", json={"period": "7d", "force_mock": True})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert "operational_overview" in data2
    assert "high_value_content" in data2
    assert "category_dynamics" in data2["high_value_content"]

    # 3. Export Markdown API
    resp3 = client.get("/api/v1/analytics/reports/export-markdown?period=7d")
    assert resp3.status_code == 200
    assert "text/markdown" in resp3.headers["content-type"]
    assert "# 📊" in resp3.text

    # 4. Feishu Config API
    resp4 = client.get("/api/v1/analytics/feishu/config")
    assert resp4.status_code == 200
    cfg = resp4.json()
    assert "webhook_url" in cfg

    # Save Feishu Config
    resp5 = client.post("/api/v1/analytics/feishu/config", json={
        "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/mock_key",
        "secret": "my_secret",
        "enabled": True,
    })
    assert resp5.status_code == 200

    # 5. Feishu Test API & Push API (mocked payload dispatch)
    with patch("apps.api.routes.analytics.feishu_service._post_payload", new_callable=AsyncMock) as mock_post:
        from packages.analytics.contracts import FeishuPushResponse
        mock_post.return_value = FeishuPushResponse(
            success=True,
            status_code=200,
            message="推送成功，飞书群已接收到业务卡片消息",
            feishu_log_id="mock_log_id",
        )

        resp6 = client.post("/api/v1/analytics/feishu/test", json={
            "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/mock_key",
        })
        assert resp6.status_code == 200
        assert resp6.json()["success"] is True

        resp7 = client.post("/api/v1/analytics/feishu/push", json={"period": "7d"})
        assert resp7.status_code == 200
        assert resp7.json()["success"] is True


@pytest.mark.asyncio
async def test_voc_report_period_dynamic_topics_and_cluster_metrics():
    """Validates real cluster topic counting, period temporal isolation, chit-chat elimination and category metrics."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        conv = Conversation(
            workspace_id=ws.id,
            source_type="wechat_archive",
            source_conversation_id="conv_dyn_1",
            display_name="C2核心用户群",
        )
        session.add(conv)
        await session.flush()

        # Seed Messages using current datetime
        anchor_dt = datetime.now()
        past_dt = anchor_dt - timedelta(days=3)

        m_anchor = Message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id="user_1",
            source_file_id="f1",
            source_sequence=1,
            source_record_hash="hash_a1",
            raw_text="AI制谱后保存一直失效，调子全乱了",
            normalized_text="AI制谱后保存一直失效，调子全乱了",
            sent_at=anchor_dt,
        )
        m_past = Message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id="user_2",
            source_file_id="f1",
            source_sequence=2,
            source_record_hash="hash_a2",
            raw_text="固件OTA在线升级死机，开机蓝灯常亮",
            normalized_text="固件OTA在线升级死机，开机蓝灯常亮",
            sent_at=past_dt,
        )
        session.add_all([m_anchor, m_past])
        await session.flush()

        # Seed 3 Episodes: 2 today (1 real bug, 1 chit-chat), 1 past (real blocker bug)
        ep_today_bug = Episode(
            workspace_id=ws.id,
            conversation_id=conv.id,
            title="AI制谱后保存失效与和弦混乱",
            summary="用户反馈AI制谱完成后草稿无法保存，调子乱",
            category_hint="曲谱与乐库",
            started_at=anchor_dt,
            ended_at=anchor_dt,
            message_count=18,
            participants_json=["user_1", "user_3"],
            state="active",
        )
        ep_today_chat = Episode(
            workspace_id=ws.id,
            conversation_id=conv.id,
            title="日常问候琴友开箱视频点赞与气氛调控",
            summary="琴友打卡问候",
            category_hint="综合交流",
            started_at=anchor_dt,
            ended_at=anchor_dt,
            message_count=12,
            participants_json=["user_4"],
            state="active",
        )
        ep_past_bug = Episode(
            workspace_id=ws.id,
            conversation_id=conv.id,
            title="C2设备低电量OTA升级死机无法开机",
            summary="低电量升级固件变砖，需要返厂",
            category_hint="固件与电源管理",
            started_at=past_dt,
            ended_at=past_dt,
            message_count=25,
            participants_json=["user_2", "user_5", "user_6"],
            state="active",
        )
        session.add_all([ep_today_bug, ep_today_chat, ep_past_bug])
        await session.flush()

        # Seed Insight linked to ep_today_bug
        ins_today = Insight(
            workspace_id=ws.id,
            episode_id=ep_today_bug.id,
            insight_type="issue",
            module="曲谱与乐库 · AI制谱与扒谱",
            severity="major",
            priority="P1",
            summary="用户反馈AI制谱草稿保存失效且和弦识别错乱",
            description="用户在多次操作后发现制谱草稿无法保存",
            state="approved",
        )
        ins_past = Insight(
            workspace_id=ws.id,
            episode_id=ep_past_bug.id,
            insight_type="issue",
            module="固件与电源 · OTA升级",
            severity="blocker",
            priority="P0",
            summary="低电量OTA升级导致吉他主板锁死",
            description="低电量强行OTA导致固件时序崩溃",
            state="approved",
        )
        session.add_all([ins_today, ins_past])
        await session.commit()

        generator = ReportGenerator(session)

        # 1. Test Today Overview & High Value Content
        op_today = await generator.get_operational_overview(period="today")
        assert op_today.total_episodes == 2
        # Deduplicated topic clusters count
        assert op_today.total_topics == 2
        assert op_today.total_insights == 1

        hv_today = await generator.get_high_value_content(period="today", force_mock=True)
        assert len(hv_today.high_value_topics) == 1
        t_today = hv_today.high_value_topics[0]
        assert "AI制谱" in t_today.title
        assert t_today.severity == "major"
        assert t_today.suggested_action is not None
        # Verify chit chat topic is filtered
        assert not any("开箱视频" in t.title for t in hv_today.high_value_topics)

        # Category dynamics should have distinct counts
        cat_sheet = next(c for c in hv_today.category_dynamics if c.category_zh == "曲谱与乐库")
        assert cat_sheet.topic_count == 1
        assert cat_sheet.insight_count == 1
        assert cat_sheet.total_count == 2

        # 2. Test 7d Overview & High Value Content
        op_7d = await generator.get_operational_overview(period="7d")
        assert op_7d.total_episodes == 3
        assert op_7d.total_topics == 3  # 3 distinct clusters in the 7d window
        assert op_7d.total_insights == 2

        hv_7d = await generator.get_high_value_content(period="7d", force_mock=True)
        assert len(hv_7d.high_value_topics) == 2
        # Blocker issue (ep_past_bug) should be ranked first due to blocker severity weight
        assert hv_7d.high_value_topics[0].severity == "blocker"
        assert "OTA升级" in hv_7d.high_value_topics[0].title


@pytest.mark.asyncio
async def test_voc_report_real_calendar_time_and_zero_data_integrity():
    """
    Validates that:
    1. VoC report time ranges strictly anchor on real calendar time (datetime.now()),
       even if past archived messages exist in the database (e.g. up to 2026-09-08).
    2. Zero-data periods (e.g. today when no messages received yet) gracefully handle:
       - OperationalOverview: 0 counts, truthful brief_summary
       - HighValueContent: 0 categories/topics/insights, objective recommendations, non-hallucinated AI summary
    3. Markdown rendering succeeds for both zero-data periods and fully populated periods.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_maker() as session:
        repo = RepositoryRegistry(session)
        ws = await repo.get_or_create_default_workspace()

        conv = Conversation(
            workspace_id=ws.id,
            source_type="wechat_archive",
            source_conversation_id="conv_archived",
            display_name="历史交流群",
        )
        session.add(conv)
        await session.flush()

        # Seed archived messages dated 2026-09-05 (which is outside today and 7d, but inside 30d and all)
        archived_dt = datetime(2026, 9, 5, 12, 0, 0)
        m1 = Message(
            workspace_id=ws.id,
            conversation_id=conv.id,
            participant_id="user_arch",
            source_file_id="f1",
            source_sequence=1,
            source_record_hash="hash_arch",
            raw_text="历史反馈：蓝牙偶发断连",
            normalized_text="历史反馈：蓝牙偶发断连",
            sent_at=archived_dt,
        )
        session.add(m1)
        await session.flush()

        ep1 = Episode(
            workspace_id=ws.id,
            conversation_id=conv.id,
            title="蓝牙偶发断连与自动重连机制",
            summary="历史用户反馈蓝牙连接不稳定",
            category_hint="连接与配对",
            started_at=archived_dt,
            ended_at=archived_dt,
            message_count=10,
            participants_json=["user_arch"],
            state="active",
        )
        session.add(ep1)
        await session.flush()

        ins1 = Insight(
            workspace_id=ws.id,
            episode_id=ep1.id,
            insight_type="issue",
            module="连接与配对 · 蓝牙连接",
            severity="major",
            priority="P1",
            summary="蓝牙偶发断连与重连超时",
            description="[现象] 蓝牙偶发断连 [建议] 增加重试机制",
            state="approved",
        )
        session.add(ins1)
        await session.commit()

        # Fixed reference time for deterministic validation: 2026-09-21
        ref_now = datetime(2026, 9, 21, 15, 30, 0)
        generator = ReportGenerator(session)

        # 1. Test "today": 2026-09-21 (0 messages/episodes)
        curr_s, curr_e, prev_s, prev_e, label, days, comp = await generator._resolve_period_dates("today", now=ref_now)
        assert curr_s.strftime("%Y-%m-%d") == "2026-09-21"
        assert curr_e.strftime("%Y-%m-%d") == "2026-09-21"
        assert days == 1
        assert "2026年09月21日" in label

        rep_today = await generator.generate_report(period="today", force_mock=True)
        # Verify dates on operational overview match real calendar date
        assert rep_today.operational_overview.start_date == datetime.now().strftime("%Y-%m-%d")
        assert rep_today.operational_overview.days_count == 1
        assert rep_today.operational_overview.total_messages == 0
        assert rep_today.operational_overview.total_episodes == 0
        assert rep_today.operational_overview.total_topics == 0
        assert rep_today.operational_overview.total_insights == 0
        assert "社群流水线运行平稳" in rep_today.operational_overview.brief_summary
        # High value content should have 0 items and graceful fallback text
        assert len(rep_today.high_value_content.category_dynamics) == 0
        assert len(rep_today.high_value_content.high_value_topics) == 0
        assert len(rep_today.high_value_content.critical_insights) == 0
        assert "暂无新增社群消息或突发异常上报" in rep_today.high_value_content.ai_executive_summary
        assert any("常规监控" in r for r in rep_today.high_value_content.key_recommendations)

        # 2. Test "7d": [today - 6d, today]
        today_date = datetime.now().date()
        expected_7d_start = (today_date - timedelta(days=6)).strftime("%Y-%m-%d")
        rep_7d = await generator.generate_report(period="7d", force_mock=True)
        assert rep_7d.operational_overview.start_date == expected_7d_start
        assert rep_7d.operational_overview.days_count == 7
        assert rep_7d.operational_overview.total_messages == 0
        assert len(rep_7d.high_value_content.high_value_topics) == 0

        # 3. Test "all": should contain the archived records (2026-09-05)
        rep_all = await generator.generate_report(period="all", force_mock=True)
        assert rep_all.operational_overview.total_messages == 1
        assert rep_all.operational_overview.total_episodes == 1
        assert rep_all.operational_overview.total_topics == 1
        assert rep_all.operational_overview.total_insights == 1
        assert len(rep_all.high_value_content.category_dynamics) >= 1
        assert len(rep_all.high_value_content.high_value_topics) == 1
        assert len(rep_all.high_value_content.critical_insights) == 1

        # 4. Markdown Export test
        md_today = generator.render_markdown(rep_today)
        assert "社群流水线运行平稳" in md_today
        md_all = generator.render_markdown(rep_all)
        assert "蓝牙偶发断连" in md_all


