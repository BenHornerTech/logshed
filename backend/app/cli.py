"""
CLI tool for Homelab Log Hub admin operations.

Usage:
    python -m app.cli reset-admin --password <new_password>

Provides password recovery by UPSERTing the admin_auth row (id=1)
with a new Argon2id password hash.
"""

import argparse
import datetime
import sys

from argon2 import PasswordHasher

from app.core.migrations import get_connection, run_migrations

# Default database path inside the container
_DEFAULT_DB_PATH = "/data/logs.db"


def reset_admin(password: str, db_path: str = _DEFAULT_DB_PATH) -> None:
    """
    Reset the admin password by UPSERTing admin_auth row id=1.

    Works whether the admin_auth table is empty (first-time) or
    already has a password set (recovery).

    Args:
        password: The new plaintext password to hash and store.
        db_path: Path to the SQLite database.
    """
    # Ensure the database and schema exist
    run_migrations(db_path)

    ph = PasswordHasher()
    password_hash = ph.hash(password)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    conn = get_connection(db_path)
    try:
        # UPSERT: insert if no row exists, update if id=1 already present
        conn.execute(
            """
            INSERT INTO admin_auth (id, password_hash, created_at, updated_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                password_hash = excluded.password_hash,
                updated_at = excluded.updated_at
            """,
            (password_hash, now, now),
        )
        conn.commit()
        print("Admin password has been reset successfully.")
    except Exception as e:
        conn.rollback()
        print(f"Error resetting admin password: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="app.cli",
        description="Homelab Log Hub CLI administration tool",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # reset-admin subcommand
    reset_parser = subparsers.add_parser(
        "reset-admin",
        help="Reset the admin password",
    )
    reset_parser.add_argument(
        "--password",
        required=True,
        help="New admin password",
    )
    reset_parser.add_argument(
        "--db-path",
        default=_DEFAULT_DB_PATH,
        help=f"Path to SQLite database (default: {_DEFAULT_DB_PATH})",
    )

    args = parser.parse_args()

    if args.command == "reset-admin":
        reset_admin(args.password, args.db_path)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
