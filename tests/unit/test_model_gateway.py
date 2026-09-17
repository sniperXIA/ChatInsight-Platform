import pytest
from packages.media_pipeline.contracts import ImageEnrichmentOutput
from packages.model_gateway.mock_adapter import MockModelAdapter
from packages.model_gateway.openrouter_adapter import OpenRouterAdapter


@pytest.mark.asyncio
async def test_mock_adapter_returns_valid_schema():
    mock = MockModelAdapter()
    output, usage = await mock.analyze_image(
        image_bytes=b"dummy_bytes",
        mime_type="image/jpeg",
        prompt="Analyze this",
        response_schema=ImageEnrichmentOutput,
    )

    assert isinstance(output, ImageEnrichmentOutput)
    assert len(output.ocr_blocks) > 0
    assert output.screen_or_scene is not None
    assert usage["total_tokens"] > 0
    assert mock.call_count == 1


def test_openrouter_json_extraction():
    adapter = OpenRouterAdapter(api_key="dummy")

    raw_1 = '```json\n{"summary": "test", "ocr_blocks": []}\n```'
    clean_1 = adapter._extract_json_text(raw_1)
    assert clean_1 == '{"summary": "test", "ocr_blocks": []}'

    raw_2 = '{"summary": "direct json"}'
    clean_2 = adapter._extract_json_text(raw_2)
    assert clean_2 == '{"summary": "direct json"}'
