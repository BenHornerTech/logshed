"""
Application configuration for LogShed.
Manages environment variables, filesystem paths, and defaults.
"""

import os
from pathlib import Path
from typing import Optional

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
