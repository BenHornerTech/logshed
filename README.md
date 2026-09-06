# LogShed 🪵🛖

A lightweight, self-hosted homelab log aggregator and syslog server featuring real-time log ingestion, fast full-text search (SQLite + FTS5), and user-directed AI incident analysis.

---

## Features

- **Platform-Agnostic & Lightweight**: Single container, single process with zero heavy external database dependencies (uses standard library SQLite with WAL mode + FTS5).
- **Dual Ingestion**:
  - **Syslog**: Async UDP & TCP on port `1514` supporting RFC 3164 and RFC 5424 formats with automatic client IP tagging and hostname resolution.
  - **Docker Engine API**: Direct container tailing via local Unix socket (`/var/run/docker.sock`) or remote Docker host / proxy (`tcp://<host>:2375`) without external Docker SDK bloat.
- **Keyed Multiline Assembly**: Assembles stack traces, tracebacks, and multiline logs cleanly on a per-stream basis.
- **Fast Full-Text Search**: Instant search and filtering across hosts, containers, severity levels, and time windows.
- **On-Demand AI Analysis**: User-initiated troubleshooting powered by your choice of LLM (OpenAI, Google Gemini, Ollama, LocalAI, etc.) with automatic client-side credential redaction before dispatch.
- **Configurable Retention**: Automated background pruning with SQLite page vacuuming and storage trend metrics. Supports 1 to 365 days of retention (default: 30 days).

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
| `TZ` | `UTC` | Container timezone. |
| `COOKIE_SECURE` | `false` | Set to `true` if running behind an SSL reverse proxy that does not send `X-Forwarded-Proto`. |
| `LOGSHED_SECRET_KEY` | *(auto-generated)* | 32-byte URL-safe base64 key for encrypting runtime settings at rest. |

> **Note**: Sensitive credentials (such as LLM API keys) and retention policies are configured entirely at runtime in the **Settings** panel within the web interface, encrypted at rest using AES-128-CBC / HMAC-SHA256 (Fernet).
>
> **Log Retention Disclaimer**: Retention is configurable between 1 and 365 days (default: 30 days). While extending retention up to 365 days is permitted, homelab users should consider hardware and performance implications: storing up to a year of logs substantially increases the SQLite database disk footprint and may increase query latencies on resource-constrained homelab hardware (such as Raspberry Pis or low-power mini PCs).

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
