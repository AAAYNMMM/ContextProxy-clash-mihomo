from copy import deepcopy
from pathlib import Path

from backend.paths import CONFIG_DIR


APP_SETTINGS_FILE = CONFIG_DIR / "app_settings.yaml"


def get_default_app_settings() -> dict:
    return {
        "proxy": {
            "listen_host": "127.0.0.1",
            "listen_port": 18000,
            "receiver_port": 17890,
        },
        "auto_select": {
            "check_interval": 20,
            "max_fail_count": 2,
            "delay_timeout_ms": 5000,
            "test_url": "https://cp.cloudflare.com/generate_204",
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
            "enable_system_proxy_on_start": True,
            "disable_system_proxy_on_stop": True,
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


def _to_int(value, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default

    if min_value is not None and value < min_value:
        return default

    if max_value is not None and value > max_value:
        return default

    return value


def _to_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value

    return default


def load_app_settings() -> dict:
    defaults = get_default_app_settings()
    raw = _load_yaml(APP_SETTINGS_FILE)
    settings = deepcopy(defaults)

    proxy = raw.get("proxy", {})
    if isinstance(proxy, dict):
        settings["proxy"]["listen_host"] = str(proxy.get("listen_host") or defaults["proxy"]["listen_host"])
        settings["proxy"]["listen_port"] = _to_int(proxy.get("listen_port"), defaults["proxy"]["listen_port"], 1, 65535)
        settings["proxy"]["receiver_port"] = _to_int(proxy.get("receiver_port"), defaults["proxy"]["receiver_port"], 1, 65535)

    auto_select = raw.get("auto_select", raw.get("auto_selector", {}))
    if isinstance(auto_select, dict):
        settings["auto_select"]["check_interval"] = _to_int(
            auto_select.get("check_interval"),
            defaults["auto_select"]["check_interval"],
            5,
        )
        settings["auto_select"]["max_fail_count"] = _to_int(
            auto_select.get("max_fail_count"),
            defaults["auto_select"]["max_fail_count"],
            1,
        )
        settings["auto_select"]["delay_timeout_ms"] = _to_int(
            auto_select.get("delay_timeout_ms"),
            defaults["auto_select"]["delay_timeout_ms"],
            1000,
        )
        settings["auto_select"]["test_url"] = str(auto_select.get("test_url") or defaults["auto_select"]["test_url"])

    mihomo = raw.get("mihomo", {})
    if isinstance(mihomo, dict):
        settings["mihomo"]["exe"] = str(mihomo.get("exe") or defaults["mihomo"]["exe"])
        settings["mihomo"]["controller_port"] = _to_int(
            mihomo.get("controller_port"),
            defaults["mihomo"]["controller_port"],
            1,
            65535,
        )
        settings["mihomo"]["mixed_port"] = _to_int(
            mihomo.get("mixed_port"),
            defaults["mihomo"]["mixed_port"],
            1,
            65535,
        )

    ui = raw.get("ui", {})
    if isinstance(ui, dict):
        for key, default_value in defaults["ui"].items():
            settings["ui"][key] = _to_bool(ui.get(key), default_value)

    return settings


def get_proxy_settings() -> dict:
    return load_app_settings()["proxy"]


def get_auto_select_settings() -> dict:
    return load_app_settings()["auto_select"]


def get_mihomo_settings() -> dict:
    return load_app_settings()["mihomo"]


def get_mihomo_controller_port() -> int:
    return get_mihomo_settings()["controller_port"]


def get_mihomo_mixed_port() -> int:
    return get_mihomo_settings()["mixed_port"]


def get_ui_settings() -> dict:
    return load_app_settings()["ui"]
