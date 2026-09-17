# LogShed - LLM Instructions

## Build & Test Commands
- Run backend tests: `.venv/bin/pytest backend/tests/ -v`
- Run single test file: `.venv/bin/pytest backend/tests/test_schema.py -v`
- Run frontend tests: `cd frontend && npm test`
- Build frontend: `cd frontend && npm run build`
- Python Environment: Always execute Python tools using `.venv/bin/python` or `.venv/bin/pytest` (or prefix shell commands with `source .venv/bin/activate && ...`). Never use the global system Python.
- Command Execution & Tooling Guidelines: Avoid running dynamic inline code with `python -c '...'` or complex subshell evaluations, as these trigger interactive security confirmation prompts. Instead:
  * For package/module inspection: use static commands like `.venv/bin/pip show <pkg>` or `.venv/bin/python -m <module> --help`.
  * For code exploration and symbol inspection: use file reading and search tools (`view_file`, `grep_search`) rather than executing runtime Python snippets.
  * If dynamic execution is strictly required, write a temporary script into the agent scratch directory rather than running inline strings in the shell.


## Architecture & Code Standards
- **Runtime:** Python 3.12 (`asyncio`) + FastAPI + SQLite (WAL mode + FTS5).
- **Frontend:** React + Vite + Tailwind CSS (bundled to `backend/app/static`).
- **Process Model:** Single container, **single OS process**, running multiple concurrent `asyncio` tasks (`SyslogServer`, `DockerTailer`, `QueueConsumer`, `FTSIndexWorker`, `PruneWorker`, `StorageMetricsWorker`, `ModelRefreshWorker`) under supervisor isolation. Never run multiple uvicorn/gunicorn worker processes (`--workers 1` only) - the in-memory ingestion queue, rate limiter, and drop counters are process-local and would silently desync across separate OS processes.
- **Docker Access:** Respect `DOCKER_HOST` (supports socket or `tecnativa/docker-socket-proxy`).
- **Security Baseline:**
  * App drops privileges via `gosu` to run as a non-root user defined by `PUID` and `PGID` environment variables (defaults to 1000:1000).
  * On-demand redaction of all sensitive tokens/passwords via `redactor.py` before LLM dispatch.
  * Native Auth: Argon2id password hashing + HTTP-only SameSite=Lax session cookies.
  * Database & Migrations: SQLite (WAL mode + FTS5 external content table) versioned via `PRAGMA user_version = 2`. Asynchronous FTS5 indexing decoupled from raw ingestion via supervised `FTSIndexWorker` (target catch-up latency <= 1000ms), durable state tracking in `fts_index_state` (last_indexed_id), conditional triggers guarding against unindexed row deletions, and thread-local read connection reuse via `run_db_query` in `backend/app/api/deps.py`. No external database servers.

## Workflow Protocol
1. Consult `docs/SPEC.md` for technical schemas, endpoints, and exact trigger definitions.
2. At the end of every phase, run the phase verification test suite.

## Dependency & Performance Guardrails
- **Zero Unapproved Dependencies:** Any package explicitly required by the core backend/frontend specs (`fastapi`, `uvicorn`, `httpx`, `argon2-cffi`, `cryptography`, `google-genai`, `openai`, `pydantic`, `@tanstack/react-virtual`, `recharts`) is pre-approved. Do not add `aiosqlite`. Do not add the `docker` or `aiodocker` SDKs - talk to the Docker Engine API (both `unix:///var/run/docker.sock` and `tcp://proxy:2375`) using `httpx`, with `httpx.HTTPTransport(uds=...)` for the Unix socket case.
- **Standard Library First:** For anything not already dictated by `docs/SPEC.md`, default to Python standard library modules (`sqlite3`, `json`, `dataclasses`, `pathlib`, `logging`, `typing`) before reaching for external packages. Use stdlib `sqlite3` + `asyncio.to_thread()` for all database operations.
- **No Heavyweight Tooling:** Strictly forbid data-science or heavy ORM libraries (e.g., `pandas`, `numpy`, `scipy`, `sqlalchemy`) - these are never approved, regardless of `docs/SPEC.md`.

## Typography & Formatting Guardrails
- **No Em Dashes:** Never use em dashes (the character \u2014) anywhere in the codebase, UI text, error messages, test descriptions, or documentation markdown files. Always use standard hyphens (` - `) or clean commas/parentheses instead. Avoid fancy curly quotes or typographer symbols in code strings.