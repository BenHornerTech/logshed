# LogShed 🪵🛖

A lightweight, self-hosted homelab log aggregator and syslog server featuring real-time log ingestion, fast full-text search (SQLite + FTS5), and user-directed AI incident analysis.

---

## Features

- **Platform-Agnostic & Lightweight**: Single container, single process with zero heavy external database dependencies (uses standard library SQLite with WAL mode + FTS5).
- **Dual Ingestion**:
  - **Syslog**: Async UDP & TCP on port `1514` (configurable via `SYSLOG_PORT`) supporting RFC 3164 and RFC 5424 formats, RFC 6587 octet-counted and newline-delimited TCP framing, with automatic client IP tagging and hostname resolution.
  - **Docker Engine API**: Direct container tailing via local Unix socket (`/var/run/docker.sock`) or remote Docker host / proxy (`tcp://<host>:2375`) without external Docker SDK bloat.
- **Keyed Multiline Assembly & Raw Log Fidelity**: Assembles stack traces, tracebacks, and multiline logs cleanly on a per-stream basis while preserving original raw message payloads.
- **Fast Full-Text Search**: Instant search and filtering across hosts, containers, severity levels, and time windows.
- **Configurable Retention**: Automated background pruning with SQLite freelist page reuse and storage trend metrics. Supports 1 to 30 days of retention by default (default: 14 days), with configurable maximum via `MAX_RETENTION_DAYS`.

---

## Quickstart

### Docker Compose

```yaml
services:
  logshed:
    image: ghcr.io/yourusername/logshed:latest
    container_name: logshed
    restart: unless-stopped
    ports:
      - "8080:8080"        # Web UI and API
      - "1514:1514/udp"    # Syslog UDP
      - "1514:1514/tcp"    # Syslog TCP
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=UTC
      - PORT=8080
      - DOCKER_HOST=unix:///var/run/docker.sock
      - DOCKER_SOURCE_ALIAS=docker
    volumes:
      - ./data:/data
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

### Docker Run

```bash
docker run -d \
  --name logshed \
  --restart unless-stopped \
  -p 8080:8080 \
  -p 1514:1514/udp \
  -p 1514:1514/tcp \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=UTC \
  -e DOCKER_HOST=unix:///var/run/docker.sock \
  -e DOCKER_SOURCE_ALIAS=docker \
  -v /path/to/appdata:/data \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  ghcr.io/yourusername/logshed:latest
```

### Unraid Deployment

An official Unraid Community Applications template is provided in `unraid-template.xml`.

> **Important Storage Recommendation for Unraid**:
> When configuring the container path `/data`, always map it to a **direct cache pool path** (such as `/mnt/cache/appdata/logshed` or `/mnt/<pool_name>/appdata/logshed`).
> **Avoid using `/mnt/user/appdata/logshed`**: Unraid's `/mnt/user/` FUSE user-share layer (`shfs`) does not reliably support POSIX shared memory (`mmap`) or SQLite advisory file locking under concurrent WAL checkpoint operations. Pointing directly to your SSD cache pool bypasses FUSE, guarantees native POSIX locking, and prevents spinning up parity array disks.

---

## Configuration

### Environment Variables

The following environment variables are supplied at container boot:

| Variable | Default | Description |
|---|---|---|
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | Docker endpoint URI (`unix:///path/to/docker.sock` or `tcp://host:port`). |
| `DOCKER_SOURCE_ALIAS` | `docker` | Attribution label applied to Docker container logs in the UI and database. |
| `DOCKER_EXCLUDE_CONTAINERS` | *(empty)* | Comma-separated list of container names or IDs to exclude from log tailing. |
| `PUID` | `1000` | Process user ID used by the internal non-root `appuser`. |
| `PGID` | `1000` | Process group ID used by the internal non-root `appuser`. |
| `PORT` | `8080` | HTTP port for the web dashboard and REST API. |
| `SYSLOG_PORT` | `1514` | Syslog UDP and TCP listening port (1-65535). |
| `TZ` | `UTC` | Container timezone. |
| `COOKIE_SECURE` | `false` | Set to `true` if running behind an SSL reverse proxy that does not send `X-Forwarded-Proto`. |
| `LOGSHED_SECRET_KEY` | *(auto-generated)* | 32-byte URL-safe base64 key for encrypting runtime settings at rest. |
| `MAX_RETENTION_DAYS` | `30` | Maximum log retention period in days (minimum 1). Configures the upper bound in the UI slider. Advanced users can increase this to retain logs for longer periods. |

> **Note**: Sensitive credentials (such as LLM API keys) and retention policies are configured entirely at runtime in the **Settings** panel within the web interface, encrypted at rest using AES-128-CBC / HMAC-SHA256 (Fernet).
>
> **Log Retention**: Retention is configurable between 1 and `MAX_RETENTION_DAYS` (default: 14 days, max: 30 days). Advanced users can increase `MAX_RETENTION_DAYS` via environment variable if homelab hardware and storage capacity allow. Retention pruning purges expired logs, compacts the FTS5 search index, and checkpoints the WAL without holding exclusive offline database locks; SQLite automatically reuses free database pages for incoming logs without requiring an intrusive offline VACUUM.

>
> **Log Redaction & AI Notice**: LogShed includes automatic server-side scrubbing to redact common secrets (passwords, bearer tokens, API keys, private keys, and connection strings) before dispatching prompts to LLM providers. However, automated credential scrubbing operates on a best-effort basis and may not catch every sensitive token or secret. Please review the editable prompt in the UI before sending—you are responsible for the contents and sensitive data you transmit to external AI providers.
>
> **Raw Logs & Timestamp Presentation**: In the LogShed console, every log row displays a dedicated timestamp column reflecting the arrival time or parsed syslog packet header time. Inside the log message payload itself, you may notice that some logs display an embedded application timestamp while others do not. This is by design: LogShed's goal is to preserve and display raw, authentic log messages. We intentionally avoid aggressive or destructive regex stripping of message bodies, as attempting to strip timestamps from raw lines risks corrupting custom log formats or messing up multiline splitting and stack trace reassembly.

---

## Multi-Host & Homelab Architecture

LogShed accommodates homelab setups ranging from a single server to multi-node clusters.

### 1. Local Host (Direct Unix Socket)
Mount the local Docker socket into the container:
```yaml
environment:
  - DOCKER_HOST=unix:///var/run/docker.sock
  - DOCKER_SOURCE_ALIAS=docker
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
```

### 2. Single Remote Docker Host (Socket Proxy)
Connect to a remote server running Docker or `tecnativa/docker-socket-proxy` over TCP without mounting any local socket:
```yaml
environment:
  - DOCKER_HOST=tcp://192.168.1.50:2375
  - DOCKER_SOURCE_ALIAS=pve-docker
```

### 3. Multi-Host Docker Environments (Syslog Forwarding)
When running multiple servers or nodes (e.g. Proxmox LXC/VMs, secondary servers, Raspberry Pis), configure the Docker daemon on each remote host to forward container logs to LogShed on port `1514`.

Add the following to `/etc/docker/daemon.json` on the remote hosts and restart Docker (`sudo systemctl restart docker`):

**Via UDP (Default):**
```json
{
  "log-driver": "syslog",
  "log-opts": {
    "syslog-address": "udp://<LOGSHED_IP>:1514",
    "tag": "{{.Name}}"
  }
}
```

**Via TCP:**
```json
{
  "log-driver": "syslog",
  "log-opts": {
    "syslog-address": "tcp://<LOGSHED_IP>:1514",
    "tag": "{{.Name}}"
  }
}
```

With this pattern:
- The remote host IP or configured hostname automatically maps to the log source.
- `{{.Name}}` tags each message with the container's friendly name, populating `app_name` in LogShed.
- You can map IP addresses to friendly host names in LogShed's **Host Aliases** settings.

### 4. Network Devices & OS Syslog
Configure your routers (OPNsense, pfSense, UniFi), switches, Proxmox VE nodes, and Linux servers (`rsyslog` / `syslog-ng`) to send standard syslog traffic to LogShed on UDP or TCP port `1514`.

---

## Development

```bash
# Set up Python virtual environment
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# Run backend tests
.venv/bin/pytest backend/tests/ -v

# Run frontend tests & build
cd frontend
npm install
npm test
npm run build
```
