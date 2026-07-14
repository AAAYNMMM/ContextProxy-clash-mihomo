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
from backend.atomic_writer import atomic_write_text, atomic_write_yaml


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
    "latency_test": {
        "timeout_ms": 5000,
        "test_url": "https://www.gstatic.com/generate_204",
    },
    "ui": {
        "close_to_tray": True,
        "start_minimized": False,
        "auto_start_proxy": False,
        "auto_manage_system_proxy": True,
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
            "nodes": [],
        },
        "AI": {
            "port": 7891,
            "nodes": [],
        },
        "Media": {
            "port": 7892,
            "nodes": [],
        },
    }
}


DOMAIN_RULES_TEMPLATE = (
    "# Format: group,domain rule\n"
    "# Proxy,*.google.com\n"
    "# AI,*.example.com\n"
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

    atomic_write_yaml(path, data)


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
    latency_test = settings.get("latency_test", {})
    legacy_auto_select = settings.get("auto_select", {})
    if isinstance(legacy_auto_select, dict):
        if "timeout_ms" not in latency_test and "delay_timeout_ms" in legacy_auto_select:
            latency_test["timeout_ms"] = legacy_auto_select.get("delay_timeout_ms")
            changed = True
        if "test_url" not in latency_test and "test_url" in legacy_auto_select:
            latency_test["test_url"] = legacy_auto_select.get("test_url")
            changed = True
    if isinstance(latency_test, dict) and latency_test.get("test_url") in {
        "http://www.gstatic.com/generate_204",
        "http://www.google.com/generate_204",
    }:
        latency_test["test_url"] = "https://www.gstatic.com/generate_204"
        changed = True
    settings["latency_test"] = latency_test
    if "auto_select" in settings:
        settings.pop("auto_select", None)
        changed = True
    if "auto_selector" in settings:
        settings.pop("auto_selector", None)
        changed = True
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
        return

    removed_legacy_controllers = False
    for group_data in groups.values():
        if isinstance(group_data, dict) and "controller" in group_data:
            group_data.pop("controller", None)
            removed_legacy_controllers = True

    if removed_legacy_controllers:
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
        _save_yaml(SUBSCRIPTIONS_META_FILE, {"subscriptions": {}})


def ensure_rule_files() -> None:
    if not GROUPS_DOMAINS_FILE.is_file():
        atomic_write_text(GROUPS_DOMAINS_FILE, DOMAIN_RULES_TEMPLATE)

    if not APP_PROCESSES_FILE.is_file():
        atomic_write_text(APP_PROCESSES_FILE, APP_RULES_TEMPLATE)


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
