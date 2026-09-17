import os
from pathlib import Path
import pytest
import pytest_asyncio
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.persistence.db import Base, get_session


@pytest_asyncio.fixture
async def client(tmp_path):
    test_db_path = tmp_path / "test_settings.db"
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{test_db_path}", echo=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_get_and_update_model_settings(client, tmp_path):
    env_file = Path(".env")
    original_env_content = env_file.read_text(encoding="utf-8") if env_file.exists() else None
    original_api_key = os.getenv("OPENROUTER_API_KEY")
    temp_cfg = tmp_path / "test_model_settings.json"

    try:
        with patch("packages.model_gateway.settings_manager.SETTINGS_FILE_PATH", temp_cfg):
            # 1. Get Model Settings
            get_res = await client.get("/api/v1/settings/models")
            assert get_res.status_code == 200
            data = get_res.json()
            assert "base_url" in data
            assert "default_model" in data

            # 2. Update Model Settings
            update_res = await client.post(
                "/api/v1/settings/models",
                json={
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key": "sk-or-v1-test-key-1234567890",
                    "default_model": "deepseek/deepseek-chat",
                    "vision_model": "qwen/qwen-2.5-vl-72b-instruct",
                    "insight_model": "deepseek/deepseek-chat",
                    "judge_model": "deepseek/deepseek-chat",
                    "assistant_model": "deepseek/deepseek-chat",
                },
            )
            assert update_res.status_code == 200
            saved = update_res.json()
            assert saved["default_model"] == "deepseek/deepseek-chat"

            # 2.1 Update again with MASKED api_key (e.g. "sk-or-...7890")
            masked_update_res = await client.post(
                "/api/v1/settings/models",
                json={
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key": "sk-or-...7890",
                    "default_model": "deepseek/deepseek-chat",
                },
            )
            assert masked_update_res.status_code == 200
            from packages.model_gateway.settings_manager import SettingsManager
            cfg_now = SettingsManager.get_settings(reload=True)
            assert cfg_now.api_key == "sk-or-v1-test-key-1234567890"  # Real key preserved!
        # 3. Test Model Connectivity with null content (reasoning models)
        from apps.api.routes.settings import test_model_connectivity, TestModelRequest
        mock_resp = Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": "Thinking completed. ChatInsight OK",
                        }
                    }
                ]
            },
        )
        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            test_resp = await test_model_connectivity(
                TestModelRequest(
                    model_name="deepseek/deepseek-r1",
                    module_type="insight_extraction",
                    temperature=0.05,
                    top_k=50,
                )
            )
            assert test_resp.success is True
            assert "ChatInsight OK" in test_resp.response_preview
    finally:
        # Restore environment and .env
        from packages.model_gateway.settings_manager import SettingsManager
        SettingsManager.clear_cache()
        if original_env_content is not None:
            env_file.write_text(original_env_content, encoding="utf-8")
        if original_api_key:
            os.environ["OPENROUTER_API_KEY"] = original_api_key
