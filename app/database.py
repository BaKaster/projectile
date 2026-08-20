from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings


def create_database(settings: Settings):
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return engine, session_factory


async def ensure_schema_compatibility(engine) -> None:
    """Small forward-only migration for databases created by the MVP bootstrap."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
                "name_is_generated BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
        await connection.execute(
            text(
                "UPDATE projects SET name_is_generated = TRUE "
                "WHERE name IN ('Новый чат', 'Анализ документов')"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE documents "
                "ADD COLUMN IF NOT EXISTS source_path VARCHAR(1024)"
            )
        )
        await connection.execute(
            text(
                "UPDATE documents SET source_path = original_filename "
                "WHERE source_path IS NULL"
            )
        )
        await connection.execute(
            text("ALTER TABLE documents ALTER COLUMN source_path SET NOT NULL")
        )
        await connection.execute(
            text(
                "ALTER TABLE project_analyses "
                "ADD COLUMN IF NOT EXISTS issues JSONB NOT NULL DEFAULT '[]'::jsonb"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS "
                "document_digests JSONB NOT NULL DEFAULT '[]'::jsonb"
            )
        )
        await connection.execute(
            text(
                "ALTER TABLE project_analyses "
                "ALTER COLUMN prompt_version TYPE VARCHAR(100)"
            )
        )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session
