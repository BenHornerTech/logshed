# Technical Specification: LogShed

## 1. System Architecture & Process Model
Single Docker container running Python 3.12 (`asyncio`) + FastAPI backend serving a pre-built React SPA, with background ingestion workers managed under isolated supervisors.

- **Process Boundaries:** Main thread runs `uvicorn` and supervised background tasks (`SyslogServer`, `DockerTailer`, `QueueConsumer`, `PruneWorker`).  SQLite operations must use stdlib `sqlite3` and offload synchronous queries via `asyncio.to_thread()` (per AGENTS.md, `aiosqlite` is not used).
- **Failure Isolation:** Worker exceptions must be caught, logged, and restarted with exponential backoff without crashing the event loop.
- **Security & Privileges:** Container starts as root to allow `entrypoint.sh` to configure permissions. It reads `PUID` and `PGID` environment variables (defaulting to 1000:1000), maps the `appuser` to match, dynamically detects `/var/run/docker.sock` GID and adds the user to that group, changes ownership of `/data`, and drops privileges via `gosu appuser`.
- **Docker Endpoint:** Connects via `DOCKER_HOST` environment variable (`unix:///var/run/docker.sock` or `tcp://proxy:2375` for `tecnativa/docker-socket-proxy`).

### 1.1 Environment Variables vs. Runtime Settings
Only the following are true environment variables, supplied at container start and never stored in the database:
- `DOCKER_HOST` — Docker endpoint (socket path or `tcp://` proxy address, e.g. `unix:///var/run/docker.sock` or `tcp://192.168.1.50:2375`). Defaults to `unix:///var/run/docker.sock` if unset. Not exposed as an Unraid template `Config` entry (see §8.2) — the socket path is fixed by the `/var/run/docker.sock` volume mount; only set `DOCKER_HOST` explicitly when using a `tcp://` socket-proxy instead of a direct mount.
- `DOCKER_SOURCE_ALIAS` — Source attribution alias for Docker logs ingested via `DOCKER_HOST` (defaults to `docker` if unset).
- `DOCKER_EXCLUDE_CONTAINERS` — Optional comma-separated list of container names or IDs to exclude from log tailing (e.g. `logshed,custom_redis`).
- `TZ` — container timezone.
- `PORT` — web/API port (defaults to `8080` if unset).
- `SYSLOG_PORT` — Syslog listening port for UDP and TCP (defaults to `1514` if unset).
- `PUID` and `PGID` — user and group IDs for the application to run as (defaults to `1000` if unset).
- `LOGSHED_SECRET_KEY` — optional override for the Fernet master key; if unset, one is generated at `/data/.secret_key` on first boot.
- `COOKIE_SECURE` — optional boolean (`true`/`false`, defaults to `false`). When `false` (the default), session cookies are issued without the `Secure` flag to allow direct HTTP access over local IP addresses in homelabs, or automatically detects HTTPS via `X-Forwarded-Proto` header or request scheme. Set to `true` when running behind an SSL-terminating reverse proxy that does not send `X-Forwarded-Proto`.
- `MAX_RETENTION_DAYS` — Optional maximum log retention period in days (defaults to `30`, minimum `1`). Caps the retention period selectable in the UI. Advanced users can override this to retain logs for longer periods.


All other configuration — AI provider, AI API key, AI base URL, AI model, and `retention_days` (default 14 days, up to `MAX_RETENTION_DAYS`) — is **runtime-configurable only**, entered via the Settings UI, encrypted with `cryptography.fernet`, and persisted in the `system_settings` table (see §5, §6). These values must never be read from environment variables or written to `.env.example`.


---

## 2. Database Schema & Storage Engine (SQLite + FTS5)
Database path: `/data/logs.db`. WAL mode enabled (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;`).
Schema migrations managed via `backend/app/core/migrations.py` using `PRAGMA user_version`.

### 2.1 Core DDL (Migration v1)
```sql
PRAGMA user_version = 1;

CREATE TABLE logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    received_at DATETIME NOT NULL,
    source_ip TEXT NOT NULL,
    source_alias TEXT NOT NULL,
    app_name TEXT NOT NULL,
    facility INTEGER NOT NULL DEFAULT 1,
    severity INTEGER NOT NULL DEFAULT 6,
    message TEXT NOT NULL,
    raw TEXT NOT NULL
);

-- Relational Indexes for Structured Query Filtering
CREATE INDEX idx_logs_time_sev ON logs(timestamp DESC, severity);
CREATE INDEX idx_logs_app_time ON logs(app_name, timestamp DESC);
CREATE INDEX idx_logs_src_time ON logs(source_alias, timestamp DESC);

-- Full-Text Search Virtual Table (External Content Table)
CREATE VIRTUAL TABLE logs_fts USING fts5(
    app_name,
    source_alias,
    message,
    content='logs',
    content_rowid='id'
);

-- FTS5 Synchronization Triggers (Explicit External Content Deletion Pattern)
CREATE TRIGGER logs_ai AFTER INSERT ON logs BEGIN
    INSERT INTO logs_fts(rowid, app_name, source_alias, message) 
    VALUES (new.id, new.app_name, new.source_alias, new.message);
END;

CREATE TRIGGER logs_ad AFTER DELETE ON logs BEGIN
    INSERT INTO logs_fts(logs_fts, rowid, app_name, source_alias, message) 
    VALUES('delete', old.id, old.app_name, old.source_alias, old.message);
END;

CREATE TRIGGER logs_au AFTER UPDATE ON logs BEGIN
    INSERT INTO logs_fts(logs_fts, rowid, app_name, source_alias, message) 
    VALUES('delete', old.id, old.app_name, old.source_alias, old.message);
    INSERT INTO logs_fts(rowid, app_name, source_alias, message) 
    VALUES (new.id, new.app_name, new.source_alias, new.message);
END;

CREATE TABLE host_aliases (
    ip TEXT PRIMARY KEY,
    alias TEXT NOT NULL,
    notes TEXT,
    created_at DATETIME NOT NULL
);

CREATE TABLE storage_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at DATETIME NOT NULL,       -- not unique: hourly + manual-prune samples can land in the same second
    db_size_bytes INTEGER NOT NULL,      -- logs.db + logs.db-wal + logs.db-shm
    disk_free_bytes INTEGER NOT NULL,    -- Free space on /data mount
    disk_total_bytes INTEGER NOT NULL,   -- Total capacity of /data mount
    total_logs_count INTEGER NOT NULL
);

CREATE INDEX idx_storage_metrics_time ON storage_metrics(recorded_at DESC);

CREATE TABLE ai_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    source_alias TEXT NOT NULL,
    app_name TEXT NOT NULL,
    log_count INTEGER NOT NULL,
    user_context TEXT,
    model TEXT NOT NULL,
    prompt_sent TEXT NOT NULL,
    response_text TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    tokens_thoughts INTEGER NOT NULL DEFAULT 0,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    system_prompt TEXT
);

CREATE TABLE admin_auth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

CREATE TABLE system_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at DATETIME NOT NULL,
    is_encrypted BOOLEAN DEFAULT 0
);

```

### 2.2 Retention Pruning
Daily task runs iterative batch pruning to prevent WAL expansion and lock contention:
1. Loop batch deletions until no matching rows remain:
   `DELETE FROM logs WHERE id IN (SELECT id FROM logs WHERE timestamp < datetime('now', '-' || :retention_days || ' days') LIMIT 5000);`
2. Execute FTS5 index compaction:
   `INSERT INTO logs_fts(logs_fts) VALUES('optimize');`
3. Checkpoint and truncate the WAL:
   `PRAGMA wal_checkpoint(TRUNCATE);`

### 2.3 Storage Metrics & Disk Monitoring
- **Sampling Strategy:** Background worker samples metrics hourly and immediately following any manual/automated prune event.
  - Compute total database disk footprint via `os.path.getsize()` across `/data/logs.db`, `/data/logs.db-wal`, and `/data/logs.db-shm`.
  - Read host mount capacity and free space using `shutil.disk_usage("/data")`.
  - Record snapshot into `storage_metrics` table.
- **Metrics Retention:** Prune records from `storage_metrics` older than 30 days during the daily retention cleanup cycle.

---

## 3. Ingestion & In-Memory Pipeline

```
[Syslog Listener (UDP/TCP 1514)] ──┐
├──> [Keyed Multiline Assembler] ──> [asyncio.Queue(10000)] ──> [SQLite Batch Writer (stdlib sqlite3 + asyncio.to_thread)]
[Docker Tailer (Socket/Proxy)]  ──┘

```

* **Syslog Ingestion:** Async UDP and TCP on port `1514` (configurable via `SYSLOG_PORT`). RFC 3164 and RFC 5424 parsing with RFC 6587 octet-counted and newline-delimited TCP framing. Unparseable messages default to severity 6 (Info) preserving raw content.
* **Docker Ingestion:** Connects via `DOCKER_HOST`. Tails running containers and listens for Docker lifecycle events (`start`/`die`). Sets `source_alias="docker"` (or value of `DOCKER_SOURCE_ALIAS`) and `app_name=container_name`.
* **Keyed Multiline Assembler:** Buffers continuation lines (e.g., lines starting with whitespace, `\t`, `Caused by:`, `Traceback`) mapped by stream key:
  * Syslog streams: `stream_key = f"{source_ip}:{app_name}"`
  * Docker streams: `stream_key = f"docker:{container_id}"`
  * Flushes buffered lines into a single log entry on a **150ms per-stream timeout** or upon receiving a new RFC-compliant timestamped header for that stream.
* **Raw Log Storage:** Logs are committed to SQLite in their original unredacted format. Redaction is not applied at ingestion.
* **Bounded Buffer & Batch Flusher:** `asyncio.Queue(maxsize=10000)`. If saturated, increment atomic `dropped_logs_total` counter. Flusher commits batches to SQLite every 2000ms or when the batch reaches 5000 records. The flusher must aggressively drain pending items using `queue.get_nowait()` during each cycle to maximize throughput and prevent artificial bottlenecks.

---

## 4. On-Demand AI Analysis Engine

AI interactions are strictly user-initiated. No background workers or automated pipelines dispatch logs to external LLMs.

### 4.1 Log Selection & Context Enrichment Workflow
1. **Selection:** User selects one or multiple log entries in the UI across single or multiple hosts. Cross-host log selection is supported. The prompt builder annotates each dispatched log line with its originating host/source alias (`[{timestamp}] [{source_alias}] [{app_name}] {message}`) and aggregates notes for all unique hosts present in the batch.
2. **On-Demand Redaction Pass:** The backend filters selected logs through `redactor.py` (scrubbing tokens, passwords, JWTs, AWS keys) before returning the preview payload to the UI.
3. **Payload Inspection & User Enrichment:**
   - UI opens an analysis modal displaying:
     * The scrubbed, redacted text preview exactly as it will be dispatched to the LLM.
     * Active provider and model name.
     * Estimated token count.
     * Free-text user context input field.
4. **Execution Gate:** Outbound API calls occur **only** when the user explicitly clicks **"Run Analysis"**.

### 4.2 AI Provider Abstraction
Unified client supporting Google Gemini (`google-genai` SDK) and OpenAI-compatible endpoints (`openai` SDK, configurable `base_url` for Ollama/vLLM/LocalAI).
- **Model Configuration:** Configurable default model per provider (e.g., `gemini-3.7-flash`, `gpt-4o`, `llama3.2`), with an optional per-request override in the UI modal.

- **Prompt Construction:**
  - System prompt establishes role as an expert systems engineer and Linux/Docker administrator.
  - Context includes:
    * System metadata (Host alias, container/app name).
    * Sequenced log block in chronological order.
    * User-provided notes/context (if present).
  - Model generates a structured Markdown response containing:
    1. **Summary:** 1–2 sentence overview of the issue.
    2. **Root Cause Analysis:** Explanation of why the event occurred.
    3. **Actionable Remediation:** Step-by-step commands, configuration fixes, or debugging steps.
  - **Audit Logging:** Every manual request is recorded in `ai_audit_log` (prompt, user context, response and tokens used).


---

## 5. Security & Authentication

* **Password Hashing:** `argon2id` via `argon2-cffi` (single library - do not also add `pwdlib`).
* **First-Run Setup Lockout:** `/api/auth/setup` is only accessible when the `admin_auth` table is empty. If an admin record exists, `/api/auth/setup` immediately returns `403 Forbidden`.
* **Session Security:** Cryptographically signed, HTTP-only, `SameSite=Lax` session cookies. No JWTs in browser storage.
* **Rate Limiting:** In-memory sliding window on `/api/auth/login` (5 failed attempts per IP per minute).
* **Secrets at Rest:** API keys encrypted with `cryptography.fernet`. Master encryption key stored at `/data/.secret_key` (generated automatically on first boot with `0600` permissions).
* **CLI Password Recovery:** Single-command rescue executable inside container:
```bash
python -m app.cli reset-admin --password <new_password>

```



---

## 6. API Contracts

| Method | Endpoint | Purpose | Payload / Parameters |
| --- | --- | --- | --- |
| **Authentication** |  |  |  |
| `POST` | `/api/auth/setup` | First-time admin creation (returns `403` if already configured) | `{"password": "..."}` |
| `POST` | `/api/auth/login` | Session login (rate-limited) | `{"password": "..."}` |
| `POST` | `/api/auth/logout` | Invalidate session cookie | None |
| `GET` | `/api/auth/status` | Read setup status and current session authentication state | None (returns `{"setup_required": bool, "authenticated": bool}`) |
| `POST` | `/api/auth/password` | Update admin password for authenticated session | `{"current_password": "...", "new_password": "..."}` |
| **Log Management** |  |  |  |
| `GET` | `/api/logs` | Search & filter logs. Severity filter follows RFC 5424 numeric ordering directly, where lower numbers are more severe (`WHERE severity <= :severity_max`, e.g., `severity_max=3` returns Emergency(0) through Error(3)) | `query`, `severity_max` (0-7), `app_name`, `source`, `from`, `to`, `limit`, `offset` |
| `GET` | `/api/logs/stream` | Real-time Server-Sent Events (SSE) | `severity_max`, `source`, `app_name` |
| `GET` | `/api/logs/facets` | Fetch all distinct sources, apps, and their mappings | None |
| `GET` | `/api/logs/{id}/context` | Fetch surrounding context lines scoped to the same `source_alias` and `app_name` | Query params: `lines=10`, `same_app: bool` |
| **On-Demand AI Engine** |  |  |  |
| `POST` | `/api/ai/preview` | Generate redacted preview and token estimate | `{"log_ids": [101, 102]}` |
| `POST` | `/api/ai/diagnose` | Execute user-confirmed AI diagnosis | `{"log_ids": [101, 102], "user_context": "...", "provider": "gemini|openai", "model": "..."}` |
| `GET` | `/api/ai/audit` | Fetch historical AI queries & token usage | Query params: `limit`, `offset` |
| `DELETE` | `/api/ai/audit/{audit_id}` | Delete a single AI audit record | None |
| `DELETE` | `/api/ai/audit` | Clear all AI audit records | None |
| **Host Aliases** |  |  |  |
| `GET` | `/api/aliases` | List IP-to-Host mappings | None |
| `POST` | `/api/aliases` | Upsert host alias mapping | `{"ip": "...", "alias": "...", "notes": "..."}` |
| `DELETE` | `/api/aliases/{ip}` | Remove host alias | None |
| **Settings** |  |  |  |
| `GET` | `/api/settings` | Read application configuration (keys masked) | None |
| `POST` | `/api/settings` | Update settings (encrypted at rest) | `{"ai_provider": "...", "ai_model": "...", "ai_api_key": "...", "ai_base_url": "...", "retention_days": 14}` |
| **System & Maintenance** |  |  |  |
| `GET` | `/api/health` | Container healthcheck & queue metrics | Returns DB status, queue depth, dropped log count |
| `POST` | `/api/maintenance/prune` | Trigger manual retention purge | None |
| `GET` | `/api/system/storage` | Fetch live disk usage & 30-day history | `{"db_size_bytes": 18247000000, "disk_free_bytes": 450000000000, "disk_total_bytes": 1000000000000, "history": [{"recorded_at": "...", "db_size_bytes": 18247000000, "disk_free_bytes": 450000000000, "disk_total_bytes": 1000000000000, "total_logs_count": 26000000}]}` |


---

## 7. Frontend Specification (React + Vite + Tailwind)

* **Console Viewer (Live Stream):**
  * Virtualized log list (`@tanstack/react-virtual`) capable of handling 50k+ lines without DOM lag.
  * Real-time Server-Sent Events (SSE) stream with auto-scroll and pause-on-scroll-up detection.
  * Severity color-coded badges (Emergency/Alert/Crit/Error = Red, Warning = Yellow, Notice/Info/Debug = Slate/Blue).
  * Checkbox multi-select mode with a floating action bar: `"Run Analysis (N)"` or `"Inspect (N) Selected Logs with AI"`.

* **Selection & Previews:**
  * Multi-selection supports entries across single or multiple hosts with clear host attribution.
  * Buffer selection controls ("Select All" / "Deselect All") in the UI to quickly select all logs currently loaded in the client-side browser buffer (capped at the 200-log AI analysis ceiling) or clear selection.
  * AI analysis modal displays the scrubbed/redacted text returned by /api/ai/preview.
  * Add a Model Selection dropdown inside the AI modal and Settings panel.
  * Document that severity sliders/pills map to RFC 5424 numerical priorities (0 = Emergency … 7 = Debug).

* **Search & Filter Bar:**
  * Full-text search input with SQLite FTS5 syntax support.
  * Timestamp / date-range picker.
  * Dropdown filters for Host Alias (`source_alias`), Source IP, and Container/App Name (`app_name`).
  * Severity threshold filter slider/pills (e.g., `<= Error`).


* **Log Detail & Context Inspector:**
  * Slide-over panel displaying parsed metadata, source IP, facility, exact timestamp, and raw unparsed syslog payload.
  * One-click action to load surrounding context logs (previous/subsequent 10 entries around the selected record).
  * Direct action buttons: `"Add Host Alias"` (if unmapped) and `"Select for AI Analysis"`.


* **On-Demand AI Analysis Modal / Slide-Over:**
  * Redacted log preview displaying the exact text to be dispatched (with one-click copy).
  * Real-time token counter.
  * Free-text user context textarea to provide situational background (e.g., recent system updates, topology changes).
  * Markdown-rendered analysis display (Summary, Root Cause, Remediation steps with copyable code/command blocks).


* **Host Alias Manager:**
  * Dedicated table to manage IP-to-Hostname mappings (e.g., `192.168.1.1` $\rightarrow$ `OPNsense Firewall`).
  * Quick-add prompts for newly detected, unmapped IP addresses.


* **Settings & Audit Panel:**
  * Encrypted API key management (Google Gemini, OpenAI / custom OpenAI-compatible endpoint like Ollama/vLLM).
  * **Storage & Retention Dashboard:**
    * Log retention slider (1–30 days by default, configurable up to MAX_RETENTION_DAYS, default 14 days) with manual `"Purge Expired Logs Now"` trigger.
    * **Current Storage Card:** Dual-metric display showing active Database Footprint (MB/GB) alongside a visual progress bar for Available Mount Disk Space.
    * **30-Day Storage Trend Chart:** Compact line/area chart (via `recharts` or lightweight SVG) plotting DB disk footprint and total log volume over the past 30 days.
    * Real-time optimistic UI update on manual purge showing immediate reclaimed space.
  * Interactive AI Audit Log table showing historical prompt dispatches, user notes, responses and token consumption.



* **Build & Asset Distribution:**
* Vite configured to build production static assets directly into `backend/app/static/`.
* FastAPI configured to serve static assets with an SPA fallback to `index.html`.

---

## 8. Deployment & Container Specification

### 8.1 Dockerfile Requirements

* **Stage 1 (Frontend):** Node 20 alpine builds React SPA (`npm run build`).
* **Stage 2 (Runtime):** Python 3.12-slim with `tini`, `curl`, `gosu`. Copies backend and frontend build.
* **Healthcheck:**
```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8080}/api/health || exit 1

```


* **Ports:** `8080/tcp` (Web UI & API), `1514/udp` and `1514/tcp` (Syslog, configurable via `SYSLOG_PORT`).
* **Volumes:** `/data` (Storage), `/var/run/docker.sock` (Host socket or omitted if using socket-proxy).

### 8.2 Unraid Template Directives

* Map `/data` to direct cache-pool appdata: `/mnt/cache/appdata/logshed` (avoids Unraid FUSE `shfs` locking/mmap issues on SQLite WAL and prevents spinning up array parity disks).
* Map host port `1514` (UDP/TCP) to container `1514`.

### 8.3 Multi-Host & Remote Docker Deployment Architecture

LogShed supports heterogeneous, multi-host homelab configurations across bare metal, virtual machines, and multiple Docker hosts:

1. **Local Host (Direct Socket Mount):**
   Mount `/var/run/docker.sock:/var/run/docker.sock:ro` into the LogShed container. Default `DOCKER_HOST=unix:///var/run/docker.sock` and `DOCKER_SOURCE_ALIAS=docker`.

2. **Single Remote Docker Host (Socket Proxy):**
   Connect directly to a remote Docker daemon or Docker socket proxy (such as `tecnativa/docker-socket-proxy`) without mounting any local socket:
   ```env
   DOCKER_HOST=tcp://192.168.1.50:2375
   DOCKER_SOURCE_ALIAS=remote-docker
   ```

3. **Multi-Host Docker Environments (Syslog Forwarding):**
   For environments running containers across multiple nodes (e.g. Proxmox LXC/VMs, multiple physical servers), configure each remote Docker daemon's native syslog log driver in `/etc/docker/daemon.json` to forward container logs to LogShed on port `1514`:
   ```json
   {
     "log-driver": "syslog",
     "log-opts": {
       "syslog-address": "udp://<logshed-ip>:1514",
       "tag": "{{.Name}}"
     }
   }
   ```
   Or via TCP:
   ```json
   {
     "log-driver": "syslog",
     "log-opts": {
       "syslog-address": "tcp://<logshed-ip>:1514",
       "tag": "{{.Name}}"
     }
   }
   ```
   The remote host's IP or hostname is automatically attributed by LogShed's Syslog collector, and the container name is mapped to `app_name` via the tag.

