import os
import subprocess
from pathlib import Path

from backend.activity_bus import write_log
from backend.config import MIHOMO_DIR
from backend.mihomo_config_generator import generate_all_configs
from backend.mihomo_paths import get_mihomo_exe_path, mihomo_exe_missing_message
from backend.port_manager import prepare_mihomo_runtime_ports
from backend.process_utils import close_process_log, popen_hidden, run_hidden


mihomo_process: subprocess.Popen | None = None
MAIN_CONFIG_FILE = Path(MIHOMO_DIR) / "config.yaml"


def get_exe_path() -> str:
    return str(get_mihomo_exe_path())


def get_main_config_file() -> str:
    return str(MAIN_CONFIG_FILE)


def is_process_running(process: subprocess.Popen | None) -> bool:
    return process is not None and process.poll() is None


def _force_kill_process(process: subprocess.Popen):
    if not is_process_running(process):
        return

    try:
        if os.name == "nt":
            run_hidden(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                log_name="process.log",
                check=False,
            )
        else:
            process.kill()
        process.wait(timeout=3)
    except Exception as exc:
        write_log("mihomo", f"force kill failed pid={process.pid}: {exc}", "WARN")


def _is_main_config_command(command_line: str) -> bool:
    command_line = command_line.lower()
    config_name = str(MAIN_CONFIG_FILE).lower()
    return "mihomo" in command_line and "config.yaml" in command_line and config_name in command_line


def _find_existing_main_mihomo_pids() -> list[int]:
    if os.name != "nt":
        return []

    try:
        import psutil
    except ImportError:
        return []

    pids = []
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (process.info.get("name") or "").lower()
            if "mihomo" not in name:
                continue
            cmdline = " ".join(process.info.get("cmdline") or [])
            if _is_main_config_command(cmdline):
                pids.append(int(process.info["pid"]))
        except Exception:
            continue
    return pids


def _cleanup_recorded_process_if_exited():
    global mihomo_process
    if mihomo_process is not None and mihomo_process.poll() is not None:
        write_log("mihomo", f"recorded process already exited, pid={mihomo_process.pid}", "WARN")
        close_process_log(mihomo_process)
        mihomo_process = None


def launch_mihomo_all():
    global mihomo_process

    _cleanup_recorded_process_if_exited()
    if is_process_running(mihomo_process):
        write_log("mihomo", f"single core already running, pid={mihomo_process.pid}")
        return mihomo_process

    existing_pids = _find_existing_main_mihomo_pids()
    if existing_pids:
        write_log("mihomo", f"single core already exists, pids={existing_pids}; skip duplicate launch", "WARN")
        return None

    ok, error, changes = prepare_mihomo_runtime_ports(write_settings=True, check_system=True)
    if not ok:
        raise RuntimeError(error or "mihomo runtime port check failed")
    for change in changes:
        write_log("mihomo", f"runtime port adjusted: {change}")

    generate_all_configs()

    exe_path = get_exe_path()
    config_file = get_main_config_file()

    if not os.path.isfile(exe_path):
        raise FileNotFoundError(mihomo_exe_missing_message(exe_path))

    if not os.path.isfile(config_file):
        raise FileNotFoundError(f"mihomo config not found: {config_file}")

    extra_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0

    write_log("mihomo", f"launch single core: {exe_path} -f {config_file}")
    mihomo_process = popen_hidden(
        [exe_path, "-f", config_file],
        log_name="mihomo.log",
        extra_flags=extra_flags,
    )

    write_log("mihomo", f"single core started, pid={mihomo_process.pid}, config={config_file}")
    return mihomo_process


def stop_all_mihomo():
    global mihomo_process

    process = mihomo_process
    if is_process_running(process):
        write_log("mihomo", f"stopping single core, pid={process.pid}")
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            _force_kill_process(process)

        if process.poll() is None:
            _force_kill_process(process)

        write_log("mihomo", "single core stopped")

    if process is not None:
        close_process_log(process)

    mihomo_process = None
    _stop_residual_main_config_processes()


def restart_mihomo_core():
    write_log("mihomo", "restarting single core", "WARN")
    stop_all_mihomo()
    return launch_mihomo_all()


def _stop_residual_main_config_processes():
    if os.name != "nt":
        return

    config_path = str(MAIN_CONFIG_FILE)
    try:
        run_hidden(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"$config = '{config_path}'; "
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.Name -like 'mihomo*' -and "
                    "$_.CommandLine -like \"*$config*\" } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
                ),
            ],
            log_name="process.log",
            check=False,
        )
    except Exception:
        pass
