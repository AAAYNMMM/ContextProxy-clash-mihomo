import json
import secrets
from pathlib import Path

import yaml

from backend.app_settings import get_proxy_settings
from backend.paths import APP_PROCESSES_FILE, CONFIG_DIR, GROUPS_DOMAINS_FILE, LOGS_DIR
from backend.runtime_config import get_group_port_map, get_valid_group_names


CORE_CONFIG_FILE = CONFIG_DIR / "contextproxy_core.json"
LEGACY_CORE_CONFIG_FILE = CONFIG_DIR / "contextproxy_core.yaml"
CORE_TOKEN_FILE = CONFIG_DIR / "contextproxy_core.token"


def _abs_path(path: Path) -> str:
    return str(path.resolve())


def _read_rule_file(path: Path) -> list[dict]:
    valid_groups = get_valid_group_names()
    rules: list[dict] = []
    if not path.is_file():
        return rules

    with open(path, "r", encoding="utf-8-sig") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "," not in line:
                continue
            group, pattern = line.split(",", 1)
            group = group.strip()
            pattern = pattern.strip().lower()
            if not group or not pattern:
                continue
            if group not in valid_groups:
                continue
            rules.append({"group": group, "pattern": pattern})
    return rules


def load_core_config() -> dict:
    config_file = CORE_CONFIG_FILE if CORE_CONFIG_FILE.is_file() else LEGACY_CORE_CONFIG_FILE
    if not config_file.is_file():
        return {}

    try:
        with open(config_file, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        try:
            with open(config_file, "r", encoding="utf-8") as file:
                data = yaml.safe_load(file) or {}
        except Exception:
            return {}

    return data if isinstance(data, dict) else {}


def get_core_token() -> str:
    CORE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if CORE_TOKEN_FILE.is_file():
        token = CORE_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token

    token = secrets.token_urlsafe(32)
    CORE_TOKEN_FILE.write_text(token, encoding="utf-8")
    return token


def generate_contextproxy_core_config() -> dict:
    proxy_settings = get_proxy_settings()
    group_ports = get_group_port_map()
    listen_host = str(proxy_settings.get("listen_host") or "127.0.0.1")
    listen_port = int(proxy_settings.get("listen_port") or 18000)
    receiver_port = int(proxy_settings.get("receiver_port") or 17890)

    config = {
        "listen": {
            "proxy_host": listen_host,
            "proxy_port": listen_port,
            "api_host": "127.0.0.1",
            "api_port": receiver_port,
        },
        "groups": group_ports,
        "files": {
            "domain_rules": _abs_path(GROUPS_DOMAINS_FILE),
            "process_rules": _abs_path(APP_PROCESSES_FILE),
        },
        "logs": {
            "dir": _abs_path(LOGS_DIR),
        },
        "security": {
            "token": get_core_token(),
        },
        "behavior": {
            "tab_wait_enabled": True,
            "tab_wait_browser_ms": 250,
            "tab_wait_unknown_ms": 120,
            "tab_wait_unknown_process_ms": 80,
            "non_tab_ttl_sec": 300,
            "tab_capable_ttl_sec": 1800,
            "browser_registry_ttl_sec": 1800,
            "tcp_table_snapshot_ms": 200,
            "process_identity_ttl_sec": 300,
            "connection_pid_cache_ttl_ms": 1000,
            "negative_process_cache_ttl_ms": 75,
            "process_state_cleanup_interval_sec": 60,
        },
        "transfer": {
            "normal_buffer_kb": 64,
            "high_buffer_kb": 512,
            "high_throughput_window_sec": 2,
            "high_throughput_bytes": 2 * 1024 * 1024,
            "low_throughput_window_sec": 10,
            "low_throughput_bytes": 512 * 1024,
            "write_timeout_sec": 30,
            "idle_timeout_sec": 0,
        },
        "socket": {
            "tcp_nodelay": True,
            "keepalive": True,
            "keepalive_sec": 30,
            "read_buffer_bytes": 1024 * 1024,
            "write_buffer_bytes": 1024 * 1024,
        },
        "default_proxy": "Proxy",
        "direct": "Direct",
        "special_groups": ["AI", "Media"],
        "domain_rules": _read_rule_file(GROUPS_DOMAINS_FILE),
        "process_rules": _read_rule_file(APP_PROCESSES_FILE),
    }

    CORE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CORE_CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
    return config


def get_core_listen_settings() -> tuple[str, int, str, int]:
    config = load_core_config()
    listen = config.get("listen", {}) if isinstance(config, dict) else {}
    proxy_host = str(listen.get("proxy_host") or "127.0.0.1")
    proxy_port = int(listen.get("proxy_port") or 18000)
    api_host = str(listen.get("api_host") or "127.0.0.1")
    api_port = int(listen.get("api_port") or 17890)
    return proxy_host, proxy_port, api_host, api_port
