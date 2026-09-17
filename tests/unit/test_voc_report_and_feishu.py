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
        assert len(overview.daily_trends) == 8
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
