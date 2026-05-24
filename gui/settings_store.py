from copy import deepcopy
from datetime import datetime
from pathlib import Path

from backend.paths import CONFIG_DIR

APP_SETTINGS_FILE = CONFIG_DIR / "app_settings.yaml"


def get_default_settings() -> dict:
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
            "test_url": "http://www.gstatic.com/generate_204",
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
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False)
    except Exception as exc:
        return False, str(exc)

    return True, None


def _merge_defaults(settings: dict) -> dict:
    merged = deepcopy(get_default_settings())
    settings = dict(settings)

    if "auto_select" not in settings and "auto_selector" in settings:
        settings["auto_select"] = settings.get("auto_selector")

    for section, defaults in merged.items():
        incoming = settings.get(section, {})
        if not isinstance(incoming, dict):
            continue

        for key in defaults.keys():
            if key in incoming:
                merged[section][key] = incoming[key]

    return merged


def load_app_settings() -> dict:
    settings = _merge_defaults(_load_yaml(APP_SETTINGS_FILE))

    if not APP_SETTINGS_FILE.is_file():
        save_app_settings(settings)

    return settings


def _is_port(value) -> bool:
    return isinstance(value, int) and 1 <= value <= 65535


def validate_settings(settings: dict) -> tuple[bool, str | None]:
    proxy = settings.get("proxy", {})
    auto_selector = settings.get("auto_select", {})

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

    if mihomo.get("controller_port") == mihomo.get("mixed_port"):
        return False, "mihomo controller 端口和 mixed-port 不能相同"

    if not isinstance(auto_selector.get("check_interval"), int) or auto_selector["check_interval"] < 5:
        return False, "\u5f53\u524d\u8282\u70b9\u68c0\u6d4b\u95f4\u9694\u5fc5\u987b\u5927\u4e8e\u7b49\u4e8e 5"

    if not isinstance(auto_selector.get("max_fail_count"), int) or auto_selector["max_fail_count"] < 1:
        return False, "\u8fde\u7eed\u5931\u8d25\u6b21\u6570\u5fc5\u987b\u5927\u4e8e\u7b49\u4e8e 1"

    if not isinstance(auto_selector.get("delay_timeout_ms"), int) or auto_selector["delay_timeout_ms"] < 1000:
        return False, "\u5ef6\u8fdf\u6d4b\u8bd5\u8d85\u65f6\u5fc5\u987b\u5927\u4e8e\u7b49\u4e8e 1000 ms"

    if not str(auto_selector.get("test_url", "")).strip():
        return False, "\u6d4b\u8bd5 URL \u4e0d\u80fd\u4e3a\u7a7a"

    return True, None


def save_app_settings(settings: dict) -> tuple[bool, str | None]:
    settings = _merge_defaults(settings)
    valid, error = validate_settings(settings)
    if not valid:
        return False, error

    settings["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return _save_yaml(APP_SETTINGS_FILE, settings)
