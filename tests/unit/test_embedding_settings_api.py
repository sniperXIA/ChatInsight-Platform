import pytest
from fastapi.testclient import TestClient
from apps.api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_presets_include_embedding_recommendations(client):
    """Verify built-in presets include default and recommended embedding models."""
    response = client.get("/api/v1/settings/presets")
    assert response.status_code == 200
    data = response.json()
    presets = data.get("presets", [])
    assert len(presets) > 0

    qwen_preset = next((p for p in presets if p["id"] == "qwen"), None)
    assert qwen_preset is not None
    assert qwen_preset.get("default_embedding_model") == "text-embedding-v3"
    assert "text-embedding-v3" in qwen_preset.get("recommended_embedding_models", [])

    openrouter_preset = next((p for p in presets if p["id"] == "openrouter"), None)
    assert openrouter_preset is not None
    assert "openai/text-embedding-3-small" in openrouter_preset.get("recommended_embedding_models", [])


def test_test_embedding_connectivity_mock(client):
    """Verify test-embedding API works with mock provider and returns vector dimensions and preview."""
    payload = {
        "provider": "mock",
        "dimensions": 768,
        "test_text": "ChatInsight 社群向量嵌入连通性测试",
    }
    response = client.post("/api/v1/settings/models/test-embedding", json=payload)
    assert response.status_code == 200
    res = response.json()
    assert res["success"] is True
    assert res["dimensions"] == 768
    assert len(res["embedding_preview"]) == 6
    assert res["model"] == "mock-768d"
    assert res["endpoint_used"] == "local://mock-hash-projection"
    assert res["tokens_used"] > 0
    assert res["error"] is None


def test_test_embedding_connectivity_custom_dimension_mock(client):
    """Verify test-embedding API supports flexible dimensions."""
    payload = {
        "provider": "mock",
        "dimensions": 1024,
        "test_text": "LiberLive C2 智能吉他用户声音反馈",
    }
    response = client.post("/api/v1/settings/models/test-embedding", json=payload)
    assert response.status_code == 200
    res = response.json()
    assert res["success"] is True
    assert res["dimensions"] == 1024
    assert len(res["embedding_preview"]) == 6


def test_update_embedding_model_settings(client):
    """Verify updating embedding settings via /api/v1/settings/models."""
    get_res = client.get("/api/v1/settings/models")
    assert get_res.status_code == 200
    current_settings = get_res.json()

    try:
        # Modify embedding settings
        update_payload = dict(current_settings)
        update_payload["embedding"] = {
            "enabled": True,
            "provider": "qwen",
            "model": "text-embedding-v3",
            "dimensions": 1024,
            "batch_size": 32,
            "base_url": "",
            "api_key": "",
        }

        post_res = client.post("/api/v1/settings/models", json=update_payload)
        assert post_res.status_code == 200
        saved = post_res.json()
        assert saved["embedding"]["model"] == "text-embedding-v3"
        assert saved["embedding"]["batch_size"] == 32
        assert saved["embedding"]["dimensions"] == 1024 or saved["embedding"]["dimension"] == 1024
    finally:
        client.post("/api/v1/settings/models", json=current_settings)


@pytest.mark.asyncio
async def test_openrouter_adapter_accepts_flexible_kwargs_and_generates_embeddings():
    """Verify OpenRouterAdapter handles kwargs like default_model and timeout without TypeError, and slices batch properly."""
    from unittest.mock import AsyncMock, patch, MagicMock
    from packages.model_gateway.openrouter_adapter import OpenRouterAdapter

    adapter = OpenRouterAdapter(
        api_key="sk-test-fake",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="text-embedding-v3",
        timeout=30.0,
        unrecognized_kwarg="some_val",
    )
    assert adapter.default_text_model == "text-embedding-v3"
    assert adapter.timeout == 30.0

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"index": 0, "embedding": [0.123] * 1536},
            {"index": 1, "embedding": [0.456] * 1536},
        ],
        "usage": {"total_tokens": 42},
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        vecs, usage = await adapter.generate_embeddings(
            ["text 1", "text 2"],
            model="text-embedding-v3",
            batch_size=2,
            dimensions=1536,
        )
        assert len(vecs) == 2
        assert len(vecs[0]) == 1536
        assert usage["total_tokens"] == 42
        assert mock_post.await_count == 1


def test_test_embedding_connectivity_remote_mocked(client):
    """Verify test-embedding API works for remote DashScope/OpenRouter provider with mocked HTTP."""
    from unittest.mock import AsyncMock, patch, MagicMock

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"index": 0, "embedding": [0.05, -0.12, 0.88, 0.33, -0.04, 0.55] + [0.0] * 1530},
        ],
        "usage": {"total_tokens": 15},
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        payload = {
            "provider": "dashscope",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-realtestkey123456",
            "model": "text-embedding-v3",
            "dimensions": 1536,
            "test_text": "吉他固件更新反馈测试",
        }
        response = client.post("/api/v1/settings/models/test-embedding", json=payload)
        assert response.status_code == 200
        res = response.json()
        assert res["success"] is True
        assert res["dimensions"] == 1536
        assert len(res["embedding_preview"]) == 6
        assert res["embedding_preview"][0] == 0.05
        assert res["tokens_used"] == 15
        assert "dashscope.aliyuncs.com" in res["endpoint_used"]
        assert res["error"] is None


def test_test_embedding_connectivity_missing_key_warning(client):
    """Verify test-embedding API returns user-friendly guidance when API key is missing for remote provider."""
    from unittest.mock import patch, MagicMock
    with patch.dict("os.environ", {"OPENROUTER_API_KEY": "", "DASHSCOPE_API_KEY": ""}):
        with patch("packages.model_gateway.settings_manager.SettingsManager.get_settings") as mock_settings:
            mock_cfg = MagicMock()
            mock_cfg.api_key = ""
            mock_cfg.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            mock_cfg.embedding = None
            mock_settings.return_value = mock_cfg

            payload = {
                "provider": "dashscope",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "",
                "model": "text-embedding-v3",
            }
            response = client.post("/api/v1/settings/models/test-embedding", json=payload)
            assert response.status_code == 200
            res = response.json()
            assert res["success"] is False
            assert "未检测到有效的 API Key" in res["error"]

