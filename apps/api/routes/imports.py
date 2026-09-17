from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.importers.batch_importer import BatchImporter, ImportStats
from packages.importers.contracts import PrecheckReport
from packages.importers.scanner import DirectoryScanner
from packages.persistence.db import get_session
from packages.persistence.models import ImportBatch, SourceRoot
from packages.persistence.repositories.registry import RepositoryRegistry

router = APIRouter(prefix="/api/v1/imports", tags=["Imports & Scanning"])


class ScanRequest(BaseModel):
    source_root_id: Optional[str] = None
    custom_path: Optional[str] = None
    target_date: Optional[str] = None
    target_group: Optional[str] = None


class ExecuteImportRequest(BaseModel):
    source_root_id: Optional[str] = None
    custom_path: Optional[str] = None
    target_date: Optional[str] = None
    target_group: Optional[str] = None


class BatchSummaryResponse(BaseModel):
    id: str
    batch_key: str
    date_str: str
    conversation_name: str
    state: str
    summary_json: dict[str, Any]
    created_at: str
    completed_at: Optional[str]


@router.post("/scan", response_model=PrecheckReport)
async def scan_and_precheck(
    payload: ScanRequest,
    session: AsyncSession = Depends(get_session),
):
    target_path = payload.custom_path
    if payload.source_root_id:
        stmt = select(SourceRoot).where(SourceRoot.id == payload.source_root_id)
        res = await session.execute(stmt)
        root = res.scalar_one_or_none()
        if not root:
            raise HTTPException(status_code=404, detail="Source root not found")
        target_path = root.root_path

    if not target_path:
        raise HTTPException(status_code=400, detail="Either source_root_id or custom_path must be provided")

    scanner = DirectoryScanner()
    try:
        report, _ = scanner.scan_root(
            root_path=target_path,
            target_date=payload.target_date,
            target_group=payload.target_group,
        )
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scanning failed: {str(e)}")


@router.post("/execute", response_model=list[ImportStats])
async def execute_import(
    payload: ExecuteImportRequest,
    session: AsyncSession = Depends(get_session),
):
    target_path = payload.custom_path
    disp_name = "默认数据源"

    if payload.source_root_id:
        stmt = select(SourceRoot).where(SourceRoot.id == payload.source_root_id)
        res = await session.execute(stmt)
        root = res.scalar_one_or_none()
        if not root:
            raise HTTPException(status_code=404, detail="Source root not found")
        target_path = root.root_path
        disp_name = root.display_name

    if not target_path:
        raise HTTPException(status_code=400, detail="Either source_root_id or custom_path must be provided")

    scanner = DirectoryScanner()
    report, batches = scanner.scan_root(
        root_path=target_path,
        target_date=payload.target_date,
        target_group=payload.target_group,
    )

    if report.blocking_issues:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Import blocked by data quality issues",
                "blocking_issues": [b.model_dump() for b in report.blocking_issues],
            },
        )

    importer = BatchImporter(session)
    results: list[ImportStats] = []

    for batch in batches:
        stats = await importer.import_parsed_batch(
            batch=batch,
            root_path=target_path,
            display_name=disp_name,
        )
        results.append(stats)

    await session.commit()
    return results


@router.get("/batches", response_model=list[BatchSummaryResponse])
async def list_import_batches(session: AsyncSession = Depends(get_session)):
    stmt = select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(100)
    res = await session.execute(stmt)
    batches = res.scalars().all()
    return [
        BatchSummaryResponse(
            id=b.id,
            batch_key=b.batch_key,
            date_str=b.date_str,
            conversation_name=b.conversation_name,
            state=b.state,
            summary_json=b.summary_json or {},
            created_at=b.created_at.isoformat(),
            completed_at=b.completed_at.isoformat() if b.completed_at else None,
        )
        for b in batches
    ]
