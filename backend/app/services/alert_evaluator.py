"""
Alert Engine Service for LogShed.

Provides in-memory sliding-window timestamp deques for threshold rules,
regex and keyword pattern matching, and cooldown flap dampening (suppress_until).
Integrates with QueueConsumer batch processing, redactor, AI diagnosis, and notifier.
"""

import asyncio
from collections import deque
import datetime
import fnmatch
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Optional, Union

from app.core.redactor import redact
from app.services.notifier import get_notifier
from app.services.security_presets import extract_ip_from_message

logger = logging.getLogger(__name__)


def format_sample_log_for_alert(raw_msg: str, max_lines: int = 5, max_chars: int = 500) -> str:
    """
    Format and truncate log message for clean mobile/webhook push notifications.
    Truncates long multi-line or excessively long strings to preserve readability.
    """
    if not raw_msg:
        return ""
    cleaned = raw_msg.strip()
    lines = cleaned.splitlines()
    if len(lines) > max_lines:
        cleaned = "\n".join(lines[:max_lines]) + "\n... [truncated]"
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "... [truncated]"
    return cleaned


def strip_markdown(text: str) -> str:
    """Strip markdown formatting syntax for clean plain-text push notifications."""
    if not text:
        return ""
    t = re.sub(r"```[a-zA-Z]*\n?", "", text)
    t = t.replace("```", "").replace("`", "")
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", t)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.MULTILINE)
    return t.strip()


def match_wildcard(pattern: Optional[str], text: Optional[str]) -> bool:
    """
    Wildcard pattern matching supporting '*' and '?'.
    If no wildcard characters exist in pattern, performs an exact case-insensitive match.
    """
    if not pattern or not pattern.strip():
        return True
    if not text:
        return False

    p = pattern.lower().strip()
    t = text.lower().strip()
    if "*" in p or "?" in p:
        return fnmatch.fnmatchcase(t, p)
    return p == t


class CompiledAlertRule:
    """Compiled alert rule representation with cached regex pattern."""

    def __init__(
        self,
        id: int,
        name: str,
        rule_type: str,
        channel_id: Optional[int],
        filter_app: Optional[str],
        filter_severity: Optional[int],
        match_pattern: Optional[str],
        threshold_count: int,
        window_seconds: int,
        cooldown_seconds: int,
        ai_enrichment: bool,
        is_enabled: bool,
        trigger_count: int = 0,
        last_triggered_at: Optional[str] = None,
        suppress_until: Optional[str] = None,
    ):
        self.id = id
        self.name = name
        self.rule_type = rule_type
        self.channel_id = channel_id
        self.filter_app = filter_app
        self.filter_severity = filter_severity
        self.match_pattern = match_pattern
        self.threshold_count = max(1, threshold_count)
        self.window_seconds = max(1, window_seconds)
        self.cooldown_seconds = max(0, cooldown_seconds)
        self.ai_enrichment = bool(ai_enrichment)
        self.is_enabled = bool(is_enabled)
        self.trigger_count = trigger_count
        self.suppress_until = suppress_until
        self.suppress_until_epoch: Optional[float] = None
        self._recompute_suppress_epoch()

        self.compiled_regex: Optional[re.Pattern] = None
        if self.match_pattern and self.match_pattern.strip() and self.match_pattern.strip() != "*":
            try:
                self.compiled_regex = re.compile(self.match_pattern.strip(), re.IGNORECASE)
            except re.error:
                self.compiled_regex = None

    def _recompute_suppress_epoch(self) -> None:
        """Parse suppress_until ISO string into a UTC epoch timestamp."""
        if not self.suppress_until:
            self.suppress_until_epoch = None
            return
        try:
            clean_suppress = str(self.suppress_until).replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(clean_suppress)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            epoch = dt.timestamp()
            now_epoch = datetime.datetime.now(datetime.timezone.utc).timestamp()
            if epoch <= now_epoch:
                self.suppress_until = None
                self.suppress_until_epoch = None
            else:
                self.suppress_until_epoch = epoch
        except Exception:
            self.suppress_until_epoch = None

    def matches(self, entry: dict[str, Any]) -> bool:
        """Check if an incoming log entry satisfies this rule's match criteria."""
        if not self.is_enabled:
            return False

        # 1. App filter check (supports comma-separated apps from multi-select)
        if self.filter_app and self.filter_app.strip():
            app_name = entry.get("app_name") or ""
            patterns = [p.strip() for p in self.filter_app.split(",") if p.strip()]
            if patterns and not any(match_wildcard(p, app_name) for p in patterns):
                return False

        # 2. Severity filter check (syslog 0-7, lower is more severe)
        if self.filter_severity is not None:
            sev = entry.get("severity")
            actual_sev = 6 if sev is None else sev
            if actual_sev > self.filter_severity:
                return False

        # 3. Message pattern check
        if self.match_pattern and self.match_pattern.strip() and self.match_pattern.strip() != "*":
            message = str(entry.get("message") or "")
            raw = str(entry.get("raw") or "")

            if self.compiled_regex is not None:
                if not (self.compiled_regex.search(message) or (raw and self.compiled_regex.search(raw))):
                    return False
            else:
                pat_lower = self.match_pattern.strip().lower()
                if pat_lower not in message.lower() and (not raw or pat_lower not in raw.lower()):
                    return False

        return True


class AlertEvaluator:
    """
    In-memory evaluation engine for alert rules.
    Maintains sliding-window timestamp deques and evaluates logs in real time.
    """

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path:
            self.db_path = Path(db_path)
        else:
            try:
                from app.core.config import get_db_path
                self.db_path = get_db_path()
            except Exception:
                self.db_path = None
        self._rules: list[CompiledAlertRule] = []
        self._windows: dict[int, deque[tuple[float, dict[str, Any]]]] = {}
        self._pending_tasks: set[asyncio.Task] = set()
        self._lock = threading.Lock()
        if self.db_path:
            self.reload_rules()

    def reload_rules(self, conn: Optional[sqlite3.Connection] = None) -> None:
        """Reload active alert rules from SQLite and update compiled cache."""
        from app.core.config import get_db_path
        effective_db = self.db_path or get_db_path()
        if not effective_db and conn is None:
            return

        def _load(c: sqlite3.Connection):
            cur = c.cursor()
            cur.execute(
                """
                SELECT id, name, rule_type, channel_id, filter_app, filter_severity,
                       match_pattern, threshold_count, window_seconds, cooldown_seconds,
                       ai_enrichment, is_enabled, trigger_count, last_triggered_at, suppress_until
                FROM alert_rules
                WHERE is_enabled = 1
                ORDER BY id ASC
                """
            )
            return cur.fetchall()

        if conn is not None:
            rows = _load(conn)
        elif effective_db:
            from app.api.deps import get_thread_read_connection
            c = get_thread_read_connection(effective_db)
            rows = _load(c)
        else:
            rows = []

        compiled: list[CompiledAlertRule] = []
        active_ids: set[int] = set()

        for r in rows:
            rule = CompiledAlertRule(
                id=r[0],
                name=r[1],
                rule_type=r[2],
                channel_id=r[3],
                filter_app=r[4],
                filter_severity=r[5],
                match_pattern=r[6],
                threshold_count=r[7],
                window_seconds=r[8],
                cooldown_seconds=r[9],
                ai_enrichment=bool(r[10]),
                is_enabled=bool(r[11]),
                trigger_count=r[12] or 0,
                last_triggered_at=r[13],
                suppress_until=r[14],
            )
            compiled.append(rule)
            active_ids.add(rule.id)

        with self._lock:
            self._rules = compiled
            # Clean up windows for rules that are no longer active
            stale_ids = [rid for rid in self._windows if rid not in active_ids]
            for rid in stale_ids:
                del self._windows[rid]

            for rule in compiled:
                if rule.id not in self._windows:
                    self._windows[rule.id] = deque()

    async def evaluate_batch(self, batch: list[dict[str, Any]]) -> None:
        """
        Evaluate an ingested batch of logs against active alert rules.
        Schedules background alert dispatch tasks when thresholds are reached.
        """
        if not batch:
            return

        with self._lock:
            rules = list(self._rules)

        if not rules:
            return

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_epoch = now_utc.timestamp()
        now_iso = now_utc.isoformat()

        fired_events: list[tuple[CompiledAlertRule, list[dict[str, Any]]]] = []

        with self._lock:
            for rule in rules:
                window = self._windows.setdefault(rule.id, deque())

                for entry in batch:
                    try:
                        if not rule.matches(entry):
                            continue

                        # Extract log timestamp ensuring UTC
                        ts_val = entry.get("timestamp")
                        entry_epoch = now_epoch
                        if ts_val:
                            try:
                                clean_ts = str(ts_val).replace("Z", "+00:00")
                                dt = datetime.datetime.fromisoformat(clean_ts)
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                                entry_epoch = dt.timestamp()
                            except Exception:
                                entry_epoch = now_epoch

                        # Evict expired entries outside the sliding window
                        window_cutoff = entry_epoch - rule.window_seconds
                        while window and window[0][0] < window_cutoff:
                            window.popleft()

                        # Append entry preserving chronological order under timestamp skew
                        if not window or entry_epoch >= window[-1][0]:
                            window.append((entry_epoch, entry))
                        else:
                            window.append((entry_epoch, entry))
                            sorted_items = sorted(window, key=lambda x: x[0])
                            window.clear()
                            window.extend(sorted_items)
                            while window and window[0][0] < window_cutoff:
                                window.popleft()

                        # Check threshold condition
                        if len(window) >= rule.threshold_count:
                            # Verify cooldown dampening via cached epoch
                            is_suppressed = bool(
                                rule.suppress_until_epoch and now_epoch < rule.suppress_until_epoch
                            )

                            if is_suppressed:
                                # Cap window to threshold_count during cooldown to avoid memory ballooning
                                while len(window) > rule.threshold_count:
                                    window.popleft()
                            else:
                                # Trigger alert firing
                                triggering_logs = [item[1] for item in list(window)]
                                window.clear()

                                # Update cooldown dampening in memory
                                rule.trigger_count += 1
                                rule.last_triggered_at = now_iso
                                suppress_end = now_utc + datetime.timedelta(seconds=rule.cooldown_seconds)
                                rule.suppress_until = suppress_end.isoformat()
                                rule.suppress_until_epoch = suppress_end.timestamp()

                                fired_events.append((rule, triggering_logs))
                                break  # Break out of batch loop for this rule

                    except Exception as e:
                        logger.debug(f"Error evaluating rule {rule.id}: {e}")
                        continue

        # Process fired alert events outside lock
        for rule, logs in fired_events:
            # Update database state for rule non-blocking via asyncio.to_thread
            await asyncio.to_thread(self._update_rule_trigger_state, rule)
            # Dispatch alert notification asynchronously in background with lifecycle tracking
            task = asyncio.create_task(self._dispatch_alert(rule, logs))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

    def _update_rule_trigger_state(self, rule: CompiledAlertRule) -> None:
        """Persist last_triggered_at, suppress_until, and trigger_count to SQLite."""
        from app.core.config import get_db_path
        from app.api.deps import get_thread_read_connection
        effective_db = self.db_path or get_db_path()
        if not effective_db:
            return

        try:
            conn = get_thread_read_connection(effective_db)
            with conn:
                conn.execute(
                    """
                    UPDATE alert_rules
                    SET last_triggered_at = ?, suppress_until = ?, trigger_count = ?
                    WHERE id = ?
                    """,
                    (rule.last_triggered_at, rule.suppress_until, rule.trigger_count, rule.id),
                )
        except Exception as exc:
            logger.warning(f"Failed to persist trigger state for rule {rule.id}: {exc}")

    async def _dispatch_alert(
        self,
        rule: CompiledAlertRule,
        triggering_logs: list[dict[str, Any]],
    ) -> None:
        """
        Process alert firing:
        1. Extract IP indicators (for security canary alerts).
        2. Perform AI diagnosis enrichment if enabled.
        3. Persist record into alert_history table non-blocking via asyncio.to_thread.
        4. Send notification via NotifierService.
        """
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        now_iso = now_utc.isoformat()

        # Extract offending IP, host, and app/container if present in triggering logs
        sample_log = ""
        extracted_ip = None
        extracted_host = None
        extracted_app = None
        if triggering_logs:
            sample_entry = triggering_logs[-1]
            sample_log = str(sample_entry.get("message") or sample_entry.get("raw") or "")
            for log_entry in reversed(triggering_logs):
                if not extracted_ip:
                    msg = str(log_entry.get("message") or "")
                    cand_ip = extract_ip_from_message(msg)
                    if cand_ip:
                        extracted_ip = cand_ip
                if not extracted_host:
                    cand_host = log_entry.get("source_alias") or log_entry.get("source_ip") or log_entry.get("host")
                    if cand_host:
                        extracted_host = str(cand_host).strip()
                if not extracted_app:
                    cand_app = log_entry.get("app_name") or log_entry.get("container_name") or log_entry.get("tag")
                    if cand_app:
                        extracted_app = str(cand_app).strip()

        incident_summary = None
        ai_success = False
        ai_error_note = None
        actual_ai_model = None

        # Perform AI enrichment if enabled on the rule
        if rule.ai_enrichment and triggering_logs:
            try:
                from app.core.config import get_db_path
                from app.api.deps import run_db_query
                from app.core.config import DEFAULT_AI_MODEL
                from app.services.ai_service import read_ai_settings
                from app.services.ai_engine import execute_ai_analysis

                ai_settings, _ = await run_db_query(read_ai_settings, custom_db_path=self.db_path)

                ai_provider = (ai_settings.get("ai_provider") or "gemini").lower()
                default_model = DEFAULT_AI_MODEL if ai_provider == "gemini" else ("gpt-4o" if ai_provider == "openai" else "llama3.2")
                ai_model = ai_settings.get("ai_model") or default_model
                ai_key = ai_settings.get("ai_api_key", "")
                ai_base = ai_settings.get("ai_base_url")
                fallback_str = ai_settings.get("ai_fallback_models") or ""
                fallback_models = [m.strip() for m in fallback_str.split(",") if m.strip()]

                # Format and redact log window
                raw_lines = [
                    f"[{l.get('timestamp')}] [{l.get('source_alias') or l.get('source_ip') or 'unknown'}] [{l.get('app_name') or 'unknown'}] {l.get('message', '')}"
                    for l in triggering_logs[-50:]  # Cap at recent 50 logs
                ]
                redacted_lines = redact(raw_lines)
                redacted_text = "\n".join(redacted_lines) if isinstance(redacted_lines, list) else str(redacted_lines)

                prompt = (
                    f"### Security / Operations Incident Alert\n"
                    f"- Alert Rule: {rule.name}\n"
                    f"- Rule Type: {rule.rule_type}\n"
                    f"- Host / Source: {extracted_host or 'Unknown'}\n"
                    f"- App / Container: {extracted_app or 'Unknown'}\n"
                    f"- Threshold: {rule.threshold_count} matches in {rule.window_seconds}s\n"
                    f"- Offending IP: {extracted_ip or 'None detected'}\n\n"
                    f"### Redacted Log Stream (Chronological)\n"
                    f"```\n{redacted_text}\n```\n\n"
                    f"Review this incident and provide structured Summary, Root Cause, and Actionable Remediation."
                )

                logger.info(
                    f"Starting AI incident analysis for rule '{rule.name}' with primary model '{ai_model or 'default'}' "
                    f"and fallback models {fallback_models}"
                )

                ai_res = await execute_ai_analysis(
                    provider=ai_provider,
                    model=ai_model,
                    api_key=ai_key,
                    base_url=ai_base,
                    source_alias=triggering_logs[0].get("source_alias") or "alert",
                    app_name=triggering_logs[0].get("app_name") or "alert",
                    redacted_logs=redacted_text,
                    log_count=len(triggering_logs),
                    prompt_override=prompt,
                    fallback_models=fallback_models,
                )
                if len(ai_res) == 11:
                    summary, root_cause, remediation, _, _, _, _, _, _, actual_model, fallback_attempts = ai_res
                else:
                    summary, root_cause, remediation = ai_res[:3]
                    actual_model = ai_model
                    fallback_attempts = []

                actual_ai_model = actual_model

                if fallback_attempts:
                    logger.info(
                        f"AI enrichment for alert '{rule.name}' succeeded via fallback model '{actual_model}' "
                        f"after failovers: {fallback_attempts}"
                    )

                diag_parts = []
                if summary and summary.strip():
                    diag_parts.append(summary.strip())
                if root_cause and root_cause.strip():
                    diag_parts.append(f"### Root Cause\n{root_cause.strip()}")
                if remediation and remediation.strip():
                    diag_parts.append(f"### Remediation\n{remediation.strip()}")
                incident_summary = "\n\n".join(diag_parts) if diag_parts else (summary or "Incident review complete.")
                ai_success = True
            except Exception as ai_err:
                logger.warning(
                    f"AI enrichment failed for alert rule '{rule.name}' after attempting candidate models: {ai_err}"
                )
                ai_success = False
                clean_err = str(ai_err).splitlines()[0]
                if len(clean_err) > 250:
                    clean_err = clean_err[:247] + "..."
                incident_summary = f"AI analysis failed: {clean_err}"
                ai_error_note = clean_err
        else:
            incident_summary = f"Alert triggered with {len(triggering_logs)} matching event(s)."

        # Record event in alert_history table via worker thread
        def _execute_insert(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO alert_history
                (rule_id, rule_name, channel_id, trigger_count, sample_log, incident_summary, ai_enrichment, ai_model, triggered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.id,
                    rule.name,
                    rule.channel_id,
                    len(triggering_logs),
                    sample_log[:1000],
                    incident_summary,
                    int(rule.ai_enrichment),
                    actual_ai_model,
                    now_iso,
                ),
            )

        try:
            from app.api.deps import run_db_query
            await run_db_query(_execute_insert, custom_db_path=self.db_path)
        except Exception as db_err:
            logger.error(f"Failed to record alert history for rule {rule.id}: {db_err}")

        # Construct clean push notification payload
        notification_title = f"LogShed Alert: {rule.name}"
        truncated_log = format_sample_log_for_alert(sample_log)
        clean_log = truncated_log.replace("```", "").replace("`", "").strip()
        body_lines = [
            f"Host: {extracted_host or 'Unknown'}",
            f"App: {extracted_app or 'Unknown'}",
            f"Log: {clean_log}",
        ]

        if rule.ai_enrichment:
            if ai_success and summary:
                clean_summary = strip_markdown(summary)
                if len(clean_summary) > 200:
                    clean_summary = clean_summary[:197] + "..."
                body_lines.append(f"AI Analysis: {clean_summary}")
            elif ai_error_note:
                body_lines.append(f"AI Analysis: Unavailable ({ai_error_note})")

            app_url = (os.environ.get("APP_URL") or os.environ.get("app_url") or "").strip().rstrip("/")
            if app_url:
                body_lines.append(f"Link: {app_url}/alerts/history")

        notification_body = "\n".join(body_lines)

        notifier = get_notifier()
        try:
            await notifier.send_notification(
                title=notification_title,
                body=notification_body,
                channel_id=rule.channel_id,
            )
        except Exception as notify_err:
            logger.error(f"Failed to dispatch alert notification for {rule.name}: {notify_err}")

    async def stop(self) -> None:
        """Wait for any in-flight alert dispatch tasks to complete."""
        if self._pending_tasks:
            tasks = list(self._pending_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)


# Module-level singleton
_alert_evaluator: Optional[AlertEvaluator] = None
_evaluator_lock = threading.Lock()


def get_alert_evaluator() -> AlertEvaluator:
    """Return the global AlertEvaluator singleton instance."""
    global _alert_evaluator
    with _evaluator_lock:
        if _alert_evaluator is None:
            try:
                from app.core.config import get_db_path
                _alert_evaluator = AlertEvaluator(get_db_path())
            except Exception:
                _alert_evaluator = AlertEvaluator()
        elif not _alert_evaluator.db_path:
            try:
                from app.core.config import get_db_path
                _alert_evaluator.db_path = get_db_path()
            except Exception:
                pass
        return _alert_evaluator


def init_alert_evaluator(db_path: Union[str, Path]) -> AlertEvaluator:
    """Initialize or update the global AlertEvaluator singleton with database path."""
    global _alert_evaluator
    with _evaluator_lock:
        if _alert_evaluator is None:
            _alert_evaluator = AlertEvaluator(db_path)
        else:
            _alert_evaluator.db_path = Path(db_path)
            _alert_evaluator.reload_rules()
        return _alert_evaluator
