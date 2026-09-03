"""
Notification API endpoints for LogShed.
Dispatches test messages and user-initiated AI analysis alerts to Pushover.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, run_db_query
from app.core.security import decrypt_value
from app.models import MessageResponse, NotificationQueuedResponse, PushoverNotificationRequest
from app.services.notifier import send_pushover_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


async def _get_pushover_credentials() -> tuple[str, str]:
    """Helper to fetch and decrypt Pushover credentials from system_settings."""
    def _read(conn):
        cursor = conn.cursor()
        cursor.execute(
            "SELECT key, value, is_encrypted FROM system_settings WHERE key IN ('pushover_user_key', 'pushover_app_token')"
        )
        rows = cursor.fetchall()
        creds = {}
        for r in rows:
            k = r["key"]
            v = r["value"] or ""
            if bool(r["is_encrypted"]) and v:
                try:
                    v = decrypt_value(v)
                except Exception:
                    v = ""
            creds[k] = v
        return creds.get("pushover_user_key", ""), creds.get("pushover_app_token", "")

    user_key, app_token = await run_db_query(_read)
    return user_key, app_token


@router.post("/test", response_model=MessageResponse)
async def test_notifications(user: dict = Depends(get_current_user)) -> MessageResponse:
    """
    Test Pushover credentials by sending a test alert to configured user/app.
    """
    user_key, app_token = await _get_pushover_credentials()
    if not user_key or not app_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pushover User Key and App Token must be configured in Settings.",
        )

    try:
        await send_pushover_message(
            user_key=user_key,
            app_token=app_token,
            title="[LogShed] Test Notification",
            message="Pushover notifications configured successfully for LogShed!",
            priority=0,
        )
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as exc:
        logger.error(f"Pushover test notification failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Pushover test notification failed: {exc}",
        )

    return MessageResponse(status="ok", detail="Pushover test notification sent successfully.")


@router.post("/pushover", response_model=NotificationQueuedResponse)
async def send_pushover_notification(
    req: PushoverNotificationRequest,
    user: dict = Depends(get_current_user),
) -> NotificationQueuedResponse:
    """
    Dispatch manual notification to Pushover devices.
    Forwards exact title, message, and priority with NO server-side severity classification.
    """
    user_key, app_token = await _get_pushover_credentials()
    if not user_key or not app_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pushover User Key and App Token must be configured in Settings.",
        )

    try:
        await send_pushover_message(
            user_key=user_key,
            app_token=app_token,
            title=req.title,
            message=req.message,
            priority=req.priority if req.priority is not None else 0,
        )
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as exc:
        logger.error(f"Pushover notification failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Pushover notification failed: {exc}",
        )

    return NotificationQueuedResponse(status="sent")
