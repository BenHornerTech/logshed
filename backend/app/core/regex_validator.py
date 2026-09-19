"""
Regex validation and ReDoS protection utilities for LogShed.

Provides security checks against pathological nested repetition antipatterns
susceptible to catastrophic backtracking (Regular Expression Denial of Service).
"""

import re
from typing import Optional, Tuple
from fastapi import HTTPException, status

# Detect nested repetition antipatterns:
# e.g. (a+)+, ([a-z]+)*, (a*)+, ((a+)+)+, (a+){2,}, (?:a+)+, (\w+)*
_NESTED_REPETITION_RE = re.compile(
    r"""
    (
        \((?:\?[:=!<=])?        # Group start: (, (?:, (?=, (?!, (?<=, (?<!
        (?:[^\(\)]*?            # Inner content without unescaped parenthesis
            (?:
                [+*]            # Inner quantifier + or *
                |\{\d+,\d*\}    # Inner range quantifier {n,} or {n,m}
            )
        [^\(\)]*?)
        \)                      # Group end
        (?:\+|\*|\{\d+,\d*\})   # Outer quantifier +, *, or {n,m}
    )
    """,
    re.VERBOSE,
)


def is_catastrophic_backtracking_pattern(pattern: str) -> bool:
    """
    Check if a regex pattern contains nested repetition structures
    vulnerable to catastrophic polynomial or exponential backtracking.
    """
    if not pattern:
        return False
    # Strip escaped characters so literal escaped characters like \\+ or \\( do not trigger false matches
    stripped = re.sub(r"\\.", "", pattern)
    return bool(_NESTED_REPETITION_RE.search(stripped))


def check_regex_safety(pattern: Optional[str]) -> Tuple[bool, Optional[str]]:
    """
    Inspect a user-supplied regular expression pattern for validity and safety.

    Returns:
        (is_safe, error_message)
    """
    if not pattern or not pattern.strip() or pattern.strip() == "*":
        return True, None

    trimmed = pattern.strip()

    # 1. Check for pathological nested repetition antipatterns
    if is_catastrophic_backtracking_pattern(trimmed):
        return (
            False,
            "Regular expression contains nested repetitions susceptible to catastrophic backtracking.",
        )

    # 2. Validate regex compilation
    try:
        re.compile(trimmed)
    except re.error as exc:
        return False, f"Invalid regex syntax (Invalid regular expression): {exc}"

    return True, None


def validate_regex_pattern(pattern: Optional[str]) -> None:
    """
    Validate a user-supplied regular expression pattern.
    Raises HTTPException(400) if syntax is invalid or vulnerable to catastrophic backtracking.
    """
    is_safe, error_msg = check_regex_safety(pattern)
    if not is_safe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg or "Invalid regular expression pattern.",
        )
