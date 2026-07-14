from pathlib import Path

from backend.app_settings import get_mihomo_settings
from backend.config import MIHOMO_DIR, MIHOMO_EXE


def get_mihomo_exe_setting() -> str:
    settings = get_mihomo_settings()
    configured_exe = str(settings.get("exe") or "").strip()
    return configured_exe or MIHOMO_EXE


def get_mihomo_exe_name() -> str:
    """Compatibility alias for older callers.

    The setting may now contain a full path selected from the settings page,
    not just a filename.
    """
    return get_mihomo_exe_setting()


def resolve_mihomo_exe_path(value: str | Path | None) -> Path:
    """Resolve a selected executable path while retaining legacy filenames."""
    configured = str(value or "").strip()
    if not configured:
        return Path(MIHOMO_DIR) / MIHOMO_EXE

    candidate = Path(configured).expanduser()
    return candidate if candidate.is_absolute() else Path(MIHOMO_DIR) / candidate


def get_mihomo_exe_path() -> Path:
    return resolve_mihomo_exe_path(get_mihomo_exe_setting())


def mihomo_exe_missing_message(path: Path | str | None = None) -> str:
    exe_path = Path(path) if path is not None else get_mihomo_exe_path()
    return (
        f"mihomo executable not found: {exe_path}\n"
        "Please select the mihomo executable in Settings."
    )
