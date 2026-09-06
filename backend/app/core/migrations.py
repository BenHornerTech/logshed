"""
Lightweight SQLite migration runner for LogShed.

This module provides a simple mechanism to manage SQLite database schema versions
using `PRAGMA user_version`. It ensures that database schemas are set up
and upgraded sequentially during application startup.

It uses the standard library `sqlite3` module exclusively and is designed to
be executed synchronously (e.g., via `asyncio.to_thread` from an async context).
All database connections opened through this module will automatically have WAL
mode and other performance pragmas enabled.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

def get_connection(db_path: Union[str, Path]) -> sqlite3.Connection:
    """
    Open a SQLite database connection and configure WAL mode pragmas.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        A configured sqlite3.Connection instance.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    # Ensure foreign keys are enforced
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def get_user_version(conn: sqlite3.Connection) -> int:
    """
    Read the current `PRAGMA user_version` from the database.

    Args:
        conn: The active sqlite3 connection.

    Returns:
        The current schema version as an integer.
    """
    cursor = conn.execute("PRAGMA user_version;")
    result = cursor.fetchone()
    return result[0] if result else 0

def set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """
    Set the `PRAGMA user_version` in the database.

    Args:
        conn: The active sqlite3 connection.
        version: The new version integer to set.
    """
    conn.execute(f"PRAGMA user_version = {version};")

def migrate_v1(conn: sqlite3.Connection) -> None:
    """
    Execute the full v1 DDL schema. This is Migration 1.

    Args:
        conn: The active sqlite3 connection.
    """
    logger.info("Running migration v1...")
    conn.executescript('''
CREATE TABLE logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    received_at DATETIME NOT NULL,
    source_ip TEXT NOT NULL,
    source_alias TEXT NOT NULL,
    app_name TEXT NOT NULL,
    facility INTEGER NOT NULL DEFAULT 1,
    severity INTEGER NOT NULL DEFAULT 6,
    message TEXT NOT NULL,
    raw TEXT NOT NULL
);

CREATE INDEX idx_logs_time_sev ON logs(timestamp DESC, severity);
CREATE INDEX idx_logs_app_time ON logs(app_name, timestamp DESC);
CREATE INDEX idx_logs_src_time ON logs(source_alias, timestamp DESC);
CREATE INDEX idx_logs_source_ip ON logs(source_ip);

CREATE VIRTUAL TABLE logs_fts USING fts5(
    app_name,
    source_alias,
    message,
    content='logs',
    content_rowid='id'
);

CREATE TRIGGER logs_ai AFTER INSERT ON logs BEGIN
    INSERT INTO logs_fts(rowid, app_name, source_alias, message)
    VALUES (new.id, new.app_name, new.source_alias, new.message);
END;

CREATE TRIGGER logs_ad AFTER DELETE ON logs BEGIN
    INSERT INTO logs_fts(logs_fts, rowid, app_name, source_alias, message)
    VALUES('delete', old.id, old.app_name, old.source_alias, old.message);
END;

CREATE TRIGGER logs_au AFTER UPDATE ON logs BEGIN
    INSERT INTO logs_fts(logs_fts, rowid, app_name, source_alias, message)
    VALUES('delete', old.id, old.app_name, old.source_alias, old.message);
    INSERT INTO logs_fts(rowid, app_name, source_alias, message)
    VALUES (new.id, new.app_name, new.source_alias, new.message);
END;

CREATE TABLE host_aliases (
    ip TEXT PRIMARY KEY,
    alias TEXT NOT NULL,
    notes TEXT,
    created_at DATETIME NOT NULL
);

CREATE TABLE storage_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at DATETIME NOT NULL,
    db_size_bytes INTEGER NOT NULL,
    disk_free_bytes INTEGER NOT NULL,
    disk_total_bytes INTEGER NOT NULL,
    total_logs_count INTEGER NOT NULL
);

CREATE INDEX idx_storage_metrics_time ON storage_metrics(recorded_at DESC);

CREATE TABLE ai_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    source_alias TEXT NOT NULL,
    app_name TEXT NOT NULL,
    log_count INTEGER NOT NULL,
    user_context TEXT,
    model TEXT NOT NULL,
    prompt_sent TEXT NOT NULL,
    response_text TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    tokens_thoughts INTEGER NOT NULL DEFAULT 0,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    system_prompt TEXT
);

CREATE TABLE admin_auth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE TABLE system_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at DATETIME NOT NULL,
    is_encrypted BOOLEAN DEFAULT 0
);

INSERT OR IGNORE INTO system_settings (key, value, updated_at, is_encrypted)
VALUES ('retention_days', '14', datetime('now'), 0);
''')


# Registry of migrations to run. Must be ordered by version ascending.
MIGRATIONS = [
    (1, migrate_v1),
]

def run_migrations(db_path: Union[str, Path]) -> None:
    """
    Main entry point for database migrations.

    Opens the database, enables WAL mode pragmas, and runs all pending migrations
    in order based on the current PRAGMA user_version.

    Args:
        db_path: Path to the SQLite database file.
    """
    db_path = Path(db_path)
    # Ensure the parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with get_connection(db_path) as conn:
        current_version = get_user_version(conn)
        logger.info(f"Current database version: {current_version}")

        for target_version, migration_func in MIGRATIONS:
            if current_version < target_version:
                logger.info(f"Migrating from {current_version} to {target_version}")
                try:
                    migration_func(conn)
                    set_user_version(conn, target_version)
                    conn.commit()
                    current_version = target_version
                    logger.info(f"Successfully migrated to version {target_version}")
                except Exception as e:
                    conn.rollback()
                    logger.error(f"Migration to version {target_version} failed: {e}")
                    raise
            else:
                logger.debug(f"Skipping migration {target_version}, already applied.")

        # Startup sanitization: ensure any legacy or future-dated timestamps are clamped to received_at
        try:
            conn.execute("UPDATE logs SET timestamp = received_at WHERE timestamp > received_at;")
            conn.commit()
        except sqlite3.OperationalError:
            pass

        # Ensure default retention_days setting exists
        try:
            conn.execute(
                "INSERT OR IGNORE INTO system_settings (key, value, updated_at, is_encrypted) "
                "VALUES ('retention_days', '14', datetime('now'), 0);"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass

