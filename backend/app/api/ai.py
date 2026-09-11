"""
AI Preview, Analysis, and Audit Log API endpoints for LogShed.
"""

import asyncio
import datetime
import json
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user, run_db_query
from app.core.config import DEFAULT_AI_MODEL, is_debug_or_dev
from app.core.redactor import redact
from app.core.security import decrypt_value
from app.models import (
    AiAuditDeleteResponse,
    AiAuditItem,
    AiAuditListResponse,
    AiDiagnosisRequest,
    AiDiagnosisResponse,
    AiModelInfo,
    AiModelsResponse,
    AiPreviewRequest,
    AiPreviewResponse,
)
from app.services.ai_engine import (
    DEFAULT_SYSTEM_PROMPT,
    build_analysis_prompt,
    execute_ai_analysis,
    fetch_available_models,
    is_text_generation_model,
    truncate_logs_to_budget,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI"])


def _fetch_and_validate_logs(conn, log_ids: list[int]):
    """
    Helper to fetch logs by IDs and sort chronologically.
    """
    if len(log_ids) > 200:
        return None, "Maximum of 200 log IDs allowed per request."
    if not log_ids:
        return None, "No logs found for provided IDs."

    placeholders = ",".join("?" * len(log_ids))
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT id, timestamp, source_ip, source_alias, app_name, severity, message
        FROM logs
        WHERE id IN ({placeholders})
        ORDER BY timestamp ASC, id ASC
        """,
        log_ids,
    )
    rows = cursor.fetchall()
    if not rows:
        return None, "No logs found for provided IDs."

    return rows, None


def _get_aggregated_host_notes(conn, rows: list) -> Optional[str]:
    """
    Aggregate host notes from host_aliases matching any source_ip or source_alias in the batch.
    For a single host with notes, returns the direct notes string.
    For multiple hosts with notes, returns structured host-attributed notes.
    """
    unique_ips = list({r["source_ip"] for r in rows if r["source_ip"]})
    unique_aliases = list({r["source_alias"] for r in rows if r["source_alias"]})

    cursor = conn.cursor()
    cursor.execute("SELECT ip, alias, notes FROM host_aliases")
    all_aliases = cursor.fetchall()

    notes_by_host: dict[str, str] = {}
    for a in all_aliases:
        note = (a["notes"] or "").strip()
        if note and (a["ip"] in unique_ips or a["alias"] in unique_aliases):
            key = a["alias"] or a["ip"]
            notes_by_host[key] = note

    if not notes_by_host:
        return None

    # If all selected rows belong to a single host (alias or IP)
    unique_batch_hosts = {r["source_alias"] or r["source_ip"] for r in rows}
    if len(unique_batch_hosts) <= 1 and len(notes_by_host) == 1:
        return list(notes_by_host.values())[0]

    # Multi-host batch: attribute each note to its host
    return "\n".join(f"- [{h}]: {n}" for h, n in sorted(notes_by_host.items()))


def _is_cache_fresh(updated_at_str: Optional[str], max_age_seconds: int = 86400) -> bool:
    """Helper to check if cached models are within the 24-hour TTL."""
    if not updated_at_str:
        return False
    try:
        dt = datetime.datetime.fromisoformat(updated_at_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - dt).total_seconds() < max_age_seconds
    except Exception:
        return False


@router.get("/models", response_model=AiModelsResponse)
async def list_available_models(
    provider: Optional[str] = Query(None, description="AI Provider ('gemini', 'openai', 'openai_compatible')"),
    refresh: bool = Query(False, description="Force live refresh from provider API"),
    user: dict = Depends(get_current_user),
) -> AiModelsResponse:
    """
    Retrieve active text models for the specified or configured provider.
    Models are cached in SQLite with a 24-hour TTL and refreshed on demand or periodically.
    If no API key is configured, returns has_api_key=False with an empty list.
    """
    def _read_config(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT key, value, is_encrypted, updated_at FROM system_settings")
        rows = cursor.fetchall()
        settings_dict = {}
        updated_dict = {}
        for r in rows:
            k = r["key"]
            val = r["value"]
            is_enc = bool(r["is_encrypted"])
            if is_enc and val:
                try:
                    decrypted = decrypt_value(val)
                    settings_dict[k] = decrypted
                except Exception:
                    settings_dict[k] = ""
            else:
                settings_dict[k] = val or ""
            updated_dict[k] = r["updated_at"]
        return settings_dict, updated_dict

    stored_settings, updated_map = await run_db_query(_read_config)
    clean_provider = (provider or stored_settings.get("ai_provider") or "gemini").lower()
    api_key = stored_settings.get("ai_api_key", "").strip()
    base_url = stored_settings.get("ai_base_url")

    # If the provider requires an API key and none is set, prompt user
    if clean_provider in ("gemini", "openai") and not api_key:
        provider_name = "Google Gemini" if clean_provider == "gemini" else "OpenAI"
        return AiModelsResponse(
            provider=clean_provider,
            models=[],
            has_api_key=False,
            is_live=False,
            error=f"No API key configured for {provider_name}. Please configure your API key in Settings to view available models.",
        )

    cache_key = f"ai_models_cache_{clean_provider}"
    cached_json = stored_settings.get(cache_key)
    cached_updated_at = updated_map.get(cache_key)

    # Return cached models if fresh and refresh not forced
    if not refresh and cached_json and _is_cache_fresh(cached_updated_at):
        try:
            cached_items = json.loads(cached_json)
            clean_items = [
                item for item in cached_items
                if is_text_generation_model(item.get("id", ""), item.get("description", ""))
            ]
            return AiModelsResponse(
                provider=clean_provider,
                models=[AiModelInfo(**item) for item in clean_items],
                has_api_key=True,
                cached_at=str(cached_updated_at),
                is_live=False,
                error=None,
            )
        except Exception as e:
            logger.warning(f"Failed to parse cached models for {clean_provider}: {e}")

    # Query provider live
    try:
        discovered = await fetch_available_models(
            provider=clean_provider,
            api_key=api_key if api_key else None,
            base_url=base_url if base_url else None,
        )

        # Save to SQLite system_settings cache
        def _save_cache(conn):
            cursor = conn.cursor()
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cursor.execute(
                """
                INSERT INTO system_settings (key, value, updated_at, is_encrypted)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (cache_key, json.dumps(discovered), now_iso),
            )
            conn.commit()

        await run_db_query(_save_cache)
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return AiModelsResponse(
            provider=clean_provider,
            models=[AiModelInfo(**m) for m in discovered],
            has_api_key=True,
            cached_at=now_str,
            is_live=True,
            error=None,
        )
    except Exception as exc:
        logger.warning(f"Failed to fetch live models from provider '{clean_provider}': {exc}")
        # If we have a stale cache, return it with a non-fatal warning error
        if cached_json:
            try:
                stale_items = json.loads(cached_json)
                clean_stale = [
                    item for item in stale_items
                    if is_text_generation_model(item.get("id", ""), item.get("description", ""))
                ]
                return AiModelsResponse(
                    provider=clean_provider,
                    models=[AiModelInfo(**item) for item in clean_stale],
                    has_api_key=True,
                    cached_at=str(cached_updated_at),
                    is_live=False,
                    error=f"Could not refresh models from {clean_provider}: {exc}. Displaying cached list.",
                )
            except Exception:
                pass

        return AiModelsResponse(
            provider=clean_provider,
            models=[],
            has_api_key=True,
            is_live=False,
            error=f"Failed to load models from {clean_provider}: {exc}",
        )


@router.post("/preview", response_model=AiPreviewResponse)
async def preview_ai_prompt(
    req: AiPreviewRequest,
    user: dict = Depends(get_current_user),
) -> AiPreviewResponse:
    """
    Generate a redacted preview of selected logs with token estimation.
    Supports single or multi-host log selections.
    Makes NO outbound LLM calls.
    """
    if len(req.log_ids) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot analyze more than 200 logs at once.",
        )

    def _fetch(conn):
        rows, err = _fetch_and_validate_logs(conn, req.log_ids)
        if err:
            return None, err

        cursor = conn.cursor()
        cursor.execute(
            "SELECT key, value FROM system_settings WHERE key IN ('ai_provider', 'ai_model', 'ai_system_prompt', 'ai_fallback_models')"
        )
        settings_map = {r["key"]: r["value"] for r in cursor.fetchall()}
        provider = settings_map.get("ai_provider") or "gemini"
        default_model = DEFAULT_AI_MODEL if provider == "gemini" else ("gpt-4o" if provider == "openai" else "llama3.2")
        model = settings_map.get("ai_model") or default_model
        system_prompt = settings_map.get("ai_system_prompt") or DEFAULT_SYSTEM_PROMPT
        fallback_models_str = settings_map.get("ai_fallback_models") or ""
        fallback_models = [m.strip() for m in fallback_models_str.split(",") if m.strip()]

        host_notes = _get_aggregated_host_notes(conn, rows)

        return (rows, provider, model, system_prompt, fallback_models, host_notes), None

    result, err = await run_db_query(_fetch)
    if err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err,
        )

    rows, provider, model, system_prompt, fallback_models, host_notes = result
    unique_aliases = sorted(list({r["source_alias"] for r in rows if r["source_alias"]}))
    source_alias = ", ".join(unique_aliases) if unique_aliases else (rows[0]["source_ip"] or "unknown")

    unique_apps = sorted(list({r["app_name"] for r in rows if r["app_name"]}))
    app_name = ", ".join(unique_apps) if unique_apps else "unknown"

    raw_lines = [
        f"[{r['timestamp']}] [{r['source_alias'] or r['source_ip'] or 'unknown'}] [{r['app_name']}] {r['message']}"
        for r in rows
    ]
    redacted_lines = redact(raw_lines)
    redacted_logs_text = "\n".join(redacted_lines) if isinstance(redacted_lines, list) else str(redacted_lines)
    redacted_logs_text = truncate_logs_to_budget(redacted_logs_text)

    redacted_host_notes = str(redact(host_notes)) if host_notes else None
    redacted_user_context = str(redact(req.user_context)) if req.user_context else None

    if req.prompt_override and req.prompt_override.strip():
        full_prompt = str(redact(req.prompt_override.strip()))
    else:
        full_prompt = build_analysis_prompt(
            source_alias=source_alias,
            app_name=app_name,
            redacted_logs=redacted_logs_text,
            log_count=len(rows),
            user_context=redacted_user_context,
            host_notes=redacted_host_notes,
        )

    # Estimate token count (~3.5 characters per token including system prompt and framing overhead)
    estimated_tokens = max(1, int(len(full_prompt) // 3.5 + len(system_prompt) // 3.5 + 50))

    return AiPreviewResponse(
        redacted_prompt=full_prompt,
        estimated_tokens=estimated_tokens,
        provider=provider,
        model=model,
        fallback_models=fallback_models,
        log_count=len(rows),
        source_alias=source_alias,
        app_name=app_name,
        system_prompt=system_prompt,
    )


async def _prepare_diagnosis_context(req: AiDiagnosisRequest) -> dict:
    if len(req.log_ids) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot analyze more than 200 logs at once.",
        )

    def _fetch_data_and_settings(conn):
        rows, err = _fetch_and_validate_logs(conn, req.log_ids)
        if err:
            return None, err

        cursor = conn.cursor()
        cursor.execute(
            "SELECT key, value, is_encrypted FROM system_settings WHERE key IN ('ai_provider', 'ai_model', 'ai_api_key', 'ai_base_url', 'ai_system_prompt', 'ai_fallback_models')"
        )
        settings_rows = cursor.fetchall()
        settings = {}
        for r in settings_rows:
            k = r["key"]
            v = r["value"] or ""
            if bool(r["is_encrypted"]) and v:
                try:
                    v = decrypt_value(v)
                except Exception:
                    v = ""
            settings[k] = v

        host_notes = _get_aggregated_host_notes(conn, rows)
        return (rows, settings, host_notes), None

    result, err = await run_db_query(_fetch_data_and_settings)
    if err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err,
        )

    rows, settings, host_notes = result
    unique_aliases = sorted(list({r["source_alias"] for r in rows if r["source_alias"]}))
    source_alias = ", ".join(unique_aliases) if unique_aliases else (rows[0]["source_ip"] or "unknown")

    unique_apps = sorted(list({r["app_name"] for r in rows if r["app_name"]}))
    app_name = ", ".join(unique_apps) if unique_apps else "unknown"

    raw_lines = [
        f"[{r['timestamp']}] [{r['source_alias'] or r['source_ip'] or 'unknown'}] [{r['app_name']}] {r['message']}"
        for r in rows
    ]
    redacted_lines = redact(raw_lines)
    redacted_logs = "\n".join(redacted_lines) if isinstance(redacted_lines, list) else str(redacted_lines)

    provider = req.provider or settings.get("ai_provider") or "gemini"
    default_model = DEFAULT_AI_MODEL if provider == "gemini" else ("gpt-4o" if provider == "openai" else "llama3.2")
    model = req.model or settings.get("ai_model") or default_model
    api_key = settings.get("ai_api_key", "")
    base_url = settings.get("ai_base_url") or None
    system_prompt = (
        req.system_prompt_override.strip()
        if (req.system_prompt_override and req.system_prompt_override.strip())
        else (settings.get("ai_system_prompt") or DEFAULT_SYSTEM_PROMPT)
    )

    fallback_models_str = settings.get("ai_fallback_models") or ""
    configured_fallbacks = [m.strip() for m in fallback_models_str.split(",") if m.strip()]
    fallback_models = req.fallback_models if req.fallback_models is not None else configured_fallbacks

    redacted_host_notes = str(redact(host_notes)) if host_notes else None
    redacted_user_context = str(redact(req.user_context)) if req.user_context else None
    redacted_prompt_override = str(redact(req.prompt_override)) if req.prompt_override else None

    return {
        "rows": rows,
        "source_alias": source_alias,
        "app_name": app_name,
        "redacted_logs": redacted_logs,
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "system_prompt": system_prompt,
        "fallback_models": fallback_models,
        "redacted_user_context": redacted_user_context,
        "redacted_host_notes": redacted_host_notes,
        "redacted_prompt_override": redacted_prompt_override,
    }


async def _save_diagnosis_audit(
    source_alias: str,
    app_name: str,
    log_count: int,
    user_context: Optional[str],
    actual_model: str,
    prompt_sent: str,
    raw_response: str,
    tokens_in: int,
    tokens_out: int,
    tokens_thoughts: int,
    tokens_used: int,
    system_prompt: Optional[str],
) -> int:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def _save_audit(conn):
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO ai_audit_log
            (timestamp, source_alias, app_name, log_count, user_context, model, prompt_sent, response_text, tokens_in, tokens_out, tokens_thoughts, tokens_used, system_prompt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                source_alias,
                app_name,
                log_count,
                user_context or "",
                actual_model,
                prompt_sent,
                raw_response,
                tokens_in,
                tokens_out,
                tokens_thoughts,
                tokens_used,
                system_prompt,
            ),
        )
        audit_id = cursor.lastrowid
        conn.commit()
        return audit_id

    return await run_db_query(_save_audit)


@router.post("/diagnose", response_model=AiDiagnosisResponse)
async def diagnose_logs(
    req: AiDiagnosisRequest,
    user: dict = Depends(get_current_user),
) -> AiDiagnosisResponse:
    """
    Executes full on-demand AI root-cause diagnosis on selected logs.
    Persists diagnosis in ai_audit_log and returns structured output.
    """
    try:
        ctx = await _prepare_diagnosis_context(req)

        ai_res = await execute_ai_analysis(
            provider=ctx["provider"],
            model=ctx["model"],
            api_key=ctx["api_key"],
            base_url=ctx["base_url"],
            source_alias=ctx["source_alias"],
            app_name=ctx["app_name"],
            redacted_logs=ctx["redacted_logs"],
            log_count=len(ctx["rows"]),
            user_context=ctx["redacted_user_context"],
            host_notes=ctx["redacted_host_notes"],
            prompt_override=ctx["redacted_prompt_override"],
            system_prompt=ctx["system_prompt"],
            fallback_models=ctx["fallback_models"],
        )

        if len(ai_res) == 11:
            summary, root_cause, remediation, raw_response, prompt_sent, tokens_in, tokens_out, tokens_thoughts, tokens_used, actual_model, fallback_attempts = ai_res
        else:
            summary, root_cause, remediation, raw_response, prompt_sent, tokens_in, tokens_out, tokens_thoughts, tokens_used = ai_res[:9]
            actual_model = ctx["model"]
            fallback_attempts = []

        audit_id = await _save_diagnosis_audit(
            source_alias=ctx["source_alias"],
            app_name=ctx["app_name"],
            log_count=len(ctx["rows"]),
            user_context=ctx["redacted_user_context"],
            actual_model=actual_model,
            prompt_sent=prompt_sent,
            raw_response=raw_response,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_thoughts=tokens_thoughts,
            tokens_used=tokens_used,
            system_prompt=ctx["system_prompt"],
        )

        return AiDiagnosisResponse(
            summary=summary,
            root_cause=root_cause,
            remediation=remediation,
            model_used=actual_model,
            fallback_used=(actual_model != ctx["model"]),
            fallback_attempts=fallback_attempts,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_thoughts=tokens_thoughts,
            tokens_used=tokens_used,
            audit_id=audit_id,
        )
    except HTTPException:
        raise
    except ValueError as ve:
        clean_err = str(redact(str(ve)[:500]))
        if is_debug_or_dev():
            logger.warning(f"AI analysis request failed: {clean_err}", exc_info=True)
        else:
            logger.warning(f"AI analysis request failed: {clean_err}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except (asyncio.TimeoutError, TimeoutError) as te:
        clean_err = str(te) if str(te) else "Request timed out after deadline"
        if is_debug_or_dev():
            logger.warning(f"AI analysis request timed out: {clean_err}", exc_info=True)
        else:
            logger.warning(f"AI analysis request timed out: {clean_err}")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"AI analysis request timed out: {clean_err}",
        )
    except Exception as exc:
        clean_err = str(redact(str(exc)[:500]))
        if is_debug_or_dev():
            logger.warning(f"AI analysis request failed: {clean_err}", exc_info=True)
        else:
            logger.warning(f"AI analysis request failed: {clean_err}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI analysis failed: {clean_err}",
        )


@router.post("/diagnose/stream")
async def diagnose_logs_stream(
    req: AiDiagnosisRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Executes full on-demand AI root-cause diagnosis, streaming SSE progress events
    (calling, failover, complete, error) to provide immediate live feedback to operators.
    """
    ctx = await _prepare_diagnosis_context(req)

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        async def on_progress(evt: dict):
            await queue.put(evt)

        # Emit initial event
        await queue.put({
            "stage": "init",
            "model": ctx["model"],
            "fallback_models": ctx["fallback_models"],
            "message": f"Initiating diagnosis with {ctx['model']}...",
        })

        async def worker():
            try:
                ai_res = await execute_ai_analysis(
                    provider=ctx["provider"],
                    model=ctx["model"],
                    api_key=ctx["api_key"],
                    base_url=ctx["base_url"],
                    source_alias=ctx["source_alias"],
                    app_name=ctx["app_name"],
                    redacted_logs=ctx["redacted_logs"],
                    log_count=len(ctx["rows"]),
                    user_context=ctx["redacted_user_context"],
                    host_notes=ctx["redacted_host_notes"],
                    prompt_override=ctx["redacted_prompt_override"],
                    system_prompt=ctx["system_prompt"],
                    fallback_models=ctx["fallback_models"],
                    on_progress=on_progress,
                )

                if len(ai_res) == 11:
                    summary, root_cause, remediation, raw_response, prompt_sent, tokens_in, tokens_out, tokens_thoughts, tokens_used, actual_model, fallback_attempts = ai_res
                else:
                    summary, root_cause, remediation, raw_response, prompt_sent, tokens_in, tokens_out, tokens_thoughts, tokens_used = ai_res[:9]
                    actual_model = ctx["model"]
                    fallback_attempts = []

                audit_id = await _save_diagnosis_audit(
                    source_alias=ctx["source_alias"],
                    app_name=ctx["app_name"],
                    log_count=len(ctx["rows"]),
                    user_context=ctx["redacted_user_context"],
                    actual_model=actual_model,
                    prompt_sent=prompt_sent,
                    raw_response=raw_response,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    tokens_thoughts=tokens_thoughts,
                    tokens_used=tokens_used,
                    system_prompt=ctx["system_prompt"],
                )

                resp = AiDiagnosisResponse(
                    summary=summary,
                    root_cause=root_cause,
                    remediation=remediation,
                    model_used=actual_model,
                    fallback_used=(actual_model != ctx["model"]),
                    fallback_attempts=fallback_attempts,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    tokens_thoughts=tokens_thoughts,
                    tokens_used=tokens_used,
                    audit_id=audit_id,
                )
                await queue.put({
                    "stage": "complete",
                    "result": resp.model_dump(),
                })
            except Exception as exc:
                if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or not str(exc).strip():
                    clean_err = "Request timed out"
                else:
                    clean_err = str(redact(str(exc)[:500]))
                await queue.put({
                    "stage": "error",
                    "message": clean_err,
                })
            finally:
                await queue.put(None)

        task = asyncio.create_task(worker())

        try:
            while True:
                if await request.is_disconnected():
                    task.cancel()
                    break
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            task.cancel()
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/audit", response_model=AiAuditListResponse)
async def list_ai_audit(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
) -> AiAuditListResponse:
    """
    Retrieve historical AI analyses from ai_audit_log.
    """
    def _read_audit(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ai_audit_log")
        total = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT id, timestamp, source_alias, app_name, log_count, user_context, model, prompt_sent, response_text,
                   COALESCE(tokens_in, 0) AS tokens_in,
                   COALESCE(tokens_out, 0) AS tokens_out,
                   COALESCE(tokens_thoughts, MAX(0, tokens_used - (COALESCE(tokens_in, 0) + COALESCE(tokens_out, 0)))) AS tokens_thoughts,
                   tokens_used,
                   system_prompt
            FROM ai_audit_log
            ORDER BY timestamp DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = cursor.fetchall()
        items = []
        for r in rows:
            p_sent = r["prompt_sent"]
            if p_sent and not p_sent.startswith("### System Metadata") and not p_sent.startswith("### Redacted Log Stream"):
                # Reconstitute full prompt structure for older records that stored only raw logs
                p_sent = build_analysis_prompt(
                    source_alias=r["source_alias"],
                    app_name=r["app_name"],
                    redacted_logs=p_sent,
                    log_count=r["log_count"],
                    user_context=r["user_context"],
                )

            items.append(
                AiAuditItem(
                    id=r["id"],
                    timestamp=str(r["timestamp"]),
                    source_alias=r["source_alias"],
                    app_name=r["app_name"],
                    log_count=r["log_count"],
                    user_context=r["user_context"],
                    model=r["model"],
                    prompt_sent=p_sent,
                    response_text=r["response_text"],
                    tokens_in=r["tokens_in"],
                    tokens_out=r["tokens_out"],
                    tokens_thoughts=r["tokens_thoughts"],
                    tokens_used=r["tokens_used"],
                    system_prompt=r["system_prompt"] or DEFAULT_SYSTEM_PROMPT,
                )
            )
        return items, total

    items, total = await run_db_query(_read_audit)
    return AiAuditListResponse(items=items, total=total)


@router.delete("/audit/{audit_id}", response_model=AiAuditDeleteResponse)
async def delete_ai_audit_item(
    audit_id: int,
    user: dict = Depends(get_current_user),
) -> AiAuditDeleteResponse:
    """
    Delete a single AI audit log entry by ID.
    """
    def _delete(conn):
        cursor = conn.cursor()
        cursor.execute("DELETE FROM ai_audit_log WHERE id = ?", (audit_id,))
        affected = cursor.rowcount
        conn.commit()
        return affected > 0

    found = await run_db_query(_delete)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI audit entry not found")
    return AiAuditDeleteResponse(status="ok", deleted_id=audit_id, deleted_count=1)


@router.delete("/audit", response_model=AiAuditDeleteResponse)
async def clear_ai_audit_log(
    user: dict = Depends(get_current_user),
) -> AiAuditDeleteResponse:
    """
    Clear all AI audit log entries.
    """
    def _clear(conn):
        cursor = conn.cursor()
        cursor.execute("DELETE FROM ai_audit_log")
        affected = cursor.rowcount
        conn.commit()
        return affected

    count = await run_db_query(_clear)
    return AiAuditDeleteResponse(status="ok", deleted_count=count)
