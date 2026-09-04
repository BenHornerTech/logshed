"""
Tests for database schema, WAL pragmas, FTS5 sync triggers, and migration runner.
"""

from pathlib import Path
import sqlite3
import pytest

from app.core.migrations import (
    MIGRATIONS,
    get_connection,
    get_user_version,
    set_user_version,
    run_migrations,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a fresh migrated database and return its path."""
    p = tmp_path / "logs.db"
    run_migrations(p)
    return p


# ===================================================================
# 1. Schema Integrity & Baseline v1.0.0 Checks
# ===================================================================

class TestSchemaIntegrity:

    def test_user_version_set_to_one(self, db_path: Path):
        """After baseline migration, user_version should be exactly 1."""
        conn = get_connection(db_path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == 1

    def test_wal_mode_enabled(self, db_path: Path):
        """WAL journal mode and performance pragmas should be active."""
        conn = get_connection(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()

        assert mode.lower() == "wal"
        # synchronous NORMAL is 1
        assert sync == 1
        assert busy == 5000
        assert fk == 1

    def test_all_tables_exist(self, db_path: Path):
        """All baseline v1.0.0 tables should exist."""
        conn = get_connection(db_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        expected = {
            "logs",
            "host_aliases",
            "storage_metrics",
            "ai_audit_log",
            "admin_auth",
            "system_settings",
        }
        assert expected.issubset(tables)

    def test_fts_table_exists(self, db_path: Path):
        """logs_fts virtual table should exist."""
        conn = get_connection(db_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "logs_fts" in tables

    def test_triggers_exist(self, db_path: Path):
        """FTS sync triggers should exist."""
        conn = get_connection(db_path)
        triggers = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        conn.close()
        assert {"logs_ai", "logs_ad", "logs_au"}.issubset(triggers)

    def test_indexes_exist(self, db_path: Path):
        """B-tree indexes should exist."""
        conn = get_connection(db_path)
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        conn.close()
        expected = {
            "idx_logs_time_sev",
            "idx_logs_app_time",
            "idx_logs_src_time",
            "idx_logs_source_ip",
            "idx_storage_metrics_time",
        }
        assert expected.issubset(indexes)

    def test_ai_audit_log_columns(self, db_path: Path):
        """ai_audit_log should include token metrics and system_prompt columns."""
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(ai_audit_log);")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        expected = {
            "id",
            "timestamp",
            "source_alias",
            "app_name",
            "log_count",
            "user_context",
            "model",
            "prompt_sent",
            "response_text",
            "tokens_in",
            "tokens_out",
            "tokens_thoughts",
            "tokens_used",
            "system_prompt",
        }
        assert expected.issubset(columns)

    def test_host_aliases_columns(self, db_path: Path):
        """host_aliases should include notes column."""
        conn = get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(host_aliases);")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert {"ip", "alias", "notes", "created_at"}.issubset(columns)

    def test_admin_auth_single_row_constraint(self, db_path: Path):
        """admin_auth should enforce id=1 check constraint."""
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) "
            "VALUES (1, 'hash', '2024-01-01', '2024-01-01')"
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) "
                "VALUES (2, 'hash2', '2024-01-01', '2024-01-01')"
            )
        conn.close()


# ===================================================================
# 2. Migration Runner
# ===================================================================

class TestMigrationRunner:

    def test_idempotent_migration(self, tmp_path: Path):
        """Running migrations repeatedly should be idempotent."""
        p = tmp_path / "idempotent.db"
        run_migrations(p)
        run_migrations(p)
        conn = get_connection(p)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == 1

    def test_sequential_future_migration_execution(self, tmp_path: Path, monkeypatch):
        """Sequential runner executes newly added migrations in ascending order."""
        p = tmp_path / "future_mig.db"
        run_migrations(p)

        # Confirm v1
        conn = get_connection(p)
        assert get_user_version(conn) == 1
        conn.close()

        # Simulate adding a future migration v2
        migration_v2_ran = False

        def mock_migrate_v2(conn: sqlite3.Connection) -> None:
            nonlocal migration_v2_ran
            migration_v2_ran = True
            conn.execute("CREATE TABLE future_test (id INTEGER PRIMARY KEY);")

        extended_migrations = list(MIGRATIONS) + [(2, mock_migrate_v2)]
        monkeypatch.setattr("app.core.migrations.MIGRATIONS", extended_migrations)

        run_migrations(p)
        assert migration_v2_ran is True

        conn = get_connection(p)
        assert get_user_version(conn) == 2
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "future_test" in tables
        conn.close()


# ===================================================================
# 3. FTS5 External Content Sync Triggers
# ===================================================================

class TestFTS5Sync:

    def _insert_log(self, conn: sqlite3.Connection, app: str, alias: str, msg: str) -> int:
        cur = conn.execute(
            "INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, message, raw) "
            "VALUES ('2024-01-01T00:00:00', '2024-01-01T00:00:00', '10.0.0.1', ?, ?, ?, ?)",
            (alias, app, msg, f"{app}: {msg}"),
        )
        conn.commit()
        return cur.lastrowid

    def test_fts5_insert_sync(self, db_path: Path):
        """Inserting into logs should immediately make the row searchable via logs_fts."""
        conn = get_connection(db_path)
        row_id = self._insert_log(conn, "nginx", "web-server", "connection refused by upstream")

        rows = conn.execute(
            "SELECT rowid, app_name, source_alias, message FROM logs_fts WHERE logs_fts MATCH 'refused'"
        ).fetchall()
        conn.close()

        assert len(rows) == 1
        assert rows[0][0] == row_id
        assert rows[0][1] == "nginx"
        assert rows[0][2] == "web-server"
        assert "connection refused" in rows[0][3]

    def test_fts5_delete_sync(self, db_path: Path):
        """Deleting from logs should immediately remove the row from logs_fts."""
        conn = get_connection(db_path)
        row_id = self._insert_log(conn, "sshd", "bastion", "failed password for root")

        # Verify it exists in FTS
        rows = conn.execute(
            "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'bastion'"
        ).fetchall()
        assert len(rows) == 1

        # Delete from logs
        conn.execute("DELETE FROM logs WHERE id = ?", (row_id,))
        conn.commit()

        # Should no longer appear in FTS
        rows_after = conn.execute(
            "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'bastion'"
        ).fetchall()
        conn.close()

        assert len(rows_after) == 0

    def test_fts5_update_sync(self, db_path: Path):
        """Updating a log row should update its FTS index entry."""
        conn = get_connection(db_path)
        row_id = self._insert_log(conn, "app", "srv1", "initial message text")

        # Update message
        conn.execute(
            "UPDATE logs SET message = 'updated completely new' WHERE id = ?",
            (row_id,),
        )
        conn.commit()

        # Old text should not match
        old_match = conn.execute(
            "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'initial'"
        ).fetchall()
        assert len(old_match) == 0

        # New text should match
        new_match = conn.execute(
            "SELECT rowid FROM logs_fts WHERE logs_fts MATCH 'completely'"
        ).fetchall()
        conn.close()

        assert len(new_match) == 1
        assert new_match[0][0] == row_id

    def test_fts5_no_orphan_records(self, db_path: Path):
        """Inserting and deleting multiple records should leave zero FTS entries."""
        conn = get_connection(db_path)
        ids = []
        for i in range(5):
            ids.append(self._insert_log(conn, f"app{i}", f"host{i}", f"test payload {i}"))

        # Verify all are indexed
        count = conn.execute(
            "SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH 'payload'"
        ).fetchone()[0]
        assert count == 5

        # Delete all
        for log_id in ids:
            conn.execute("DELETE FROM logs WHERE id = ?", (log_id,))
        conn.commit()

        count_after = conn.execute(
            "SELECT COUNT(*) FROM logs_fts WHERE logs_fts MATCH 'payload'"
        ).fetchone()[0]
        conn.close()

        assert count_after == 0
