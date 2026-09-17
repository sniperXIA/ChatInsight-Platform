import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from packages.feishu_bitable.contracts import (
    FeishuBitableConfig,
    BitablePushResponse,
    BitablePushStats,
)
from packages.feishu_bitable.bitable_client import FeishuBitableClient
from packages.feishu_bitable.feishu_bitable_service import FeishuBitableService
from packages.persistence.db import get_session_context, init_db_engine, create_all_tables
from packages.persistence.models import Insight, InsightPushRecord


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
    init_db_engine()
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

        # 3. Test recent list
        recent = await service.get_recent_pushed_insights(limit=5)
        assert isinstance(recent, list)
