"""
Notification API endpoints (stubs for Phase 4 UI testing and Phase 5 integration).
"""

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models import MessageResponse, NotificationQueuedResponse, PushoverNotificationRequest

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.post("/test", response_model=MessageResponse)
async def test_notifications(user: dict = Depends(get_current_user)) -> MessageResponse:
    """
    Stub endpoint to test notification service connectivity for UI integration.
    """
    return MessageResponse(status="ok")


@router.post("/pushover", response_model=NotificationQueuedResponse)
async def send_pushover_notification(
    req: PushoverNotificationRequest,
    user: dict = Depends(get_current_user),
) -> NotificationQueuedResponse:
    """
    Stub endpoint to dispatch notification to Pushover devices for UI integration.
    """
    return NotificationQueuedResponse(status="queued")
