"""
Log querying, streaming (SSE), and context API endpoints for LogShed.
"""

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user, run_db_query
from app.core.sse import sse_manager
from app.models import LogContextResponse, LogEntry, LogListResponse

import sqlite3

logger = logging.getLogger(__name__)

import re

router = APIRouter(prefix="/logs", tags=["Logs"])

# Reserved FTS5 syntax keywords
_FTS_KEYWORDS = {"AND", "OR", "NOT", "NEAR"}


def _format_fts_query(query_str: str) -> str:
    """
    Format user query for FTS5 search with prefix matching.
    - If user entered quoted phrases (e.g. "exact phrase"), preserve them.
    - If terms do not end in '*' and are not boolean operators (AND, OR, NOT, NEAR),
      automatically append '*' for search-as-you-type prefix matching.
    - If column filters are used (e.g. app_name:nginx), apply wildcard to the value.
    """
    q = query_str.strip()
    if not q:
        return ""

    # Regex matches:
    # 1. Quoted strings: "[^"]*" or '[^']*'
    # 2. Column filters: [a-zA-Z_]+:(?:"[^"]*"|[^\s()]+)
    # 3. Parentheses: \( or \)
    # 4. Words/terms: [^\s()]+
    pattern = re.compile(r'("[^"]*"|\'[^\']*\'|[a-zA-Z_]+:(?:"[^"]*"|[^\s()]+)|\(|\)|[^\s()]+)')
    tokens = pattern.findall(q)
    if not tokens:
        return q

    formatted_tokens = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue

        # Quoted strings or parentheses
        if token.startswith(('"', "'")) or token in ("(", ")"):
            formatted_tokens.append(token)
            continue

        # Boolean keywords
        if token.upper() in _FTS_KEYWORDS:
            formatted_tokens.append(token.upper())
            continue

        # Column filters: app_name:nginx or app_name:"web server"
        if ":" in token:
            col, val = token.split(":", 1)
            if val.startswith(('"', "'")) or val.endswith("*") or not val:
                formatted_tokens.append(token)
            else:
                clean_val = val.replace('"', '""')
                formatted_tokens.append(f"{col}:{clean_val}*")
            continue

        # Regular word token: append wildcard if not already present
        if token.endswith("*"):
            formatted_tokens.append(token)
        else:
            clean_token = token.replace('"', '""')
            formatted_tokens.append(f"{clean_token}*")

    return " ".join(formatted_tokens)


def _escape_fts_tokens(query_str: str) -> str:
    """
    Fallback token sanitizer that wraps words in quotes with wildcard suffix
    to handle malformed user input without causing FTS5 syntax errors.
    """
    q = query_str.strip()
    if not q:
        return ""
    words = q.split()
    tokens = []
    for w in words:
        clean = w.replace('"', '').replace("'", '').replace('*', '').strip()
        if clean:
            tokens.append(f'"{clean}"*')
    return " ".join(tokens)


def _sanitize_fts_query(query_str: str) -> str:
    """Backwards-compatible alias for _escape_fts_tokens."""
    return _escape_fts_tokens(query_str)


@router.get("", response_model=LogListResponse)
async def list_logs(
    query: Optional[str] = Query(None, description="Full-text search query (FTS5)"),
    source: Optional[str] = Query(None, description="Filter by source_alias or source_ip"),
    app_name: Optional[str] = Query(None, description="Filter by application name"),
    severity_max: Optional[int] = Query(None, ge=0, le=7, description="Max severity (0-7, lower is more severe)"),
    from_: Optional[str] = Query(None, alias="from", description="ISO datetime start filter"),
    to: Optional[str] = Query(None, description="ISO datetime end filter"),
    limit: int = Query(100, ge=1, le=1000, description="Max logs to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    user: dict = Depends(get_current_user),
) -> LogListResponse:
    """
    Query logs with full-text search, source/app filtering, severity range, and time bounds.
    Supports native SQLite FTS5 query syntax (e.g. column filters, AND/OR/NOT, wildcards)
    and automatic prefix matching for search-as-you-type.
    """
    def _query_db(conn):
        where_clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        is_fts = bool(query and query.strip())
        from_table = "logs"

        if is_fts:
            from_table = "logs JOIN logs_fts ON logs.id = logs_fts.rowid"
            fts_term = _format_fts_query(query)
            where_clauses.append("logs_fts MATCH :fts_term")
            params["fts_term"] = fts_term

        if source:
            where_clauses.append("(logs.source_alias = :source OR logs.source_ip = :source)")
            params["source"] = source

        if app_name:
            where_clauses.append("logs.app_name = :app_name")
            params["app_name"] = app_name

        if severity_max is not None:
            where_clauses.append("logs.severity <= :severity_max")
            params["severity_max"] = severity_max

        if from_:
            where_clauses.append("logs.timestamp >= :from_time")
            params["from_time"] = from_

        if to:
            where_clauses.append("logs.timestamp <= :to_time")
            params["to_time"] = to

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Total count query
        count_sql = f"SELECT COUNT(*) FROM {from_table} {where_sql}"
        # Log selection query
        select_sql = f"""
            SELECT logs.id, logs.timestamp, logs.received_at, logs.source_ip, logs.source_alias,
                   logs.app_name, logs.facility, logs.severity, logs.message, logs.raw
            FROM {from_table}
            {where_sql}
            ORDER BY logs.timestamp DESC, logs.id DESC
            LIMIT :limit OFFSET :offset
        """

        cursor = conn.cursor()
        try:
            cursor.execute(count_sql, params)
            total = cursor.fetchone()[0]
            cursor.execute(select_sql, params)
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            if is_fts:
                logger.info(f"FTS5 query '{params.get('fts_term')}' failed ({e}), falling back to tokenized search.")
                params["fts_term"] = _escape_fts_tokens(query)
                try:
                    cursor.execute(count_sql, params)
                    total = cursor.fetchone()[0]
                    cursor.execute(select_sql, params)
                    rows = cursor.fetchall()
                except sqlite3.OperationalError:
                    total = 0
                    rows = []
            else:
                raise

        logs = [
            LogEntry(
                id=r["id"],
                timestamp=str(r["timestamp"]),
                received_at=str(r["received_at"]),
                source_ip=r["source_ip"],
                source_alias=r["source_alias"],
                app_name=r["app_name"],
                facility=r["facility"],
                severity=r["severity"],
                message=r["message"],
                raw=r["raw"],
            )
            for r in rows
        ]

        return logs, total

    logs, total = await run_db_query(_query_db)
    return LogListResponse(logs=logs, total=total, limit=limit, offset=offset)


@router.get("/stream")
async def stream_logs(
    request: Request,
    severity_max: Optional[int] = Query(None, ge=0, le=7),
    source: Optional[str] = Query(None),
    app_name: Optional[str] = Query(None),
    max_events: Optional[int] = Query(None, description="Max events to stream before closing (useful for tests/bounded streams)"),
    user: dict = Depends(get_current_user),
):
    """
    Server-Sent Events (SSE) endpoint to stream real-time incoming logs to the browser.
    """
    queue = await sse_manager.subscribe()

    async def event_generator() -> AsyncGenerator[str, None]:
        sent = 0
        try:
            while True:
                if max_events is not None and sent >= max_events:
                    break
                if await request.is_disconnected():
                    break

                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=0.5)

                    # Apply optional stream filters
                    if severity_max is not None and entry.get("severity", 6) > severity_max:
                        continue
                    if source and entry.get("source_alias") != source and entry.get("source_ip") != source:
                        continue
                    if app_name and entry.get("app_name") != app_name:
                        continue

                    data = json.dumps(entry)
                    sent += 1
                    yield f"event: log\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    break
        finally:
            await sse_manager.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{id}/context", response_model=LogContextResponse)
async def get_log_context(
    id: int,
    lines: int = Query(10, ge=1, le=100, description="Surrounding lines before and after"),
    user: dict = Depends(get_current_user),
) -> LogContextResponse:
    """
    Fetch surrounding context lines strictly scoped to the same source_alias and app_name.
    """
    def _fetch_context(conn):
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM logs WHERE id = ?", (id,))
        target_row = cursor.fetchone()
        if not target_row:
            return None, []

        source_alias = target_row["source_alias"]
        app_name = target_row["app_name"]
        t_time = target_row["timestamp"]
        t_id = target_row["id"]

        # Fetch lines before (chronologically earlier)
        cursor.execute(
            """
            SELECT * FROM logs 
            WHERE source_alias = ? AND app_name = ? AND (timestamp < ? OR (timestamp = ? AND id < ?))
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (source_alias, app_name, t_time, t_time, t_id, lines),
        )
        before_rows = cursor.fetchall()
        before_rows.reverse()  # Chronological order

        # Fetch lines after (chronologically later)
        cursor.execute(
            """
            SELECT * FROM logs 
            WHERE source_alias = ? AND app_name = ? AND (timestamp > ? OR (timestamp = ? AND id > ?))
            ORDER BY timestamp ASC, id ASC
            LIMIT ?
            """,
            (source_alias, app_name, t_time, t_time, t_id, lines),
        )
        after_rows = cursor.fetchall()

        all_rows = before_rows + [target_row] + after_rows
        logs = [
            LogEntry(
                id=r["id"],
                timestamp=str(r["timestamp"]),
                received_at=str(r["received_at"]),
                source_ip=r["source_ip"],
                source_alias=r["source_alias"],
                app_name=r["app_name"],
                facility=r["facility"],
                severity=r["severity"],
                message=r["message"],
                raw=r["raw"],
            )
            for r in all_rows
        ]
        return target_row, logs

    target_row, logs = await run_db_query(_fetch_context)
    if not target_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Log entry {id} not found.",
        )

    return LogContextResponse(target_id=id, logs=logs)
