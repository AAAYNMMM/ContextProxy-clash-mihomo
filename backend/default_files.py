from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path

from backend.paths import (
    APP_PROCESSES_FILE,
    CONFIG_DIR,
    GROUPS_DOMAINS_FILE,
    LOGS_DIR,
    MIHOMO_DIR,
)


APP_SETTINGS_FILE = CONFIG_DIR / "app_settings.yaml"
GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"
NODE_POOL_FILE = CONFIG_DIR / "node_pool.yaml"
SUBSCRIPTIONS_DIR = CONFIG_DIR / "subscriptions"
SUBSCRIPTIONS_META_FILE = SUBSCRIPTIONS_DIR / "subscriptions.yaml"


DEFAULT_APP_SETTINGS = {
    "proxy": {
        "listen_host": "127.0.0.1",
        "listen_port": 18000,
        "receiver_port": 17890,
    },
    "mihomo": {
        "exe": "",
        "mixed_port": 7899,
        "controller_port": 9090,
    },
    "auto_select": {
        "check_interval": 20,
        "max_fail_count": 2,
        "delay_timeout_ms": 5000,
        "test_url": "http://www.gstatic.com/generate_204",
    },
    "ui": {
        "close_to_tray": True,
        "start_minimized": False,
        "auto_start_proxy": False,
        "enable_system_proxy_on_start": True,
        "disable_system_proxy_on_stop": True,
    },
    "logging": {
        "console_enabled": False,
        "debug_enabled": False,
        "max_recent_activities": 200,
    },
}


DEFAULT_GROUP_NODES = {
    "groups": {
        "Proxy": {
            "port": 7890,
            "controller": 9090,
            "nodes": [],
        },
        "AI": {
            "port": 7891,
            "controller": 9091,
            "nodes": [],
        },
        "Media": {
            "port": 7892,
            "controller": 9092,
            "nodes": [],
        },
    }
}


DOMAIN_RULES_TEMPLATE = (
    "# Format: group,domain rule\n"
    "# Proxy,*.google.com\n"
    "# AI,*.openai.com\n"
)

APP_RULES_TEMPLATE = (
    "# Format: group,process name\n"
    "# Proxy,chrome.exe\n"
    "# Proxy,Code.exe\n"
)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_yaml(path: Path):
    if not path.is_file():
        return None

    try:
        import yaml
    except ImportError:
        return None

    try:
        with open(path, "r", encoding="utf-8") as file:
            return yaml.safe_load(file)
    except Exception:
        return None


def _save_yaml(path: Path, data) -> None:
    try:
        import yaml
    except ImportError:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False)


def _merge_missing_defaults(existing, defaults):
    if not isinstance(existing, dict):
        return deepcopy(defaults), True

    changed = False
    merged = deepcopy(existing)
    for key, default_value in defaults.items():
        if key not in merged:
            merged[key] = deepcopy(default_value)
            changed = True
            continue

        if isinstance(default_value, dict):
            child, child_changed = _merge_missing_defaults(merged.get(key), default_value)
            merged[key] = child
            changed = changed or child_changed

    return merged, changed


def ensure_app_settings_file() -> None:
    existing = _load_yaml(APP_SETTINGS_FILE)
    settings, changed = _merge_missing_defaults(existing, DEFAULT_APP_SETTINGS)
    if changed or not APP_SETTINGS_FILE.is_file():
        settings["updated_at"] = _now_str()
        _save_yaml(APP_SETTINGS_FILE, settings)


def ensure_group_nodes_file() -> None:
    existing = _load_yaml(GROUP_NODES_FILE)
    if not isinstance(existing, dict):
        data = deepcopy(DEFAULT_GROUP_NODES)
        data["updated_at"] = _now_str()
        _save_yaml(GROUP_NODES_FILE, data)
        return

    groups = existing.get("groups")
    if not isinstance(groups, dict) or not groups:
        existing["groups"] = deepcopy(DEFAULT_GROUP_NODES["groups"])
        existing["updated_at"] = _now_str()
        _save_yaml(GROUP_NODES_FILE, existing)


def ensure_node_pool_file() -> None:
    if not NODE_POOL_FILE.is_file():
        _save_yaml(
            NODE_POOL_FILE,
            {
                "updated_at": _now_str(),
                "node_count": 0,
                "nodes": {},
            },
        )


def ensure_subscriptions_meta_file() -> None:
    SUBSCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    if not SUBSCRIPTIONS_META_FILE.is_file():
        _save_yaml(SUBSCRIPTIONS_META_FILE, {"subscriptions": []})


def ensure_rule_files() -> None:
    if not GROUPS_DOMAINS_FILE.is_file():
        GROUPS_DOMAINS_FILE.write_text(DOMAIN_RULES_TEMPLATE, encoding="utf-8")

    if not APP_PROCESSES_FILE.is_file():
        APP_PROCESSES_FILE.write_text(APP_RULES_TEMPLATE, encoding="utf-8")


def ensure_default_files() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    SUBSCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    MIHOMO_DIR.mkdir(parents=True, exist_ok=True)

    ensure_app_settings_file()
    ensure_group_nodes_file()
    ensure_node_pool_file()
    ensure_subscriptions_meta_file()
    ensure_rule_files()
