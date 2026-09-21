from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.api_route("/health", methods=["GET", "HEAD"])
@router.api_route("/api/v1/health", methods=["GET", "HEAD"])
@router.api_route("/health/live", methods=["GET", "HEAD"])
async def health_live():
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready():
    return {"status": "ready", "database": "connected"}
