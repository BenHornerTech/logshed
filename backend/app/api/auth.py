"""
Authentication API endpoints for Homelab Log Hub.
Provides setup lockout, Argon2id verification, rate-limited login, and session cookies.
"""

import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.deps import get_optional_user, run_db_query
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    hash_password,
    verify_password,
)
from app.models import AuthStatusResponse, LoginRequest, MessageResponse, SetupRequest

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _get_client_ip(request: Request) -> str:
    """Extract client IP address, checking X-Forwarded-For header first."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


@router.post("/setup", response_model=MessageResponse)
async def setup_admin(req: SetupRequest, response: Response) -> MessageResponse:
    """
    First-run setup: creates admin user and password.
    Returns 403 Forbidden once admin_auth is populated.
    """
    def _check_and_insert(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM admin_auth")
        count = cursor.fetchone()[0]
        if count > 0:
            return False

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        pwd_hash = hash_password(req.password)
        cursor.execute(
            "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) VALUES (1, ?, ?, ?)",
            (pwd_hash, now, now),
        )
        conn.commit()
        return True

    success = await run_db_query(_check_and_insert)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin account has already been set up.",
        )

    # Issue signed session cookie
    token = create_session_token(user_id=1)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
        max_age=7 * 24 * 3600,
    )
    return MessageResponse(status="ok")


@router.post("/login", response_model=MessageResponse)
async def login(req: LoginRequest, request: Request, response: Response) -> MessageResponse:
    """
    Session login endpoint.
    Enforces strict in-memory rate limiting on failed attempts per IP.
    """
    client_ip = _get_client_ip(request)

    # Check brute-force rate limit
    if login_rate_limiter.is_rate_limited(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please try again in a minute.",
        )

    def _get_admin(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM admin_auth WHERE id = 1")
        row = cursor.fetchone()
        return row[0] if row else None

    stored_hash = await run_db_query(_get_admin)
    if not stored_hash or not verify_password(stored_hash, req.password):
        login_rate_limiter.record_failure(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password.",
        )

    # Reset failed attempts on success
    login_rate_limiter.record_success(client_ip)

    token = create_session_token(user_id=1)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
        max_age=7 * 24 * 3600,
    )
    return MessageResponse(status="ok")


@router.post("/logout", response_model=MessageResponse)
async def logout(response: Response) -> MessageResponse:
    """Clears the session cookie."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
    )
    return MessageResponse(status="ok")


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(request: Request) -> AuthStatusResponse:
    """Returns application setup status and whether current session is authenticated."""
    def _is_setup(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM admin_auth")
        return cursor.fetchone()[0] > 0

    is_setup = await run_db_query(_is_setup)
    user = await get_optional_user(request)

    return AuthStatusResponse(
        setup_required=not is_setup,
        authenticated=user is not None,
    )
