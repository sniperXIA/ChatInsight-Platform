import json
import pytest
from pydantic import BaseModel
from httpx import Response
from unittest.mock import AsyncMock, patch

from packages.model_gateway.openrouter_adapter import OpenRouterAdapter
from packages.model_gateway.settings_manager import (
    SettingsManager,
    ModelSettingsConfig,
    ModuleConfigItem,
    is_vision_model,
)

class DummySchema(BaseModel):
    title: str
    count: int

def test_is_vision_model_classification():
    assert is_vision_model('qwen/qwen-2.5-vl-72b-instruct') is True
    assert is_vision_model('meta/llama-3.2-11b-vision-instruct') is True
    assert is_vision_model('gpt-4o') is True
    assert is_vision_model('deepseek-ai/deepseek-vl-7b-chat') is True
    assert is_vision_model('minimax/minimax-m3:free') is True
    assert is_vision_model('minimax-vl-01') is True
    assert is_vision_model('deepseek/deepseek-chat') is False
    assert is_vision_model('meta/llama-3.3-70b-instruct') is False

def test_json_thinking_tag_stripping():
    adapter = OpenRouterAdapter(api_key='fake')
    raw_with_think = '<think>Reasoning step 1 2 3</think>```json\n{"title": "Test", "count": 42}\n```'
    clean = adapter._extract_json_text(raw_with_think)
    obj = DummySchema.model_validate_json(clean)
    assert obj.title == 'Test'
    assert obj.count == 42

@pytest.mark.asyncio
async def test_openrouter_adapter_retry_on_response_format_error():
    adapter = OpenRouterAdapter(api_key='fake_key', base_url='https://fake.api.com/v1')
    
    resp_400 = Response(400, text=json.dumps({"error": "response_format is not supported"}))
    resp_200 = Response(200, text=json.dumps({
        "choices": [{"message": {"content": json.dumps({"title": "Success", "count": 10})}}]
    }))
    
    with patch('httpx.AsyncClient.post', side_effect=[resp_400, resp_200]):
        res, usage = await adapter._call_and_parse(
            messages=[{'role': 'user', 'content': 'test'}],
            response_schema=DummySchema,
            model='test-model',
        )
        assert res.title == 'Success'
        assert res.count == 10

@pytest.mark.asyncio
async def test_openrouter_adapter_multimodal_fallback_on_text_model():
    adapter = OpenRouterAdapter(api_key='fake_key', base_url='https://fake.api.com/v1')
    
    resp_400 = Response(400, text=json.dumps({"error": "Model does not support image input"}))
    resp_200 = Response(200, text=json.dumps({
        "choices": [{"message": {"content": json.dumps({"title": "Fallback Image Summary", "count": 1})}}]
    }))
    
    with patch('httpx.AsyncClient.post', side_effect=[resp_400, resp_200]):
        res, usage = await adapter.analyze_image(
            image_bytes=b'fake_bytes',
            mime_type='image/png',
            prompt='analyze image',
            response_schema=DummySchema,
            model='minimax/minimax-m3:free',
        )
        assert res.title == 'Fallback Image Summary'
        assert res.count == 1

@pytest.mark.asyncio
async def test_openrouter_adapter_recovers_truncated_json():
    adapter = OpenRouterAdapter(api_key='fake_key', base_url='https://fake.api.com/v1')
    
    # Truncated JSON without closing brackets
    truncated_content = '''{
  "insights": [
    {
      "title": "Recovered Item",
      "count": 99
    },
    {
      "title": "Cutoff Item",
      "count"
'''
    class BatchSchema(BaseModel):
        insights: list[DummySchema] = []

    resp_200 = Response(200, text=json.dumps({
        "choices": [{"message": {"content": truncated_content}}]
    }))
    
    with patch('httpx.AsyncClient.post', return_value=resp_200):
        res, usage = await adapter._call_and_parse(
            messages=[{'role': 'user', 'content': 'test'}],
            response_schema=BatchSchema,
            model='test-model',
        )
        assert len(res.insights) == 1
        assert res.insights[0].title == "Recovered Item"
        assert res.insights[0].count == 99
