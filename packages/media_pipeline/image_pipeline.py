import io
from pathlib import Path
from PIL import Image, ImageOps
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models import sha256_file
from packages.media_pipeline.contracts import ImageEnrichmentOutput
from packages.model_gateway.gateway import ModelGateway
from packages.observability.analysis_service import AnalysisService
from packages.persistence.models import MediaAsset, MediaEnrichment, SourceFile, SourceRoot
from packages.persistence.repositories.registry import RepositoryRegistry


class ImageEnrichmentPipeline:
    """Pipeline for analyzing images (OCR, UI state, device connections, error codes)."""

    def __init__(self, session: AsyncSession, model_gateway: ModelGateway | None = None):
        self.session = session
        self.gateway = model_gateway or ModelGateway()
        self.analysis_service = AnalysisService(session)
        self.repo = RepositoryRegistry(session)

    async def analyze_image_asset(
        self,
        media_id: str,
        force_mock: bool = False,
        model_override: str | None = None,
    ) -> tuple[ImageEnrichmentOutput, MediaEnrichment, bool]:
        # 1. Fetch MediaAsset & SourceFile
        media = await self._get_media_asset(media_id)
        file_path = await self._resolve_raw_path(media)

        # 2. Image Preprocessing with Pillow
        image_bytes, mime_type = self._preprocess_image(file_path)
        img_sha = sha256_file(str(file_path))

        # 3. Model Configuration
        from packages.model_gateway.settings_manager import SettingsManager
        settings = SettingsManager.get_settings()
        mod_cfg = settings.multimodal

        provider = self.gateway.get_vision_provider(force_mock=force_mock)
        provider_name = "mock" if force_mock else (settings.provider or "openrouter")
        selected_model = model_override or mod_cfg.model or getattr(provider, "default_vision_model", "qwen/qwen-2.5-vl-72b-instruct")

        prompt = (
            "请客观分析这张来自玩家/用户群聊的图片：\n"
            "1. 提取所有可见的文字内容 (OCR) 并按区域分块；\n"
            "2. 识别当前所属界面或场景（如演奏界面、系统设置、报错弹窗、硬件连接实物）；\n"
            "3. 提取用户可见的操作动作、异常状态、错误码提示及硬件连接状态；\n"
            "4. 提取产品模块与设备实体（如 LiberLive C2, 扩展音色卡, 系统返回键等）；\n"
            "5. 如有画面模糊或无法确定的事项，请明确在 uncertainty 中列出。"
        )

        async def _call_model():
            return await provider.analyze_image(
                image_bytes=image_bytes,
                mime_type=mime_type,
                prompt=prompt,
                response_schema=ImageEnrichmentOutput,
                system_prompt=mod_cfg.system_prompt,
                model=selected_model,
                temperature=mod_cfg.temperature,
                top_p=mod_cfg.top_p,
                top_k=mod_cfg.top_k,
                max_tokens=mod_cfg.max_tokens,
            )

        # 4. Cached Execution via AnalysisService
        output, run, was_cached = await self.analysis_service.execute_cached(
            workspace_id=media.workspace_id,
            run_type="image_enrichment",
            target_type="media_asset",
            target_id=media.id,
            provider=provider_name,
            model=selected_model,
            prompt_key="image_enrichment_v2",
            input_data={"sha256": img_sha, "size_bytes": media.size_bytes},
            config_data={
                "model": selected_model,
                "schema": "ImageEnrichmentOutput_v2",
                "temperature": mod_cfg.temperature,
                "top_p": mod_cfg.top_p,
            },
            response_schema=ImageEnrichmentOutput,
            call_fn=_call_model,
        )

        # 5. Build Searchable Text
        ocr_texts = [b.text for b in output.ocr_blocks]
        searchable = " ".join([output.summary] + ocr_texts + output.error_codes + [e.value for e in output.entities])

        # 6. Save or Update MediaEnrichment Record
        enrichment = await self.repo.upsert_media_enrichment(
            workspace_id=media.workspace_id,
            media_id=media.id,
            analysis_run_id=run.id,
            summary=output.summary,
            searchable_text=searchable,
            ocr_json=[b.model_dump() for b in output.ocr_blocks],
            visual_json={
                "screen_or_scene": output.screen_or_scene,
                "user_action": output.user_action,
                "observed_state": output.observed_state,
                "error_codes": output.error_codes,
                "entities": [e.model_dump() for e in output.entities],
                "safety_or_privacy_notes": output.safety_or_privacy_notes,
            },
            uncertainty_json=output.uncertainty,
            state="available",
        )

        return output, enrichment, was_cached

    def _preprocess_image(self, file_path: Path) -> tuple[bytes, str]:
        """Auto-rotate EXIF, resize if gigantic, and output clean bytes."""
        with Image.open(file_path) as img:
            # Auto-orient based on EXIF tags
            img = ImageOps.exif_transpose(img) or img

            # Convert RGBA to RGB for standard JPEG
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # Max dimension check (downscale if > 3000px on long edge for efficiency)
            max_edge = max(img.size)
            if max_edge > 3000:
                scale = 3000.0 / max_edge
                new_size = (int(img.width * scale), int(img.height * scale))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            return buf.getvalue(), "image/jpeg"

    async def _get_media_asset(self, media_id: str) -> MediaAsset:
        from sqlalchemy import select

        stmt = select(MediaAsset).where(MediaAsset.id == media_id)
        res = await self.session.execute(stmt)
        media = res.scalar_one_or_none()
        if not media:
            raise FileNotFoundError(f"Media asset with ID {media_id} not found")
        return media

    async def _resolve_raw_path(self, media: MediaAsset) -> Path:
        from sqlalchemy import select

        stmt = (
            select(SourceFile, SourceRoot)
            .join(SourceRoot, SourceFile.source_root_id == SourceRoot.id)
            .where(SourceFile.id == media.source_file_id)
        )
        res = await self.session.execute(stmt)
        row = res.first()
        if not row:
            raise FileNotFoundError(f"Source file record for media {media.id} not found")

        sf, root = row
        full_path = Path(root.root_path) / sf.relative_path
        if not full_path.exists():
            raise FileNotFoundError(f"Raw media file does not exist at {full_path}")
        return full_path
