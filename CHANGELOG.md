# Changelog

All notable changes to LogShed will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Dynamic Stream Display Cleaning**: Client-side message cleaning in `LiveLogStream` dynamically stripping redundant leading timestamps, repeated severity prefixes (`[error]`, `WARN:`, `INFO`, `LOG:`), and timezone codes from stream table cells while preserving subsystem brackets (e.g. `[MONITOR]`, `[celery.worker.strategy]`, Postgres session PIDs) and keeping raw database payloads intact.
- **Extended Ingestion Timestamp & Severity Formats**: Syslog and Docker collectors now parse ISO 8601/RFC 3339 timestamps and slash dates (`YYYY/MM/DD`) without requiring RFC 5424 version headers, support bracketed envelopes (`[TIMESTAMP] [HOST] [APP]`), handle Python logging comma-separated milliseconds, named timezones (`UTC`, `BST`, etc.), and perform content-based severity fallback promotion for unprioritized syslog messages.

---

## [1.1.0] - 2026-09-11

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

[Unreleased]: https://github.com/BenHornerTech/logshed/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/BenHornerTech/logshed/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/BenHornerTech/logshed/releases/tag/v1.0.0
