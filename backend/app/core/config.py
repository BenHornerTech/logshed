"""
Application configuration for Homelab Log Hub.
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
    return os.environ.get("LOG_HUB_SECRET_KEY")

def get_port() -> int:
    """Returns the web server listening port."""
    try:
        return int(os.environ.get("PORT", "8080"))
    except ValueError:
        return 8080

def get_docker_host() -> str:
    """Returns the Docker host socket or proxy address."""
    return os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")

def get_cors_origins() -> list[str]:
    """Returns the list of allowed CORS origins from CORS_ORIGINS env var or defaults."""
    env_origins = os.environ.get("CORS_ORIGINS")
    if env_origins:
        return [origin.strip() for origin in env_origins.split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
