"""
Application configuration for LogShed.
Manages environment variables, filesystem paths, and defaults.
"""

import logging
import os
from pathlib import Path
from typing import Optional, Union

def get_data_dir() -> Path:
    """Returns the configured data directory path."""
    return Path(os.environ.get("DATA_DIR", "/data"))

def get_db_path() -> Path:
    """Returns the configured database file path."""
    env_path = os.environ.get("DB_PATH")
    if env_path:
        return Path(env_path)
    return get_data_dir() / "logs.db"

def get_secret_key_path() -> Path:
    """Returns the path to the persisted secret key file."""
    env_path = os.environ.get("SECRET_KEY_PATH")
    if env_path:
        return Path(env_path)
    return get_data_dir() / ".secret_key"

def get_secret_key_override() -> Optional[str]:
    """Returns the secret key override from environment if provided."""
    return os.environ.get("LOGSHED_SECRET_KEY")

def get_port() -> int:
    """Returns the web server listening port."""
    try:
        return int(os.environ.get("PORT", "8080"))
    except ValueError:
        return 8080

def get_syslog_port() -> int:
    """
    Returns the configured Syslog UDP and TCP listening port.
    Reads SYSLOG_PORT environment variable (default: 1514).
    Validates that the port is an integer in the range 1 <= port <= 65535,
    falling back to 1514 if unset or invalid.
    """
    raw = os.environ.get("SYSLOG_PORT", "1514")
    try:
        val = int(raw.strip())
        if 1 <= val <= 65535:
            return val
    except (ValueError, TypeError):
        pass
    return 1514

def get_docker_host() -> str:
    """Returns the Docker host socket or proxy address."""
    return os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")

def is_debug_or_dev() -> bool:
    """Returns True if running in development mode or debug is enabled."""
    return (
        os.environ.get("ENVIRONMENT", "").lower() == "development"
        or os.environ.get("DEBUG", "").lower() in ("true", "1", "yes")
    )


def get_cors_origins() -> list[str]:
    """
    Returns the list of allowed CORS origins.
    Strictly defaults to [] in production (same-origin React SPA bundle).
    Populates development origins only if ENVIRONMENT=development or DEBUG=True,
    or if explicitly overridden via the CORS_ORIGINS environment variable.
    """
    env_origins = os.environ.get("CORS_ORIGINS")
    if env_origins is not None:
        return [origin.strip() for origin in env_origins.split(",") if origin.strip()]

    if is_debug_or_dev():
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    return []


def get_max_retention_days() -> int:
    """
    Returns the maximum allowed log retention period in days.
    Defaults to 30 days. Configurable via the MAX_RETENTION_DAYS environment variable.
    Enforced to have a minimum value of at least 1 day.
    """
    raw = os.environ.get("MAX_RETENTION_DAYS")
    if raw is not None:
        try:
            val = int(raw.strip())
            return max(1, val)
        except ValueError:
            pass
    return 30


def is_max_retention_days_overridden() -> bool:
    """
    Returns True if MAX_RETENTION_DAYS is explicitly defined as a valid integer in the environment.
    """
    raw = os.environ.get("MAX_RETENTION_DAYS")
    if raw is not None and raw.strip():
        try:
            int(raw.strip())
            return True
        except ValueError:
            return False
    return False


VALID_LOG_LEVELS: dict[str, Optional[int]] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
    "DISABLED": None,
    "OFF": None,
    "NONE": None,
    "FALSE": None,
    "0": None,
}

DEFAULT_AI_MODEL = "gemini-3.7-flash"
DEFAULT_AI_TIMEOUT: float = float(os.environ.get("LOGSHED_AI_TIMEOUT", "45.0"))
DEFAULT_AI_THINKING_BUDGET: int = int(os.environ.get("LOGSHED_AI_THINKING_BUDGET", "1024"))
DEFAULT_INTERNAL_LOG_LEVEL = logging.WARNING


def parse_internal_log_level(val: Optional[Union[str, int]]) -> Optional[int]:
    """
    Parses a log level representation into an integer logging level or None if disabled.
    Accepts string level names (DEBUG, INFO, WARNING, WARN, ERROR, CRITICAL, FATAL, DISABLED, OFF, NONE)
    or integer levels.
    Defaults to logging.WARNING if val is None or empty string.
    Falls back to logging.WARNING if val is unrecognized.
    """
    if val is None:
        return DEFAULT_INTERNAL_LOG_LEVEL
    if isinstance(val, int):
        return val
    s = str(val).strip().upper()
    if not s:
        return DEFAULT_INTERNAL_LOG_LEVEL
    if s in VALID_LOG_LEVELS:
        return VALID_LOG_LEVELS[s]
    try:
        return int(s)
    except ValueError:
        pass
    return DEFAULT_INTERNAL_LOG_LEVEL


def get_internal_log_level() -> Optional[int]:
    """
    Returns the configured logging level for LogShed's internal log handler.
    Reads the LOGSHED_INTERNAL_LOG_LEVEL environment variable (default: WARNING).
    Returns None if internal logging is disabled (e.g. 'DISABLED', 'OFF', 'NONE').
    """
    raw = os.environ.get("LOGSHED_INTERNAL_LOG_LEVEL")
    return parse_internal_log_level(raw)


def get_internal_log_level_name(level: Optional[int] = ...) -> str:
    """
    Returns the canonical string representation of an internal logging level.
    If level is omitted, reads from current environment configuration.
    """
    if level is ...:
        level = get_internal_log_level()
    if level is None:
        return "DISABLED"
    name = logging.getLevelName(level)
    if isinstance(name, str) and not name.startswith("Level "):
        return name
    return str(level)


def to_canonical_log_level_name(val: Optional[Union[str, int]]) -> str:
    """
    Converts any log level representation (string name, alias, integer, None)
    to its canonical string name: DEBUG, INFO, WARNING, ERROR, CRITICAL, or DISABLED.
    """
    parsed = parse_internal_log_level(val)
    return get_internal_log_level_name(parsed)

