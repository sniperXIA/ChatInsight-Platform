from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get("/health")
@router.get("/api/v1/health")
@router.get("/health/live")
async def health_live():
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready():
    return {"status": "ready", "database": "connected"}
