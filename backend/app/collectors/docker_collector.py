"""
Docker container log collector for Homelab Log Hub.

Connects via DOCKER_HOST (unix:///var/run/docker.sock or tcp://proxy:2375)
using httpx (with HTTPTransport(uds=...) for Unix sockets).
Monitors Docker lifecycle events (start/die) and streams container stdout/stderr.
Routes parsed logs through the shared KeyedMultilineAssembler into the shared
asyncio.Queue — no separate queue or assembler.
"""

import asyncio
import datetime
import json
import logging
import os
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.core.pipeline import KeyedMultilineAssembler

logger = logging.getLogger(__name__)

# Docker Engine API version to target
DOCKER_API_VERSION = "v1.43"

# Supervisor backoff parameters
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 60.0
_BACKOFF_FACTOR = 2.0


def _parse_docker_host() -> tuple[str, Optional[str]]:
    """
    Parse DOCKER_HOST env var into (base_url, uds_path).

    Returns:
        (base_url, uds_path):
            - For unix sockets: ("http://localhost", "/var/run/docker.sock")
            - For tcp: ("http://proxy:2375", None)
    """
    docker_host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")

    if docker_host.startswith("unix://"):
        socket_path = docker_host[len("unix://"):]
        return f"http://localhost/{DOCKER_API_VERSION}", socket_path

    if docker_host.startswith("tcp://"):
        parsed = urlparse(docker_host)
        host = parsed.hostname or "localhost"
        port = parsed.port or 2375
        return f"http://{host}:{port}/{DOCKER_API_VERSION}", None

    # Fallback: treat as TCP address
    return f"http://{docker_host}/{DOCKER_API_VERSION}", None


def _build_client(base_url: str, uds_path: Optional[str]) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient configured for the Docker endpoint."""
    if uds_path:
        transport = httpx.AsyncHTTPTransport(uds=uds_path)
        return httpx.AsyncClient(
            transport=transport,
            base_url=base_url,
            timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
        )
    else:
        return httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
        )


def _parse_docker_log_line(raw_bytes: bytes) -> str:
    """
    Parse a Docker log stream frame.

    Docker multiplexed stream format (when tty=false):
    [8-byte header][payload]
    Header: stream_type(1) + padding(3) + size(4 big-endian)

    When tty=true, the stream is raw text with no framing.
    """
    if len(raw_bytes) >= 8:
        stream_type = raw_bytes[0]
        # stream_type: 0=stdin, 1=stdout, 2=stderr
        if stream_type in (0, 1, 2):
            payload_size = int.from_bytes(raw_bytes[4:8], "big")
            payload = raw_bytes[8:8 + payload_size]
            return payload.decode("utf-8", errors="replace").rstrip("\n\r")

    # Fallback: treat entire bytes as text (tty mode)
    return raw_bytes.decode("utf-8", errors="replace").rstrip("\n\r")


def _make_log_entry(
    container_name: str,
    container_id: str,
    message: str,
    severity: int = 6,
) -> dict:
    """Build a log entry dict compatible with the shared pipeline."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {
        "timestamp": now,
        "received_at": now,
        "source_ip": "docker",
        "source_alias": "unraid-docker",
        "app_name": container_name,
        "facility": 1,
        "severity": severity,
        "message": message,
        "raw": message,
    }


async def _get_running_containers(client: httpx.AsyncClient) -> list[dict]:
    """Fetch currently running containers from the Docker API."""
    try:
        resp = await client.get("/containers/json")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to list Docker containers: {e}")
        return []


async def _get_container_name(client: httpx.AsyncClient, container_id: str) -> str:
    """Inspect a container to get its name."""
    try:
        resp = await client.get(f"/containers/{container_id}/json")
        resp.raise_for_status()
        info = resp.json()
        name = info.get("Name", container_id)
        # Docker names start with '/'
        return name.lstrip("/")
    except Exception:
        return container_id[:12]


async def _tail_container_logs(
    client: httpx.AsyncClient,
    container_id: str,
    container_name: str,
    assembler: KeyedMultilineAssembler,
    cancel_event: asyncio.Event,
) -> None:
    """
    Stream logs for a single container, feeding lines through the assembler.

    Streams from 'now' (tail=0, follow=true) so we only get new log lines.
    """
    stream_key = f"docker:{container_id}"
    url = f"/containers/{container_id}/logs"
    params = {
        "stdout": "true",
        "stderr": "true",
        "follow": "true",
        "tail": "0",
        "timestamps": "false",
    }

    try:
        async with client.stream("GET", url, params=params) as resp:
            resp.raise_for_status()
            buffer = b""
            async for chunk in resp.aiter_bytes():
                if cancel_event.is_set():
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line_bytes, buffer = buffer.split(b"\n", 1)
                    if not line_bytes:
                        continue
                    message = _parse_docker_log_line(line_bytes)
                    if not message:
                        continue
                    entry = _make_log_entry(container_name, container_id, message)
                    await assembler.feed(stream_key, entry)
    except httpx.RemoteProtocolError:
        # Container stopped or connection reset
        logger.debug(f"Log stream ended for container {container_name} ({container_id[:12]})")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        if not cancel_event.is_set():
            logger.warning(f"Log stream error for {container_name}: {e}")


class DockerTailer:
    """
    Supervisor that monitors Docker events and tails container logs.

    Manages lifecycle:
    - On start: enumerate running containers and begin tailing each one.
    - On 'start' event: begin tailing the newly started container.
    - On 'die' event: cancel the tailer task for that container.
    - On disconnect: exponential backoff reconnect.
    """

    def __init__(self, assembler: KeyedMultilineAssembler):
        self._assembler = assembler
        self._running = False
        self._cancel_event = asyncio.Event()
        # container_id -> (task, cancel_event)
        self._tailers: dict[str, tuple[asyncio.Task, asyncio.Event]] = {}

    async def run(self) -> None:
        """
        Main supervisor loop with auto-reconnect backoff.
        """
        self._running = True
        self._cancel_event.clear()
        backoff = _INITIAL_BACKOFF

        while self._running:
            try:
                base_url, uds_path = _parse_docker_host()
                async with _build_client(base_url, uds_path) as client:
                    logger.info("Connected to Docker daemon")
                    backoff = _INITIAL_BACKOFF  # Reset on successful connect

                    # Start tailing all currently running containers
                    await self._attach_running_containers(client)

                    # Stream Docker events for start/die
                    await self._watch_events(client)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not self._running:
                    break
                logger.warning(
                    f"Docker connection lost: {e}. Reconnecting in {backoff:.0f}s..."
                )
                await self._cleanup_tailers()
                await asyncio.sleep(backoff)
                backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF)

    async def stop(self) -> None:
        """Signal graceful shutdown."""
        self._running = False
        self._cancel_event.set()
        await self._cleanup_tailers()
        logger.info("Docker tailer stopped")

    async def _attach_running_containers(self, client: httpx.AsyncClient) -> None:
        """Enumerate running containers and start a log tailer for each."""
        containers = await _get_running_containers(client)
        for c in containers:
            cid = c.get("Id", "")
            names = c.get("Names", [])
            name = names[0].lstrip("/") if names else cid[:12]
            self._start_tailer(client, cid, name)

    def _start_tailer(
        self,
        client: httpx.AsyncClient,
        container_id: str,
        container_name: str,
    ) -> None:
        """Start a log tailer task for a container if not already active."""
        if container_id in self._tailers:
            return

        cancel = asyncio.Event()
        task = asyncio.create_task(
            _tail_container_logs(
                client, container_id, container_name,
                self._assembler, cancel,
            ),
            name=f"docker-tail-{container_name}",
        )
        self._tailers[container_id] = (task, cancel)
        logger.info(f"Started tailing container {container_name} ({container_id[:12]})")

    async def _stop_tailer(self, container_id: str) -> None:
        """Stop and clean up a single container tailer."""
        if container_id not in self._tailers:
            return

        task, cancel = self._tailers.pop(container_id)
        cancel.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _cleanup_tailers(self) -> None:
        """Stop all active tailer tasks."""
        ids = list(self._tailers.keys())
        for cid in ids:
            await self._stop_tailer(cid)

    async def _watch_events(self, client: httpx.AsyncClient) -> None:
        """
        Stream Docker events, reacting to 'start' and 'die' container events.

        Uses the /events endpoint with server-sent JSON lines.
        """
        params = {
            "type": "container",
            "filters": json.dumps({"event": ["start", "die"]}),
        }

        async with client.stream("GET", "/events", params=params) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if self._cancel_event.is_set():
                    return

                if not line.strip():
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                action = event.get("Action", "")
                actor = event.get("Actor", {})
                cid = actor.get("ID", event.get("id", ""))
                attrs = actor.get("Attributes", {})
                name = attrs.get("name", cid[:12])

                if action == "start":
                    logger.info(f"Container started: {name} ({cid[:12]})")
                    self._start_tailer(client, cid, name)
                elif action == "die":
                    logger.info(f"Container died: {name} ({cid[:12]})")
                    await self._stop_tailer(cid)
