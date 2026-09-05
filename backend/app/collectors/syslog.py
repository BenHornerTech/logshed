"""
Syslog collector module for LogShed.
Supports UDP and TCP on port 1514, parsing RFC 3164 and RFC 5424.
"""

import asyncio
import datetime
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.core.migrations import get_connection
from app.core.pipeline import KeyedMultilineAssembler

logger = logging.getLogger(__name__)

def parse_syslog_message(data: bytes, source_ip: str) -> dict[str, Any]:
    """
    Parse a raw syslog message (bytes) into a structured dict.
    """
    raw_str = data.decode("utf-8", errors="replace").strip("\r\n")
    now = datetime.datetime.now(datetime.timezone.utc)
    
    result = {
        "timestamp": now.isoformat(),
        "received_at": now.isoformat(),
        "source_ip": source_ip,
        "source_alias": source_ip,
        "app_name": "unknown",
        "facility": 1,
        "severity": 6,
        "message": raw_str,
        "raw": raw_str
    }
    
    content = raw_str
    
    # Parse PRI
    pri_match = re.match(r"^<(\d{1,3})>", content)
    if pri_match:
        pri = int(pri_match.group(1))
        result["facility"] = pri // 8
        result["severity"] = pri % 8
        content = content[pri_match.end():]
        
    # Check RFC 5424: version MUST be exactly '1' followed by a space.
    # This prevents misidentifying RFC 3164 messages that start with a digit.
    if content.startswith("1 "):
        content = content[2:]  # skip "1 "
        # RFC 5424 format after version:
        # TIMESTAMP SP HOSTNAME SP APP-NAME SP PROCID SP MSGID SP STRUCTURED-DATA [SP MSG]
        # Parse TIMESTAMP HOSTNAME APP-NAME PROCID MSGID by splitting on first 5 spaces
        header_parts = content.split(" ", 5)
        if len(header_parts) >= 6:
            timestamp, hostname, app_name, procid, msgid = header_parts[:5]
            remainder = header_parts[5]
            
            # Parse structured data (bracket-aware)
            # SD is either "-" (NILVALUE) or one or more [sdid ...] blocks
            if remainder.startswith("-"):
                # NILVALUE structured data
                sd = "-"
                msg = remainder[1:].lstrip(" ")
            elif remainder.startswith("["):
                # Bracket-aware extraction: consume all [...] blocks
                sd_end = 0
                i = 0
                while i < len(remainder) and remainder[i] == "[":
                    # Find matching closing bracket (not escaped)
                    j = i + 1
                    while j < len(remainder):
                        if remainder[j] == "]":
                            sd_end = j + 1
                            break
                        if remainder[j] == "\\" and j + 1 < len(remainder):
                            j += 1  # skip escaped char
                        j += 1
                    i = sd_end
                    # skip optional space between SD elements
                    if i < len(remainder) and remainder[i] == " " and i + 1 < len(remainder) and remainder[i + 1] == "[":
                        i += 1
                sd = remainder[:sd_end]
                msg = remainder[sd_end:].lstrip(" ")
            else:
                # Malformed SD — treat entire remainder as message
                sd = ""
                msg = remainder

            if timestamp != "-":
                result["timestamp"] = timestamp
            if hostname != "-":
                result["hostname"] = hostname
            if app_name != "-":
                result["app_name"] = app_name
            result["message"] = msg
        else:
            # Not enough fields for valid 5424 — treat as unparsed
            pass
    else:
        # RFC 3164
        # Mmm dd HH:MM:SS or Mmm  d HH:MM:SS
        ts_match = re.match(r"^([A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2})\s+", content)
        if ts_match:
            ts_str = ts_match.group(1)
            content = content[ts_match.end():]
            
            # Try to parse timestamp
            try:
                # Add current year since RFC 3164 doesn't include it
                # Handle space-padded day
                ts_str_clean = re.sub(r'\s+', ' ', ts_str)
                dt = datetime.datetime.strptime(f"{now.year} {ts_str_clean}", "%Y %b %d %H:%M:%S")
                parsed_month = dt.month
                # Year boundary heuristic: if parsed month is ahead of current month,
                # the message likely came from the previous year
                if parsed_month > now.month:
                    dt = dt.replace(year=now.year - 1)

                # RFC 3164 timestamps lack timezone information and are emitted in the sender's local time.
                # First, attempt to localize using the host/container configured local timezone.
                local_tz = datetime.datetime.now().astimezone().tzinfo
                dt_utc = dt.replace(tzinfo=local_tz).astimezone(datetime.timezone.utc)
                
                # If the resulting UTC timestamp is in the future compared to arrival time (now),
                # the sender is in a positive timezone ahead of the container's timezone.
                # Compensate for the sender timezone offset difference.
                diff_seconds = (dt_utc - now).total_seconds()
                if diff_seconds > 60:
                    offset_hours = round(diff_seconds / 3600)
                    dt_utc -= datetime.timedelta(hours=offset_hours)
                
                # Preserve microsecond arrival precision for proper sub-second ordering
                dt_utc = dt_utc.replace(microsecond=now.microsecond)
                result["timestamp"] = dt_utc.isoformat()
            except ValueError:
                pass
                
            parts = content.split(" ", 1)
            if len(parts) == 2:
                hostname = parts[0]
                content = parts[1]
                if hostname and hostname != "-":
                    result["hostname"] = hostname
                
                app_match = re.match(r"^([^:\s]+?)(?:\[\d+\])?:\s*(.*)", content)
                if app_match:
                    result["app_name"] = app_match.group(1)
                    result["message"] = app_match.group(2)
                else:
                    app_match2 = re.match(r"^([^:\s]+)\s+(.*)", content)
                    if app_match2:
                        result["app_name"] = app_match2.group(1)
                        result["message"] = app_match2.group(2)
                    else:
                        result["message"] = content
                        
    return result


_active_caches: list["AliasCache"] = []

def reload_active_alias_caches() -> None:
    """Reload all active in-memory alias caches immediately."""
    for cache in list(_active_caches):
        cache.load_aliases()


class AliasCache:
    """
    Preloaded in-memory alias cache. Bulk-loads all host_aliases from the database
    on startup, then refreshes every refresh_interval seconds via a background task.
    Lookups are zero-cost dict reads — no DB I/O per message.
    """

    def __init__(self, db_path: str | Path, refresh_interval: float = 60.0):
        self._db_path = Path(db_path)
        self._refresh_interval = refresh_interval
        self._aliases: dict[str, str] = {}
        self._lock = threading.Lock()
        self._refresh_task: asyncio.Task | None = None
        if self not in _active_caches:
            _active_caches.append(self)

    def resolve(self, source_ip: str) -> str:
        """Look up source_ip in the preloaded alias map. O(1) dict lookup."""
        with self._lock:
            return self._aliases.get(source_ip, source_ip)

    def load_aliases(self) -> None:
        """Synchronous: bulk-load all aliases from host_aliases table."""
        new_aliases: dict[str, str] = {}
        try:
            conn = get_connection(self._db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT ip, alias FROM host_aliases")
                for row in cursor.fetchall():
                    new_aliases[row[0]] = row[1]
            finally:
                conn.close()
        except sqlite3.OperationalError:
            # Table might not exist yet during early startup
            pass
        except Exception as e:
            logger.error(f"Error loading alias cache: {e}")
            return  # Keep existing cache on error

        with self._lock:
            self._aliases = new_aliases

    async def start(self) -> None:
        """Start the periodic refresh background task."""
        # Initial synchronous load
        await asyncio.to_thread(self.load_aliases)
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        """Stop the periodic refresh task."""
        if self in _active_caches:
            _active_caches.remove(self)

        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except (asyncio.CancelledError, Exception):
                pass
            self._refresh_task = None

    async def _refresh_loop(self) -> None:
        """Background loop that reloads aliases every refresh_interval seconds."""
        while True:
            try:
                await asyncio.sleep(self._refresh_interval)
                await asyncio.to_thread(self.load_aliases)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error refreshing alias cache: {e}")


def resolve_alias(source_ip: str, db_path: str | Path) -> str:
    """
    Legacy synchronous alias lookup for CLI and backward compatibility.
    For high-throughput syslog ingestion, use AliasCache.resolve() instead.
    """
    try:
        conn = get_connection(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT alias FROM host_aliases WHERE ip = ?", (source_ip,))
            row = cursor.fetchone()
            if row:
                return row[0]
        finally:
            conn.close()
    except Exception:
        pass
    return source_ip


class SyslogUDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, assembler: KeyedMultilineAssembler, alias_cache: AliasCache):
        self.assembler = assembler
        self.alias_cache = alias_cache
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        logger.info("Syslog UDP Server started")

    def datagram_received(self, data: bytes, addr: tuple[str, int]):
        source_ip = addr[0]
        asyncio.ensure_future(self.process_message(data, source_ip))
        
    async def process_message(self, data: bytes, source_ip: str):
        try:
            parsed = parse_syslog_message(data, source_ip)
            # Zero-cost in-memory lookup — no DB I/O
            source_alias = self.alias_cache.resolve(source_ip)
            if source_alias == source_ip:
                hostname = parsed.get("hostname")
                if hostname and hostname not in ("-", "unknown"):
                    source_alias = hostname
            parsed["source_alias"] = source_alias
            stream_key = f"{source_ip}:{parsed['app_name']}"
            await self.assembler.feed(stream_key, parsed)
        except Exception as e:
            logger.error(f"Error processing UDP syslog message: {e}")


MAX_TCP_BUFFER = 65536  # 64 KB limit to prevent unbounded memory growth / OOM DoS


class SyslogTCPProtocol(asyncio.Protocol):
    def __init__(self, assembler: KeyedMultilineAssembler, alias_cache: AliasCache):
        self.assembler = assembler
        self.alias_cache = alias_cache
        self.buffer = b""
        self.peername = None
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        self.peername = transport.get_extra_info('peername')
        logger.debug(f"Syslog TCP connection from {self.peername}")

    def data_received(self, data: bytes):
        self.buffer += data
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if line:
                source_ip = self.peername[0] if self.peername else "unknown"
                asyncio.ensure_future(self.process_message(line, source_ip))

        if len(self.buffer) > MAX_TCP_BUFFER:
            logger.warning(
                f"Syslog TCP buffer exceeded {MAX_TCP_BUFFER} bytes without newline from {self.peername}. Closing connection."
            )
            self.buffer = b""
            if self.transport:
                self.transport.close()
                
    async def process_message(self, data: bytes, source_ip: str):
        try:
            parsed = parse_syslog_message(data, source_ip)
            # Zero-cost in-memory lookup — no DB I/O
            source_alias = self.alias_cache.resolve(source_ip)
            if source_alias == source_ip:
                hostname = parsed.get("hostname")
                if hostname and hostname not in ("-", "unknown"):
                    source_alias = hostname
            parsed["source_alias"] = source_alias
            stream_key = f"{source_ip}:{parsed['app_name']}"
            await self.assembler.feed(stream_key, parsed)
        except Exception as e:
            logger.error(f"Error processing TCP syslog message: {e}")

    def connection_lost(self, exc):
        logger.debug(f"Syslog TCP connection lost from {self.peername}")


class SyslogServer:
    def __init__(self, assembler: KeyedMultilineAssembler, db_path: str | Path, host: str = '0.0.0.0', port: int = 1514):
        self.assembler = assembler
        self.db_path = db_path
        self.host = host
        self.port = port
        self.udp_transport = None
        self.tcp_server = None
        self.alias_cache = AliasCache(db_path)

    async def start(self) -> None:
        """Create and start alias cache refresh, then both UDP and TCP transports."""
        loop = asyncio.get_running_loop()

        # Pre-load aliases before starting listeners
        await self.alias_cache.start()
        
        # Start UDP
        try:
            self.udp_transport, _ = await loop.create_datagram_endpoint(
                lambda: SyslogUDPProtocol(self.assembler, self.alias_cache),
                local_addr=(self.host, self.port)
            )
            logger.info(f"Started Syslog UDP server on {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start Syslog UDP server: {e}")

        # Start TCP
        try:
            self.tcp_server = await loop.create_server(
                lambda: SyslogTCPProtocol(self.assembler, self.alias_cache),
                self.host, self.port
            )
            logger.info(f"Started Syslog TCP server on {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start Syslog TCP server: {e}")

    async def stop(self) -> None:
        """Close transports and stop alias cache refresh."""
        await self.alias_cache.stop()

        if self.udp_transport:
            self.udp_transport.close()
            
        if self.tcp_server:
            self.tcp_server.close()
            await self.tcp_server.wait_closed()
            
        logger.info("Syslog servers stopped")

