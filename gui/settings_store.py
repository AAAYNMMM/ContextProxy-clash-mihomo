from copy import deepcopy
from datetime import datetime
from pathlib import Path

from backend.paths import CONFIG_DIR
from backend.atomic_writer import atomic_write_yaml

APP_SETTINGS_FILE = CONFIG_DIR / "app_settings.yaml"


def get_default_settings() -> dict:
    return {
        "proxy": {
            "listen_host": "127.0.0.1",
            "listen_port": 18000,
            "receiver_port": 17890,
        },
        "latency_test": {
            "timeout_ms": 5000,
            "test_url": "https://www.gstatic.com/generate_204",
        },
        "mihomo": {
            "exe": "",
            "controller_port": 9090,
            "mixed_port": 7899,
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


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}

    try:
        import yaml
    except ImportError:
        return {}

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def _save_yaml(path: Path, data: dict) -> tuple[bool, str | None]:
    try:
        import yaml
    except ImportError:
        return False, "PyYAML is not installed"

    try:
        atomic_write_yaml(path, data)
    except Exception as exc:
        return False, str(exc)

    return True, None


def _merge_defaults(settings: dict) -> dict:
    merged = deepcopy(get_default_settings())
    settings = dict(settings)

    if "latency_test" not in settings:
        legacy_auto_select = settings.get("auto_select", settings.get("auto_selector", {}))
        if isinstance(legacy_auto_select, dict):
            settings["latency_test"] = {
                "timeout_ms": legacy_auto_select.get("delay_timeout_ms", 5000),
                "test_url": legacy_auto_select.get("test_url", "https://www.gstatic.com/generate_204"),
            }

    legacy_ui = settings.get("ui")
    if isinstance(legacy_ui, dict) and "auto_manage_system_proxy" not in legacy_ui:
        enable_on_start = legacy_ui.get("enable_system_proxy_on_start", True)
        disable_on_stop = legacy_ui.get("disable_system_proxy_on_stop", True)
        settings["ui"] = {
            **legacy_ui,
            "auto_manage_system_proxy": (
                enable_on_start if isinstance(enable_on_start, bool) else True
            )
            and (
                disable_on_stop if isinstance(disable_on_stop, bool) else True
            ),
        }

    for section, defaults in merged.items():
        incoming = settings.get(section, {})
        if not isinstance(incoming, dict):
            continue

        for key in defaults.keys():
            if key in incoming:
                merged[section][key] = incoming[key]

    test_url = str(merged.get("latency_test", {}).get("test_url") or "")
    if test_url in {
        "http://www.gstatic.com/generate_204",
        "http://www.google.com/generate_204",
    }:
        merged["latency_test"]["test_url"] = "https://www.gstatic.com/generate_204"
    merged.pop("auto_select", None)
    merged.pop("auto_selector", None)

    return merged


def load_app_settings() -> dict:
    settings = _merge_defaults(_load_yaml(APP_SETTINGS_FILE))

    if not APP_SETTINGS_FILE.is_file():
        save_app_settings_file_only(settings)

    return settings


def _is_port(value) -> bool:
    return isinstance(value, int) and 1 <= value <= 65535


def validate_settings(settings: dict) -> tuple[bool, str | None]:
    proxy = settings.get("proxy", {})
    latency_test = settings.get("latency_test", {})

    if not str(proxy.get("listen_host", "")).strip():
        return False, "\u672c\u5730\u76d1\u542c\u5730\u5740\u4e0d\u80fd\u4e3a\u7a7a"

    if not _is_port(proxy.get("listen_port")):
        return False, "\u672c\u5730\u4ee3\u7406\u7aef\u53e3\u5fc5\u987b\u662f 1-65535"

    if not _is_port(proxy.get("receiver_port")):
        return False, "Tab \u4e0a\u62a5\u63a5\u6536\u7aef\u53e3\u5fc5\u987b\u662f 1-65535"

    mihomo = settings.get("mihomo", {})
    if not _is_port(mihomo.get("controller_port")):
        return False, "mihomo controller 端口必须是 1-65535"

    if not _is_port(mihomo.get("mixed_port")):
        return False, "mihomo mixed-port 必须是 1-65535"

    seen_ports = {}
    for label, port in (
        ("本地代理", proxy.get("listen_port")),
        ("Tab 上报接收", proxy.get("receiver_port")),
        ("mihomo controller", mihomo.get("controller_port")),
        ("mihomo mixed-port", mihomo.get("mixed_port")),
    ):
        existing_label = seen_ports.get(port)
        if existing_label:
            return False, f"{label} 与 {existing_label} 端口不能相同"
        seen_ports[port] = label

    if not isinstance(latency_test.get("timeout_ms"), int) or latency_test["timeout_ms"] < 1000:
        return False, "\u5ef6\u8fdf\u6d4b\u8bd5\u8d85\u65f6\u5fc5\u987b\u5927\u4e8e\u7b49\u4e8e 1000 ms"

    if not str(latency_test.get("test_url", "")).strip():
        return False, "\u6d4b\u8bd5 URL \u4e0d\u80fd\u4e3a\u7a7a"

    return True, None


def save_app_settings_file_only(settings: dict) -> tuple[bool, str | None]:
    """File-only save helper. Runtime apply must use backend.config_apply."""
    from backend.config_apply import save_app_settings_file_internal

    return save_app_settings_file_internal(settings)
