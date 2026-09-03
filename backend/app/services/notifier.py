"""
Pushover Notification Service for LogShed.
Dispatches user-initiated log summaries and test alerts to Pushover mobile endpoints.
"""

import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


async def send_pushover_message(
    user_key: str,
    app_token: str,
    title: str,
    message: str,
    priority: int = 0,
    timeout: float = 15.0,
) -> dict:
    """
    Send a notification to Pushover devices.
    Accepts title, message, and priority directly from caller without server-side classification.
    """
    if not user_key or not app_token:
        raise ValueError("Pushover User Key and App Token must be configured.")

    if not message or not message.strip():
        raise ValueError("Notification message cannot be empty.")

    payload = {
        "token": app_token,
        "user": user_key,
        "title": title or "[LogShed]",
        "message": message,
        "priority": priority,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.post(PUSHOVER_API_URL, data=payload)
        
        try:
            data = res.json()
        except Exception:
            data = {"raw": res.text}

        if res.status_code != 200 or data.get("status") != 1:
            errors = data.get("errors", [f"HTTP {res.status_code}: {res.text}"])
            err_msg = f"Pushover dispatch failed: {', '.join(errors)}"
            logger.error(err_msg)
            raise RuntimeError(err_msg)

        return data
