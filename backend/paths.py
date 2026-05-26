import sys
from pathlib import Path


def get_project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = get_project_root()
CONFIG_DIR = PROJECT_ROOT / "config"
MIHOMO_DIR = PROJECT_ROOT / "mihomo"
EXTENSION_DIR = PROJECT_ROOT / "extension"
LOGS_DIR = PROJECT_ROOT / "logs"

GROUPS_DOMAINS_FILE = PROJECT_ROOT / "groups_domains.txt"
APP_PROCESSES_FILE = PROJECT_ROOT / "app_processes.txt"


def ensure_log_dir() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR
