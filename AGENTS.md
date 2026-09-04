# LogShed - LLM Instructions

## Build & Test Commands
- Run backend tests: `.venv/bin/pytest backend/tests/ -v`
- Run single test file: `.venv/bin/pytest backend/tests/test_schema.py -v`
- Run frontend tests: `cd frontend && npm test`
- Build frontend: `cd frontend && npm run build`
- Python Environment: Always execute Python tools using `.venv/bin/python` or `.venv/bin/pytest` (or prefix shell commands with `source .venv/bin/activate && ...`). Never use the global system Python.

## Architecture & Code Standards
- **Runtime:** Python 3.12 (`asyncio`) + FastAPI + SQLite (WAL mode + FTS5).
- **Frontend:** React + Vite + Tailwind CSS (bundled to `backend/app/static`).
- **Process Model:** Single container, **single OS process**, running multiple concurrent `asyncio` tasks (`SyslogServer`, `DockerTailer`, `QueueConsumer`, `PruneWorker`) under supervisor isolation. Never run multiple uvicorn/gunicorn worker processes (`--workers 1` only) — the in-memory ingestion queue, rate limiter, and drop counters are process-local and would silently desync across separate OS processes.
- **Docker Access:** Respect `DOCKER_HOST` (supports socket or `tecnativa/docker-socket-proxy`).
- **Security Baseline:**
  * App drops privileges via `gosu` to run as a non-root user defined by `PUID` and `PGID` environment variables (defaults to 1000:1000).
  * On-demand redaction of all sensitive tokens/passwords via `redactor.py` before LLM dispatch.
  * Native Auth: Argon2id password hashing + HTTP-only SameSite=Lax session cookies.
  * No external database servers; use SQLite migrations via `PRAGMA user_version`.

## Workflow Protocol
1. Consult `SPEC.md` for technical schemas, endpoints, and exact trigger definitions.
2. Read `PROGRESS.md` before starting any task to understand current implementation state.
3. At the end of every phase, run the phase verification test suite.
4. Update `PROGRESS.md` with: completed items, test results, and next steps before concluding.

## Dependency & Performance Guardrails
- **Zero Unapproved Dependencies:** Any package explicitly required by the core backend/frontend specs (`fastapi`, `uvicorn`, `httpx`, `argon2-cffi`, `cryptography`, `google-genai`, `openai`, `pydantic`, `@tanstack/react-virtual`, `recharts`) is pre-approved. Do not add `aiosqlite`. Do not add the `docker` or `aiodocker` SDKs — talk to the Docker Engine API (both `unix:///var/run/docker.sock` and `tcp://proxy:2375`) using `httpx`, with `httpx.HTTPTransport(uds=...)` for the Unix socket case.
- **Standard Library First:** For anything not already dictated by `SPEC.md`, default to Python standard library modules (`sqlite3`, `json`, `dataclasses`, `pathlib`, `logging`, `typing`) before reaching for external packages. Use stdlib `sqlite3` + `asyncio.to_thread()` for all database operations.
- **No Heavyweight Tooling:** Strictly forbid data-science or heavy ORM libraries (e.g., `pandas`, `numpy`, `scipy`, `sqlalchemy`) — these are never approved, regardless of `SPEC.md`.