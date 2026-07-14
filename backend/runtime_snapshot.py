from __future__ import annotations

from copy import deepcopy
import threading
import time

_lock = threading.RLock()
_metrics: dict | None = None
_updated_at = 0.0


def update_core_metrics(metrics: dict) -> None:
    global _metrics, _updated_at
    if not isinstance(metrics, dict):
        return
    with _lock:
        _metrics = deepcopy(metrics)
        _updated_at = time.monotonic()


def get_core_metrics(max_age: float = 8.0) -> dict | None:
    with _lock:
        if _metrics is None:
            return None
        if max_age > 0 and time.monotonic() - _updated_at > max_age:
            return None
        return deepcopy(_metrics)


def clear_core_metrics() -> None:
    global _metrics, _updated_at
    with _lock:
        _metrics = None
        _updated_at = 0.0
