"""
Notification delivery service for LogShed.

Integrates with Apprise to provide universal webhook and alert notifications
across 80+ platforms (Discord, Gotify, Telegram, Ntfy, Pushover, Slack, Email, etc.).
Handles URL encryption at rest via LogShed master encryption key, token masking,
test deliveries, and asynchronous dispatch using asyncio.to_thread.
"""

import asyncio
import logging
from typing import Optional, Tuple

import apprise

from app.core.security import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)


def mask_notification_url(raw_url: str) -> str:
    """
    Mask sensitive secrets, tokens, and passwords in a notification URL for safe client display.

    Uses Apprise's native privacy mode if parseable, with fallback redaction
    for arbitrary URLs.
    """
    if not raw_url:
        return ""

    stripped = raw_url.strip()
    try:
        ap_obj = apprise.Apprise()
        if ap_obj.add(stripped):
            masked_urls = [server.url(privacy=True) for server in ap_obj]
            if masked_urls:
                clean_url = masked_urls[0].split("/?")[0] if "/?" in masked_urls[0] else masked_urls[0].split("?")[0]
                return clean_url
    except Exception as e:
        logger.debug(f"Apprise privacy mask fallback: {e}")

    if "://" in stripped:
        scheme, remainder = stripped.split("://", 1)
        if "/" in remainder:
            host_part, _ = remainder.split("/", 1)
            return f"{scheme}://{host_part}/********"
        return f"{scheme}://********"
    return "********"


def validate_notification_url(url: str) -> Tuple[bool, Optional[str]]:
    """
    Validate that a notification URL is supported and syntax-valid according to Apprise.

    Returns:
        (is_valid, error_message)
    """
    if not url or not url.strip():
        return False, "Notification URL cannot be empty."

    trimmed = url.strip()
    try:
        ap_obj = apprise.Apprise()
        added = ap_obj.add(trimmed)
        if not added:
            return False, "Unsupported notification URL schema or invalid format."
        return True, None
    except Exception as exc:
        return False, f"Invalid notification URL: {exc}"


def encrypt_channel_url(url: str) -> str:
    """Encrypt a plaintext notification URL for persistent storage in SQLite."""
    return encrypt_value(url.strip())


def decrypt_channel_url(encrypted_url: str) -> str:
    """Decrypt a stored ciphertext notification URL into plaintext."""
    return decrypt_value(encrypted_url)


def _configure_apprise_servers(ap_obj: apprise.Apprise) -> None:
    """
    Ensure rich formatting (HTML/Markdown) is enabled on notification services
    that support it (such as Pushover, Discord, Gotify, Ntfy) when not explicitly set in the URL.
    For Pushover, enables HTML formatting so bold labels and paragraphs render natively
    rather than showing unrendered raw markdown asterisks.
    """
    for server in ap_obj:
        cls_name = server.__class__.__name__
        if cls_name == "NotifyPushover":
            if server.notify_format == apprise.NotifyFormat.TEXT:
                server.notify_format = apprise.NotifyFormat.HTML
        elif cls_name in ("NotifyDiscord", "NotifyNtfy", "NotifyGotify"):
            if server.notify_format == apprise.NotifyFormat.TEXT:
                server.notify_format = apprise.NotifyFormat.MARKDOWN


def _sync_send_notification(
    urls: list[str],
    title: str,
    body: str,
    body_format: str = apprise.NotifyFormat.MARKDOWN,
) -> bool:
    """
    Synchronous Apprise dispatch function executed in a worker thread via asyncio.to_thread.
    Passes body_format (defaults to NotifyFormat.MARKDOWN) so services like Pushover, Slack,
    Discord, and Telegram receive rich formatted text (HTML/Markdown) rather than raw text.
    """
    if not urls:
        logger.debug("No notification URLs provided to dispatch.")
        return False

    try:
        ap_obj = apprise.Apprise()
        for u in urls:
            ap_obj.add(u)
        _configure_apprise_servers(ap_obj)

        success = ap_obj.notify(
            title=title,
            body=body,
            body_format=body_format,
        )
        return bool(success)
    except Exception as exc:
        logger.error(f"Failed to dispatch notification: {exc}")
        return False


def _sync_test_channel(
    url: str,
    title: str,
    body: str,
    body_format: str = apprise.NotifyFormat.MARKDOWN,
) -> Tuple[bool, str]:
    """
    Synchronously test a single notification URL via Apprise in a worker thread.
    """
    try:
        ap_obj = apprise.Apprise()
        added = ap_obj.add(url.strip())
        if not added:
            return False, "Failed to initialize notification target. Please check URL syntax."
        _configure_apprise_servers(ap_obj)

        success = ap_obj.notify(
            title=title,
            body=body,
            body_format=body_format,
        )
        if success:
            return True, "Notification sent successfully."
        else:
            return False, "Notification service rejected delivery. Please verify webhook credentials or token permissions."
    except Exception as exc:
        return False, f"Delivery error: {exc}"



class NotifierService:
    """
    Notification service managing channel decryption, testing, and async dispatches.
    """

    def __init__(self, db_path=None):
        self._db_path = db_path

    async def test_channel(
        self,
        url: str,
        title: str = "LogShed Notification Test",
        body: str = "**LogShed Test:** Connection successful! LogShed is configured to send alerts to this channel.",
        body_format: str = apprise.NotifyFormat.MARKDOWN,
    ) -> Tuple[bool, str]:
        """
        Deliver an immediate test notification to verify connectivity.
        """
        valid, err = validate_notification_url(url)
        if not valid:
            return False, err or "Invalid notification URL."

        return await asyncio.to_thread(_sync_test_channel, url, title, body, body_format)

    async def send_notification(
        self,
        title: str,
        body: str,
        channel_id: Optional[int] = None,
        body_format: str = apprise.NotifyFormat.MARKDOWN,
    ) -> bool:
        """
        Dispatch a notification to enabled channels (or a specific channel ID).
        """
        from pathlib import Path
        from app.api.deps import run_db_query
        from app.core.config import get_db_path

        db_path = self._db_path or get_db_path()

        def _fetch_channel_urls(conn) -> list[str]:
            cur = conn.cursor()
            if channel_id is not None:
                cur.execute(
                    "SELECT url FROM notification_channels WHERE id = ? AND is_enabled = 1",
                    (channel_id,),
                )
            else:
                cur.execute(
                    "SELECT url FROM notification_channels WHERE is_enabled = 1"
                )
            rows = cur.fetchall()
            decrypted_urls = []
            for r in rows:
                encrypted_url = r["url"] if hasattr(r, "keys") else r[0]
                try:
                    plain_url = decrypt_channel_url(encrypted_url)
                    if plain_url:
                        decrypted_urls.append(plain_url)
                except Exception as e:
                    logger.error(f"Failed to decrypt notification channel URL: {e}")
            return decrypted_urls

        try:
            urls = await run_db_query(_fetch_channel_urls, custom_db_path=Path(db_path))
        except Exception as exc:
            logger.error(f"Error querying notification channels: {exc}")
            return False

        if not urls:
            logger.debug("No active notification channels found for dispatch.")
            return False

        return await asyncio.to_thread(_sync_send_notification, urls, title, body, body_format)


_notifier_instance: Optional[NotifierService] = None


def get_notifier() -> NotifierService:
    """Return the global NotifierService singleton instance."""
    global _notifier_instance
    if _notifier_instance is None:
        _notifier_instance = NotifierService()
    return _notifier_instance
