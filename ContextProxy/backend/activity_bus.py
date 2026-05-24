from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

_activity_callback: Callable[[str, str], None] | None = None
_callback_lock = threading.RLock()
_throttle_lock = threading.RLock()
_last_emit_at: dict[str, float] = {}


def set_activity_callback(callback: Callable[[str, str], None] | None):
    """Register a GUI-safe callback for important activity messages.

    The callback may be invoked from backend threads. GUI code should forward it
    through Qt signals before touching widgets.
    """
    global _activity_callback
    with _callback_lock:
        _activity_callback = callback


def _append_file(path: Path, message: str):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8", errors="replace") as file:
            file.write(f"{timestamp} {message}\n")
    except Exception:
        # Logging must never break proxy flow.
        pass


def write_log(category: str, message: str, level: str = "INFO"):
    category = (category or "backend").strip().lower().replace("/", "_").replace("\\", "_")
    _append_file(LOG_DIR / f"{category}.log", f"[{level}] {message}")


def should_emit(key: str | None, ttl: float = 0) -> bool:
    if not key or ttl <= 0:
        return True

    now = time.monotonic()
    with _throttle_lock:
        last = _last_emit_at.get(key)
        if last is not None and now - last < ttl:
            return False
        _last_emit_at[key] = now
        return True


def emit_activity(message: str, level: str = "INFO", key: str | None = None, ttl: float = 0):
    """Send an important activity to GUI and write it to activity.log.

    Messages are throttled by key when ttl > 0. The GUI decides the final visual
    style; this function keeps backend output off the console.
    """
    if not should_emit(key, ttl):
        return False

    level = (level or "INFO").upper()
    write_log("activity", message, level)

    with _callback_lock:
        callback = _activity_callback

    if callback:
        try:
            callback(message, level)
        except Exception as exc:
            write_log("activity", f"activity callback failed: {exc}", "WARN")
    return True


def emit_routing_event(
    kind: str,
    request_host: str,
    final_group: str,
    tab_host: str | None = None,
    process_name: str | None = None,
    ttl: float = 10,
):
    """Emit throttled Tab/App routing events for GUI recent activity.

    Direct traffic is intentionally not shown in recent activity because it is
    extremely noisy under system proxy mode. Detailed lines still go to
    routing.log for troubleshooting.
    """
    kind = (kind or "").lower().strip()
    request_host = (request_host or "").strip()
    final_group = (final_group or "").strip()
    tab_host = (tab_host or "").strip()
    process_name = (process_name or "").strip()

    detail = {
        "kind": kind,
        "tab_host": tab_host,
        "process_name": process_name,
        "request_host": request_host,
        "final_group": final_group,
    }
    write_log("routing", str(detail), "INFO")

    if final_group.lower() == "direct":
        return False

    if kind == "tab":
        key = f"routing:tab:{tab_host}:{request_host}:{final_group}".lower()
        message = f"Tab 分流：{tab_host or '-'} / {request_host} -> {final_group}"
    elif kind == "app":
        key = f"routing:app:{process_name}:{request_host}:{final_group}".lower()
        message = f"App 分流：{process_name or '-'} / {request_host} -> {final_group}"
    else:
        key = f"routing:{kind}:{request_host}:{final_group}".lower()
        message = f"分流：{request_host} -> {final_group}"

    return emit_activity(message, "INFO", key=key, ttl=ttl)
