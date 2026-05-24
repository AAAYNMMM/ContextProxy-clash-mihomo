import asyncio
import time

import psutil

from backend.config import TCP_LISTEN_PORT
from backend.activity_bus import write_log


PROCESS_PORT_CACHE = {}
PROCESS_CACHE_TTL = 1.0
PROCESS_CACHE_SCAN_INTERVAL = 1.0

_process_cache_task = None
_process_cache_running = False


def make_cache_key(ip, port):
    return f"{ip}:{port}"


def _get_addr_ip_port(addr):
    if not addr:
        return None, None

    if hasattr(addr, "ip") and hasattr(addr, "port"):
        return addr.ip, addr.port

    try:
        return addr[0], addr[1]
    except Exception:
        return None, None


def refresh_process_cache_once():
    now = time.monotonic()
    refreshed = {}

    try:
        for conn in psutil.net_connections(kind="tcp"):
            if not conn.pid:
                continue

            laddr_ip, laddr_port = _get_addr_ip_port(conn.laddr)
            _raddr_ip, raddr_port = _get_addr_ip_port(conn.raddr)

            if not laddr_ip or not laddr_port or raddr_port != TCP_LISTEN_PORT:
                continue

            try:
                process_name = psutil.Process(conn.pid).name()
            except Exception:
                continue

            refreshed[make_cache_key(laddr_ip, laddr_port)] = {
                "process_name": process_name,
                "updated_at": now,
            }

    except Exception as exc:
        write_log("process_cache", f"refresh failed: {exc}", "WARN")
        return

    PROCESS_PORT_CACHE.update(refreshed)
    _cleanup_expired_cache(now)


def _cleanup_expired_cache(now):
    expired_keys = [
        key
        for key, value in PROCESS_PORT_CACHE.items()
        if now - value.get("updated_at", 0) > PROCESS_CACHE_TTL
    ]

    for key in expired_keys:
        PROCESS_PORT_CACHE.pop(key, None)


def get_process_name_from_cache(client_ip, client_port):
    key = make_cache_key(client_ip, client_port)
    entry = PROCESS_PORT_CACHE.get(key)
    if not entry:
        return None

    if time.monotonic() - entry.get("updated_at", 0) > PROCESS_CACHE_TTL:
        PROCESS_PORT_CACHE.pop(key, None)
        return None

    return entry.get("process_name")


async def start_process_cache_watcher():
    global _process_cache_task, _process_cache_running

    if _process_cache_running:
        return

    _process_cache_running = True
    _process_cache_task = asyncio.current_task()
    write_log("process_cache", f"started, scan interval {PROCESS_CACHE_SCAN_INTERVAL:.1f}s")

    while _process_cache_running:
        refresh_process_cache_once()
        await asyncio.sleep(PROCESS_CACHE_SCAN_INTERVAL)


def stop_process_cache_watcher():
    global _process_cache_task, _process_cache_running

    _process_cache_running = False

    if _process_cache_task and not _process_cache_task.done():
        _process_cache_task.cancel()

    _process_cache_task = None
