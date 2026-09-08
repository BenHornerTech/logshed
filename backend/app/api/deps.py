"""
FastAPI dependency injectors and database execution helpers for LogShed.
All SQLite queries are dispatched via asyncio.to_thread() to keep the event loop non-blocking.
"""

import asyncio
import datetime
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_db_path
from app.core.migrations import get_connection
from app.core.security import SESSION_COOKIE_NAME, verify_session_token

T = TypeVar("T")

_cached_admin_updated_at: Optional[float] = None
_cached_admin_updated_at_time: float = 0.0
_ADMIN_CACHE_TTL: float = 5.0  # seconds


def invalidate_admin_auth_cache() -> None:
    """Invalidate cached admin password updated_at timestamp."""
    global _cached_admin_updated_at, _cached_admin_updated_at_time
    _cached_admin_updated_at = None
    _cached_admin_updated_at_time = 0.0


async def run_db_query(fn: Callable[[sqlite3.Connection], T], custom_db_path: Optional[Path] = None) -> T:
    """
    Executes a synchronous database function in a worker thread using asyncio.to_thread().
    Automatically manages connection lifecycle.
    """
    db_path = custom_db_path or get_db_path()

    def _execute() -> T:
        conn = get_connection(db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                return fn(conn)
        finally:
            conn.close()

    return await asyncio.to_thread(_execute)


async def get_current_user(request: Request) -> dict[str, Any]:
    """
    FastAPI dependency that validates the signed session cookie.
    Raises 401 Unauthorized if cookie is missing, invalid, or expired.
    Also validates that the session was not issued prior to the latest admin password update.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    payload = verify_session_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please log in again.",
        )

    iat = payload.get("iat")
    if iat is not None:
        global _cached_admin_updated_at, _cached_admin_updated_at_time
        now_mono = time.monotonic()
        updated_at_epoch = _cached_admin_updated_at

        if updated_at_epoch is None or (now_mono - _cached_admin_updated_at_time) > _ADMIN_CACHE_TTL:
            def _get_admin_updated_at(conn: sqlite3.Connection) -> Optional[str]:
                cursor = conn.cursor()
                cursor.execute("SELECT updated_at FROM admin_auth WHERE id = 1")
                row = cursor.fetchone()
                return row[0] if row else None

            updated_at_str = await run_db_query(_get_admin_updated_at)
            if updated_at_str:
                try:
                    dt = datetime.datetime.fromisoformat(updated_at_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    updated_at_epoch = dt.timestamp()
                    _cached_admin_updated_at = updated_at_epoch
                    _cached_admin_updated_at_time = now_mono
                except Exception:
                    updated_at_epoch = None
            else:
                _cached_admin_updated_at = None
                _cached_admin_updated_at_time = now_mono

        if updated_at_epoch is not None:
            iat_val = float(iat)
            is_revoked = (
                int(iat_val) < int(updated_at_epoch)
                if (isinstance(iat, int) or iat_val.is_integer())
                else iat_val < updated_at_epoch
            )
            if is_revoked:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session expired due to password change",
                )

    return payload


async def get_optional_user(request: Request) -> Optional[dict[str, Any]]:
    """
    FastAPI dependency that extracts user session if present, but does not raise.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return verify_session_token(token)
