import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_database_url() -> str:
    """Get database URL from environment or default to local SQLite with aiosqlite."""
    url = os.getenv("CI_DATABASE_URL")
    if not url:
        db_path = os.getenv("CI_SQLITE_PATH", "chatinsight.db")
        url = f"sqlite+aiosqlite:///{db_path}"
    return url


def init_db_engine(db_url: str | None = None) -> AsyncEngine:
    global _engine, _session_maker
    url = db_url or get_database_url()
    
    # SQLite optimizations: WAL mode, foreign keys, 60s busy timeout
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 60.0}

    _engine = create_async_engine(
        url,
        echo=False,
        connect_args=connect_args,
    )
    _session_maker = async_sessionmaker(
        bind=_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    return _engine


async def create_all_tables(engine: AsyncEngine | None = None) -> None:
    """Create all declarative tables if they do not exist."""
    import packages.persistence.models  # noqa: F401 - ensure all declarative models are registered on Base.metadata
    eng = engine or _engine or init_db_engine()
    async with eng.begin() as conn:
        # SQLite foreign key enforcement
        if eng.url.drivername.startswith("sqlite"):
            from sqlalchemy import text
            await conn.execute(text("PRAGMA foreign_keys=ON;"))
            await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async session scope for FastAPI dependency injection."""
    if _session_maker is None:
        init_db_engine()
    assert _session_maker is not None
    async with _session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Provide an async context manager scope for CLI and background workers."""
    if _session_maker is None:
        init_db_engine()
    assert _session_maker is not None
    async with _session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
