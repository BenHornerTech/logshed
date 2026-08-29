"""
In-memory sliding window rate limiter for login brute-force prevention.
Tracks failed attempts per IP address within a 60-second window.
"""

import time
from collections import defaultdict
from typing import DefaultDict

class LoginRateLimiter:
    """
    Sliding-window rate limiter for failed login attempts.
    Allows up to `max_attempts` failures per `window_seconds` per IP.
    """

    def __init__(self, max_attempts: int = 5, window_seconds: float = 60.0):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        # Maps IP string to list of failure epoch timestamps
        self._failures: DefaultDict[str, list[float]] = defaultdict(list)

    def _cleanup_ip(self, ip: str, now: float) -> None:
        """Removes timestamps outside the current sliding window."""
        cutoff = now - self.window_seconds
        self._failures[ip] = [t for t in self._failures[ip] if t > cutoff]
        if not self._failures[ip]:
            self._failures.pop(ip, None)

    def is_rate_limited(self, ip: str) -> bool:
        """
        Check if an IP has exceeded the allowed failed attempts within the window.
        """
        now = time.time()
        self._cleanup_ip(ip, now)
        return len(self._failures.get(ip, [])) >= self.max_attempts

    def record_failure(self, ip: str) -> None:
        """Record a failed login attempt for the given IP."""
        now = time.time()
        self._cleanup_ip(ip, now)
        self._failures[ip].append(now)

    def record_success(self, ip: str) -> None:
        """
        Clear failure history for an IP on successful login so legitimate
        users are not locked out.
        """
        self._failures.pop(ip, None)

    def reset(self) -> None:
        """Clear all rate limit state (useful for testing)."""
        self._failures.clear()


# Global singleton instance
login_rate_limiter = LoginRateLimiter(max_attempts=5, window_seconds=60.0)
