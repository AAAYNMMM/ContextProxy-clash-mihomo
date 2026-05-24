from pathlib import Path

from backend.app_settings import get_mihomo_settings
from backend.config import MIHOMO_DIR, MIHOMO_EXE


def get_mihomo_exe_name() -> str:
    settings = get_mihomo_settings()
    configured_exe = str(settings.get("exe") or "").strip()
    return configured_exe or MIHOMO_EXE


def get_mihomo_exe_path() -> Path:
    return Path(MIHOMO_DIR) / get_mihomo_exe_name()


def mihomo_exe_missing_message(path: Path | str | None = None) -> str:
    exe_path = Path(path) if path is not None else get_mihomo_exe_path()
    return (
        f"mihomo executable not found: {exe_path}\n"
        "Please check the mihomo.exe setting in config/app_settings.yaml."
    )
