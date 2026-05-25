import os
import subprocess
from pathlib import Path

from backend.activity_bus import write_log
from backend.core_config import (
    CORE_CONFIG_FILE,
    generate_contextproxy_core_config,
    get_core_listen_settings,
    get_core_token,
)
from backend.local_http import local_get, local_post
from backend.paths import PROJECT_ROOT
from backend.process_utils import close_process_log, popen_hidden, run_hidden


CORE_EXE_NAME = "contextproxy-core.exe" if os.name == "nt" else "contextproxy-core"
CORE_DIR = PROJECT_ROOT / "core"
CORE_EXE_FILE = CORE_DIR / CORE_EXE_NAME
core_process: subprocess.Popen | None = None


def get_core_exe_path() -> Path:
    return CORE_EXE_FILE


def is_core_available() -> bool:
    return CORE_EXE_FILE.is_file()


def is_core_running() -> bool:
    return core_process is not None and core_process.poll() is None


def start_core():
    global core_process
    if is_core_running():
        return core_process

    generate_contextproxy_core_config()
    if not CORE_EXE_FILE.is_file():
        raise FileNotFoundError(
            f"contextproxy core not found: {CORE_EXE_FILE}. 请确认 core/{CORE_EXE_NAME} 存在。"
        )

    write_log("core", f"starting contextproxy core: {CORE_EXE_FILE} -config {CORE_CONFIG_FILE}")
    core_process = popen_hidden(
        [str(CORE_EXE_FILE), "-config", str(CORE_CONFIG_FILE)],
        log_name="core.log",
    )
    write_log("core", f"contextproxy core started, pid={core_process.pid}")
    return core_process


def stop_core():
    global core_process
    process = core_process
    if process is None:
        write_log("core", "contextproxy core already stopped")
        return

    if process.poll() is None:
        write_log("core", f"stopping contextproxy core, pid={process.pid}")
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            if os.name == "nt":
                run_hidden(["taskkill", "/PID", str(process.pid), "/T", "/F"], log_name="process.log", check=False)
            else:
                process.kill()
        write_log("core", "contextproxy core stopped")
    else:
        write_log("core", f"contextproxy core already exited, code={process.returncode}")

    close_process_log(process)
    core_process = None


def _auth_headers() -> dict[str, str]:
    return {"X-ContextProxy-Token": get_core_token()}


def core_health_ok(timeout: float = 0.5) -> bool:
    _proxy_host, _proxy_port, api_host, api_port = get_core_listen_settings()
    try:
        response = local_get(f"http://{api_host}:{api_port}/health", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def reload_core_config() -> bool:
    if not is_core_running():
        return False
    generate_contextproxy_core_config()
    _proxy_host, _proxy_port, api_host, api_port = get_core_listen_settings()
    try:
        response = local_post(f"http://{api_host}:{api_port}/reload", headers=_auth_headers(), timeout=2)
        return response.status_code == 200
    except Exception as exc:
        write_log("core", f"reload core config failed: {exc}", "WARN")
        return False


def close_core_connections(groups=None, hosts=None, processes=None) -> int:
    if not is_core_running():
        return 0
    _proxy_host, _proxy_port, api_host, api_port = get_core_listen_settings()
    payload = {
        "groups": list(groups or []),
        "hosts": list(hosts or []),
        "processes": list(processes or []),
    }
    try:
        response = local_post(f"http://{api_host}:{api_port}/close", json=payload, headers=_auth_headers(), timeout=2)
        if response.status_code != 200:
            return 0
        data = response.json()
        return int(data.get("closed") or 0)
    except Exception as exc:
        write_log("core", f"close core connections failed: {exc}", "WARN")
        return 0


def pause_core_groups(groups=None, hold_ms: int = 800) -> int:
    if not is_core_running():
        return 0
    _proxy_host, _proxy_port, api_host, api_port = get_core_listen_settings()
    payload = {"groups": list(groups or []), "hold_ms": int(hold_ms or 800)}
    try:
        response = local_post(
            f"http://{api_host}:{api_port}/pause_groups",
            json=payload,
            headers=_auth_headers(),
            timeout=2,
        )
        if response.status_code != 200:
            return 0
        data = response.json()
        return len(data.get("paused") or [])
    except Exception as exc:
        write_log("core", f"pause core groups failed: {exc}", "WARN")
        return 0


def resume_core_groups(groups=None) -> int:
    if not is_core_running():
        return 0
    _proxy_host, _proxy_port, api_host, api_port = get_core_listen_settings()
    payload = {"groups": list(groups or [])}
    try:
        response = local_post(
            f"http://{api_host}:{api_port}/resume_groups",
            json=payload,
            headers=_auth_headers(),
            timeout=2,
        )
        if response.status_code != 200:
            return 0
        data = response.json()
        return len(data.get("resumed") or [])
    except Exception as exc:
        write_log("core", f"resume core groups failed: {exc}", "WARN")
        return 0
