"""
Phase 5 Verification Test Suite for LogShed.
Tests on-demand AI preview & execution (Gemini, OpenAI/compatible), secret redaction,
audit logging, same-host constraints, and manual Pushover notifications.
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
from app.core.config import get_secret_key_path
from app.core.migrations import run_migrations
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    encrypt_value,
    get_or_create_master_key,
    hash_password,
    reset_crypto_cache,
)
from app.core.sse import sse_manager
from app.main import create_app
from app.services import ai_engine, notifier


@pytest.fixture(autouse=True)
def reset_globals(tmp_path: Path, monkeypatch):
    """Reset queues, rate limiter, encryption keys, and environment for each test."""
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
    monkeypatch.delenv("LOGSHED_SECRET_KEY", raising=False)

    run_migrations(db_file)
    get_or_create_master_key(key_file)

    yield

    pipeline_mod._log_queue = None
    pipeline_mod._dropped_logs_total = 0
    login_rate_limiter.reset()
    reset_crypto_cache()
    sse_manager.reset()


@pytest.fixture
def populated_db(tmp_path: Path):
    db_file = tmp_path / "logs.db"
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()

    # Populate admin auth
    pwd_hash = hash_password("SuperSecretAdminPassword123!")
    cursor.execute(
        "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) VALUES (1, ?, '2026-08-29T10:00:00Z', '2026-08-29T10:00:00Z')",
        (pwd_hash,),
    )

    # Populate system settings with encrypted secrets
    enc_api_key = encrypt_value("test-gemini-key-12345")
    enc_pushover_user = encrypt_value("test-pushover-user-key")
    enc_pushover_token = encrypt_value("test-pushover-app-token")

    cursor.execute("INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_provider', 'gemini', '2026-08-29T10:00:00Z', 0)")
    cursor.execute("INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_model', 'gemini-2.5-flash', '2026-08-29T10:00:00Z', 0)")
    cursor.execute("INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_api_key', ?, '2026-08-29T10:00:00Z', 1)", (enc_api_key,))
    cursor.execute("INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('pushover_user_key', ?, '2026-08-29T10:00:00Z', 1)", (enc_pushover_user,))
    cursor.execute("INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('pushover_app_token', ?, '2026-08-29T10:00:00Z', 1)", (enc_pushover_token,))

    # Populate test logs: 1 & 2 on host router (192.168.1.1), 3 on proxmox-01 (192.168.1.50)
    logs = [
        ("2026-08-29T12:00:01Z", "2026-08-29T12:00:01Z", "192.168.1.1", "router", "dnsmasq", 1, 3, "failed auth token=Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.do_not_leak_this_token", "raw1"),
        ("2026-08-29T12:00:02Z", "2026-08-29T12:00:02Z", "192.168.1.1", "router", "dnsmasq", 1, 4, "upstream timeout connecting to 1.1.1.1:53 with api_key=supersecret12345", "raw2"),
        ("2026-08-29T12:00:03Z", "2026-08-29T12:00:03Z", "192.168.1.50", "proxmox-01", "pve-ha", 1, 3, "quorum lost on node 2", "raw3"),
    ]
    cursor.executemany(
        """
        INSERT INTO logs (timestamp, received_at, source_ip, source_alias, app_name, facility, severity, message, raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        logs,
    )
    conn.commit()
    conn.close()
    return str(db_file)


@pytest.fixture
def auth_cookie() -> dict[str, str]:
    token = create_session_token(user_id=1)
    return {SESSION_COOKIE_NAME: token}


@pytest_asyncio.fixture
async def auth_client(auth_cookie):
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=auth_cookie) as ac:
        yield ac


class TestAiEngineDirect:
    """Direct unit tests for prompt building, response parsing, and provider dispatch functions."""

    def test_build_analysis_prompt_structure(self):
        prompt = ai_engine.build_analysis_prompt(
            source_alias="router",
            app_name="dnsmasq",
            sanitized_logs="[2026-08-29T12:00:01Z] [dnsmasq] test log line",
            log_count=1,
            user_context="Firmware recently updated",
        )
        assert "Host / Source: router" in prompt
        assert "Container / Service: dnsmasq" in prompt
        assert "Firmware recently updated" in prompt
        assert "[dnsmasq] test log line" in prompt

    def test_parse_structured_ai_response(self):
        sample = """
## Summary
A critical database lock contention occurred.

## Root Cause
Multiple transaction queries deadlock on shared index.

## Actionable Remediation
1. Kill long-running lock PID.
2. Optimize transaction isolation level.
"""
        summary, root_cause, remediation = ai_engine.parse_structured_ai_response(sample)
        assert summary == "A critical database lock contention occurred."
        assert root_cause == "Multiple transaction queries deadlock on shared index."
        assert "1. Kill long-running lock PID." in remediation

    def test_parse_structured_ai_response_fallback(self):
        unstructured = "This is a simple single-paragraph diagnosis of an outage."
        summary, root_cause, remediation = ai_engine.parse_structured_ai_response(unstructured)
        assert summary == unstructured
        assert "summary" in root_cause.lower()
        assert "service logs" in remediation.lower()

    @pytest.mark.asyncio
    async def test_dispatch_gemini_request_mocked(self):
        mock_usage = MagicMock()
        mock_usage.prompt_token_count = 250
        mock_usage.candidates_token_count = 70
        mock_usage.thoughts_token_count = 0
        mock_usage.total_token_count = 320

        mock_response = MagicMock()
        mock_response.text = "## Summary\nDNS fail\n\n## Root Cause\nTimeout\n\n## Actionable Remediation\nRestart"
        mock_response.usage_metadata = mock_usage

        mock_generate = AsyncMock(return_value=mock_response)
        with patch("app.services.ai_engine.genai") as mock_genai:
            mock_client_instance = MagicMock()
            mock_client_instance.aio.models.generate_content = mock_generate
            mock_genai.Client.return_value = mock_client_instance

            text, tokens_in, tokens_out, tokens_thoughts, tokens = await ai_engine.dispatch_gemini_request(
                api_key="test-key",
                model="gemini-2.5-flash",
                prompt="test prompt",
            )
            assert "DNS fail" in text
            assert tokens_in == 250
            assert tokens_out == 70
            assert tokens_thoughts == 0
            assert tokens == 320
            assert mock_generate.called
            # Verify API key is passed to the Client constructor
            mock_genai.Client.assert_called_once_with(api_key="test-key")

    @pytest.mark.asyncio
    async def test_dispatch_gemini_error_sanitized(self):
        mock_generate = AsyncMock(
            side_effect=Exception('Invalid API key api_key=secret12345678 in request')
        )
        with patch("app.services.ai_engine.genai") as mock_genai:
            mock_client_instance = MagicMock()
            mock_client_instance.aio.models.generate_content = mock_generate
            mock_genai.Client.return_value = mock_client_instance

            with pytest.raises(RuntimeError) as exc_info:
                await ai_engine.dispatch_gemini_request(
                    api_key="test-key",
                    model="gemini-2.5-flash",
                    prompt="test prompt",
                )
            assert "secret12345678" not in str(exc_info.value)
            assert "[REDACTED]" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_dispatch_openai_request_mocked(self):
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 160
        mock_usage.completion_tokens = 50
        mock_usage.completion_tokens_details = None
        mock_usage.total_tokens = 210

        mock_message = MagicMock()
        mock_message.content = "## Summary\nOllama summary\n\n## Root Cause\nOllama cause\n\n## Actionable Remediation\nOllama fix"

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        mock_create = AsyncMock(return_value=mock_response)
        with patch("app.services.ai_engine.AsyncOpenAI") as mock_openai_cls:
            mock_client_instance = MagicMock()
            mock_client_instance.chat.completions.create = mock_create
            mock_openai_cls.return_value = mock_client_instance

            text, tokens_in, tokens_out, tokens_thoughts, tokens = await ai_engine.dispatch_openai_request(
                api_key="sk-test",
                model="gpt-4o",
                prompt="test prompt",
                base_url="http://localhost:11434/v1",
            )
            assert "Ollama summary" in text
            assert tokens_in == 160
            assert tokens_out == 50
            assert tokens_thoughts == 0
            assert tokens == 210
            assert mock_create.called
            # Verify the custom base_url was passed to the client
            mock_openai_cls.assert_called_once_with(
                api_key="sk-test",
                base_url="http://localhost:11434/v1",
                timeout=60.0,
            )


class TestAiPreviewAndGating:
    @pytest.mark.asyncio
    async def test_preview_returns_redacted_text_and_no_llm_call(self, populated_db, auth_client):
        """Preview scrubs tokens and never triggers an outbound LLM call."""
        with patch("app.api.ai.execute_ai_analysis", new_callable=AsyncMock) as mock_exec:
            res = await auth_client.post(
                "/api/ai/preview",
                json={"log_ids": [1, 2]},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["source_alias"] == "router"
            assert data["app_name"] == "dnsmasq"
            assert data["log_count"] == 2
            assert "[REDACTED]" in data["sanitized_prompt"]
            assert "do_not_leak_this_token" not in data["sanitized_prompt"]
            assert "supersecret12345" not in data["sanitized_prompt"]
            assert data["provider"] == "gemini"
            assert data["model"] == "gemini-2.5-flash"
            assert data["estimated_tokens"] > 0
            # Ensure zero LLM calls were made
            mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_preview_and_analyze_reject_multi_host_ids(self, populated_db, auth_client):
        """Preview and Analyze reject log IDs spanning disparate source_alias / source_ip with 400."""
        # Log 1 is router (192.168.1.1), Log 3 is proxmox-01 (192.168.1.50)
        preview_res = await auth_client.post(
            "/api/ai/preview",
            json={"log_ids": [1, 3]},
        )
        assert preview_res.status_code == 400
        assert "Selected logs must share the same host alias" in preview_res.json()["detail"]

        analyze_res = await auth_client.post(
            "/api/ai/analyze",
            json={"log_ids": [1, 3]},
        )
        assert analyze_res.status_code == 400
        assert "Selected logs must share the same host alias" in analyze_res.json()["detail"]


class TestAiAnalyzeWorkflow:
    @pytest.mark.asyncio
    async def test_analyze_gemini_provider(self, populated_db, auth_client):
        """Analyze dispatches to Gemini endpoint, parses sections, and writes to audit log."""
        mock_raw_response = (
            "## Summary\n"
            "DNS server encountered authentication failure and upstream connection timeouts.\n\n"
            "## Root Cause\n"
            "Invalid Bearer token supplied by client combined with unreachable upstream DNS resolver 1.1.1.1.\n\n"
            "## Actionable Remediation\n"
            "1. Verify client authorization headers.\n"
            "2. Check firewall outbound UDP/TCP port 53 to 1.1.1.1."
        )

        with patch(
            "app.api.ai.execute_ai_analysis",
            new_callable=AsyncMock,
            return_value=(
                "DNS server encountered authentication failure and upstream connection timeouts.",
                "Invalid Bearer token supplied by client combined with unreachable upstream DNS resolver 1.1.1.1.",
                "1. Verify client authorization headers.\n2. Check firewall outbound UDP/TCP port 53 to 1.1.1.1.",
                mock_raw_response,
                "### System Metadata\n- Host: router\n\n### Sanitized Log Stream (Chronological)\n```\nlog line\n```",
                180,
                65,
                0,
                245,
            ),
        ) as mock_exec:
            res = await auth_client.post(
                "/api/ai/analyze",
                json={
                    "log_ids": [1, 2],
                    "user_context": "Investigating network blip after update",
                    "provider": "gemini",
                    "model": "gemini-2.5-flash",
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert "DNS server encountered authentication failure" in data["summary"]
            assert "Invalid Bearer token supplied" in data["root_cause"]
            assert "Verify client authorization headers" in data["remediation"]
            assert data["model_used"] == "gemini-2.5-flash"
            assert data["tokens_in"] == 180
            assert data["tokens_out"] == 65
            assert data["tokens_thoughts"] == 0
            assert data["tokens_used"] == 245
            assert data["audit_id"] is not None

            # Verify mock call parameters
            assert mock_exec.called
            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["provider"] == "gemini"
            assert call_kwargs["model"] == "gemini-2.5-flash"
            assert call_kwargs["api_key"] == "test-gemini-key-12345"
            assert call_kwargs["source_alias"] == "router"
            assert call_kwargs["app_name"] == "dnsmasq"
            assert call_kwargs["log_count"] == 2
            assert call_kwargs["user_context"] == "Investigating network blip after update"
            assert "[REDACTED]" in call_kwargs["sanitized_logs"]
            assert "do_not_leak_this_token" not in call_kwargs["sanitized_logs"]

    @pytest.mark.asyncio
    async def test_analyze_openai_compatible_provider(self, populated_db, auth_client, tmp_path):
        """Analyze dispatches to OpenAI / OpenAI-compatible endpoint with custom base_url."""
        db_file = tmp_path / "logs.db"
        conn = sqlite3.connect(str(db_file))
        enc_ollama_key = encrypt_value("ollama-key")
        conn.execute("UPDATE system_settings SET value = 'openai_compatible' WHERE key = 'ai_provider'")
        conn.execute("UPDATE system_settings SET value = 'llama3.2' WHERE key = 'ai_model'")
        conn.execute("UPDATE system_settings SET value = ? WHERE key = 'ai_api_key'", (enc_ollama_key,))
        conn.execute("INSERT OR REPLACE INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_base_url', 'http://192.168.1.100:11434/v1', '2026-08-29T10:00:00Z', 0)")
        conn.commit()
        conn.close()

        mock_raw = "## Summary\nOllama local model diagnosis.\n\n## Root Cause\nDNS timeout.\n\n## Actionable Remediation\nRestart."
        with patch(
            "app.api.ai.execute_ai_analysis",
            new_callable=AsyncMock,
            return_value=(
                "Ollama local model diagnosis.",
                "DNS timeout.",
                "Restart.",
                mock_raw,
                "### System Metadata\n- Host: router\n\n### Sanitized Log Stream (Chronological)\n```\nlog line\n```",
                140,
                50,
                0,
                190,
            ),
        ) as mock_exec:
            res = await auth_client.post(
                "/api/ai/analyze",
                json={
                    "log_ids": [1, 2],
                    "provider": "openai_compatible",
                    "model": "llama3.2",
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert "Ollama local model diagnosis" in data["summary"]
            assert data["tokens_in"] == 140
            assert data["tokens_out"] == 50
            assert data["tokens_thoughts"] == 0
            assert data["tokens_used"] == 190
            assert data["model_used"] == "llama3.2"

            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["provider"] == "openai_compatible"
            assert call_kwargs["model"] == "llama3.2"
            assert call_kwargs["api_key"] == "ollama-key"
            assert call_kwargs["base_url"] == "http://192.168.1.100:11434/v1"

    @pytest.mark.asyncio
    async def test_audit_log_persisted_and_queryable(self, populated_db, auth_client):
        """Every analyze execution records exactly one row to ai_audit_log and is accessible via /api/ai/audit."""
        with patch(
            "app.api.ai.execute_ai_analysis",
            new_callable=AsyncMock,
            return_value=(
                "Audit test summary",
                "Audit test cause",
                "Audit test fix",
                "Raw audit text",
                "### System Metadata\n- Host: router\n\n### Sanitized Log Stream\n```\nlogs\n```",
                80,
                31,
                0,
                111,
            ),
        ):
            res = await auth_client.post(
                "/api/ai/analyze",
                json={"log_ids": [1, 2], "user_context": "Audit check context"},
            )
            assert res.status_code == 200
            audit_id = res.json()["audit_id"]

        audit_res = await auth_client.get("/api/ai/audit?limit=10")
        assert audit_res.status_code == 200
        audit_data = audit_res.json()
        assert audit_data["total"] >= 1
        
        matching = [item for item in audit_data["items"] if item["id"] == audit_id]
        assert len(matching) == 1
        entry = matching[0]
        assert entry["source_alias"] == "router"
        assert entry["app_name"] == "dnsmasq"
        assert entry["log_count"] == 2
        assert entry["user_context"] == "Audit check context"
        assert entry["tokens_used"] == 111

    @pytest.mark.asyncio
    async def test_delete_ai_audit_item_and_clear_all(self, populated_db, auth_client):
        """DELETE /api/ai/audit/{id} deletes a single item, and DELETE /api/ai/audit clears all."""
        # Create an audit entry
        with patch(
            "app.api.ai.execute_ai_analysis",
            new_callable=AsyncMock,
            return_value=("Summary", "Cause", "Fix", "Raw", "Prompt", 50, 20, 0, 70),
        ):
            res = await auth_client.post(
                "/api/ai/analyze",
                json={"log_ids": [1, 2]},
            )
            assert res.status_code == 200
            audit_id = res.json()["audit_id"]

        # Delete single item
        del_res = await auth_client.delete(f"/api/ai/audit/{audit_id}")
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "ok"
        assert del_res.json()["deleted_id"] == audit_id

        # Re-deleting returns 404
        del_res_404 = await auth_client.delete(f"/api/ai/audit/{audit_id}")
        assert del_res_404.status_code == 404

        # Create another item and test clear all
        with patch(
            "app.api.ai.execute_ai_analysis",
            new_callable=AsyncMock,
            return_value=("Summary 2", "Cause 2", "Fix 2", "Raw 2", "Prompt 2", 50, 20, 0, 70),
        ):
            await auth_client.post("/api/ai/analyze", json={"log_ids": [1, 2]})

        clear_res = await auth_client.delete("/api/ai/audit")
        assert clear_res.status_code == 200
        assert clear_res.json()["status"] == "ok"

        # Verify audit is empty
        list_res = await auth_client.get("/api/ai/audit")
        assert list_res.status_code == 200
        assert list_res.json()["total"] == 0
    @pytest.mark.asyncio
    async def test_test_notifications_endpoint(self, populated_db, auth_client):
        """POST /api/notifications/test sends a verification message to Pushover API."""
        with patch("app.api.notifications.send_pushover_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": 1, "request": "req-12345"}

            res = await auth_client.post("/api/notifications/test")
            assert res.status_code == 200
            assert res.json()["status"] == "ok"

            assert mock_send.called
            call_kwargs = mock_send.call_args[1]
            assert call_kwargs["user_key"] == "test-pushover-user-key"
            assert call_kwargs["app_token"] == "test-pushover-app-token"
            assert "Test Notification" in call_kwargs["title"]
            assert call_kwargs["priority"] == 0

    @pytest.mark.asyncio
    async def test_send_pushover_preserves_title_message_priority(self, populated_db, auth_client):
        """POST /api/notifications/pushover forwards user-supplied title, message, priority without server classification."""
        custom_title = "[LogShed Analysis] router: dnsmasq"
        custom_message = "Summary: DNS error detected.\n\nRemediation:\nRestart container."
        custom_priority = 1

        with patch("app.api.notifications.send_pushover_message", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"status": 1, "request": "req-999"}

            res = await auth_client.post(
                "/api/notifications/pushover",
                json={
                    "title": custom_title,
                    "message": custom_message,
                    "priority": custom_priority,
                },
            )
            assert res.status_code == 200
            assert res.json()["status"] == "sent"

            assert mock_send.called
            call_kwargs = mock_send.call_args[1]
            assert call_kwargs["user_key"] == "test-pushover-user-key"
            assert call_kwargs["app_token"] == "test-pushover-app-token"
            assert call_kwargs["title"] == custom_title
            assert call_kwargs["message"] == custom_message
            assert call_kwargs["priority"] == 1

    @pytest.mark.asyncio
    async def test_pushover_missing_credentials_returns_400(self, tmp_path, auth_client):
        """Pushover requests fail cleanly with 400 when keys are not configured."""
        db_file = tmp_path / "logs.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("UPDATE system_settings SET value = '' WHERE key IN ('pushover_user_key', 'pushover_app_token')")
        conn.commit()
        conn.close()

        res = await auth_client.post(
            "/api/notifications/pushover",
            json={"title": "Test", "message": "Test message"},
        )
        assert res.status_code == 400
        assert "must be configured" in res.json()["detail"]
