"""
Shared core utility functions for string matching, normalization, and timestamp parsing.
"""

import datetime
import fnmatch
from typing import Any, Optional


def match_wildcard(pattern: Optional[str], text: Optional[str]) -> bool:
    """
    Case-insensitive wildcard matching supporting '*' and '?'.
    If no wildcard characters exist in pattern, performs an exact case-insensitive match.
    """
    if not pattern or not pattern.strip():
        return True
    if not text:
        return False

    p = pattern.lower().strip()
    t = text.lower().strip()
    if "*" in p or "?" in p:
        return fnmatch.fnmatchcase(t, p)
    return p == t


def parse_iso_to_epoch(ts_val: Any, fallback: Optional[float] = None) -> float:
    """
    Parse an ISO-8601 timestamp string or datetime object into a UTC epoch timestamp.
    Returns fallback (defaulting to 0.0 if None) when parsing fails or input is empty.
    """
    default_fallback = 0.0 if fallback is None else fallback
    if ts_val is None:
        return default_fallback
    if isinstance(ts_val, (int, float)):
        return float(ts_val)
    if isinstance(ts_val, datetime.datetime):
        if ts_val.tzinfo is None:
            ts_val = ts_val.replace(tzinfo=datetime.timezone.utc)
        return ts_val.timestamp()
    try:
        clean_ts = str(ts_val).strip().replace("Z", "+00:00")
        if not clean_ts:
            return default_fallback
        dt = datetime.datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return default_fallback
