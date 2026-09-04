"""
Tests for AI engine, prompt construction, token estimation, Gemini/OpenAI dispatch, and audit logging.
"""

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import pipeline as pipeline_mod
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
from app.services import ai_engine


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

    pwd_hash = hash_password("SuperSecretAdminPassword123!")
    cursor.execute(
        "INSERT INTO admin_auth (id, password_hash, created_at, updated_at) VALUES (1, ?, '2026-08-29T10:00:00Z', '2026-08-29T10:00:00Z')",
        (pwd_hash,),
    )

    enc_api_key = encrypt_value("test-gemini-key-12345")
    cursor.execute("INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_provider', 'gemini', '2026-08-29T10:00:00Z', 0)")
    cursor.execute("INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_model', 'gemini-2.5-flash', '2026-08-29T10:00:00Z', 0)")
    cursor.execute("INSERT INTO system_settings (key, value, updated_at, is_encrypted) VALUES ('ai_api_key', ?, '2026-08-29T10:00:00Z', 1)", (enc_api_key,))

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


# ===================================================================
# 1. AI Engine Direct Functions
# ===================================================================

class TestAiEngineDirect:

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

    def test_build_analysis_prompt_with_host_notes(self):
        prompt = ai_engine.build_analysis_prompt(
            source_alias="pve1",
            app_name="pvedaemon",
            sanitized_logs="[2026-08-29T12:00:01Z] [pvedaemon] test log line",
            log_count=1,
            host_notes="Primary Proxmox VE hypervisor running kernel 6.8 with ZFS pool 'rpool'",
        )
        assert "- Host Notes: Primary Proxmox VE hypervisor running kernel 6.8 with ZFS pool 'rpool'" in prompt
        meta_section = prompt.split("### Sanitized Log Stream")[0]
        assert "### System Metadata" in meta_section
        assert "- Host Notes: Primary Proxmox VE hypervisor running kernel 6.8 with ZFS pool 'rpool'" in meta_section

    def test_build_analysis_prompt_without_host_notes(self):
        prompt = ai_engine.build_analysis_prompt(
            source_alias="pve1",
            app_name="pvedaemon",
            sanitized_logs="[2026-08-29T12:00:01Z] [pvedaemon] test log line",
            log_count=1,
            host_notes=None,
        )
        assert "- Host Notes:" not in prompt

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
            mock_openai_cls.assert_called_once_with(
                api_key="sk-test",
                base_url="http://localhost:11434/v1",
                timeout=60.0,
            )

    @pytest.mark.asyncio
    async def test_execute_ai_analysis_with_prompt_override(self):
        with patch("app.services.ai_engine.dispatch_gemini_request", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = (
                "## Summary\nCustom summary\n\n## Root Cause\nCustom cause\n\n## Actionable Remediation\nCustom fix",
                100,
                50,
                0,
                150,
            )
            summary, root_cause, remediation, raw_response, prompt_sent, tokens_in, tokens_out, tokens_thoughts, tokens_used = await ai_engine.execute_ai_analysis(
                provider="gemini",
                model="gemini-2.5-flash",
                api_key="key",
                base_url=None,
                source_alias="router",
                app_name="dnsmasq",
                sanitized_logs="raw logs",
                log_count=1,
                prompt_override="Operator explicitly edited prompt payload",
            )
            assert prompt_sent == "Operator explicitly edited prompt payload"
            mock_dispatch.assert_called_once_with(
                api_key="key",
                model="gemini-2.5-flash",
                prompt="Operator explicitly edited prompt payload",
                system_prompt=None,
            )

    @pytest.mark.asyncio
    async def test_dispatch_gemini_error_logging_concise_warning(self, monkeypatch):
        mock_generate = AsyncMock(side_effect=Exception("API quota exceeded for project 12345"))
        with patch("app.services.ai_engine.genai") as mock_genai, \
             patch("app.services.ai_engine.logger.warning") as mock_warn:
            mock_client_instance = MagicMock()
            mock_client_instance.aio.models.generate_content = mock_generate
            mock_genai.Client.return_value = mock_client_instance

            # 1. In production, suppress stack trace
            monkeypatch.delenv("DEBUG", raising=False)
            monkeypatch.delenv("ENVIRONMENT", raising=False)
            with pytest.raises(RuntimeError):
                await ai_engine.dispatch_gemini_request(
                    api_key="test-key",
                    model="gemini-2.5-flash",
                    prompt="test prompt",
                )
            mock_warn.assert_called_with("AI analysis request failed: API quota exceeded for project 12345")

            # 2. In development (DEBUG=True), include stack trace
            monkeypatch.setenv("DEBUG", "True")
            with pytest.raises(RuntimeError):
                await ai_engine.dispatch_gemini_request(
                    api_key="test-key",
                    model="gemini-2.5-flash",
                    prompt="test prompt",
                )
            mock_warn.assert_called_with("AI analysis request failed: API quota exceeded for project 12345", exc_info=True)


# ===================================================================
# 2. AI Preview & Gating Endpoints
# ===================================================================

class TestAiPreviewAndGating:

    @pytest.mark.asyncio
    async def test_preview_returns_redacted_text_and_no_llm_call(self, populated_db, auth_client):
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
            mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_preview_and_analyze_reject_multi_host_ids(self, populated_db, auth_client):
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

    @pytest.mark.asyncio
    async def test_preview_injects_host_alias_notes(self, populated_db, auth_client):
        conn = sqlite3.connect(populated_db)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO host_aliases (ip, alias, notes, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("192.168.1.50", "proxmox-01", "Primary Proxmox VE hypervisor running kernel 6.8 with ZFS pool 'rpool'"),
        )
        conn.commit()
        conn.close()

        res = await auth_client.post("/api/ai/preview", json={"log_ids": [3]})
        assert res.status_code == 200
        prompt = res.json()["sanitized_prompt"]
        assert "- Host Notes: Primary Proxmox VE hypervisor running kernel 6.8 with ZFS pool 'rpool'" in prompt

    @pytest.mark.asyncio
    async def test_preview_token_estimation_includes_system_prompt_and_envelopes(self, populated_db, auth_client):
        res = await auth_client.post(
            "/api/ai/preview",
            json={"log_ids": [1, 2]},
        )
        assert res.status_code == 200
        data = res.json()
        full_prompt = data["sanitized_prompt"]
        expected_tokens = max(1, int(len(full_prompt) // 3.5 + len(ai_engine.SYSTEM_PROMPT) // 3.5 + 50))
        assert data["estimated_tokens"] == expected_tokens
        assert data["estimated_tokens"] >= 250

    @pytest.mark.asyncio
    async def test_preview_returns_system_prompt_and_tokens(self, populated_db, auth_client):
        res = await auth_client.post("/api/ai/preview", json={"log_ids": [1, 2]})
        assert res.status_code == 200
        data = res.json()
        assert "system_prompt" in data
        assert len(data["system_prompt"]) > 0
        assert data["estimated_tokens"] > 100


# ===================================================================
# 3. AI Analyze Workflow & Audit Logging
# ===================================================================

class TestAiAnalyzeWorkflow:

    @pytest.mark.asyncio
    async def test_analyze_passes_host_notes_to_engine(self, populated_db, auth_client):
        conn = sqlite3.connect(populated_db)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO host_aliases (ip, alias, notes, created_at) VALUES (?, ?, ?, datetime('now'))",
            ("192.168.1.1", "router", "Edge router running pfSense 2.7.2"),
        )
        conn.commit()
        conn.close()

        with patch(
            "app.api.ai.execute_ai_analysis",
            new_callable=AsyncMock,
            return_value=(
                "Summary.",
                "Root cause.",
                "Remediation.",
                "Raw response",
                "Prompt sent",
                100,
                50,
                0,
                150,
            ),
        ) as mock_exec:
            res = await auth_client.post("/api/ai/analyze", json={"log_ids": [1]})
            assert res.status_code == 200
            assert mock_exec.called
            _, kwargs = mock_exec.call_args
            assert kwargs.get("host_notes") == "Edge router running pfSense 2.7.2"

    @pytest.mark.asyncio
    async def test_analyze_gemini_provider(self, populated_db, auth_client):
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

        del_res = await auth_client.delete(f"/api/ai/audit/{audit_id}")
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "ok"
        assert del_res.json()["deleted_id"] == audit_id

        del_res_404 = await auth_client.delete(f"/api/ai/audit/{audit_id}")
        assert del_res_404.status_code == 404

        with patch(
            "app.api.ai.execute_ai_analysis",
            new_callable=AsyncMock,
            return_value=("Summary 2", "Cause 2", "Fix 2", "Raw 2", "Prompt 2", 50, 20, 0, 70),
        ):
            await auth_client.post("/api/ai/analyze", json={"log_ids": [1, 2]})

        clear_res = await auth_client.delete("/api/ai/audit")
        assert clear_res.status_code == 200
        assert clear_res.json()["status"] == "ok"

        list_res = await auth_client.get("/api/ai/audit")
        assert list_res.status_code == 200
        assert list_res.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_analyze_with_prompt_override_dispatches_directly_and_audits(self, populated_db, auth_client):
        mock_raw_response = (
            "## Summary\nOverridden prompt summary.\n\n"
            "## Root Cause\nOverridden prompt cause.\n\n"
            "## Actionable Remediation\nOverridden prompt remediation."
        )
        custom_prompt = "### System Metadata\n- Host: router\nCustom operator-edited prompt payload: dnsmasq dropped queries on router."

        with patch(
            "app.api.ai.execute_ai_analysis",
            new_callable=AsyncMock,
            return_value=(
                "Overridden prompt summary.",
                "Overridden prompt cause.",
                "Overridden prompt remediation.",
                mock_raw_response,
                custom_prompt,
                120,
                40,
                0,
                160,
            ),
        ) as mock_exec:
            res = await auth_client.post(
                "/api/ai/analyze",
                json={
                    "log_ids": [1, 2],
                    "prompt_override": custom_prompt,
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert data["summary"] == "Overridden prompt summary."
            audit_id = data["audit_id"]

            assert mock_exec.called
            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["prompt_override"] == custom_prompt

            audit_res = await auth_client.get("/api/ai/audit?limit=5")
            assert audit_res.status_code == 200
            matching = [item for item in audit_res.json()["items"] if item["id"] == audit_id]
            assert len(matching) == 1
            assert matching[0]["prompt_sent"] == custom_prompt

    @pytest.mark.asyncio
    async def test_analyze_api_failure_logging_concise(self, populated_db, auth_client, monkeypatch):
        monkeypatch.delenv("DEBUG", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)

        with patch("app.api.ai.execute_ai_analysis", side_effect=Exception("Connection timeout to LLM provider")), \
             patch("app.api.ai.logger.warning") as mock_warn:
            res = await auth_client.post("/api/ai/analyze", json={"log_ids": [1, 2]})
            assert res.status_code == 502
            assert "Connection timeout to LLM provider" in res.json()["detail"]
            mock_warn.assert_called_with("AI analysis request failed: Connection timeout to LLM provider")

    @pytest.mark.asyncio
    async def test_settings_ai_system_prompt_crud(self, populated_db, auth_client):
        res = await auth_client.get("/api/settings")
        assert res.status_code == 200
        data = res.json()
        assert "expert systems engineer" in data["ai_system_prompt"]

        custom_prompt = "You are a custom AI diagnostic specialist for Docker containers."
        post_res = await auth_client.post("/api/settings", json={"ai_system_prompt": custom_prompt})
        assert post_res.status_code == 200

        res2 = await auth_client.get("/api/settings")
        assert res2.status_code == 200
        assert res2.json()["ai_system_prompt"] == custom_prompt

    @pytest.mark.asyncio
    async def test_analyze_with_system_prompt_override_and_audit(self, populated_db, auth_client):
        custom_sys = "Custom system instructions for root cause triage."
        mock_raw = "## Summary\nTest summary\n\n## Root Cause\nTest cause\n\n## Actionable Remediation\nTest fix"
        with patch(
            "app.api.ai.execute_ai_analysis",
            new_callable=AsyncMock,
            return_value=(
                "Test summary",
                "Test cause",
                "Test fix",
                mock_raw,
                "Prompt text",
                100,
                50,
                0,
                150,
            ),
        ) as mock_exec:
            res = await auth_client.post(
                "/api/ai/analyze",
                json={
                    "log_ids": [1, 2],
                    "system_prompt_override": custom_sys,
                },
            )
            assert res.status_code == 200
            data = res.json()
            audit_id = data["audit_id"]

            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["system_prompt"] == custom_sys

            audit_res = await auth_client.get("/api/ai/audit?limit=5")
            assert audit_res.status_code == 200
            matching = [item for item in audit_res.json()["items"] if item["id"] == audit_id]
            assert len(matching) == 1
            assert matching[0]["system_prompt"] == custom_sys
