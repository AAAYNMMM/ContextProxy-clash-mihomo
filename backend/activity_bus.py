from __future__ import annotations

import atexit
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO

from backend.paths import LOGS_DIR

LOG_DIR = LOGS_DIR
MAX_LOG_FILE_BYTES = 5 * 1024 * 1024
LOG_QUEUE_SIZE = 4096

_activity_callback: Callable[[str, str], None] | None = None
_callback_lock = threading.RLock()
_throttle_lock = threading.RLock()
_last_emit_at: dict[str, float] = {}


class _AsyncLogWriter:
    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[Path, str] | None] = queue.Queue(maxsize=LOG_QUEUE_SIZE)
        self._files: dict[Path, TextIO] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._dropped = 0
        self._thread = threading.Thread(target=self._run, name="ContextProxyLogWriter", daemon=True)
        self._thread.start()

    def write(self, path: Path, message: str) -> None:
        with self._lock:
            if self._closed:
                return
        try:
            self._queue.put_nowait((path, message))
        except queue.Full:
            with self._lock:
                self._dropped += 1

    def _open_file(self, path: Path) -> TextIO:
        handle = self._files.get(path)
        if handle is not None:
            try:
                if handle.tell() < MAX_LOG_FILE_BYTES:
                    return handle
                handle.flush()
                handle.close()
            except Exception:
                try:
                    handle.close()
                except Exception:
                    pass
            self._files.pop(path, None)
            self._rotate(path)

        path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate(path)
        handle = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
        self._files[path] = handle
        return handle

    def _rotate(self, path: Path) -> None:
        try:
            if not path.is_file() or path.stat().st_size < MAX_LOG_FILE_BYTES:
                return
            rotated = path.with_name(path.name + ".1")
            rotated.unlink(missing_ok=True)
            path.replace(rotated)
        except Exception:
            pass

    def _flush(self) -> None:
        for handle in list(self._files.values()):
            try:
                handle.flush()
            except Exception:
                pass

    def _write_item(self, path: Path, message: str) -> None:
        try:
            handle = self._open_file(path)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"{timestamp} {message}\n")
        except Exception:
            pass

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                self._flush()
                continue
            if item is None:
                self._queue.task_done()
                break
            path, message = item
            self._write_item(path, message)
            self._queue.task_done()

        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                self._write_item(*item)
            self._queue.task_done()

        with self._lock:
            dropped = self._dropped
        if dropped:
            self._write_item(
                LOG_DIR / "activity.log",
                f"[WARN] asynchronous log queue dropped {dropped} entries",
            )

        self._flush()
        for handle in list(self._files.values()):
            try:
                handle.close()
            except Exception:
                pass
        self._files.clear()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._queue.put(None, timeout=0.5)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(None)
            except Exception:
                return
        self._thread.join(timeout=2.0)


_log_writer = _AsyncLogWriter()


def set_activity_callback(callback: Callable[[str, str], None] | None):
    global _activity_callback
    with _callback_lock:
        _activity_callback = callback


def write_log(category: str, message: str, level: str = "INFO"):
    category = (category or "backend").strip().lower().replace("/", "_").replace("\\", "_")
    _log_writer.write(LOG_DIR / f"{category}.log", f"[{level}] {message}")


def should_emit(key: str | None, ttl: float = 0) -> bool:
    if not key or ttl <= 0:
        return True

    now = time.monotonic()
    with _throttle_lock:
        last = _last_emit_at.get(key)
        if last is not None and now - last < ttl:
            return False
        _last_emit_at[key] = now
        if len(_last_emit_at) > 4096:
            cutoff = now - 3600
            stale = [item_key for item_key, timestamp in _last_emit_at.items() if timestamp < cutoff]
            for item_key in stale:
                _last_emit_at.pop(item_key, None)
        return True


def emit_activity(message: str, level: str = "INFO", key: str | None = None, ttl: float = 0):
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


def shutdown_logging() -> None:
    _log_writer.close()


atexit.register(shutdown_logging)
