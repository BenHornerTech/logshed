#!/bin/sh
set -e

# Read PUID and PGID, default to 1000
PUID=${PUID:-1000}
PGID=${PGID:-1000}

# Update or create appuser group with PGID
if getent group appuser >/dev/null 2>&1; then
    groupmod -o -g "$PGID" appuser
elif getent group "$PGID" >/dev/null 2>&1; then
    groupmod -o -g "$PGID" appuser 2>/dev/null || true
else
    groupadd -o -g "$PGID" appuser
fi

# Update or create appuser user with PUID and PGID
if getent passwd appuser >/dev/null 2>&1; then
    usermod -o -u "$PUID" -g "$PGID" appuser
elif getent passwd "$PUID" >/dev/null 2>&1; then
    usermod -o -u "$PUID" -g "$PGID" appuser 2>/dev/null || true
else
    useradd -o -u "$PUID" -g "$PGID" -d /app -s /bin/sh appuser
fi

# Dynamically detect Docker socket path from DOCKER_HOST (fallback to /var/run/docker.sock)
DOCKER_SOCKET_PATH="/var/run/docker.sock"
if [ -n "$DOCKER_HOST" ]; then
    case "$DOCKER_HOST" in
        unix://*)
            DOCKER_SOCKET_PATH="${DOCKER_HOST#unix://}"
            ;;
    esac
fi

# Detect socket GID and grant appuser access
if [ -S "$DOCKER_SOCKET_PATH" ] || [ -e "$DOCKER_SOCKET_PATH" ]; then
    DOCKER_GID=$(stat -c '%g' "$DOCKER_SOCKET_PATH" 2>/dev/null || stat -f '%g' "$DOCKER_SOCKET_PATH" 2>/dev/null)
    if [ -n "$DOCKER_GID" ] && [ "$DOCKER_GID" != "0" ]; then
        if ! getent group "$DOCKER_GID" >/dev/null 2>&1; then
            groupadd -o -g "$DOCKER_GID" docker-sock-group 2>/dev/null || true
        fi
        DOCKER_GROUP=$(getent group "$DOCKER_GID" | cut -d: -f1)
        if [ -n "$DOCKER_GROUP" ]; then
            usermod -aG "$DOCKER_GROUP" appuser 2>/dev/null || true
        fi
    fi
fi

# Ensure /data exists and is owned by appuser
mkdir -p /data
chown appuser:appuser /data

# Default port
PORT=${PORT:-8080}

# Execute custom command or default single-worker uvicorn process with gosu privilege drop
if [ $# -gt 0 ]; then
    exec gosu appuser tini -- "$@"
else
    exec gosu appuser tini -- uvicorn app.main:app --app-dir /app/backend --host 0.0.0.0 --port "$PORT" --workers 1 --no-access-log
fi
