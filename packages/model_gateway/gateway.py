import os
from pathlib import Path
from typing import Any
import yaml

from packages.model_gateway.mock_adapter import MockModelAdapter
from packages.model_gateway.openrouter_adapter import OpenRouterAdapter
from packages.model_gateway.protocols import EmbeddingProvider, TextModelProvider, VisionProvider
from packages.model_gateway.settings_manager import SettingsManager


class ModelGateway:
    """Central gateway for model provider dispatching and dynamic settings configuration."""

    def __init__(self, config_path: str = "config/default.yaml"):
        self.config_path = config_path

    def get_vision_provider(self, force_mock: bool = False) -> VisionProvider:
        if force_mock:
            return MockModelAdapter()

        settings = SettingsManager.get_settings()
        api_key = settings.api_key or os.getenv("OPENROUTER_API_KEY", "")

        is_custom_or_local = (
            "localhost" in settings.base_url
            or "127.0.0.1" in settings.base_url
            or (settings.provider == "custom" and bool(settings.base_url))
        )
        if api_key or is_custom_or_local:
            timeout_s = getattr(settings, "request_timeout_seconds", 240.0) or 240.0
            return OpenRouterAdapter(
                api_key=api_key,
                base_url=settings.base_url,
                default_vision_model=settings.multimodal.model,
                timeout_seconds=timeout_s,
            )

        return MockModelAdapter()

    def get_text_provider(self, force_mock: bool = False) -> TextModelProvider:
        if force_mock:
            return MockModelAdapter()

        settings = SettingsManager.get_settings()
        api_key = settings.api_key or os.getenv("OPENROUTER_API_KEY", "")

        is_custom_or_local = (
            "localhost" in settings.base_url
            or "127.0.0.1" in settings.base_url
            or (settings.provider == "custom" and bool(settings.base_url))
        )
        if api_key or is_custom_or_local:
            timeout_s = getattr(settings, "request_timeout_seconds", 240.0) or 240.0
            return OpenRouterAdapter(
                api_key=api_key,
                base_url=settings.base_url,
                default_text_model=settings.default_model,
                timeout_seconds=timeout_s,
            )

        return MockModelAdapter()

    def get_embedding_provider(self, force_mock: bool = False) -> EmbeddingProvider:
        if force_mock:
            return MockModelAdapter()

        settings = SettingsManager.get_settings()
        api_key = settings.api_key or os.getenv("OPENROUTER_API_KEY", "")

        is_custom_or_local = (
            "localhost" in settings.base_url
            or "127.0.0.1" in settings.base_url
            or (settings.provider == "custom" and bool(settings.base_url))
        )
        if api_key or is_custom_or_local:
            timeout_s = getattr(settings, "request_timeout_seconds", 240.0) or 240.0
            return OpenRouterAdapter(
                api_key=api_key,
                base_url=settings.base_url,
                timeout_seconds=timeout_s,
            )

        return MockModelAdapter()

