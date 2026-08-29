"""
Syslog collector module for Homelab Log Hub.
Supports UDP and TCP on port 1514, parsing RFC 3164 and RFC 5424.
"""

import asyncio
import datetime
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.core.pipeline import KeyedMultilineAssembler

logger = logging.getLogger(__name__)

_alias_cache: dict[str, tuple[str, float]] = {}

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
        
    # Check RFC 5424 version digit
    v_match = re.match(r"^(\d+)\s+", content)
    if v_match:
        content = content[v_match.end():]
        # TIMESTAMP HOSTNAME APP-NAME PROCID MSGID STRUCTURED-DATA [MSG]
        parts = content.split(" ", 6)
        if len(parts) >= 6:
            timestamp, hostname, app_name, procid, msgid, sd = parts[:6]
            msg = parts[6] if len(parts) > 6 else ""
            if timestamp != "-":
                result["timestamp"] = timestamp
            if app_name != "-":
                result["app_name"] = app_name
            result["message"] = msg
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
                result["timestamp"] = dt.replace(tzinfo=datetime.timezone.utc).isoformat()
            except ValueError:
                pass
                
            parts = content.split(" ", 1)
            if len(parts) == 2:
                hostname = parts[0]
                content = parts[1]
                
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

def resolve_alias(source_ip: str, db_path: str | Path) -> str:
    """
    Look up source_ip in the host_aliases table.
    Caches aliases for 60 seconds.
    """
    now = time.time()
    if source_ip in _alias_cache:
        alias, cached_time = _alias_cache[source_ip]
        if now - cached_time < 60:
            return alias
            
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT alias FROM host_aliases WHERE ip = ?", (source_ip,))
            row = cursor.fetchone()
            if row:
                alias = row[0]
                _alias_cache[source_ip] = (alias, now)
                return alias
    except sqlite3.OperationalError:
        # Table might not exist yet
        pass
    except Exception as e:
        logger.error(f"Error resolving alias for {source_ip}: {e}")
        
    # Cache the original IP as fallback to avoid hammering the DB
    _alias_cache[source_ip] = (source_ip, now)
    return source_ip


class SyslogUDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, assembler: KeyedMultilineAssembler, db_path: str | Path):
        self.assembler = assembler
        self.db_path = db_path
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
            alias = await asyncio.to_thread(resolve_alias, source_ip, self.db_path)
            parsed["source_alias"] = alias
            stream_key = f"{source_ip}:{parsed['app_name']}"
            await self.assembler.feed(stream_key, parsed)
        except Exception as e:
            logger.error(f"Error processing UDP syslog message: {e}")


class SyslogTCPProtocol(asyncio.Protocol):
    def __init__(self, assembler: KeyedMultilineAssembler, db_path: str | Path):
        self.assembler = assembler
        self.db_path = db_path
        self.buffer = b""
        self.peername = None

    def connection_made(self, transport):
        self.peername = transport.get_extra_info('peername')
        logger.debug(f"Syslog TCP connection from {self.peername}")

    def data_received(self, data: bytes):
        self.buffer += data
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if line:
                source_ip = self.peername[0] if self.peername else "unknown"
                asyncio.ensure_future(self.process_message(line, source_ip))
                
    async def process_message(self, data: bytes, source_ip: str):
        try:
            parsed = parse_syslog_message(data, source_ip)
            alias = await asyncio.to_thread(resolve_alias, source_ip, self.db_path)
            parsed["source_alias"] = alias
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

    async def start(self) -> None:
        """Create and start both UDP and TCP transports."""
        loop = asyncio.get_running_loop()
        
        # Start UDP
        try:
            self.udp_transport, _ = await loop.create_datagram_endpoint(
                lambda: SyslogUDPProtocol(self.assembler, self.db_path),
                local_addr=(self.host, self.port)
            )
            logger.info(f"Started Syslog UDP server on {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start Syslog UDP server: {e}")

        # Start TCP
        try:
            self.tcp_server = await loop.create_server(
                lambda: SyslogTCPProtocol(self.assembler, self.db_path),
                self.host, self.port
            )
            logger.info(f"Started Syslog TCP server on {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to start Syslog TCP server: {e}")

    async def stop(self) -> None:
        """Close transports."""
        if self.udp_transport:
            self.udp_transport.close()
            
        if self.tcp_server:
            self.tcp_server.close()
            await self.tcp_server.wait_closed()
            
        logger.info("Syslog servers stopped")
