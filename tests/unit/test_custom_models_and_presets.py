import json
import os
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient, Response
from unittest.mock import patch, AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.main import app
from packages.model_gateway.settings_manager import (
    DEFAULT_SYSTEM_PROMPTS,
    PROVIDER_PRESETS,
    ModelSettingsConfig,
    ModuleConfigItem,
    SettingsManager,
    build_models_url_candidates,
    is_vision_model,
    normalize_api_base_url,
)
from packages.persistence.db import Base, get_session


def test_build_models_url_candidates():
    # 1. NVIDIA NIM Base URL without /v1
    c1 = build_models_url_candidates('https://integrate.api.nvidia.com')
    assert 'https://integrate.api.nvidia.com/v1/models' in c1
    assert 'https://integrate.api.nvidia.com/models' in c1

    # 2. NVIDIA NIM Base URL with /v1
    c2 = build_models_url_candidates('https://integrate.api.nvidia.com/v1')
    assert c2[0] == 'https://integrate.api.nvidia.com/v1/models'

    # 3. OpenRouter with /api/v1
    c3 = build_models_url_candidates('https://openrouter.ai/api/v1')
    assert 'https://openrouter.ai/api/v1/models' in c3

    # 4. Trailing slashes
    c4 = build_models_url_candidates('https://api.openai.com/v1///')
    assert c4[0] == 'https://api.openai.com/v1/models'

    # 5. Override models_url
    c5 = build_models_url_candidates('https://example.com', models_url_override='https://example.com/custom/models')
    assert c5[0] == 'https://example.com/custom/models'


def test_normalize_api_base_url():
    assert normalize_api_base_url('https://integrate.api.nvidia.com') == 'https://integrate.api.nvidia.com/v1'
    assert normalize_api_base_url('https://api.deepseek.com') == 'https://api.deepseek.com/v1'
    assert normalize_api_base_url('https://openrouter.ai/api/v1') == 'https://openrouter.ai/api/v1'


def test_settings_manager_custom_models(tmp_path):
    SettingsManager.clear_cache()
    temp_cfg_path = tmp_path / 'model_settings.json'
    with patch('packages.model_gateway.settings_manager.SETTINGS_FILE_PATH', temp_cfg_path):
        # 1. Default settings
        cfg = SettingsManager.get_settings(reload=True)
        assert cfg.custom_models == []

        # 2. Add custom model
        models = SettingsManager.add_custom_model('meta/llama-3.3-70b-instruct')
        assert 'meta/llama-3.3-70b-instruct' in models

        # Add another
        models = SettingsManager.add_custom_model('qwen2.5:14b')
        assert len(models) == 2

        # 3. Remove custom model
        models = SettingsManager.remove_custom_model('meta/llama-3.3-70b-instruct')
        assert 'meta/llama-3.3-70b-instruct' not in models
        assert 'qwen2.5:14b' in models
    SettingsManager.clear_cache()


def test_settings_manager_reset_prompt(tmp_path):
    SettingsManager.clear_cache()
    temp_cfg_path = tmp_path / 'model_settings.json'
    with patch('packages.model_gateway.settings_manager.SETTINGS_FILE_PATH', temp_cfg_path):
        # Mutate prompt
        cfg = SettingsManager.get_settings(reload=True)
        cfg.insight_extraction.system_prompt = 'Custom insight prompt'
        SettingsManager.save_settings(cfg)

        loaded = SettingsManager.get_settings()
        assert loaded.insight_extraction.system_prompt == 'Custom insight prompt'

        # Reset prompt
        default_p = SettingsManager.reset_module_prompt('insight_extraction')
        assert default_p == DEFAULT_SYSTEM_PROMPTS['insight_extraction']

        reloaded = SettingsManager.get_settings()
        assert reloaded.insight_extraction.system_prompt == DEFAULT_SYSTEM_PROMPTS['insight_extraction']
    SettingsManager.clear_cache()


@pytest.mark.asyncio
async def test_settings_api_full_flow(tmp_path):
    env_file = Path(".env")
    original_env_content = env_file.read_text(encoding="utf-8") if env_file.exists() else None
    SettingsManager.clear_cache()

    test_db_path = tmp_path / 'test_settings_api.db'
    temp_cfg_path = tmp_path / 'model_settings.json'
    test_engine = create_async_engine(f'sqlite+aiosqlite:///{test_db_path}', echo=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url='http://testserver') as client:
            with patch('packages.model_gateway.settings_manager.SETTINGS_FILE_PATH', temp_cfg_path):
                # 1. GET Presets
                presets_res = await client.get('/api/v1/settings/presets')
                assert presets_res.status_code == 200
                presets = presets_res.json()['presets']
                assert any(p['id'] == 'nvidia' for p in presets)

                # 2. GET Model Settings
                get_res = await client.get('/api/v1/settings/models')
                assert get_res.status_code == 200
                data = get_res.json()
                assert 'multimodal' in data
                assert 'insight_extraction' in data
                assert 'presets' in data

                # 3. POST Update Model Settings with granular configs
                update_payload = {
                    'provider': 'nvidia',
                    'base_url': 'https://integrate.api.nvidia.com',
                    'api_key': 'nvapi-testkey-12345678',
                    'default_model': 'meta/llama-3.3-70b-instruct',
                    # Include legacy flat field with a different value
                    'insight_model': 'old-flat-deepseek-chat',
                    'insight_extraction': {
                        'model': 'meta/llama-3.3-70b-instruct',
                        'temperature': 0.05,
                        'top_p': 0.9,
                        'top_k': 50,
                        'max_tokens': 4096,
                        'system_prompt': 'NVIDIA Custom Insight Extractor Prompt',
                    },
                    'segmentation': {
                        'model': 'nvidia/nemotron-4-340b-instruct',
                        'temperature': 0.1,
                        'top_p': 1.0,
                        'top_k': None,
                        'max_tokens': 1024,
                        'system_prompt': 'Segmentation Prompt',
                    },
                }
                update_res = await client.post('/api/v1/settings/models', json=update_payload)
                assert update_res.status_code == 200
                saved = update_res.json()
                assert saved['provider'] == 'nvidia'
                # Ensure granular model was preserved and NOT overwritten by old flat field
                assert saved['insight_extraction']['model'] == 'meta/llama-3.3-70b-instruct'
                assert saved['segmentation']['model'] == 'nvidia/nemotron-4-340b-instruct'
                assert saved['insight_extraction']['temperature'] == 0.05
                assert saved['insight_extraction']['top_k'] == 50
                assert saved['insight_extraction']['system_prompt'] == 'NVIDIA Custom Insight Extractor Prompt'

                # 4. POST Apply Default to All Modules (text model preserves vision model)
                apply_all_res = await client.post('/api/v1/settings/models', json={
                    'default_model': 'qwen/qwen-2.5-72b-instruct',
                    'apply_to_all_modules': True,
                })
                assert apply_all_res.status_code == 200
                all_saved = apply_all_res.json()
                assert all_saved['default_model'] == 'qwen/qwen-2.5-72b-instruct'
                # Text models update to default_model, multimodal retains vision model
                assert all_saved['segmentation']['model'] == 'qwen/qwen-2.5-72b-instruct'
                assert all_saved['insight_extraction']['model'] == 'qwen/qwen-2.5-72b-instruct'
                assert all_saved['clustering_judge']['model'] == 'qwen/qwen-2.5-72b-instruct'
                assert all_saved['research_assistant']['model'] == 'qwen/qwen-2.5-72b-instruct'
                assert all_saved['voc_report']['model'] == 'qwen/qwen-2.5-72b-instruct'
                assert is_vision_model(all_saved['multimodal']['model'])

                # 4b. When applying a vision model, multimodal also updates
                apply_vl_res = await client.post('/api/v1/settings/models', json={
                    'default_model': 'qwen/qwen-2.5-vl-72b-instruct',
                    'apply_to_all_modules': True,
                })
                assert apply_vl_res.status_code == 200
                assert apply_vl_res.json()['multimodal']['model'] == 'qwen/qwen-2.5-vl-72b-instruct'

                # 5. POST Custom Model
                custom_res = await client.post(
                    '/api/v1/settings/models/custom-model',
                    json={'model_name': 'nvidia/nemotron-4-340b-instruct', 'action': 'add'},
                )
                assert custom_res.status_code == 200
                assert 'nvidia/nemotron-4-340b-instruct' in custom_res.json()['custom_models']

                # 6. Reset Prompt
                reset_res = await client.post(
                    '/api/v1/settings/models/reset-prompt',
                    json={'module_name': 'insight_extraction'},
                )
                assert reset_res.status_code == 200
                assert reset_res.json()['system_prompt'] == DEFAULT_SYSTEM_PROMPTS['insight_extraction']
    finally:
        if original_env_content is not None:
            env_file.write_text(original_env_content, encoding="utf-8")
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ["OPENROUTER_API_KEY"] = ""
        SettingsManager.clear_cache()
        app.dependency_overrides.clear()
        await test_engine.dispose()
