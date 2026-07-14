from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal, Slot

from gui.dashboard_store import count_active_connections, get_core_events
from gui.process_manager import get_backend_error, get_proxy_state, is_proxy_running


class RuntimePollWorker(QObject):
    snapshot_ready = Signal(object)
    finished = Signal()

    def __init__(self, interval_seconds: float = 2.0):
        super().__init__()
        self.interval_seconds = max(0.5, float(interval_seconds))
        self._stop_event = threading.Event()
        self._last_event_id = 0
        self._boot_id = None

    @Slot()
    def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                started = time.monotonic()
                snapshot = self._collect_snapshot()
                self.snapshot_ready.emit(snapshot)
                remaining = self.interval_seconds - (time.monotonic() - started)
                if remaining > 0:
                    self._stop_event.wait(remaining)
        finally:
            self.finished.emit()

    def stop(self) -> None:
        self._stop_event.set()

    def _collect_snapshot(self) -> dict:
        state = get_proxy_state()
        running = is_proxy_running() if state not in {"starting", "stopping"} else False
        active_connections = count_active_connections() if running else 0
        events_payload = {"boot_id": self._boot_id, "events": []}

        if running:
            payload = get_core_events(self._last_event_id, limit=80)
            boot_id = payload.get("boot_id")
            if boot_id and boot_id != self._boot_id:
                self._boot_id = boot_id
                self._last_event_id = 0
                payload = get_core_events(None, limit=80)
                boot_id = payload.get("boot_id") or boot_id
            events = payload.get("events", []) if isinstance(payload, dict) else []
            for event in events if isinstance(events, list) else []:
                try:
                    self._last_event_id = max(self._last_event_id, int(event.get("id") or 0))
                except Exception:
                    continue
            events_payload = {
                "boot_id": boot_id,
                "events": events if isinstance(events, list) else [],
            }
        else:
            self._last_event_id = 0
            self._boot_id = None

        return {
            "state": state,
            "running": running,
            "active_connections": active_connections,
            "events": events_payload,
            "backend_error": get_backend_error() if state in {"stopped", "failed"} else None,
        }
