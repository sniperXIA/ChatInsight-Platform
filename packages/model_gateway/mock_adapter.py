from typing import Any, Optional, Sequence, Type, TypeVar
from pydantic import BaseModel

from packages.media_pipeline.contracts import (
    ImageEnrichmentOutput,
    ImageEntity,
    OCRBlock,
    VideoEnrichmentOutput,
    VideoTimelineSegment,
)

T = TypeVar("T", bound=BaseModel)


class MockModelAdapter:
    """Deterministic Mock Adapter for unit testing and offline development."""

    def __init__(self):
        self.call_count = 0

    async def analyze_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        response_schema: Type[T],
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        top_p: float = 1.0,
        top_k: Optional[int] = None,
        max_tokens: int = 2048,
        reasoning_effort: str | None = None,
    ) -> tuple[T, dict[str, Any]]:
        self.call_count += 1
        usage = {"prompt_tokens": 150, "completion_tokens": 80, "total_tokens": 230}

        if response_schema == ImageEnrichmentOutput:
            output = ImageEnrichmentOutput(
                summary="LiberLive C2 App 演奏界面截图，显示当前曲目与伴奏轨道",
                ocr_blocks=[
                    OCRBlock(text="LiberLive C2", confidence=0.99),
                    OCRBlock(text="扩展曲谱", confidence=0.95),
                    OCRBlock(text="返回", confidence=0.98),
                ],
                screen_or_scene="App演奏界面",
                user_action="用户正在尝试切换曲谱",
                observed_state="正常显示曲目列表",
                error_codes=[],
                entities=[
                    ImageEntity(entity_type="device_model", value="LiberLive C2", confidence=1.0),
                    ImageEntity(entity_type="app_feature", value="App演奏", confidence=0.95),
                ],
                safety_or_privacy_notes=[],
                uncertainty=[],
            )
            return output, usage  # type: ignore

        # Generic fallback instance
        return response_schema.model_construct(), usage

    async def generate_structured(
        self,
        messages: Sequence[dict[str, Any]],
        response_schema: Type[T],
        temperature: float = 0.0,
        top_p: float = 1.0,
        top_k: Optional[int] = None,
        max_tokens: int = 2048,
        model: str | None = None,
        reasoning_effort: str | None = None,
        enable_thinking: bool | None = None,
        thinking_budget: int | None = None,
        preserve_thinking: bool | None = None,
        max_completion_tokens: int | None = None,
        **kwargs: Any,
    ) -> tuple[T, dict[str, Any]]:
        self.call_count += 1
        usage = {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}
        return response_schema.model_construct(), usage

