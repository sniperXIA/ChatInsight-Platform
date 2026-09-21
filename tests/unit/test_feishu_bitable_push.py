import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from packages.feishu_bitable.contracts import (
    FeishuBitableConfig,
    BitablePushResponse,
    BitablePushStats,
)
from packages.feishu_bitable.bitable_client import FeishuBitableClient
from packages.feishu_bitable.feishu_bitable_service import FeishuBitableService
from datetime import datetime, timedelta
from packages.persistence.db import get_session_context, init_db_engine, create_all_tables
from packages.persistence.models import Insight, InsightPushRecord, Workspace, Conversation, Episode


@pytest.fixture
def bitable_client():
    return FeishuBitableClient(timeout=5.0)


def test_format_insight_payload(bitable_client):
    insight = Insight(
        id="test-ins-001",
        workspace_id="ws-1",
        episode_id="ep-1",
        module="ui.font",
        tags_json=["ui", "font_size"],
        insight_type="feature_request",
        severity="minor",
        summary="用户建议App增加浅色主题模式（白底黑字）以提升白天场景下的可视性。",
        description="在白天环境使用App时，用户因当前界面缺乏浅色主题导致屏幕难以看清。",
        state="draft",
    )

    payload = bitable_client.format_insight_payload(insight)

    assert "功能模块" in payload
    assert "内容标签" in payload
    assert isinstance(payload["内容标签"], list)
    assert all(t.startswith("#") for t in payload["内容标签"])
    assert "反馈类型" in payload
    assert "洞察标题" in payload
    assert "5W1H事实" in payload
    assert "严重级别" in payload
    assert "fields" in payload
    assert payload["fields"]["洞察标题"] == insight.summary


@pytest.mark.asyncio
async def test_client_push_to_webhook(bitable_client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"code": 0, "msg": "success"}'
    mock_resp.json.return_value = {"code": 0, "msg": "success"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        success, status_code, body, err = await bitable_client.push_to_webhook(
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/real_url",
            payload={"test": "data"},
        )
        assert success is True
        assert status_code == 200
        assert err is None


@pytest.mark.asyncio
async def test_client_push_to_webhook_with_bearer_token(bitable_client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"code": 0, "msg": "success"}'
    mock_resp.json.return_value = {"code": 0, "msg": "success"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        success, status_code, body, err = await bitable_client.push_to_webhook(
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/real_url",
            payload={"test": "data"},
            bearer_token="kxgbxp0-M5dFe1rG4swMsa3f",
        )
        assert success is True
        assert status_code == 200
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer kxgbxp0-M5dFe1rG4swMsa3f"


def test_format_5w1h_multiline():
    raw_desc = (
        "[场景/位置] 用户在车载弱光环境下使用；"
        "[具体现象] 字号太小，高对比度不足导致反光看不清；"
        "[影响程度] 导致误操作；"
        "[环境/触发条件] 夜间弱光；"
        "[社群进展] 官方已立项。"
    )
    multiline = FeishuBitableClient.format_5w1h_multiline(raw_desc)
    assert "\n" in multiline
    lines = multiline.split("\n")
    assert len(lines) == 5
    assert lines[0].startswith("【场景/位置】")
    assert lines[1].startswith("【具体现象】")
    assert lines[2].startswith("【影响程度】")
    assert lines[3].startswith("【环境/触发条件】")
    assert lines[4].startswith("【社群进展】")
    # Verify trailing punctuation was cleaned
    assert not lines[0].endswith("；")
    assert not lines[1].endswith("；")


@pytest.mark.asyncio
async def test_client_test_connectivity_payload(bitable_client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"code": 0, "msg": "ok"}'
    mock_resp.json.return_value = {"code": 0, "msg": "ok"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        success, code, msg = await bitable_client.test_connectivity(
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/real_url"
        )
        assert success is True
        assert code == 200
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        sent_json = kwargs["json"]
        assert "内容标签" in sent_json
        assert len(sent_json["内容标签"]) == 10
        assert all(t.startswith("#") for t in sent_json["内容标签"])
        assert "5W1H事实" in sent_json
        assert "\n" in sent_json["5W1H事实"]
        assert "【场景/位置】" in sent_json["5W1H事实"]


@pytest.mark.asyncio
async def test_bitable_service_e2e(tmp_path):
    test_db = str(tmp_path / "test_bitable.db")
    init_db_engine(f"sqlite+aiosqlite:///{test_db}")
    await create_all_tables()

    test_cfg_path = str(tmp_path / "bitable_test_settings.json")
    async with get_session_context() as session:
        service = FeishuBitableService(session, config_path=test_cfg_path)

        # 1. Config test
        cfg = service.load_config()
        assert isinstance(cfg, FeishuBitableConfig)

        service.save_config(
            FeishuBitableConfig(
                webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/unit_test",
                enabled=True,
            )
        )
        assert service.load_config().webhook_url == "https://open.feishu.cn/open-apis/bot/v2/hook/unit_test"

        # 2. Stats
        stats = await service.get_push_stats("all")
        assert isinstance(stats, BitablePushStats)

        # 3. Test recent list with created insight and push record
        import uuid
        ws_id = f"ws-{uuid.uuid4().hex[:8]}"
        ws = Workspace(id=ws_id, name="Test WS")
        session.add(ws)
        await session.flush()

        conv_id = f"conv-{uuid.uuid4().hex[:8]}"
        conv = Conversation(
            id=conv_id,
            workspace_id=ws.id,
            source_type="wechat_archive",
            source_conversation_id=conv_id,
            display_name="Test Chat",
        )
        session.add(conv)
        await session.flush()

        now = datetime.utcnow()
        ep_id = f"ep-{uuid.uuid4().hex[:8]}"
        ep = Episode(
            id=ep_id,
            workspace_id=ws.id,
            conversation_id=conv.id,
            title="Test Episode",
            summary="Episode Summary",
            started_at=now,
            ended_at=now,
        )
        session.add(ep)
        await session.flush()

        test_ins_id = f"test-ins-{uuid.uuid4().hex[:8]}"
        test_ins = Insight(
            id=test_ins_id,
            workspace_id=ws.id,
            episode_id=ep.id,
            module="ui.theme",
            tags_json=["ui", "theme_switch"],
            insight_type="issue",
            severity="critical",
            summary="测试高价值缺陷：界面在弱光环境下黑屏崩溃",
            description="【场景/位置】用户在夜间模式使用；【具体现象】应用黑屏闪退；【影响程度】致命阻塞；【触发条件】切换深色主题；【社群进展】已定位崩溃堆栈。",
            confidence=0.95,
            status_in_chat="unresolved",
            state="approved",
        )
        session.add(test_ins)
        await session.commit()

        push_rec = InsightPushRecord(
            id=f"test-push-{uuid.uuid4().hex[:8]}",
            workspace_id=ws.id,
            insight_id=test_ins.id,
            target_platform="feishu_bitable",
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/unit_test",
            state="success",
            status_code=200,
            response_body="ok",
            operator="admin_tester",
        )
        session.add(push_rec)
        await session.commit()

        recent = await service.get_recent_pushed_insights(limit=10)
        assert isinstance(recent, list)
        assert len(recent) >= 1
        target_item = next((item for item in recent if item.id == test_ins.id), None)
        assert target_item is not None
        assert target_item.summary == "测试高价值缺陷：界面在弱光环境下黑屏崩溃"
        assert target_item.confidence_level == "high"
        assert target_item.factual_score == 1.0
        assert "🐛" in target_item.type_label_zh or "缺陷" in target_item.type_label_zh
        assert target_item.push_status == "success"
        assert target_item.push_count >= 1
        assert len(target_item.push_history) >= 1
        assert target_item.push_history[0].operator == "admin_tester"
        assert "【场景/位置】" in target_item.fact_5w1h


@pytest.mark.asyncio
async def test_recent_pushed_insights_strict_boundaries_and_period_filtering():
    import uuid
    init_db_engine("sqlite+aiosqlite:///:memory:")
    await create_all_tables()

    now = datetime.now()
    async with get_session_context() as session:
        service = FeishuBitableService(session)

        # 1. Setup workspace and episode
        ws = Workspace(id=f"ws-{uuid.uuid4().hex[:6]}", name="Boundaries Test WS")
        session.add(ws)
        await session.flush()

        conv_id = f"conv-{uuid.uuid4().hex[:6]}"
        conv = Conversation(
            id=conv_id,
            workspace_id=ws.id,
            source_type="wechat_archive",
            source_conversation_id=conv_id,
            display_name="Boundaries Chat",
        )
        session.add(conv)
        await session.flush()

        ep = Episode(
            id=f"ep-{uuid.uuid4().hex[:6]}",
            workspace_id=ws.id,
            conversation_id=conv.id,
            title="Episode for boundaries",
            summary="Episode Summary",
            started_at=now,
            ended_at=now,
        )
        session.add(ep)
        await session.flush()

        # 2. Create 3 insights:
        # - ins_pushed_today: pushed today (success)
        # - ins_pushed_old: pushed 15 days ago (failed)
        # - ins_unpushed: never pushed (pending)
        ins_today = Insight(
            id=f"ins-today-{uuid.uuid4().hex[:6]}",
            workspace_id=ws.id,
            episode_id=ep.id,
            module="search.filter",
            insight_type="feature_request",
            severity="major",
            summary="今日成功推送的需求：多维搜索支持拼音首字母匹配",
            description="【具体现象】今日推送到飞书多维表格跟踪。",
            confidence=0.9,
            status_in_chat="unresolved",
            state="approved",
            created_at=now,
        )
        ins_old = Insight(
            id=f"ins-old-{uuid.uuid4().hex[:6]}",
            workspace_id=ws.id,
            episode_id=ep.id,
            module="account.login",
            insight_type="issue",
            severity="critical",
            summary="半个月前推送失败的缺陷：微信授权异常",
            description="【具体现象】15天前尝试推送到飞书时超时失败。",
            confidence=0.88,
            status_in_chat="unresolved",
            state="approved",
            created_at=now - timedelta(days=20),
        )
        ins_unpushed = Insight(
            id=f"ins-pending-{uuid.uuid4().hex[:6]}",
            workspace_id=ws.id,
            episode_id=ep.id,
            module="music.player",
            insight_type="inquiry",
            severity="minor",
            summary="从未推送的待推送需求：播放器后台常驻咨询",
            description="【具体现象】纯待推送需求，绝对不能出现在最近推送列表中！",
            confidence=0.75,
            status_in_chat="unresolved",
            state="draft",
            created_at=now,
        )
        session.add_all([ins_today, ins_old, ins_unpushed])
        await session.flush()

        # 3. Add push records:
        # Push record for ins_today (created now)
        rec_today = InsightPushRecord(
            id=f"push-rec-today-{uuid.uuid4().hex[:6]}",
            workspace_id=ws.id,
            insight_id=ins_today.id,
            target_platform="feishu_bitable",
            webhook_url="https://open.feishu.cn/hook",
            state="success",
            status_code=200,
            response_body="ok",
            operator="tester_today",
            created_at=now,
        )
        # Push record for ins_old (created 15 days ago)
        rec_old = InsightPushRecord(
            id=f"push-rec-old-{uuid.uuid4().hex[:6]}",
            workspace_id=ws.id,
            insight_id=ins_old.id,
            target_platform="feishu_bitable",
            webhook_url="https://open.feishu.cn/hook",
            state="failed",
            status_code=500,
            error_message="Gateway Timeout",
            operator="tester_old",
            created_at=now - timedelta(days=15),
        )
        session.add_all([rec_today, rec_old])
        await session.commit()

        # 4. Test today (今日):
        # Should ONLY return ins_today. Strict exclusion of ins_old (15d ago) and ins_unpushed (pending).
        res_today = await service.get_recent_pushed_insights(date_preset="today")
        today_ids = [item.id for item in res_today]
        assert ins_today.id in today_ids
        assert ins_old.id not in today_ids
        assert ins_unpushed.id not in today_ids
        assert all(item.push_status in ("success", "failed") for item in res_today)

        # 5. Test 7d (近7天):
        # Should return ins_today, but NOT ins_old (15d ago) and NOT ins_unpushed.
        res_7d = await service.get_recent_pushed_insights(date_preset="7d")
        d7_ids = [item.id for item in res_7d]
        assert ins_today.id in d7_ids
        assert ins_old.id not in d7_ids
        assert ins_unpushed.id not in d7_ids

        # 6. Test 30d (近30天):
        # Should return BOTH ins_today (success) and ins_old (failed). NEVER ins_unpushed.
        res_30d = await service.get_recent_pushed_insights(date_preset="30d")
        d30_ids = [item.id for item in res_30d]
        assert ins_today.id in d30_ids
        assert ins_old.id in d30_ids
        assert ins_unpushed.id not in d30_ids
        # Verify ins_old is returned with push_status == "failed"
        old_item = next(item for item in res_30d if item.id == ins_old.id)
        assert old_item.push_status == "failed"

        # 7. Test all (全周期历史):
        # Returns both pushed items, ordered by latest push time (ins_today first, then ins_old).
        res_all = await service.get_recent_pushed_insights(date_preset="all")
        assert len(res_all) == 2
        assert res_all[0].id == ins_today.id
        assert res_all[1].id == ins_old.id
        assert ins_unpushed.id not in [i.id for i in res_all]

