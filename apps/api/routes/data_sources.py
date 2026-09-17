from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.persistence.db import get_session
from packages.persistence.models import SourceRoot
from packages.persistence.repositories.registry import RepositoryRegistry

router = APIRouter(prefix="/api/v1/source-roots", tags=["Data Sources"])


class SourceRootCreate(BaseModel):
    display_name: str
    root_path: str
    source_type: str = "wechat_archive"


class SourceRootResponse(BaseModel):
    id: str
    workspace_id: str
    display_name: str
    root_path: str
    source_type: str
    read_only: bool
    enabled: bool


@router.post("", response_model=SourceRootResponse)
async def create_source_root(
    payload: SourceRootCreate,
    session: AsyncSession = Depends(get_session),
):
    path = Path(payload.root_path)
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path does not exist or is not a directory: {payload.root_path}")

    repo = RepositoryRegistry(session)
    ws = await repo.get_or_create_default_workspace()
    root = await repo.get_or_create_source_root(
        workspace_id=ws.id,
        display_name=payload.display_name,
        root_path=str(path.resolve()),
        source_type=payload.source_type,
    )

    response = SourceRootResponse(
        id=root.id,
        workspace_id=root.workspace_id,
        display_name=root.display_name,
        root_path=root.root_path,
        source_type=root.source_type,
        read_only=root.read_only,
        enabled=root.enabled,
    )
    await session.commit()
    return response


@router.get("", response_model=list[SourceRootResponse])
async def list_source_roots(session: AsyncSession = Depends(get_session)):
    stmt = select(SourceRoot).where(SourceRoot.enabled == True)
    result = await session.execute(stmt)
    roots = result.scalars().all()
    return [
        SourceRootResponse(
            id=r.id,
            workspace_id=r.workspace_id,
            display_name=r.display_name,
            root_path=r.root_path,
            source_type=r.source_type,
            read_only=r.read_only,
            enabled=r.enabled,
        )
        for r in roots
    ]
