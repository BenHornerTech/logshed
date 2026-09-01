"""
Server-Sent Events (SSE) broadcaster for real-time log streaming.
"""

import asyncio
import json
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

class SSEBroadcaster:
    """
    Manages active SSE client subscriber queues and broadcasts incoming log entries.
    """

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self, maxsize: int = 500) -> asyncio.Queue:
        """Register a new client subscriber queue."""
        q = asyncio.Queue(maxsize=maxsize)
        async with self._lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        async with self._lock:
            self._subscribers.discard(q)

    async def broadcast(self, log_entry: dict) -> None:
        """
        Async broadcast call safely pushing a copy of log_entry to all active queues under lock.
        """
        async with self._lock:
            for q in list(self._subscribers):
                try:
                    q.put_nowait(log_entry)
                except asyncio.QueueFull:
                    # Discard oldest item if queue is full to prevent lag
                    try:
                        q.get_nowait()
                        q.put_nowait(log_entry)
                    except Exception:
                        pass
                except Exception as e:
                    logger.debug(f"Error broadcasting to subscriber: {e}")

    def subscriber_count(self) -> int:
        """Return number of active SSE subscribers."""
        return len(self._subscribers)

    def reset(self) -> None:
        """Reset all subscribers (for testing)."""
        self._subscribers.clear()


# Global SSE manager singleton
sse_manager = SSEBroadcaster()
