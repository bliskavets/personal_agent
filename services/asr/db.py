"""
db.py — asyncpg connection pool and database helpers.

Manages the PostgreSQL connection pool lifecycle and provides
coroutines for schema creation and chunk insertion.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level pool (initialised once in lifespan)
# ---------------------------------------------------------------------------
_pool: Optional[asyncpg.Pool] = None


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS asr_chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id  TEXT,
    language    TEXT,
    text        TEXT NOT NULL,
    duration_s  FLOAT,
    source      TEXT
);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def create_pool() -> asyncpg.Pool:
    """Create and return a new asyncpg connection pool.

    Reads the database URL from the ``DATABASE_URL`` environment variable.
    Falls back to a sensible local default so the service can start without
    Docker for development/testing.

    Returns
    -------
    asyncpg.Pool
        Opened and ready-to-use connection pool.
    """
    global _pool
    dsn = os.getenv(
        "DATABASE_URL",
        "postgresql://asr:asr@localhost:5432/asr",
    )
    _pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
    logger.info("PostgreSQL pool created (dsn=%s)", dsn)
    return _pool


async def close_pool() -> None:
    """Gracefully close the connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed")


async def create_tables(pool: asyncpg.Pool) -> None:
    """Run DDL to create the *asr_chunks* table if it does not yet exist.

    Parameters
    ----------
    pool:
        An open asyncpg connection pool.
    """
    async with pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE_SQL)
    logger.info("Database schema verified / created")


async def insert_chunk(
    *,
    pool: asyncpg.Pool,
    text: str,
    language: Optional[str] = None,
    session_id: Optional[str] = None,
    duration_s: Optional[float] = None,
    source: Optional[str] = None,
) -> str:
    """Insert a transcription chunk and return its generated UUID.

    Parameters
    ----------
    pool:
        An open asyncpg connection pool.
    text:
        The transcribed text (required; must be non-empty).
    language:
        BCP-47 language code detected by Whisper (e.g. ``"ru"``, ``"en"``).
    session_id:
        Caller-supplied identifier that groups WebSocket stream frames
        belonging to the same recording session.
    duration_s:
        Audio duration in seconds.
    source:
        Either ``"stream"`` or ``"upload"``.

    Returns
    -------
    str
        The UUID string assigned to the new row.

    Raises
    ------
    ValueError
        If *text* is empty.
    asyncpg.PostgresError
        On any database-level failure.
    """
    if not text:
        raise ValueError("'text' must not be empty")

    sql = """
        INSERT INTO asr_chunks (session_id, language, text, duration_s, source)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id::text
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, session_id, language, text, duration_s, source)

    chunk_id: str = row["id"]
    logger.debug(
        "Inserted chunk id=%s source=%s lang=%s duration=%.2fs",
        chunk_id,
        source,
        language,
        duration_s or 0.0,
    )
    return chunk_id


def get_pool() -> asyncpg.Pool:
    """Return the module-level pool; raises if not yet initialised.

    Use this as a FastAPI dependency so that route handlers can access
    the pool without importing the module-level variable directly.

    Raises
    ------
    RuntimeError
        If :func:`create_pool` has not been awaited yet.
    """
    if _pool is None:
        raise RuntimeError("Database pool is not initialised — call create_pool() first")
    return _pool
