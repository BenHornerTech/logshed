"""
FastAPI dependency injectors and database execution helpers for Homelab Log Hub.
All SQLite queries are dispatched via asyncio.to_thread() to keep the event loop non-blocking.
"""

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from fastapi import Depends, HTTPException, Request, status

from app.core.config import get_db_path
from app.core.migrations import get_connection
from app.core.security import SESSION_COOKIE_NAME, verify_session_token

T = TypeVar("T")


async def run_db_query(fn: Callable[[sqlite3.Connection], T], custom_db_path: Optional[Path] = None) -> T:
    """
    Executes a synchronous database function in a worker thread using asyncio.to_thread().
    Automatically manages connection lifecycle.
    """
    db_path = custom_db_path or get_db_path()

    def _execute() -> T:
        with get_connection(db_path) as conn:
            conn.row_factory = sqlite3.Row
            return fn(conn)

    return await asyncio.to_thread(_execute)


async def get_current_user(request: Request) -> dict[str, Any]:
    """
    FastAPI dependency that validates the signed session cookie.
    Raises 401 Unauthorized if cookie is missing, invalid, or expired.
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

    return payload


async def get_optional_user(request: Request) -> Optional[dict[str, Any]]:
    """
    FastAPI dependency that extracts user session if present, but does not raise.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return verify_session_token(token)
