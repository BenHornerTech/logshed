# Changelog

All notable changes to LogShed will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Ingestion Drop Rules**: In-memory filtering engine evaluating host/source, application/container, and message patterns (substring, regex, or app-wide wildcard `*`) to discard repetitive log chatter before database persistence and FTS5 indexing. Includes in-memory drop counters with periodic SQLite flushing to eliminate disk write contention.
- **Saved Filter Views & URL Synchronization**: Saved filter views menu on the Console filter bar and mobile filter drawer with pinned view support, 1-tap activation, and bidirectional query parameter synchronization in the browser URL for bookmarking and sharing.
- **Interactive Rule Tester & Console Quick-Action**: Dry-run pattern tester in Settings and a "Create Drop Rule" shortcut directly within the log detail modal that pre-fills host, application, and message context.
- **Deletion Safety Net**: Confirmation modal protection for drop rules and saved views to prevent accidental deletion.
- **Asynchronous FTS5 Indexing Worker**: Decoupled full-text search indexing from the raw log ingestion path into a supervised background task (`FTSIndexWorker`), enabling raw ingestion rates without holding exclusive SQLite write locks during tokenization.
- **Chunked Batch Ingestion with RETURNING id**: Modernized `QueueConsumer` batch inserts using parameterized multi-row `INSERT INTO logs (...) VALUES (...) RETURNING id` queries to eliminate Python interpreter loops while accurately binding IDs for live SSE streaming.
- **Thread-Local Read Connection Reuse**: Reusable SQLite read connections for worker threads in `run_db_query`, eliminating ephemeral connection churn and per-request PRAGMA execution overhead.
- **Universal Notification Targets (Webhooks & Apprise)**: Universal alerting dispatcher supporting 80+ notification services (Discord, Gotify, Telegram, Ntfy, Pushover, Slack, Email) using standard URL formats. Includes URL encryption at rest with the master key, token masking for secure client display, and asynchronous dispatch via `asyncio.to_thread`.
- **Real-Time Alert Engine**: In-memory rule compilation and batch evaluation engine (`AlertEvaluator`) running inside the ingestion pipeline, supporting threshold sliding windows, pattern matching (regex and substring), severity thresholds, multi-application filtering, and cooldown flap dampening.
- **Pre-Packaged Security Canary Rules**: 1-click installable canary alerts for SSH brute-force attacks, reverse-proxy authentication floods, unauthorized sudo escalation, and kernel out-of-memory (OOM) killer events.
- **Automated AI Incident Diagnosis & Remediation**: Automated background analysis on alert triggers, generating structured summaries, root-cause analyses, and actionable remediation steps with model failover support.
- **Enriched Alert Notifications**: Multi-channel notification delivery via Apprise (Pushover, Discord, Telegram, webhooks, etc.) with host, application, offending IP, log context, and AI incident diagnosis summaries.
- **Alert Testing & Dry-Run Simulator**: Interactive test modal to validate alert matching rules and extract offending IP indicators against sample log payloads.
- **Alert Firing Log & Unified Incident Detail View**: Historical alert record tracking with model attribution, matching event counts, log snippets with quick copy actions, and formatted markdown rendering matching the Historical AI Root-Cause Analysis layout.
- **Database Schema Migration v2 Expansion**: Expanded Migration 2 to introduce `notification_channels`, `alert_rules`, and `alert_history` tables with covering indexes for alert routing and delivery.

## [1.1.0] - 2026-09-16

### Added
- **AI Fast Failover**: Automatic multi-provider failover across secondary models when encountering rate limits, timeouts, or downtime.
- **Dynamic AI Model Discovery**: Dynamic discovery and caching of available models for Google Gemini, OpenAI, and Ollama/compatible endpoints.
- **Reasoning / Thinking Budget**: Configurable reasoning token budget for extended thinking models (e.g. Gemini Thinking, OpenAI reasoning).
- **Live Streaming AI Diagnosis**: Real-time SSE streaming for AI diagnosis tokens and progress updates in the analysis modal.
- **AI Diagnosis Rate Limiting**: In-memory sliding-window rate limiter (10 req/min per session) on AI diagnosis endpoints to prevent runaway API usage.
- **Storage Management Panel**: Dedicated tab for database metrics, WAL checkpoint status, manual VACUUM, and retention pruning settings.
- **Responsive Mobile Layout**: Mobile navigation drawer, responsive search/filter controls, touch-friendly card layouts, and pull-to-refresh.
- **Browser History & URL Routing**: Deep-linking and back/forward navigation across Console, Host Aliases, Storage, and Settings tabs via HTML5 History API.
- **Update Checks & Notifications**: Background checks against GitHub Container Registry for new stable releases, with a toggle in Settings and a navbar update notification badge.
- **Settings "About LogShed"**: Added an About card in Settings displaying the installed version, copyright information, documentation links, and update status.
- **Unsaved Changes Guard**: Confirmation prompt when navigating away from Settings with unsaved changes, plus `beforeunload` browser tab protection.
- **Sticky Settings Action Bar**: Floating action bar in Settings with save/discard controls and real-time status feedback.
- **Expanded Ingestion Formats**: Added parser support for ISO 8601, slash dates, comma-separated milliseconds, named timezones, and content-based severity promotion.
- **Expanded Secret Redactor Patterns**: Added detection and scrubbing for Slack webhooks, generic secret assignments, `sshpass` flags, and Docker registry auth tokens.
- **CSRF Request Protection**: Custom request header validation middleware protecting mutating API endpoints against Cross-Site Request Forgery.
- **Secure CLI Password Prompt**: Made the `--password` flag optional in `reset-admin` CLI, falling back to secure terminal prompting via `getpass`.
- **Automated Release Publishing**: Workflow integration to extract release notes from CHANGELOG.md and publish GitHub releases upon stable tag pushes.

### Changed
- **Log Stream Rendering Performance**: Precompiled search patterns, cached virtual row rendering, and precomputed timestamp formatting to eliminate lag during high-frequency log streams.
- **Log Stream Display Cleaning**: Stripped redundant timestamps, duplicate severity prefixes, and timezone noise from log view while preserving raw payloads.
- **Database & Query Improvements**: Switched queue consumer to a persistent SQLite connection and streamlined recursive CTE queries for facet filtering.
- **Decoupled AI Service Architecture**: Extracted a dedicated AI service layer handling context preparation, token accounting, and multi-provider dispatching.
- **Navigation Structure**: Separated Settings and Storage into dedicated navigation tabs.
- **AI Modal Compact Settings**: Collapsed model selection into an accordion to give more screen space to log context and analysis.
- **Retention Override Safeguards**: Locked retention slider with an advisory badge when `MAX_RETENTION_DAYS` is set via environment variable, automatically clamping down to 30 days if the variable is removed.
- **Internal Log Ingestion**: Stored internal application logs unredacted in SQLite per specification, preserving raw data while redacting on-demand for AI prompts.
- **Terminal Escape Sequence Stripping**: Stripped ANSI escape codes and formatting sequences from Docker log streams.
- **Typography Consistency**: Replaced em dashes with standard hyphens across all documentation and comments.
- **Documentation & Architecture Alignment**: Aligned technical specifications, LLM instructions, environment configuration template, and README with the v1.1.0 application architecture.

### Fixed
- **Custom Time Filter Timezone Alignment**: Formatted datetime-local input values in client-local time instead of UTC slicing, ensuring time values selected in the browser's native picker match the displayed input text across non-UTC client timezones.
- **Syslog TCP Persistent Connections & Resource Limits**: Enabled TCP keepalive (`SO_KEEPALIVE`) on accepted client sockets, disabled TCP inactivity timeout by default (`SYSLOG_TCP_INACTIVITY_TIMEOUT=0`), and raised concurrent Syslog TCP connection limit to 250 (`SYSLOG_MAX_TCP_CONNECTIONS`).
- **Syslog Multiline Traceback Assembly**: Fixed multi-line Python tracebacks over UDP and TCP splitting into separate records or being misattributed to `unknown`.
- **Multiline Assembler Buffer Bounds**: Enforced stream memory caps (500 lines / 256 KB) and a 5-second lifetime limit to prevent unbounded buffer growth on unclosed multiline streams.
- **Docker TTY Buffer Limit**: Capped TTY stream buffers to 64 KB with warning truncation when container output lacks newlines.
- **RFC 5424 Syslog Parser Loop**: Fixed infinite loop when syslog structured data contains unclosed brackets.
- **FTS5 Search Query Validation**: Robust parsing, quoting, and syntax fallback for search-as-you-type and column filters, eliminating SQLite operational errors on partial queries.
- **Password Change Re-Authentication**: Cleared session cookies on admin password change and triggered an immediate login redirect with notice banner instead of an orphaned session.
- **Mobile Layout & Form Usability**: Prevented mobile browsers from auto-zooming on input focus, prevented quick filter pill wrapping on narrow screens, and auto-reset single-log selection on mobile modal close.
- **AI Modal Prompt Cursor Jumping**: Fixed cursor jumping to the end of the textarea when editing the first line in Full Prompt mode.
- **Login Form Password Selection**: Automatically highlights and selects password field contents on failed login for quick retyping.
- **Secret Redactor Special Characters**: Fixed connection string regex to handle passwords containing `@` symbols.
- **API Parameter Validation**: Added ISO-8601 validation for log query datetimes and IP format validation for host alias creation.
- **Password Verification Concurrency**: Offloaded Argon2id verification to worker threads to keep authentication from blocking the asyncio event loop.
- **Model Filtering**: Filtered out non-text models from AI model selection dropdowns.

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

[Unreleased]: https://github.com/BenHornerTech/logshed/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/BenHornerTech/logshed/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/BenHornerTech/logshed/releases/tag/v1.0.0
