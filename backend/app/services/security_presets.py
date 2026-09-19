"""
Security Canary Alert Presets for LogShed.

Provides 1-click predefined alert rules for common critical security events:
- SSH brute-force attacks (with automated offending IP extraction)
- Web proxy 401/403 authorization floods
- Sudo privilege escalations
- Kernel Out-Of-Memory (OOM) killer terminations
"""

import ipaddress
import re
from typing import Any, Optional


# Regex patterns for extracting client and offending IP addresses from log messages
_CONTEXTUAL_IP_REGEX = re.compile(
    r"(?:from|rhost=|client[:=]|source[_-]?ip[:=]|host\s+)\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3}|[a-fA-F0-9:]{3,})",
    re.IGNORECASE,
)
_STANDALONE_IPV4_REGEX = re.compile(r"\b([0-9]{1,3}(?:\.[0-9]{1,3}){3})\b")


def extract_ip_from_message(message: str) -> Optional[str]:
    """
    Extract offending or source IP address from a log message string.

    Evaluates contextual indicators first (e.g., 'from 192.168.1.5', 'client: 10.0.0.2', 'rhost=172.16.0.4')
    preferring routable external IPs over loopback/unspecified addresses.
    """
    if not message:
        return None

    loopback_fallback = None

    # 1. Look for contextual prefix
    for match in _CONTEXTUAL_IP_REGEX.finditer(message):
        candidate = match.group(1).strip()
        try:
            ip_obj = ipaddress.ip_address(candidate)
            if ip_obj.is_loopback or ip_obj.is_unspecified:
                if loopback_fallback is None:
                    loopback_fallback = candidate
                continue
            return candidate
        except ValueError:
            continue

    # 2. Look for standalone IPv4 address (preferring non-loopback/non-unspecified)
    for candidate in _STANDALONE_IPV4_REGEX.findall(message):
        try:
            ip_obj = ipaddress.ip_address(candidate)
            if ip_obj.is_loopback or ip_obj.is_unspecified:
                if loopback_fallback is None:
                    loopback_fallback = candidate
                continue
            return candidate
        except ValueError:
            continue

    return loopback_fallback


SECURITY_PRESETS: list[dict[str, Any]] = [
    {
        "id": "ssh_bruteforce",
        "name": "SSH Brute-Force Detection",
        "description": "Detects repeated failed SSH authentication attempts from offending IP addresses.",
        "rule_type": "threshold",
        "filter_app": "sshd",
        "filter_severity": None,
        "match_pattern": r"Failed password|authentication failure|invalid user",
        "threshold_count": 5,
        "window_seconds": 60,
        "cooldown_seconds": 300,
        "ai_enrichment": True,
    },
    {
        "id": "proxy_auth_flood",
        "name": "Web Proxy 401/403 Auth Flood",
        "description": "Detects high-volume bursts of 401 Unauthorized and 403 Forbidden responses from proxies.",
        "rule_type": "threshold",
        "filter_app": None,
        "filter_severity": None,
        "match_pattern": r"(?:HTTP/[0-9.]+\"\s+|status[=:]\s*|\b)(?:401\s+Unauthorized|403\s+Forbidden)\b|(?:HTTP/[0-9.]+\"\s+|status[=:]\s*)(?:401|403)\b",
        "threshold_count": 20,
        "window_seconds": 60,
        "cooldown_seconds": 300,
        "ai_enrichment": True,
    },
    {
        "id": "sudo_escalation",
        "name": "Sudo Privilege Escalation",
        "description": "Detects execution of commands run with elevated root privileges via sudo.",
        "rule_type": "pattern",
        "filter_app": "sudo",
        "filter_severity": None,
        "match_pattern": r"COMMAND=",
        "threshold_count": 1,
        "window_seconds": 60,
        "cooldown_seconds": 60,
        "ai_enrichment": True,
    },
    {
        "id": "oom_killer",
        "name": "Kernel Out-Of-Memory (OOM) Kill",
        "description": "Detects Linux kernel out-of-memory killer invocations and terminated container tasks.",
        "rule_type": "pattern",
        "filter_app": None,
        "filter_severity": None,
        "match_pattern": r"Out of memory:\s*Kill(?:ed)? process|invoked oom-killer|oom[_-]killer|\bKilled process \d+ \([^)]+\)",
        "threshold_count": 1,
        "window_seconds": 60,
        "cooldown_seconds": 300,
        "ai_enrichment": True,
    },
]


def get_security_presets() -> list[dict[str, Any]]:
    """Return all predefined security canary presets."""
    return list(SECURITY_PRESETS)


def get_security_preset_by_id(preset_id: str) -> Optional[dict[str, Any]]:
    """Retrieve a specific security preset by ID."""
    clean_id = (preset_id or "").strip().lower()
    for preset in SECURITY_PRESETS:
        if preset["id"].lower() == clean_id:
            return dict(preset)
    return None
