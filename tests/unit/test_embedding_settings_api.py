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
