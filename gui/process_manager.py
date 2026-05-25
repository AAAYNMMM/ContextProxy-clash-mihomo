from pathlib import Path
import sys

from backend.paths import PROJECT_ROOT
from backend.activity_bus import write_log

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.backend_service import BackendService


backend_service = BackendService()


def is_proxy_running() -> bool:
    return backend_service.is_running()


def is_proxy_starting() -> bool:
    return backend_service.is_starting()


def get_proxy_state() -> str:
    return backend_service.get_state()


def get_proxy_status() -> str:
    state = get_proxy_state()
    if state == "starting":
        return "\u542f\u52a8\u4e2d"
    if state == "stopping":
        return "\u505c\u6b62\u4e2d"
    return "\u8fd0\u884c\u4e2d" if is_proxy_running() else "\u5df2\u505c\u6b62"


def get_proxy_pid() -> int | None:
    return None


def get_backend_error() -> str | None:
    return backend_service.last_error


def start_proxy_process() -> tuple[bool, str | None]:
    write_log("lifecycle", "backend_service.start called via process_manager")
    return backend_service.start()


def stop_proxy_process(timeout: float = 3.0) -> tuple[bool, str | None]:
    _ = timeout
    write_log("lifecycle", "backend_service.stop called via process_manager")
    return backend_service.stop()


def cleanup_proxy_residue():
    write_log("lifecycle", "backend_service.cleanup_residue called via process_manager")
    backend_service.cleanup_residue()


def count_mihomo_processes() -> int:
    try:
        import psutil
    except ImportError:
        return 0

    count = 0
    for process in psutil.process_iter(["name"]):
        try:
            name = (process.info.get("name") or "").lower()
        except Exception:
            continue

        if "mihomo" in name:
            count += 1

    return count
