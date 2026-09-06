"""
Tests for logs API: FTS5 queries, multi-source/app filters, facets, surrounding context, and SSE streaming.
"""

import asyncio
from pathlib import Path
import sqlite3
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.migrations import get_connection, run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_or_create_master_key,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app


@pytest.fixture(autouse=True)
def reset_logs_env(tmp_path: Path, monkeypatch):
    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()

    db_file = tmp_path / "logs.db"
    key_file = tmp_path / ".secret_key"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("SECRET_KEY_PATH", str(key_file))

    run_migrations(db_file)
    get_or_create_master_key(key_file)

    yield

    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()


@pytest_asyncio.fixture
async def client():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def auth_cookie() -> dict[str, str]:
    token = create_session_token(user_id=1)
    return {SESSION_COOKIE_NAME: token}


def _seed_logs(db_path: Path, entries: list[dict]):
    query = """
        INSERT INTO logs (
            timestamp, received_at, source_ip, source_alias,
            app_name, facility, severity, message, raw
        ) VALUES (
            :timestamp, :received_at, :source_ip, :source_alias,
            :app_name, :facility, :severity, :message, :raw
        )
    """
    with get_connection(db_path) as conn:
        conn.executemany(query, entries)
        conn.commit()


# ===================================================================
# 1. Log Querying & Native FTS5 Filtering
# ===================================================================

class TestLogQuerying:

    @pytest.mark.asyncio
    async def test_logs_filtering_and_fts(self, client: AsyncClient, auth_cookie: dict, tmp_path: Path):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        test_entries = [
            {
                "timestamp": "2026-08-29T10:00:00Z",
                "received_at": "2026-08-29T10:00:01Z",
                "source_ip": "192.168.1.50",
                "source_alias": "homelab-host",
                "app_name": "nginx",
                "facility": 1,
                "severity": 3,
                "message": "Connection refused upstream failure on backend pool",
                "raw": "<11>1 2026-08-29T10:00:00Z homelab-host nginx - - - Connection refused upstream failure",
            },
            {
                "timestamp": "2026-08-29T11:00:00Z",
                "received_at": "2026-08-29T11:00:01Z",
                "source_ip": "192.168.1.60",
                "source_alias": "pve-node1",
                "app_name": "kernel",
                "facility": 0,
                "severity": 2,
                "message": "Out of Memory: Killed process 412 (mysqld)",
                "raw": "<10>1 2026-08-29T11:00:00Z pve-node1 kernel - - - Out of Memory: Killed process",
            },
            {
                "timestamp": "2026-08-29T12:00:00Z",
                "received_at": "2026-08-29T12:00:01Z",
                "source_ip": "192.168.1.50",
                "source_alias": "homelab-host",
                "app_name": "nextcloud",
                "facility": 1,
                "severity": 6,
                "message": "User admin logged in successfully from 192.168.1.100",
                "raw": "<14>1 2026-08-29T12:00:00Z homelab-host nextcloud - - - User admin logged in",
            },
        ]
        _seed_logs(db_file, test_entries)

        # 1. Unfiltered query
        res = await client.get("/api/logs")
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 3
        assert len(data["logs"]) == 3
        assert data["logs"][0]["app_name"] == "nextcloud"

        # 2. FTS query for 'refused'
        res_fts = await client.get("/api/logs", params={"query": "refused"})
        assert res_fts.status_code == 200
        data_fts = res_fts.json()
        assert data_fts["total"] == 1
        assert data_fts["logs"][0]["app_name"] == "nginx"

        # 3. Source filter (matches alias or IP)
        res_src = await client.get("/api/logs", params={"source": "homelab-host"})
        assert res_src.json()["total"] == 2

        res_src_ip = await client.get("/api/logs", params={"source": "192.168.1.60"})
        assert res_src_ip.json()["total"] == 1
        assert res_src_ip.json()["logs"][0]["source_alias"] == "pve-node1"

        # 4. App name filter
        res_app = await client.get("/api/logs", params={"app_name": "kernel"})
        assert res_app.json()["total"] == 1

        # 5. Severity max filter
        res_sev = await client.get("/api/logs", params={"severity_max": 3})
        assert res_sev.json()["total"] == 2
        for log in res_sev.json()["logs"]:
            assert log["severity"] <= 3

        # 6. Time bounds
        res_time = await client.get(
            "/api/logs",
            params={
                "from": "2026-08-29T10:30:00Z",
                "to": "2026-08-29T11:30:00Z",
            },
        )
        assert res_time.json()["total"] == 1
        assert res_time.json()["logs"][0]["app_name"] == "kernel"

        # 7. Multi-source filtering: comma-separated and repeated params
        res_multi_src_comma = await client.get("/api/logs", params={"source": "homelab-host,pve-node1"})
        assert res_multi_src_comma.status_code == 200
        assert res_multi_src_comma.json()["total"] == 3

        res_multi_src_repeat = await client.get("/api/logs?source=homelab-host&source=pve-node1")
        assert res_multi_src_repeat.status_code == 200
        assert res_multi_src_repeat.json()["total"] == 3

        # 8. Multi-app filtering: comma-separated and repeated params
        res_multi_app_comma = await client.get("/api/logs", params={"app_name": "nginx,kernel"})
        assert res_multi_app_comma.status_code == 200
        assert res_multi_app_comma.json()["total"] == 2
        app_names = {l["app_name"] for l in res_multi_app_comma.json()["logs"]}
        assert app_names == {"nginx", "kernel"}

        res_multi_app_repeat = await client.get("/api/logs?app_name=nginx&app_name=nextcloud")
        assert res_multi_app_repeat.status_code == 200
        assert res_multi_app_repeat.json()["total"] == 2
        app_names_repeat = {l["app_name"] for l in res_multi_app_repeat.json()["logs"]}
        assert app_names_repeat == {"nginx", "nextcloud"}

        # 9. Multi-host and multi-app combined
        res_combined = await client.get("/api/logs?source=homelab-host&app_name=nginx,kernel")
        assert res_combined.status_code == 200
        assert res_combined.json()["total"] == 1
        assert res_combined.json()["logs"][0]["app_name"] == "nginx"
        assert res_combined.json()["logs"][0]["source_alias"] == "homelab-host"

    @pytest.mark.asyncio
    async def test_native_fts5_syntax_and_syntax_error_fallback(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        entries = [
            ("2026-08-30T10:00:00Z", "2026-08-30T10:00:01Z", "10.0.0.1", "server1", "nginx", 1, 3, "Connection refused to backend upstream", "<11>nginx: Connection refused to backend upstream"),
            ("2026-08-30T10:01:00Z", "2026-08-30T10:01:01Z", "10.0.0.1", "server1", "nginx", 1, 3, "Connection timeout to redis", "<11>nginx: Connection timeout to redis"),
            ("2026-08-30T10:02:00Z", "2026-08-30T10:02:01Z", "10.0.0.2", "server2", "auth", 1, 2, "Authentication error: password mismatch", "<10>auth: Authentication error: password mismatch"),
        ]
        with get_connection(db_file) as conn:
            conn.executemany(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                entries,
            )
            conn.commit()

        # 1. Column filter syntax: app_name:auth
        res_col = await client.get("/api/logs", params={"query": "app_name:auth"})
        assert res_col.status_code == 200
        data_col = res_col.json()
        assert data_col["total"] == 1
        assert data_col["logs"][0]["app_name"] == "auth"

        # 2. Boolean operators: "Connection NOT timeout"
        res_bool = await client.get("/api/logs", params={"query": "Connection NOT timeout"})
        assert res_bool.status_code == 200
        data_bool = res_bool.json()
        assert data_bool["total"] == 1
        assert "refused" in data_bool["logs"][0]["message"]

        # 3. Wildcard prefix search: "refus*"
        res_prefix = await client.get("/api/logs", params={"query": "refus*"})
        assert res_prefix.status_code == 200
        assert res_prefix.json()["total"] == 1

        # 4. Partial word search as you type (auto prefix matching)
        res_partial = await client.get("/api/logs", params={"query": "passwor"})
        assert res_partial.status_code == 200
        assert res_partial.json()["total"] == 1
        assert "password mismatch" in res_partial.json()["logs"][0]["message"]

        # 5. Malformed syntax (unbalanced quote): fallback should execute without 500 error
        res_bad = await client.get("/api/logs", params={"query": 'Connection "refused'})
        assert res_bad.status_code == 200
        assert res_bad.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_fts_queries_with_dots_and_ip_addresses(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        entries = [
            ("2026-08-30T10:00:00Z", "2026-08-30T10:00:01Z", "192.168.1.1", "gw-router", "nginx", 1, 3, "Incoming request from 192.168.1.1 accepted", "<11>nginx: Incoming request from 192.168.1.1 accepted"),
            ("2026-08-30T10:01:00Z", "2026-08-30T10:01:01Z", "10.0.0.1", "backend-srv", "nginx", 1, 3, "Proxy pass to 10.0.0.1 failed", "<11>nginx: Proxy pass to 10.0.0.1 failed"),
            ("2026-08-30T10:02:00Z", "2026-08-30T10:02:01Z", "10.0.0.2", "auth-srv", "auth", 1, 2, "User login from 10.0.0.1 authenticated", "<10>auth: User login from 10.0.0.1 authenticated"),
        ]
        with get_connection(db_file) as conn:
            conn.executemany(
                """INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                entries,
            )
            conn.commit()

        # 1. Search for IP address with dots (e.g. 192.168.1.1)
        res_ip = await client.get("/api/logs", params={"query": "192.168.1.1"})
        assert res_ip.status_code == 200
        data_ip = res_ip.json()
        assert data_ip["total"] == 1
        assert "192.168.1.1" in data_ip["logs"][0]["message"]

        # 2. Search for column filter and IP address (e.g. app_name:nginx AND 10.0.0.1)
        res_col_and_ip = await client.get("/api/logs", params={"query": "app_name:nginx AND 10.0.0.1"})
        assert res_col_and_ip.status_code == 200
        data_col_and_ip = res_col_and_ip.json()
        assert data_col_and_ip["total"] == 1
        assert data_col_and_ip["logs"][0]["app_name"] == "nginx"
        assert "10.0.0.1" in data_col_and_ip["logs"][0]["message"]


# ===================================================================
# 2. Surrounding Context
# ===================================================================

class TestLogContext:

    @pytest.mark.asyncio
    async def test_log_context_scoped_to_same_source_and_app(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        entries = [
            {"timestamp": f"2026-08-29T10:0{i}:00Z", "received_at": f"2026-08-29T10:0{i}:01Z",
             "source_ip": "10.0.0.1", "source_alias": "srv1", "app_name": "web", "facility": 1, "severity": 6,
             "message": f"web line {i}", "raw": f"raw web {i}"}
            for i in range(1, 6)
        ]
        entries.append({
            "timestamp": "2026-08-29T10:02:30Z", "received_at": "2026-08-29T10:02:30Z",
            "source_ip": "10.0.0.1", "source_alias": "srv1", "app_name": "cron", "facility": 1, "severity": 6,
            "message": "cron job ran", "raw": "raw cron",
        })
        entries.append({
            "timestamp": "2026-08-29T10:03:30Z", "received_at": "2026-08-29T10:03:30Z",
            "source_ip": "10.0.0.2", "source_alias": "srv2", "app_name": "database", "facility": 1, "severity": 3,
            "message": "other host message", "raw": "other raw",
        })
        _seed_logs(db_file, entries)

        target_id = 3

        # Default same_app=False
        res = await client.get(f"/api/logs/{target_id}/context", params={"lines": 10, "same_app": "false"})
        assert res.status_code == 200
        data = res.json()
        assert data["target_id"] == target_id
        context_logs = data["logs"]
        for log in context_logs:
            assert log["source_alias"] == "srv1"
        apps = {log["app_name"] for log in context_logs}
        assert "web" in apps
        assert "cron" in apps
        assert all(log["source_alias"] != "srv2" for log in context_logs)

        # same_app=True
        res_app = await client.get(f"/api/logs/{target_id}/context", params={"lines": 4, "same_app": "true"})
        assert res_app.status_code == 200
        app_logs = res_app.json()["logs"]
        assert len(app_logs) == 5
        for log in app_logs:
            assert log["source_alias"] == "srv1"
            assert log["app_name"] == "web"

        # lines=2 -> 1 before + target + 1 after = 3 logs
        res_small = await client.get(f"/api/logs/{target_id}/context", params={"lines": 2, "same_app": "true"})
        assert res_small.status_code == 200
        assert len(res_small.json()["logs"]) == 3

        # Earliest log boundary
        res_first = await client.get("/api/logs/1/context", params={"lines": 10})
        assert res_first.status_code == 200
        assert res_first.json()["logs"][0]["id"] == 1

        # Latest log boundary
        latest_id = max(context_logs, key=lambda l: (l["timestamp"], l["id"]))["id"]
        res_last = await client.get(f"/api/logs/{latest_id}/context", params={"lines": 10})
        assert res_last.status_code == 200
        assert res_last.json()["logs"][-1]["id"] == latest_id

    @pytest.mark.asyncio
    async def test_log_context_404_on_missing_id(self, client: AsyncClient, auth_cookie: dict):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        res = await client.get("/api/logs/99999/context")
        assert res.status_code == 404


# ===================================================================
# 3. Real-Time SSE Streaming & Facets
# ===================================================================

class TestLogStreamAndFacets:

    @pytest.mark.asyncio
    async def test_log_stream_sse(self, client: AsyncClient, auth_cookie: dict):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        test_entry = {
            "id": 999,
            "timestamp": "2026-08-29T15:00:00Z",
            "received_at": "2026-08-29T15:00:01Z",
            "source_ip": "192.168.1.50",
            "source_alias": "homelab-host",
            "app_name": "nginx",
            "facility": 1,
            "severity": 3,
            "message": "SSE test log message",
            "raw": "raw sse",
        }

        async def _trigger_broadcast():
            for _ in range(50):
                if sse_manager.subscriber_count() > 0:
                    break
                await asyncio.sleep(0.01)
            await sse_manager.broadcast(test_entry)

        broadcast_task = asyncio.create_task(_trigger_broadcast())

        async with client.stream("GET", "/api/logs/stream", params={"max_events": 1}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = []
            async for line in response.aiter_lines():
                if line.strip():
                    lines.append(line.strip())

            assert any("event: log" in l for l in lines)
            assert any("SSE test log message" in l for l in lines)

        await broadcast_task

    @pytest.mark.asyncio
    async def test_log_stream_sse_multi_filter(self, client: AsyncClient, auth_cookie: dict):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])

        entry1 = {
            "id": 1001,
            "timestamp": "2026-08-29T15:01:00Z",
            "received_at": "2026-08-29T15:01:01Z",
            "source_ip": "192.168.1.50",
            "source_alias": "homelab-host",
            "app_name": "nginx",
            "facility": 1,
            "severity": 3,
            "message": "Filtered log 1",
            "raw": "raw 1",
        }
        entry2 = {
            "id": 1002,
            "timestamp": "2026-08-29T15:01:02Z",
            "received_at": "2026-08-29T15:01:03Z",
            "source_ip": "192.168.1.60",
            "source_alias": "pve-node1",
            "app_name": "corosync",
            "facility": 1,
            "severity": 3,
            "message": "Filtered log 2",
            "raw": "raw 2",
        }
        entry3 = {
            "id": 1003,
            "timestamp": "2026-08-29T15:01:04Z",
            "received_at": "2026-08-29T15:01:05Z",
            "source_ip": "192.168.1.70",
            "source_alias": "other-node",
            "app_name": "other-app",
            "facility": 1,
            "severity": 3,
            "message": "Ignored log 3",
            "raw": "raw 3",
        }

        async def _trigger_broadcast():
            for _ in range(50):
                if sse_manager.subscriber_count() > 0:
                    break
                await asyncio.sleep(0.01)
            await sse_manager.broadcast(entry3)
            await sse_manager.broadcast(entry1)
            await sse_manager.broadcast(entry2)

        broadcast_task = asyncio.create_task(_trigger_broadcast())

        async with client.stream("GET", "/api/logs/stream?source=homelab-host,pve-node1&max_events=2") as response:
            assert response.status_code == 200
            lines = []
            async for line in response.aiter_lines():
                if line.strip():
                    lines.append(line.strip())

            assert not any("Ignored log 3" in l for l in lines)
            assert any("Filtered log 1" in l for l in lines)
            assert any("Filtered log 2" in l for l in lines)

        await broadcast_task

    @pytest.mark.asyncio
    async def test_log_facets_returns_full_database_distinct_items_and_mappings(
        self, client: AsyncClient, auth_cookie: dict, tmp_path: Path
    ):
        client.cookies.set(SESSION_COOKIE_NAME, auth_cookie[SESSION_COOKIE_NAME])
        db_file = tmp_path / "logs.db"

        test_entries = [
            {
                "timestamp": "2026-08-29T10:00:00Z",
                "received_at": "2026-08-29T10:00:01Z",
                "source_ip": "192.168.1.50",
                "source_alias": "homelab-host",
                "app_name": "nginx",
                "facility": 1,
                "severity": 3,
                "message": "Nginx upstream error",
                "raw": "<11>1 2026-08-29T10:00:00Z homelab-host nginx - - - error",
            },
            {
                "timestamp": "2026-08-29T11:00:00Z",
                "received_at": "2026-08-29T11:00:01Z",
                "source_ip": "192.168.1.60",
                "source_alias": "pve-node1",
                "app_name": "corosync",
                "facility": 0,
                "severity": 2,
                "message": "Corosync quorum lost",
                "raw": "<10>1 2026-08-29T11:00:00Z pve-node1 corosync - - - quorum lost",
            },
            {
                "timestamp": "2026-08-29T12:00:00Z",
                "received_at": "2026-08-29T12:00:01Z",
                "source_ip": "172.22.2.4",
                "source_alias": "NPM",
                "app_name": "nginx-proxy",
                "facility": 1,
                "severity": 6,
                "message": "GET 200 OK",
                "raw": "raw npm",
            },
        ]
        _seed_logs(db_file, test_entries)

        with sqlite3.connect(str(db_file)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO host_aliases (ip, alias, created_at) VALUES (?, ?, ?)",
                ("172.22.2.4", "NPM", "2026-08-29T10:00:00Z"),
            )

        response = await client.get("/api/logs/facets")
        assert response.status_code == 200
        data = response.json()

        assert "sources" in data
        assert "apps" in data
        assert "host_to_apps" in data
        assert "app_to_hosts" in data

        assert "homelab-host" in data["sources"]
        assert "pve-node1" in data["sources"]
        assert "NPM" in data["sources"]
        assert "172.22.2.4" not in data["sources"]
        assert "nginx" in data["apps"]
        assert "corosync" in data["apps"]
        assert "nginx-proxy" in data["apps"]

        assert "nginx" in data["host_to_apps"]["homelab-host"]
        assert "corosync" in data["host_to_apps"]["pve-node1"]
        assert "nginx-proxy" in data["host_to_apps"]["NPM"]
        assert "172.22.2.4" not in data["host_to_apps"]

        assert "homelab-host" in data["app_to_hosts"]["nginx"]
        assert "pve-node1" in data["app_to_hosts"]["corosync"]
        assert "NPM" in data["app_to_hosts"]["nginx-proxy"]
