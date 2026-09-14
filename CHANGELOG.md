# Changelog

All notable changes to LogShed will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **RFC 5424 Parser Infinite Loop**: Fixed an infinite loop in `parse_syslog_message` when structured data begins with an unclosed bracket `[`, ensuring unclosed structured data breaks out safely and is treated as message payload.
- **Missing Imports in Background Model Refresh**: Added missing top-level `datetime` and `json` imports in `main.py` used by `_model_refresh_worker`.
- **AI Error Handler Redaction**: Redacted raw `ValueError` exception strings in `diagnose_logs` before returning `HTTPException` detail to prevent potential token leakage.
- **Event Loop Blocking on Password Verification**: Offloaded Argon2id `verify_password` in `login()` to a worker thread via `asyncio.to_thread` to prevent event loop blocking during authentication.
- **Host Alias Whitespace Mismatches**: Stripped leading and trailing whitespace from IP addresses and aliases on save to avoid lookup and matching mismatches.

### Changed
- **Internal Log Queue Ingestion**: Removed premature secret redaction from `InternalLogHandler.emit` so raw internal application logs are persisted unredacted to SQLite per SPEC §3 (with redaction performed on-demand for AI prompts).
- **Master Key Path Logging**: Lowered master encryption key file path logging from INFO to DEBUG in `security.py`.
- **Dead Code and Deprecated Function Cleanup**: Removed unused `get_docker_host` import from `main.py`, obsolete regexes `_ANSI_ESCAPE_RE` and `_CONTROL_CHARS_RE` from `docker_collector.py`, deprecated alias `SYSTEM_PROMPT` from `ai_engine.py`, and obsolete synchronous helper `resolve_alias` from `syslog.py`.

## [1.1.0-beta.3] - 2026-09-13

### Added
- **HTML5 History API URL Routing**: Direct URL path synchronization across application tabs (`/` and `/console` for live stream, `/aliases` for Host Aliases, `/storage` for Storage & Retention, `/settings` for System Configuration). Supports browser back and forward navigation via `popstate` event handling, direct reloads on any sub-route, and seamless integration with the unsaved settings guard.
- **Contained Touch Pull-to-Refresh Gesture**: Mobile pull-to-refresh mechanism attached to the top navigation header and stream search controls without interfering with virtual list scrolling, featuring smooth pull indicators and threshold reload triggers.

### Changed
- **Retention Override Handling & Graceful Clamping**: When `MAX_RETENTION_DAYS` is configured in the environment, the retention slider and preset buttons are locked and display an override badge. Added a concise non-technical advisory note when retention exceeds 30 days. When the environment variable is removed on container reboot, retention automatically clamps cleanly down to 30 days and persists to the database.
- **Mobile Log Card Glanceability**: Reorganized mobile 3-line log card structure to display `app_name (host_name)` at the top with host name styled matching timestamp color, and placed the local timestamp on the bottom left alongside the AI action button.
- **Auto-Scroll Banner Sizing**: Optimized mobile floating auto-scroll resume button with a wider container and centered 2-line layout (`Auto-scroll paused (...)` on line 1, `Click to jump to top` on line 2) to reduce vertical screen footprint.

### Fixed
- **Syslog Multiline Python Traceback Assembly**: Fixed an issue where multi-line Python tracebacks and exceptions (such as `ConnectionResetError`) arriving over UDP or TCP syslog as separate packets split into multiple log records, with headerless continuation lines being misattributed to `unknown` and host IP addresses. Continuation lines now correctly correlate to the active stream, inherit parent metadata, and assemble into a single log entry.
- **Mobile Selection Dismiss on AI Modal Close**: Automatically clears single-log selections on mobile viewports when closing the AI analysis modal so the floating action bar does not remain stuck, while preserving multi-select selections on desktop. Added a prominent Deselect button to the floating action bar.
- **Computer-Use Model Exclusion**: Excluded computer-use agent models (such as `gemini-2.5-computer-use-preview-10-2025`) from suggested AI model dropdowns in backend discovery, caching, and frontend model validation.
- **Log Detail Slide-Over Width**: Fixed `SlideOver` width constraint handling by removing conflicting CSS classes and setting a consistent desktop width for `LogDetailModal` so the inspector no longer fluctuates in width based on log message length.

---

## [1.1.0-beta.2] - 2026-09-12

### Added
- **Settings Unsaved Changes Guard**: Tab navigation guard in `App` intercepting navigation away from `SettingsPanel` when unsaved changes exist, prompting a confirmation dialog with options to Keep Editing, Discard & Leave, or Save & Continue. Also adds browser `beforeunload` protection against accidental tab closure or refresh.
- **Docking Sticky Action Bar**: Streamlined configuration save workflow in `SettingsPanel` with a sticky floating action bar (`sticky bottom-4`) that floats above the bottom of the viewport while scrolling through the settings form and naturally locks into place between the configuration form and password section. Includes real-time inline saving feedback, state discard reverting, and removed redundant static buttons.
- **Dynamic Stream Display Cleaning**: Client-side message cleaning in `LiveLogStream` dynamically stripping redundant leading timestamps, repeated severity prefixes (`[error]`, `WARN:`, `INFO`, `LOG:`), and timezone codes from stream table cells while preserving subsystem brackets (e.g. `[MONITOR]`, `[celery.worker.strategy]`, Postgres session PIDs) and keeping raw database payloads intact.
- **Extended Ingestion Timestamp & Severity Formats**: Syslog and Docker collectors now parse ISO 8601/RFC 3339 timestamps and slash dates (`YYYY/MM/DD`) without requiring RFC 5424 version headers, support bracketed envelopes (`[TIMESTAMP] [HOST] [APP]`), handle Python logging comma-separated milliseconds, named timezones (`UTC`, `BST`, etc.), and perform content-based severity fallback promotion for unprioritized syslog messages.

---

## [1.1.0-beta.1] - 2026-09-11

### Added
- **AI Fast Failover**: Resilient multi-provider failover automatically falling back across secondary models and providers when encountering rate limits (HTTP 429), timeouts, or provider downtime.
- **Dynamic AI Model Discovery**: Discovery endpoint (`/api/ai/models`) dynamically fetching and caching active text models for Google Gemini, OpenAI, and Ollama/OpenAI-compatible endpoints with a 24-hour TTL.
- **Thinking Budget Configuration**: Support for configuring extended reasoning tokens and thinking budgets for supported reasoning models (such as Gemini 2.0 Flash Thinking and OpenAI reasoning models).
- **Live Streaming AI Diagnosis**: Server-Sent Events (SSE) streaming endpoint (`/api/ai/diagnose/stream`) delivering real-time tokens and progress indicators directly to the UI.
- **Dedicated Storage Management Panel**: Dedicated navigation tab and panel (`StoragePanel`) providing database size metrics, WAL status, manual VACUUM execution, and granular log retention pruning settings.
- **Responsive Mobile Layout & Cards**: Mobile-first navigation drawer, responsive log search and facet filter controls, and touch-friendly management cards for host aliases and storage management on smaller viewports.

### Changed
- **Navigation & Panel Architecture**: Split settings and database storage management into dedicated top-level navigation tabs for cleaner configuration workflows.
- **Analysis Modal Accordion**: Collapsed AI provider and model configuration into a compact accordion in `AiAnalysisModal` to give more screen space for log context and streaming diagnosis output.
- **Docker Log Cleaning**: Integrated ANSI escape code stripping in `docker_collector.py` and UI formatters to strip raw terminal escape codes and style sequences from log streams.
- **Automated Beta Tagging**: Configured GitHub Actions CI workflow to automatically publish floating `:beta` container images for `-beta` and `-rc` git tags without overwriting `:latest`.
- **Navbar Cleanup**: Removed redundant manual refresh button from the navbar to rely on real-time SSE log streaming.
- **Typography Consistency**: Replaced em dashes with standard hyphens across documentation, specifications, and code comments to enforce strict typography rules.

---

## [1.0.0] - 2026-09-08

### Added
- Initial release of LogShed: lightweight, single-process log aggregator tailored for homelab environments.
- Dual ingestion engine:
  - Syslog server (UDP & TCP on port 1514) supporting RFC 3164 (BSD) and RFC 5424 (IETF) standards with transparent and octet-counted framing.
  - Docker Engine API collector streaming logs directly from `/var/run/docker.sock` (or TCP proxy).
- SQLite storage backend running in Write-Ahead Logging (WAL) mode with external-content FTS5 full-text search indexing.
- Keyed multi-line stream assembler grouping Python tracebacks and Java exceptions by source stream into single coherent log rows.
- Fast real-time log dashboard with virtual scrolling, built with React 18, Vite, and Tailwind CSS.
- Multi-facet host and application filtering with full-text search syntax (`severity:error`, prefix wildcard queries).
- On-demand AI root-cause analysis supporting Google Gemini, OpenAI, and local LLMs (Ollama / vLLM / LocalAI).
- Automatic client-side and server-side credential/secret redaction (passwords, tokens, API keys, IP addresses) before AI dispatch.
- Dynamic Host Alias Manager allowing friendly IP-to-hostname mappings with retroactive updates across existing database rows.
- Automated daily database pruning worker with configurable log retention periods (default: 14 days, up to 365 days) and freelist reuse.
- Native multi-architecture container images (`linux/amd64` and `linux/arm64`).
- Unraid Community Applications template (`unraid-template.xml`) with cache-pool POSIX locking recommendations.

[Unreleased]: https://github.com/BenHornerTech/logshed/compare/v1.1.0-beta.3...HEAD
[1.1.0-beta.3]: https://github.com/BenHornerTech/logshed/compare/v1.1.0-beta.2...v1.1.0-beta.3
[1.1.0-beta.2]: https://github.com/BenHornerTech/logshed/compare/v1.1.0-beta.1...v1.1.0-beta.2
[1.1.0-beta.1]: https://github.com/BenHornerTech/logshed/compare/v1.0.0...v1.1.0-beta.1
[1.0.0]: https://github.com/BenHornerTech/logshed/releases/tag/v1.0.0
