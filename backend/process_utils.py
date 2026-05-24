import subprocess

from backend.paths import LOGS_DIR, PROJECT_ROOT


def get_subprocess_startupinfo():
    if not hasattr(subprocess, "STARTUPINFO"):
        return None

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return startupinfo


def get_subprocess_creationflags(extra_flags: int = 0) -> int:
    flags = extra_flags
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags |= subprocess.CREATE_NO_WINDOW
    return flags


def open_log_file(log_name: str):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return open(LOGS_DIR / log_name, "a", encoding="utf-8", errors="replace", buffering=1)


def _hidden_kwargs(kwargs: dict, extra_flags: int = 0) -> dict:
    hidden = dict(kwargs)
    hidden.setdefault("cwd", str(PROJECT_ROOT))
    hidden.setdefault("stdin", subprocess.DEVNULL)
    hidden.setdefault("shell", False)
    hidden.setdefault("close_fds", True)
    hidden["creationflags"] = get_subprocess_creationflags(
        int(hidden.get("creationflags", 0)) | extra_flags
    )
    hidden["startupinfo"] = hidden.get("startupinfo") or get_subprocess_startupinfo()
    return hidden


def run_hidden(args, extra_flags: int = 0, log_name: str | None = None, **kwargs):
    log_file = None
    if log_name and not kwargs.get("capture_output"):
        log_file = open_log_file(log_name)
        kwargs.setdefault("stdout", log_file)
        kwargs.setdefault("stderr", subprocess.STDOUT)

    try:
        return subprocess.run(args, **_hidden_kwargs(kwargs, extra_flags=extra_flags))
    finally:
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass


def popen_hidden(args, log_name: str | None = None, extra_flags: int = 0, **kwargs):
    log_file = None
    if log_name:
        log_file = open_log_file(log_name)
        kwargs.setdefault("stdout", log_file)
        kwargs.setdefault("stderr", subprocess.STDOUT)

    process = subprocess.Popen(args, **_hidden_kwargs(kwargs, extra_flags=extra_flags))
    if log_file is not None:
        process._contextproxy_log_file = log_file
    return process


def close_process_log(process):
    log_file = getattr(process, "_contextproxy_log_file", None)
    if log_file is None:
        return

    try:
        log_file.close()
    except Exception:
        pass
    try:
        delattr(process, "_contextproxy_log_file")
    except Exception:
        pass
