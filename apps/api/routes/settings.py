import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from packages.importers.chat_settings import (
    ChatSettings,
    ChatSettingsManager,
    PathValidationReport,
)
from packages.importers.watchdog_service import ChatDirectoryWatcher
from packages.model_gateway.gateway import ModelGateway
from packages.model_gateway.mock_adapter import MockModelAdapter
from packages.model_gateway.openrouter_adapter import OpenRouterAdapter
from packages.model_gateway.settings_manager import (
    DEFAULT_SYSTEM_PROMPTS,
    PROVIDER_PRESETS,
    EmbeddingConfigItem,
    ModelSettingsConfig,
    ModuleConfigItem,
    SettingsManager,
    build_models_url_candidates,
    is_vision_model,
    normalize_api_base_url,
)

router = APIRouter(prefix="/api/v1/settings", tags=["System Settings & Model Configuration"])


class ModelSettingsResponse(BaseModel):
    provider: str = Field(default="qwen", description="服务商标识")
    base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", description="OpenAI 通用格式 API 地址")
    api_key: str = Field(default="", description="API 密钥 (已脱敏)")
    default_model: str = Field(default="qwen3.8-max", description="默认主力模型")
    global_enable_thinking: Optional[bool] = Field(default=None, description="全局思考模式开关")
    global_reasoning_effort: str = Field(default="none", description="全局思考程度: none | low | medium | high | xhigh | default")
    global_thinking_budget: Optional[int] = Field(default=None, description="全局思考Token预算")
    global_preserve_thinking: Optional[bool] = Field(default=None, description="全局多轮思考过程保留")
    vision_model: str = Field(default="qwen3.8-max", description="多模态视觉模型")
    insight_model: str = Field(default="qwen3.8-max", description="洞察提炼模型")
    judge_model: str = Field(default="qwen3.8-max", description="聚类裁判模型")
    assistant_model: str = Field(default="qwen3.8-max", description="研究助手模型")
    custom_models: list[str] = Field(default_factory=list, description="用户自定义添加的模型列表")
    request_timeout_seconds: float = Field(default=240.0, description="模型接口网络请求超时时间(秒)")

    # Granular Per-Module Configuration
    multimodal: ModuleConfigItem
    segmentation: ModuleConfigItem
    insight_extraction: ModuleConfigItem
    clustering_judge: ModuleConfigItem
    research_assistant: ModuleConfigItem
    voc_report: ModuleConfigItem
    embedding: Optional[EmbeddingConfigItem] = None

    presets: list[dict[str, Any]] = Field(default_factory=list, description="内置服务商预设")
    default_system_prompts: dict[str, str] = Field(default_factory=dict, description="系统默认提示词")


class UpdateModelSettingsRequest(BaseModel):
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    default_model: Optional[str] = None
    apply_to_all_modules: Optional[bool] = Field(default=False, description="一键将 default_model 应用到所有功能模块")
    global_enable_thinking: Optional[bool] = None
    global_reasoning_effort: Optional[str] = None
    global_thinking_budget: Optional[int] = None
    global_preserve_thinking: Optional[bool] = None
    apply_reasoning_effort_to_all_modules: Optional[bool] = Field(default=False, description="一键将全局思考设置应用到所有功能模块")
    vision_model: Optional[str] = None
    insight_model: Optional[str] = None
    judge_model: Optional[str] = None
    assistant_model: Optional[str] = None
    custom_models: Optional[list[str]] = None
    request_timeout_seconds: Optional[float] = None

    multimodal: Optional[ModuleConfigItem] = None
    segmentation: Optional[ModuleConfigItem] = None
    insight_extraction: Optional[ModuleConfigItem] = None
    clustering_judge: Optional[ModuleConfigItem] = None
    research_assistant: Optional[ModuleConfigItem] = None
    voc_report: Optional[ModuleConfigItem] = None
    embedding: Optional[EmbeddingConfigItem] = None


class FetchModelsRequest(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    models_url_override: Optional[str] = None


class FetchModelsResponse(BaseModel):
    success: bool
    models: list[str]
    embedding_models: list[str] = Field(default_factory=list, description="从端点检索识别出的向量嵌入模型列表")
    count: int
    endpoint_used: Optional[str] = None
    candidates_tried: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class CustomModelRequest(BaseModel):
    model_name: str
    action: str = Field(default="add", description="add | remove")


class ResetPromptRequest(BaseModel):
    module_name: str = Field(description="multimodal | segmentation | insight_extraction | clustering_judge | research_assistant | voc_report")


class TestModelRequest(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: str
    temperature: float = 0.0
    max_tokens: int = 150
    top_k: Optional[int] = None
    enable_thinking: Optional[bool] = None
    reasoning_effort: Optional[str] = None
    thinking_budget: Optional[int] = None
    preserve_thinking: Optional[bool] = None
    system_prompt: Optional[str] = None
    module_type: Optional[str] = Field(default=None, description="multimodal | segmentation | insight_extraction | clustering_judge | research_assistant | voc_report")


class TestModelResponse(BaseModel):
    success: bool
    latency_ms: float
    model: str
    endpoint_used: Optional[str] = None
    response_preview: Optional[str] = None
    reasoning_preview: Optional[str] = None
    reasoning_tokens: Optional[int] = None
    error: Optional[str] = None


class TestEmbeddingRequest(BaseModel):
    provider: Optional[str] = Field(default="qwen", description="qwen | openrouter | openai | mock | custom")
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = Field(default="text-embedding-v3", description="向量模型名称")
    dimensions: Optional[int] = Field(default=None, description="向量维度(如 1024, 1536)")
    test_text: Optional[str] = Field(default="ChatInsight 向量语义检索连通性测试", description="测试文本")


class TestEmbeddingResponse(BaseModel):
    success: bool
    latency_ms: float
    dimensions: int
    embedding_preview: list[float]
    model: str
    endpoint_used: Optional[str] = None
    tokens_used: Optional[int] = None
    error: Optional[str] = None


def mask_key(raw_key: str) -> str:
    if not raw_key:
        return ""
    if len(raw_key) > 10:
        return f"{raw_key[:6]}...{raw_key[-4:]}"
    return "******"


def build_response_from_cfg(cfg: ModelSettingsConfig) -> ModelSettingsResponse:
    return ModelSettingsResponse(
        provider=cfg.provider,
        base_url=cfg.base_url,
        api_key=mask_key(cfg.api_key),
        default_model=cfg.default_model,
        global_enable_thinking=getattr(cfg, "global_enable_thinking", None),
        global_reasoning_effort=getattr(cfg, "global_reasoning_effort", "none"),
        global_thinking_budget=getattr(cfg, "global_thinking_budget", None),
        global_preserve_thinking=getattr(cfg, "global_preserve_thinking", None),
        vision_model=cfg.multimodal.model,
        insight_model=cfg.insight_extraction.model,
        judge_model=cfg.clustering_judge.model,
        assistant_model=cfg.research_assistant.model,
        custom_models=cfg.custom_models,
        multimodal=cfg.multimodal,
        segmentation=cfg.segmentation,
        insight_extraction=cfg.insight_extraction,
        clustering_judge=cfg.clustering_judge,
        research_assistant=cfg.research_assistant,
        voc_report=cfg.voc_report,
        embedding=getattr(cfg, "embedding", None),
        request_timeout_seconds=getattr(cfg, "request_timeout_seconds", 240.0) or 240.0,
        presets=PROVIDER_PRESETS,
        default_system_prompts=DEFAULT_SYSTEM_PROMPTS,
    )


@router.get("/models", response_model=ModelSettingsResponse)
async def get_model_settings():
    """Get current AI model configuration with per-module details and presets."""
    cfg = SettingsManager.get_settings(reload=True)
    return build_response_from_cfg(cfg)


@router.post("/models", response_model=ModelSettingsResponse)
async def update_model_settings(payload: UpdateModelSettingsRequest):
    """Save and update full AI model configuration across all modules."""
    current_cfg = SettingsManager.get_settings()

    # Determine api_key:
    # If the user leaves the input blank, or if the payload contains the masked string (e.g. "sk-or-...1e0e" or "******"),
    # preserve the existing unmasked api_key from current_cfg or env!
    raw_input_key = (payload.api_key or "").strip()
    is_masked = ("..." in raw_input_key) or ("***" in raw_input_key)

    existing_real_key = current_cfg.api_key
    if not existing_real_key or ("..." in existing_real_key) or ("***" in existing_real_key):
        existing_real_key = os.getenv("OPENROUTER_API_KEY", "")
        if "..." in existing_real_key or "***" in existing_real_key:
            existing_real_key = ""

    if not raw_input_key or is_masked:
        new_key = existing_real_key
    else:
        new_key = raw_input_key

    base_url = payload.base_url.strip() if payload.base_url else current_cfg.base_url
    default_model = payload.default_model.strip() if payload.default_model else current_cfg.default_model
    provider = payload.provider.strip() if payload.provider else current_cfg.provider

    # Granular module updates:
    # 1. If payload explicitly provided the granular module object, use it directly!
    # 2. Only if the module object is None, fallback to legacy flat model names if provided.
    if payload.multimodal is not None:
        multimodal = payload.multimodal
    elif payload.vision_model:
        multimodal = current_cfg.multimodal.model_copy(update={"model": payload.vision_model.strip()})
    else:
        multimodal = current_cfg.multimodal

    if payload.segmentation is not None:
        segmentation = payload.segmentation
    else:
        segmentation = current_cfg.segmentation

    if payload.insight_extraction is not None:
        insight_extraction = payload.insight_extraction
    elif payload.insight_model:
        insight_extraction = current_cfg.insight_extraction.model_copy(update={"model": payload.insight_model.strip()})
    else:
        insight_extraction = current_cfg.insight_extraction

    if payload.clustering_judge is not None:
        clustering_judge = payload.clustering_judge
    elif payload.judge_model:
        clustering_judge = current_cfg.clustering_judge.model_copy(update={"model": payload.judge_model.strip()})
    else:
        clustering_judge = current_cfg.clustering_judge

    if payload.research_assistant is not None:
        research_assistant = payload.research_assistant
    elif payload.assistant_model:
        research_assistant = current_cfg.research_assistant.model_copy(update={"model": payload.assistant_model.strip()})
    else:
        research_assistant = current_cfg.research_assistant

    if payload.voc_report is not None:
        voc_report = payload.voc_report
    else:
        voc_report = current_cfg.voc_report

    # If apply_to_all_modules is True, propagate default_model to text module configurations
    if payload.apply_to_all_modules and default_model:
        segmentation.model = default_model
        insight_extraction.model = default_model
        clustering_judge.model = default_model
        research_assistant.model = default_model
        voc_report.model = default_model

        # Only update multimodal if default_model is a vision model
        if is_vision_model(default_model):
            multimodal.model = default_model
        elif not is_vision_model(multimodal.model):
            # Fallback to provider preset vision model
            matched_preset = next((p for p in PROVIDER_PRESETS if p["id"] == provider or p["provider_type"] == provider), None)
            multimodal.model = matched_preset["default_vision_model"] if matched_preset else "qwen3.8-max"

    custom_models = payload.custom_models if payload.custom_models is not None else current_cfg.custom_models
    request_timeout_seconds = (
        payload.request_timeout_seconds
        if payload.request_timeout_seconds is not None
        else getattr(current_cfg, "request_timeout_seconds", 240.0)
    )

    global_enable_thinking = payload.global_enable_thinking if payload.global_enable_thinking is not None else getattr(current_cfg, "global_enable_thinking", None)
    global_reasoning_effort = payload.global_reasoning_effort.strip() if payload.global_reasoning_effort else getattr(current_cfg, "global_reasoning_effort", "none")
    global_thinking_budget = payload.global_thinking_budget if payload.global_thinking_budget is not None else getattr(current_cfg, "global_thinking_budget", None)
    global_preserve_thinking = payload.global_preserve_thinking if payload.global_preserve_thinking is not None else getattr(current_cfg, "global_preserve_thinking", None)

    if payload.apply_reasoning_effort_to_all_modules:
        for mod in (multimodal, segmentation, insight_extraction, clustering_judge, research_assistant, voc_report):
            if global_reasoning_effort:
                mod.reasoning_effort = global_reasoning_effort
            if global_enable_thinking is not None:
                mod.enable_thinking = global_enable_thinking
            if global_thinking_budget is not None:
                mod.thinking_budget = global_thinking_budget
            if global_preserve_thinking is not None:
                mod.preserve_thinking = global_preserve_thinking

    updated_cfg = ModelSettingsConfig(
        provider=provider,
        base_url=base_url,
        api_key=new_key,
        default_model=default_model,
        global_enable_thinking=global_enable_thinking,
        global_reasoning_effort=global_reasoning_effort,
        global_thinking_budget=global_thinking_budget,
        global_preserve_thinking=global_preserve_thinking,
        custom_models=custom_models,
        request_timeout_seconds=request_timeout_seconds,
        multimodal=multimodal,
        segmentation=segmentation,
        insight_extraction=insight_extraction,
        clustering_judge=clustering_judge,
        research_assistant=research_assistant,
        voc_report=voc_report,
        embedding=payload.embedding if payload.embedding is not None else getattr(current_cfg, "embedding", EmbeddingConfigItem()),
    )

    saved = SettingsManager.save_settings(updated_cfg)
    return build_response_from_cfg(saved)


@router.post("/models/fetch-models", response_model=FetchModelsResponse)
async def fetch_available_models(payload: FetchModelsRequest = FetchModelsRequest()):
    """
    Call OpenAI-compatible /models endpoint to retrieve model list.
    Uses multi-candidate probing (referenced from cc-switch) for NVIDIA NIM, OpenRouter, etc.
    """
    settings = SettingsManager.get_settings()
    base_url = (payload.base_url or settings.base_url).strip()
    raw_key = (payload.api_key or "").strip()
    if not raw_key or "..." in raw_key or "***" in raw_key:
        api_key = settings.api_key or os.getenv("OPENROUTER_API_KEY", "")
    else:
        api_key = raw_key

    candidates = build_models_url_candidates(base_url, models_url_override=payload.models_url_override)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    if "openrouter.ai" in base_url:
        headers["HTTP-Referer"] = "http://localhost:8000"
        headers["X-Title"] = "ChatInsight Platform"

    candidates_tried: list[str] = []
    last_error: Optional[str] = None

    async with httpx.AsyncClient(timeout=15.0) as client:
        for url in candidates:
            candidates_tried.append(url)
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    # Standard OpenAI format: {"data": [{"id": "..."}, ...]}
                    model_ids: list[str] = []
                    if isinstance(data, dict):
                        if "data" in data and isinstance(data["data"], list):
                            for m in data["data"]:
                                if isinstance(m, dict) and m.get("id"):
                                    model_ids.append(str(m["id"]))
                                elif isinstance(m, str):
                                    model_ids.append(m)
                        elif "models" in data and isinstance(data["models"], list):
                            for m in data["models"]:
                                if isinstance(m, dict):
                                    name = m.get("name") or m.get("id") or m.get("model")
                                    if name:
                                        model_ids.append(str(name))
                                elif isinstance(m, str):
                                    model_ids.append(m)
                    elif isinstance(data, list):
                        for m in data:
                            if isinstance(m, dict) and m.get("id"):
                                model_ids.append(str(m["id"]))
                            elif isinstance(m, str):
                                model_ids.append(m)

                    model_ids = sorted(list(dict.fromkeys(model_ids)))
                    embedding_keywords = ("embed", "embedding", "bge", "bce", "gte", "e5", "text2vec", "sentence")
                    embedding_candidates = [m for m in model_ids if any(k in m.lower() for k in embedding_keywords)]
                    if model_ids:
                        return FetchModelsResponse(
                            success=True,
                            models=model_ids,
                            embedding_models=embedding_candidates,
                            count=len(model_ids),
                            endpoint_used=url,
                            candidates_tried=candidates_tried,
                        )
                elif resp.status_code in (401, 403):
                    return FetchModelsResponse(
                        success=False,
                        models=[],
                        embedding_models=[],
                        count=0,
                        endpoint_used=url,
                        candidates_tried=candidates_tried,
                        error=f"认证失败 (HTTP {resp.status_code})：请检查输入的 API Key 是否有效或具备权限。",
                    )
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except httpx.RequestError as exc:
                last_error = f"网络连接异常: {str(exc)}"

    return FetchModelsResponse(
        success=False,
        models=[],
        embedding_models=[],
        count=0,
        candidates_tried=candidates_tried,
        error=f"所有候选端点均未能获取到模型列表。最后错误: {last_error}。提示：您可以点击“+ 添加自定义模型”手动输入并使用模型名称。",
    )


@router.post("/models/custom-model")
async def manage_custom_model(payload: CustomModelRequest):
    """Add or remove custom model from persistent list."""
    if payload.action == "remove":
        models = SettingsManager.remove_custom_model(payload.model_name)
    else:
        models = SettingsManager.add_custom_model(payload.model_name)
    return {"success": True, "custom_models": models}


@router.post("/models/reset-prompt")
async def reset_module_system_prompt(payload: ResetPromptRequest):
    """Reset a module's system prompt to the system default."""
    default_prompt = SettingsManager.reset_module_prompt(payload.module_name)
    if default_prompt is None:
        raise HTTPException(status_code=404, detail=f"Module '{payload.module_name}' not found")
    return {"success": True, "module_name": payload.module_name, "system_prompt": default_prompt}


@router.get("/presets")
async def get_provider_presets():
    """Return catalog of built-in provider presets."""
    return {"presets": PROVIDER_PRESETS}


@router.post("/models/test", response_model=TestModelResponse)
async def test_model_connectivity(payload: TestModelRequest):
    """Test model connectivity, latency, and response generation."""
    settings = SettingsManager.get_settings()
    base_url = normalize_api_base_url(payload.base_url or settings.base_url)
    raw_key = (payload.api_key or "").strip()
    if not raw_key or "..." in raw_key or "***" in raw_key:
        api_key = settings.api_key or os.getenv("OPENROUTER_API_KEY", "")
    else:
        api_key = raw_key

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if "openrouter.ai" in base_url:
        headers["HTTP-Referer"] = "http://localhost:8000"
        headers["X-Title"] = "ChatInsight Platform"

    # Multimodal diagnostic pre-check
    is_vision = is_vision_model(payload.model_name)
    vision_warning = ""
    if payload.module_type == "multimodal" and not is_vision:
        vision_warning = "【提示】该模型名称未识别出视觉多模态标识（如-vl、vision、4o等）。若用于图片识别可能报错，建议配置 Qwen-VL、Llama-3.2-Vision 等多模态模型。"

    sys_prompt = payload.system_prompt or "你是一个智能测试助手，请简明回答。"
    test_max_tokens = max(payload.max_tokens or 150, 150)
    test_payload: dict[str, Any] = {
        "model": payload.model_name,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": "请回复'ChatInsight OK'以完成连通性测试"},
        ],
        "temperature": payload.temperature,
        "max_tokens": test_max_tokens,
        "max_completion_tokens": test_max_tokens,
    }
    if payload.top_k is not None:
        test_payload["top_k"] = payload.top_k

    # Qianwen & DeepSeek thinking mode parameter adaptation
    effort = (payload.reasoning_effort or "").lower().strip()
    is_disabled = (payload.enable_thinking is False) or (effort in ("none", "off"))
    is_enabled = (payload.enable_thinking is True) or (effort in ("low", "medium", "high", "xhigh")) or (payload.thinking_budget is not None and payload.thinking_budget > 0)

    if is_disabled:
        test_payload["enable_thinking"] = False
        test_payload["reasoning"] = {"effort": "none"}
        test_payload["extra_body"] = {"enable_thinking": False, "reasoning": {"effort": "none"}}
    elif is_enabled:
        test_payload["enable_thinking"] = True
        extra_body: dict[str, Any] = {"enable_thinking": True}
        if payload.thinking_budget is not None and payload.thinking_budget > 0:
            test_payload["thinking_budget"] = payload.thinking_budget
            extra_body["thinking_budget"] = payload.thinking_budget
        elif effort in ("low", "medium", "high", "xhigh"):
            test_payload["reasoning_effort"] = effort
            test_payload["reasoning"] = {"effort": effort}
            extra_body["reasoning_effort"] = effort
            extra_body["reasoning"] = {"effort": effort}
        if payload.preserve_thinking is True:
            test_payload["preserve_thinking"] = True
            extra_body["preserve_thinking"] = True
        test_payload["extra_body"] = extra_body

    t0 = time.perf_counter()
    endpoint = f"{base_url}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                endpoint,
                headers=headers,
                json=test_payload,
            )
            # If rejected because of top_k, retry without top_k
            if resp.status_code in (400, 422) and "top_k" in resp.text.lower() and "top_k" in test_payload:
                test_payload.pop("top_k", None)
                resp = await client.post(
                    endpoint,
                    headers=headers,
                    json=test_payload,
                )

            latency = (time.perf_counter() - t0) * 1000

            if resp.status_code != 200:
                return TestModelResponse(
                    success=False,
                    latency_ms=round(latency, 1),
                    model=payload.model_name,
                    endpoint_used=endpoint,
                    error=f"请求失败 ({resp.status_code}): {resp.text[:300]}",
                )

            data = resp.json()
            choices = data.get("choices", [])
            reply = ""
            reasoning = ""
            if choices and isinstance(choices, list) and len(choices) > 0 and isinstance(choices[0], dict):
                first_choice = choices[0]
                msg = first_choice.get("message")
                if isinstance(msg, dict):
                    reply = msg.get("content") or ""
                    reasoning = msg.get("reasoning_content") or ""
                    if not reply and reasoning:
                        reply = reasoning
                elif isinstance(msg, str):
                    reply = msg
                else:
                    reply = first_choice.get("text") or ""

            if not isinstance(reply, str):
                reply = str(reply or "")

            reply_str = reply.strip()
            if not reply_str:
                reply_str = "ChatInsight OK (连通成功)"

            combined_reply = f"{reply_str} {vision_warning}".strip() if vision_warning else reply_str

            usage = data.get("usage") or {}
            reasoning_tokens = None
            if isinstance(usage, dict):
                reasoning_tokens = usage.get("completion_tokens_details", {}).get("reasoning_tokens") or usage.get("reasoning_tokens")

            return TestModelResponse(
                success=True,
                latency_ms=round(latency, 1),
                model=payload.model_name,
                endpoint_used=endpoint,
                response_preview=combined_reply,
                reasoning_preview=reasoning.strip() if reasoning else None,
                reasoning_tokens=reasoning_tokens,
            )
    except Exception as exc:
        latency = (time.perf_counter() - t0) * 1000
        return TestModelResponse(
            success=False,
            latency_ms=round(latency, 1),
            model=payload.model_name,
            endpoint_used=endpoint,
            error=f"网络请求异常: {str(exc)}",
        )


@router.post("/models/test-embedding", response_model=TestEmbeddingResponse)
async def test_embedding_connectivity(payload: TestEmbeddingRequest):
    """Test embedding model connectivity, latency, vector dimensions and preview."""
    settings = SettingsManager.get_settings()
    model_name = payload.model or (settings.embedding.model if settings.embedding else None) or "text-embedding-v3"
    test_text = payload.test_text or "ChatInsight 向量语义检索连通性测试"
    provider = (payload.provider or (settings.embedding.provider if settings.embedding else None) or "qwen").lower()

    start_time = time.perf_counter()

    # 1. Mock provider
    if provider == "mock":
        dims = payload.dimensions or (settings.embedding.dimensions if settings.embedding else None) or 1024
        mock_adapter = MockModelAdapter(dimensions=dims)
        embeddings, usage = await mock_adapter.generate_embeddings([test_text])
        latency_ms = round((time.perf_counter() - start_time) * 1000, 1)
        vec = embeddings[0] if embeddings else []
        preview = [round(float(v), 4) for v in vec[:6]]
        return TestEmbeddingResponse(
            success=True,
            latency_ms=latency_ms,
            dimensions=len(vec),
            embedding_preview=preview,
            model=f"mock-{dims}d",
            endpoint_used="local://mock-hash-projection",
            tokens_used=usage.get("total_tokens", len(test_text)),
        )

    # 2. Remote provider (DashScope / OpenRouter / OpenAI / SiliconFlow / Custom)
    raw_base = (payload.base_url or (settings.embedding.base_url if settings.embedding else "") or settings.base_url).strip()
    raw_base = raw_base.rstrip("/")
    if raw_base.endswith("/chat/completions"):
        raw_base = raw_base[:-len("/chat/completions")].rstrip("/")
    elif raw_base.endswith("/embeddings"):
        raw_base = raw_base[:-len("/embeddings")].rstrip("/")

    raw_key = (payload.api_key or "").strip()
    if not raw_key or "..." in raw_key or "***" in raw_key:
        if settings.embedding and settings.embedding.api_key and "..." not in settings.embedding.api_key:
            api_key = settings.embedding.api_key
        else:
            api_key = settings.api_key or os.getenv("OPENROUTER_API_KEY", "") or os.getenv("DASHSCOPE_API_KEY", "")
    else:
        api_key = raw_key

    endpoint = f"{raw_base}/embeddings"
    try:
        adapter = OpenRouterAdapter(
            api_key=api_key,
            base_url=raw_base,
            default_model=model_name,
            timeout=30.0,
        )
        embeddings, usage = await adapter.generate_embeddings(
            [test_text],
            model=model_name,
            dimensions=payload.dimensions,
        )
        latency_ms = round((time.perf_counter() - start_time) * 1000, 1)
        vec = embeddings[0] if embeddings else []
        preview = [round(float(v), 4) for v in vec[:6]]
        tokens_used = usage.get("total_tokens") or usage.get("prompt_tokens")
        return TestEmbeddingResponse(
            success=True,
            latency_ms=latency_ms,
            dimensions=len(vec),
            embedding_preview=preview,
            model=model_name,
            endpoint_used=endpoint,
            tokens_used=tokens_used,
        )
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 1)
        error_msg = str(exc)
        if "404" in error_msg:
            error_msg += f" (提示: 端点 {endpoint} 无法访问或模型 '{model_name}' 在此服务商中不存在)"
        elif "401" in error_msg:
            error_msg += " (提示: API Key 鉴权失败，请检查密钥是否正确并具有 Embedding 权限)"
        return TestEmbeddingResponse(
            success=False,
            latency_ms=latency_ms,
            dimensions=0,
            embedding_preview=[],
            model=model_name,
            endpoint_used=endpoint,
            error=f"Embedding 端点请求异常: {error_msg}",
        )


# ==========================================
# Chat Message Settings & Watchdog Routes
# ==========================================

def _open_native_folder_dialog(initial_dir: str = "") -> str:
    """
    Opens OS-native folder selection dialog in an isolated child process.
    Supports macOS (AppleScript Cocoa), Windows (PowerShell/Tkinter), and Linux (Zenity/KDialog/Tkinter).
    Never executes GUI event loops in server worker threads, preventing thread crashes on macOS.
    """
    init_p = Path(initial_dir).resolve() if (initial_dir and os.path.isdir(initial_dir)) else Path.home()

    if sys.platform == "darwin":
        safe_init = str(init_p).replace('"', '\\"')
        script = f'''
        tell application "System Events"
            activate
            try
                set chosen to choose folder with prompt "选择微信聊天记录最外层根目录（包含日期归档的总根目录）" default location POSIX file "{safe_init}"
                return POSIX path of chosen
            on error number -128
                return ""
            end try
        end tell
        '''
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=120,
            )
            res = proc.stdout.strip()
            if res.endswith("/"):
                res = res[:-1]
            return res
        except subprocess.TimeoutExpired:
            return ""
        except Exception:
            fallback_script = f'''
            try
                set chosen to choose folder with prompt "选择微信聊天记录最外层根目录" default location POSIX file "{safe_init}"
                return POSIX path of chosen
            on error
                return ""
            end try
            '''
            proc = subprocess.run(
                ["osascript", "-e", fallback_script],
                capture_output=True,
                text=True,
                timeout=120,
            )
            res = proc.stdout.strip()
            if res.endswith("/"):
                res = res[:-1]
            return res

    elif sys.platform == "win32":
        safe_init = str(init_p).replace('"', '`"').replace('\\', '\\\\')
        ps_code = f'''
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = "选择微信聊天记录最外层根目录（包含日期归档的总根目录）"
        $dialog.ShowNewFolderButton = $false
        $initPath = "{safe_init}"
        if (Test-Path $initPath) {{
            $dialog.SelectedPath = $initPath
        }}
        $result = $dialog.ShowDialog()
        if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
            [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
            Write-Output $dialog.SelectedPath
        }}
        '''
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_code],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip().replace("\\", "/")
        except Exception:
            pass

        py_code = f'''
import os, sys
try:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes("-topmost", 1)
    d = r"{str(init_p)}"
    folder = filedialog.askdirectory(
        initialdir=d if os.path.isdir(d) else None,
        title="选择微信聊天记录最外层根目录"
    )
    if folder:
        print(folder)
except Exception:
    pass
'''
        try:
            proc = subprocess.run(
                [sys.executable, "-c", py_code],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip().replace("\\", "/")
        except Exception:
            pass
        return ""

    else:
        init_str = str(init_p)
        try:
            proc = subprocess.run(
                ["zenity", "--file-selection", "--directory", "--title=选择微信聊天记录最外层根目录", f"--filename={init_str}"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except FileNotFoundError:
            pass

        try:
            proc = subprocess.run(
                ["kdialog", "--getexistingdirectory", init_str, "--title", "选择微信聊天记录最外层根目录"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except FileNotFoundError:
            pass

        py_code = f'''
import os, sys
try:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(initialdir=r"{init_str}", title="选择微信聊天记录最外层根目录")
    if folder:
        print(folder)
except Exception:
    pass
'''
        try:
            proc = subprocess.run(
                [sys.executable, "-c", py_code],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            pass
        return ""


class UpdateChatSettingsRequest(BaseModel):
    source_path: str = Field(description="微信聊天记录最外层根目录")
    watchdog_enabled: bool = Field(default=False, description="是否开启操作系统事件监听")
    debounce_seconds: float = Field(default=5.0, description="防抖时间(秒)")
    auto_import_on_change: bool = Field(default=True, description="自动增量入库")


class ValidatePathRequest(BaseModel):
    path: str


@router.get("/chat")
async def get_chat_settings():
    """Returns current chat message settings, path validation health, and watchdog daemon status."""
    settings = ChatSettingsManager.load_settings()
    validation = ChatSettingsManager.validate_path(settings.source_path)
    watcher = ChatDirectoryWatcher.get_instance()
    return {
        "settings": settings.model_dump(),
        "validation": validation.model_dump(),
        "watchdog": watcher.get_status(),
    }


@router.post("/chat")
async def update_chat_settings(payload: UpdateChatSettingsRequest):
    """Saves chat message settings and configures watchdog state."""
    clean_path = payload.source_path.strip().replace("\\", "/")
    settings = ChatSettings(
        source_path=clean_path,
        watchdog_enabled=payload.watchdog_enabled,
        debounce_seconds=payload.debounce_seconds,
        auto_import_on_change=payload.auto_import_on_change,
    )
    saved = ChatSettingsManager.save_settings(settings)

    watcher = ChatDirectoryWatcher.get_instance()
    if payload.watchdog_enabled:
        watcher.start(
            source_path=clean_path,
            debounce_seconds=payload.debounce_seconds,
            auto_import=payload.auto_import_on_change,
        )
    else:
        watcher.stop()

    validation = ChatSettingsManager.validate_path(clean_path)
    return {
        "success": True,
        "settings": saved.model_dump(),
        "validation": validation.model_dump(),
        "watchdog": watcher.get_status(),
    }


@router.post("/browse-folder")
async def browse_system_folder(payload: Optional[dict[str, Any]] = None):
    """Opens OS native folder picker dialog in an isolated subprocess and returns the selected directory path."""
    initial_dir = (payload or {}).get("initial_dir", "")
    auto_save = (payload or {}).get("auto_save", True)
    loop = asyncio.get_running_loop()
    try:
        folder = await asyncio.wait_for(
            loop.run_in_executor(None, _open_native_folder_dialog, initial_dir),
            timeout=125.0,
        )
        if folder:
            clean_folder = folder.replace("\\", "/")
            validation = ChatSettingsManager.validate_path(clean_folder)
            saved_settings = None
            if auto_save:
                current_settings = ChatSettingsManager.load_settings()
                current_settings.source_path = clean_folder
                saved = ChatSettingsManager.save_settings(current_settings)
                saved_settings = saved.model_dump()
                watcher = ChatDirectoryWatcher.get_instance()
                if current_settings.watchdog_enabled:
                    watcher.start(
                        source_path=clean_folder,
                        debounce_seconds=current_settings.debounce_seconds,
                        auto_import=current_settings.auto_import_on_change,
                    )
            return {
                "selected_path": clean_folder,
                "cancelled": False,
                "validation": validation.model_dump(),
                "settings": saved_settings,
            }
        return {"selected_path": "", "cancelled": True}
    except asyncio.TimeoutError:
        return {
            "selected_path": "",
            "cancelled": True,
            "error": "系统窗口选择超时。若系统未弹出窗口，推荐使用右侧【网页目录浏览器】直接点选！",
        }
    except Exception as e:
        return {
            "selected_path": "",
            "cancelled": True,
            "error": f"系统窗口选择异常: {e}。推荐使用右侧【网页目录浏览器】直接点选！",
        }


@router.get("/browse-tree")
async def browse_directory_tree(path: Optional[str] = None):
    """Lists Windows drives or subdirectories under path for in-app Web directory navigation."""
    return ChatSettingsManager.list_drives_and_subdirs(path)


@router.post("/validate-chat-path")
async def validate_chat_path(payload: ValidatePathRequest):
    """Validates specified directory path and checks for date folders, group folders, and txt files."""
    validation = ChatSettingsManager.validate_path(payload.path)
    return validation.model_dump()


@router.get("/watchdog/status")
async def get_watchdog_status():
    """Returns current real-time watchdog daemon status and live metrics without heavy disk inspection."""
    watcher = ChatDirectoryWatcher.get_instance()
    return watcher.get_status()


@router.post("/watchdog/toggle")
async def toggle_watchdog(payload: Optional[dict[str, Any]] = None):
    """Toggles watchdog real-time OS event listening daemon."""
    settings = ChatSettingsManager.load_settings()
    enable = (payload or {}).get("enabled", not settings.watchdog_enabled)
    settings.watchdog_enabled = enable
    ChatSettingsManager.save_settings(settings)

    watcher = ChatDirectoryWatcher.get_instance()
    if enable:
        ok = watcher.start(
            source_path=settings.source_path,
            debounce_seconds=settings.debounce_seconds,
            auto_import=settings.auto_import_on_change,
        )
        msg = "文件变更监听已启动" if ok else "启动监听失败，请检查路径是否存在"
    else:
        watcher.stop()
        ok = True
        msg = "文件变更监听已停止"

    return {
        "success": ok,
        "message": msg,
        "watchdog": watcher.get_status(),
        "settings": settings.model_dump(),
    }


