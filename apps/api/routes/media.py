from pathlib import Path
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.media_pipeline.image_pipeline import ImageEnrichmentPipeline
from packages.media_pipeline.video_pipeline import VideoEnrichmentPipeline
from packages.persistence.db import get_session
from packages.persistence.models import (
    MediaAsset,
    MediaEnrichment,
    Message,
    MessageMediaLink,
    SourceFile,
    SourceRoot,
)

router = APIRouter(tags=["Media & Multimodal Analysis"])


class AnalyzeMediaRequest(BaseModel):
    force_mock: bool = False
    model: Optional[str] = None


class HumanOverrideRequest(BaseModel):
    notes: str
    corrected_summary: Optional[str] = None
    is_irrelevant_to_product: bool = False


class EnrichmentResponse(BaseModel):
    id: str
    media_id: str
    revision: int
    state: str
    summary: Optional[str]
    searchable_text: str
    ocr_json: Optional[Any]
    visual_json: Optional[Any]
    timeline_json: Optional[Any]
    uncertainty_json: list[str]
    human_override_json: dict
    created_at: str
    was_cached: bool = False


class PendingMediaLinkResponse(BaseModel):
    link_id: str
    message_id: str
    message_text: str
    sent_at: str
    media_id: str
    media_kind: str
    confidence: float
    method: str
    state: str
    evidence_json: dict


@router.get("/api/v1/media/{id}")
async def get_media_info(id: str, session: AsyncSession = Depends(get_session)):
    stmt = select(MediaAsset).where(MediaAsset.id == id)
    res = await session.execute(stmt)
    media = res.scalar_one_or_none()
    if not media:
        raise HTTPException(status_code=404, detail="Media asset not found")
    return {
        "id": media.id,
        "kind": media.kind,
        "mime_type": media.mime_type,
        "sha256": media.sha256,
        "size_bytes": media.size_bytes,
        "state": media.state,
        "created_at": media.created_at.isoformat(),
    }


@router.api_route("/api/v1/media/{id}/content", methods=["GET", "HEAD"])
async def get_media_content(id: str, session: AsyncSession = Depends(get_session)):
    stmt = (
        select(MediaAsset, SourceFile, SourceRoot)
        .join(SourceFile, MediaAsset.source_file_id == SourceFile.id)
        .join(SourceRoot, SourceFile.source_root_id == SourceRoot.id)
        .where(MediaAsset.id == id)
    )
    res = await session.execute(stmt)
    row = res.first()
    if not row:
        raise HTTPException(status_code=404, detail="Media file record not found")

    media, source_file, source_root = row
    root_p = Path(source_root.root_path)
    full_path = (root_p / source_file.relative_path).resolve()

    if not full_path.exists() or not full_path.is_file():
        # Auto-healing: try current active source_path from chat_settings or fallback discovery
        from packages.importers.chat_settings import ChatSettingsManager
        active_settings = ChatSettingsManager.load_settings()
        candidate_roots = [
            Path("/app/Demo用户聊天数据"),
            Path.cwd() / "Demo用户聊天数据",
            Path(active_settings.source_path),
            Path(ChatSettingsManager.get_default_source_path()),
            Path(__file__).resolve().parent.parent.parent / "Demo用户聊天数据",
        ]
        healed = False
        for cand_root in candidate_roots:
            if cand_root.exists():
                cand_file = (cand_root / source_file.relative_path).resolve()
                if cand_file.exists() and cand_file.is_file():
                    full_path = cand_file
                    source_root.root_path = str(cand_root.resolve()).replace("\\", "/")
                    await session.commit()
                    healed = True
                    break
        if not healed:
            raise HTTPException(status_code=404, detail="Raw media file not found on disk")

    return FileResponse(
        path=str(full_path),
        media_type=media.mime_type or "application/octet-stream",
        filename=full_path.name,
    )


@router.api_route("/api/v1/media/{id}/thumbnail", methods=["GET", "HEAD"])
async def get_media_thumbnail(id: str, session: AsyncSession = Depends(get_session)):
    """Serves media thumbnail, falling back to full media content if separate thumbnail is not pre-generated."""
    return await get_media_content(id, session)


@router.post("/api/v1/media/{id}/analyze", response_model=EnrichmentResponse)
async def analyze_media(
    id: str,
    payload: AnalyzeMediaRequest = AnalyzeMediaRequest(),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(MediaAsset).where(MediaAsset.id == id)
    res = await session.execute(stmt)
    media = res.scalar_one_or_none()
    if not media:
        raise HTTPException(status_code=404, detail="Media asset not found")

    if media.kind == "image":
        pipeline = ImageEnrichmentPipeline(session)
        output, enrichment, was_cached = await pipeline.analyze_image_asset(
            media_id=media.id,
            force_mock=payload.force_mock,
            model_override=payload.model,
        )
    elif media.kind == "video":
        pipeline = VideoEnrichmentPipeline(session)
        output, enrichment, was_cached = await pipeline.analyze_video_asset(
            media_id=media.id,
            force_mock=payload.force_mock,
            model_override=payload.model,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported media kind for multimodal analysis: {media.kind}")

    await session.commit()
    return EnrichmentResponse(
        id=enrichment.id,
        media_id=enrichment.media_id,
        revision=enrichment.revision,
        state=enrichment.state,
        summary=enrichment.summary,
        searchable_text=enrichment.searchable_text,
        ocr_json=enrichment.ocr_json,
        visual_json=enrichment.visual_json,
        timeline_json=enrichment.timeline_json,
        uncertainty_json=enrichment.uncertainty_json,
        human_override_json=enrichment.human_override_json,
        created_at=enrichment.created_at.isoformat(),
        was_cached=was_cached,
    )


@router.get("/api/v1/media/{id}/enrichments", response_model=list[EnrichmentResponse])
async def get_media_enrichments(id: str, session: AsyncSession = Depends(get_session)):
    stmt = select(MediaEnrichment).where(MediaEnrichment.media_id == id).order_by(MediaEnrichment.revision.desc())
    res = await session.execute(stmt)
    items = res.scalars().all()
    return [
        EnrichmentResponse(
            id=e.id,
            media_id=e.media_id,
            revision=e.revision,
            state=e.state,
            summary=e.summary,
            searchable_text=e.searchable_text,
            ocr_json=e.ocr_json,
            visual_json=e.visual_json,
            timeline_json=e.timeline_json,
            uncertainty_json=e.uncertainty_json,
            human_override_json=e.human_override_json,
            created_at=e.created_at.isoformat(),
            was_cached=False,
        )
        for e in items
    ]


@router.post("/api/v1/media-enrichments/{id}/override")
async def override_enrichment(
    id: str,
    payload: HumanOverrideRequest,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(MediaEnrichment).where(MediaEnrichment.id == id)
    res = await session.execute(stmt)
    enrichment = res.scalar_one_or_none()
    if not enrichment:
        raise HTTPException(status_code=404, detail="Media enrichment not found")

    enrichment.human_override_json = payload.model_dump()
    if payload.corrected_summary:
        enrichment.summary = payload.corrected_summary
    await session.commit()
    return {"success": True, "enrichment_id": enrichment.id, "human_override": enrichment.human_override_json}


@router.get("/api/v1/media-links/pending", response_model=list[PendingMediaLinkResponse])
async def list_pending_media_links(session: AsyncSession = Depends(get_session)):
    stmt = (
        select(MessageMediaLink, Message, MediaAsset)
        .join(Message, MessageMediaLink.message_id == Message.id)
        .join(MediaAsset, MessageMediaLink.media_id == MediaAsset.id)
        .where(MessageMediaLink.state == "needs_review")
        .order_by(Message.sent_at.desc())
        .limit(100)
    )
    res = await session.execute(stmt)
    rows = res.all()
    return [
        PendingMediaLinkResponse(
            link_id=link.id,
            message_id=msg.id,
            message_text=msg.raw_text,
            sent_at=msg.sent_at.isoformat(),
            media_id=media.id,
            media_kind=media.kind,
            confidence=link.confidence,
            method=link.method,
            state=link.state,
            evidence_json=link.evidence_json or {},
        )
        for link, msg, media in rows
    ]


@router.post("/api/v1/media-links/{id}/confirm")
async def confirm_media_link(id: str, session: AsyncSession = Depends(get_session)):
    stmt = select(MessageMediaLink).where(MessageMediaLink.id == id)
    res = await session.execute(stmt)
    link = res.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Media link not found")

    link.state = "confirmed"
    link.confidence = 1.0
    await session.commit()
    return {"success": True, "link_id": link.id, "state": link.state}


@router.post("/api/v1/media-links/{id}/reject")
async def reject_media_link(id: str, session: AsyncSession = Depends(get_session)):
    stmt = select(MessageMediaLink).where(MessageMediaLink.id == id)
    res = await session.execute(stmt)
    link = res.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Media link not found")

    link.state = "rejected"
    await session.commit()
    return {"success": True, "link_id": link.id, "state": link.state}
