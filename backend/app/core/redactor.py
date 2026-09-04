"""
On-demand secret redactor for LogShed.

This module provides regex-based scrubbing of sensitive tokens, passwords,
API keys, and credentials from log text before it is dispatched to an
external LLM for analysis.

IMPORTANT: This is NOT applied during ingestion or SQLite insertion.
Raw logs remain unredacted in the database. Redaction is applied
on-demand only when preparing log text for AI preview/diagnosis
(per SPEC.md §3 and §4.1).
"""

import re
from typing import Union

# Placeholder used for redacted values
REDACTED = "[REDACTED]"

# Compiled regex patterns for secret detection.
# Each tuple: (pattern_name, compiled_regex, replacement_template)
# Replacement templates use \1 etc. to preserve context around the redacted value.
_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # --- Authentication Headers ---
    # Bearer tokens in Authentication headers (matching RFC 7235 Authorization header syntax)
    (
        "bearer_token",
        re.compile(
            r"((?:Authorization|authorization)\s*[:=]\s*Bearer\s+)\S+",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
    # Basic auth in Authentication headers
    (
        "basic_auth",
        re.compile(
            r"((?:Authorization|authorization)\s*[:=]\s*Basic\s+)\S+",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- API Keys ---
    # Generic API key patterns (key=value, key: value)
    (
        "api_key",
        re.compile(
            r"((?:api[_-]?key|apikey|api[_-]?secret|api[_-]?token|access[_-]?key|secret[_-]?key)"
            r"\s*[:=]\s*)[\"']?[\w\-./+=]{8,}[\"']?",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
    # X-API-Key header
    (
        "x_api_key_header",
        re.compile(
            r"(X-API-Key\s*[:=]\s*)\S+",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- Password Fields ---
    # password=..., password: ..., passwd=..., etc.
    (
        "password_field",
        re.compile(
            r"((?:password|passwd|pwd|pass)\s*[:=]\s*)[\"']?[^\s,;\"'}{)(\]]+[\"']?",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- Tokens (generic) ---
    # token=..., access_token=..., refresh_token=..., auth_token=...
    (
        "token_field",
        re.compile(
            r"((?:access[_-]?token|refresh[_-]?token|auth[_-]?token|session[_-]?token|token)"
            r"\s*[:=]\s*)[\"']?[\w\-./+=]{8,}[\"']?",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- JWTs ---
    # Three base64url-encoded sections separated by dots
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        ),
        REDACTED,
    ),

    # --- AWS Credentials ---
    # AWS Access Key ID (starts with AKIA, ASIA, AIDA, AROA, or ANPA)
    (
        "aws_access_key",
        re.compile(
            r"\b((?:A3T[A-Z0-9]|AKIA|ASIA|AIDA|AROA|ANPA|AIPA|ANVA|APKA)[A-Z0-9]{16})\b",
        ),
        REDACTED,
    ),
    # AWS Secret Key (40-char base64-ish string after aws_secret prefix)
    (
        "aws_secret_key",
        re.compile(
            r"((?:aws_secret_access_key|aws_secret_key|secret_access_key)\s*[:=]\s*)[\"']?"
            r"[A-Za-z0-9/+=]{40}[\"']?",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),

    # --- Private Keys ---
    # PEM private key blocks
    (
        "private_key",
        re.compile(
            r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----"
            r"[\s\S]*?"
            r"-----END\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----",
        ),
        REDACTED,
    ),

    # --- Connection Strings ---
    # Database/service URLs with embedded credentials  (user:pass@host)
    (
        "connection_string",
        re.compile(
            r"((?:mysql|postgres|postgresql|mongodb|redis|amqp|smtp|ftp|https?)"
            r"://[^:]+:)[^@\s]+(@)",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}\2",
    ),

    # --- Pushover / Generic Service Tokens ---
    (
        "pushover_token",
        re.compile(
            r"((?:pushover[_-]?(?:user[_-]?key|app[_-]?token|token|key))\s*[:=]\s*)[\"']?\S+[\"']?",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
]


def redact(text: Union[str, list[str]]) -> Union[str, list[str]]:
    """
    Scrub sensitive secrets from log text.

    Args:
        text: A single log string or a list of log strings.

    Returns:
        The redacted text with secrets replaced by [REDACTED].
        Returns the same type as the input (str or list[str]).
    """
    if isinstance(text, list):
        return [_redact_single(line) for line in text]
    return _redact_single(text)


def _redact_single(text: str) -> str:
    """Apply all redaction patterns to a single string."""
    result = text
    for _name, pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result
