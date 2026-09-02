"""
AI Preview, Analysis, and Audit Log API endpoints for Homelab Log Hub.
"""

import datetime
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user, run_db_query
from app.core.sanitizer import sanitize
from app.core.security import decrypt_value
from app.models import (
    AiAnalyzeRequest,
    AiAnalyzeResponse,
    AiAuditDeleteResponse,
    AiAuditItem,
    AiAuditListResponse,
    AiPreviewRequest,
    AiPreviewResponse,
)
from app.services.ai_engine import build_analysis_prompt, execute_ai_analysis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI"])


def _fetch_and_validate_logs(conn, log_ids: list[int]):
    """
    Helper to fetch logs by IDs, sort chronologically, and validate single-host constraint.
    """
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

    # Verify single-host consistency across source_alias and source_ip
    source_aliases = {r["source_alias"] for r in rows}
    source_ips = {r["source_ip"] for r in rows}
    if len(source_aliases) > 1 or len(source_ips) > 1:
        return None, "Selected logs must share the same host alias and source IP."

    return rows, None


@router.post("/preview", response_model=AiPreviewResponse)
async def preview_ai_prompt(
    req: AiPreviewRequest,
    user: dict = Depends(get_current_user),
) -> AiPreviewResponse:
    """
    Generate a sanitized preview of selected logs with token estimation.
    Enforces that all selected logs belong to the exact same host alias and IP.
    Makes NO outbound LLM calls.
    """
    def _fetch(conn):
        rows, err = _fetch_and_validate_logs(conn, req.log_ids)
        if err:
            return None, err

        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM system_settings WHERE key IN ('ai_provider', 'ai_model')")
        settings_map = {r["key"]: r["value"] for r in cursor.fetchall()}
        provider = settings_map.get("ai_provider") or "gemini"
        model = settings_map.get("ai_model") or "gemini-2.5-flash"

        return (rows, provider, model), None

    result, err = await run_db_query(_fetch)
    if err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err,
        )

    rows, provider, model = result
    source_alias = rows[0]["source_alias"]
    app_name = rows[0]["app_name"]

    raw_lines = [f"[{r['timestamp']}] [{r['app_name']}] {r['message']}" for r in rows]
    sanitized_lines = sanitize(raw_lines)
    sanitized_logs_text = "\n".join(sanitized_lines) if isinstance(sanitized_lines, list) else str(sanitized_lines)

    full_prompt = build_analysis_prompt(
        source_alias=source_alias,
        app_name=app_name,
        sanitized_logs=sanitized_logs_text,
        log_count=len(rows),
    )

    # Estimate token count (~4 characters per token + framing overhead)
    estimated_tokens = max(1, len(full_prompt) // 4 + 50)

    return AiPreviewResponse(
        sanitized_prompt=full_prompt,
        estimated_tokens=estimated_tokens,
        provider=provider,
        model=model,
        log_count=len(rows),
        source_alias=source_alias,
        app_name=app_name,
    )


@router.post("/analyze", response_model=AiAnalyzeResponse)
async def analyze_logs(
    req: AiAnalyzeRequest,
    user: dict = Depends(get_current_user),
) -> AiAnalyzeResponse:
    """
    Executes full on-demand AI root-cause analysis on selected logs.
    Persists diagnosis in ai_audit_log and returns structured output.
    """
    try:
        def _fetch_data_and_settings(conn):
            rows, err = _fetch_and_validate_logs(conn, req.log_ids)
            if err:
                return None, err

            cursor = conn.cursor()
            cursor.execute(
                "SELECT key, value, is_encrypted FROM system_settings WHERE key IN ('ai_provider', 'ai_model', 'ai_api_key', 'ai_base_url')"
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

            return (rows, settings), None

        result, err = await run_db_query(_fetch_data_and_settings)
        if err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err,
            )

        rows, settings = result
        source_alias = rows[0]["source_alias"]
        app_name = rows[0]["app_name"]

        raw_lines = [f"[{r['timestamp']}] [{r['app_name']}] {r['message']}" for r in rows]
        sanitized_lines = sanitize(raw_lines)
        sanitized_logs = "\n".join(sanitized_lines) if isinstance(sanitized_lines, list) else str(sanitized_lines)

        provider = req.provider or settings.get("ai_provider") or "gemini"
        default_model = "gemini-2.5-flash" if provider == "gemini" else "gpt-4o"
        model = req.model or settings.get("ai_model") or default_model
        api_key = settings.get("ai_api_key", "")
        base_url = settings.get("ai_base_url") or None

        summary, root_cause, remediation, raw_response, prompt_sent, tokens_in, tokens_out, tokens_thoughts, tokens_used = await execute_ai_analysis(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            source_alias=source_alias,
            app_name=app_name,
            sanitized_logs=sanitized_logs,
            log_count=len(rows),
            user_context=req.user_context,
        )

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        def _save_audit(conn):
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ai_audit_log
                (timestamp, source_alias, app_name, log_count, user_context, model, prompt_sent, response_text, tokens_in, tokens_out, tokens_thoughts, tokens_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    source_alias,
                    app_name,
                    len(rows),
                    req.user_context or "",
                    model,
                    prompt_sent,
                    raw_response,
                    tokens_in,
                    tokens_out,
                    tokens_thoughts,
                    tokens_used,
                ),
            )
            audit_id = cursor.lastrowid
            conn.commit()
            return audit_id

        audit_id = await run_db_query(_save_audit)

        return AiAnalyzeResponse(
            summary=summary,
            root_cause=root_cause,
            remediation=remediation,
            model_used=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_thoughts=tokens_thoughts,
            tokens_used=tokens_used,
            audit_id=audit_id,
        )
    except HTTPException:
        raise
    except ValueError as ve:
        logger.warning(f"AI analysis validation error: {ve}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as exc:
        logger.error(f"AI analysis execution failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI analysis failed: {exc}",
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
                   tokens_used
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
            if p_sent and not p_sent.startswith("### System Metadata") and not p_sent.startswith("### Sanitized Log Stream"):
                # Reconstitute full prompt structure for older records that stored only raw sanitized logs
                p_sent = build_analysis_prompt(
                    source_alias=r["source_alias"],
                    app_name=r["app_name"],
                    sanitized_logs=p_sent,
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
