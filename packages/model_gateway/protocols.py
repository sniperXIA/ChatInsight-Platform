from typing import Any, Optional, Protocol, Sequence, Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class VisionProvider(Protocol):
    """Protocol for Multimodal Vision Language Model analyzing images and keyframes."""

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
        """Returns (validated_model_instance, token_usage_info)."""
        ...


class TextModelProvider(Protocol):
    """Protocol for Text Reasoning LLM."""

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
        ...


class ASRProvider(Protocol):
    """Protocol for Automatic Speech Recognition."""

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/wav",
        language: str | None = "zh",
    ) -> tuple[str, list[dict[str, Any]]]:
        """Returns (full_transcript, segments)."""
        ...
