from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.api.routes import (
    analytics,
    conversations,
    data_sources,
    episodes,
    feishu_bitable,
    health,
    imports,
    insights,
    media,
    messages,
    pipeline,
    search,
    settings,
    tasks,
    topics,
)
from packages.domain.models import DomainError
from packages.persistence.db import create_all_tables, init_db_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB engine and create tables
    init_db_engine()
    await create_all_tables()
    yield


app = FastAPI(
    title="ChatInsight Platform API & Dashboard",
    description="Context Core, Multimodal Analysis & Feedback Knowledge Engine",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError):
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


# Include API routes
app.include_router(health.router)
app.include_router(data_sources.router)
app.include_router(imports.router)
app.include_router(conversations.router)
app.include_router(messages.router)
app.include_router(media.router)
app.include_router(episodes.router)
app.include_router(insights.router)
app.include_router(topics.router)
app.include_router(feishu_bitable.router)
app.include_router(search.router)
app.include_router(analytics.router)
app.include_router(pipeline.router)
app.include_router(settings.router)
app.include_router(tasks.router)


# Mount Static Files & Web UI Dashboard
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>ChatInsight Platform API is online</h1><p>Visit <a href='/docs'>/docs</a> for API docs.</p>")
