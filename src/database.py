"""Database engine, session factory, and dependency injection."""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from src.config import get_settings

settings = get_settings()

# Async PostgreSQL engine with connection pool
engine = create_async_engine(
    settings.database_url_sqlalchemy,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

# Session factory — expire_on_commit=False allows accessing objects after commit
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()


async def get_session() -> AsyncGenerator[AsyncSession, Any]:
    """Yield a new async database session and ensure it's closed after use.

    Usage:
        async with get_session() as session:
            ...
    """
    async with async_session_factory() as session:
        yield session
