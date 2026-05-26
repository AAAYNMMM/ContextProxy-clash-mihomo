from pathlib import Path

from backend.paths import CONFIG_DIR


GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"

DEFAULT_GROUP_CONFIG = {
    "Proxy": {"port": 7890, "controller": 9090},
    "AI": {"port": 7891, "controller": 9091},
    "Media": {"port": 7892, "controller": 9092},
}

_GROUP_CONFIG_CACHE = None
_GROUP_CONFIG_MTIME = None


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        print("[runtime_config] PyYAML is not installed, using default group config")
        return {}

    if not path.is_file():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
    except Exception as exc:
        print(f"[runtime_config] failed to read group config: {exc}")
        return {}

    return data if isinstance(data, dict) else {}


def _normalize_group_config(data: dict) -> dict:
    groups = data.get("groups", {}) if isinstance(data, dict) else {}
    if not isinstance(groups, dict):
        return {}

    result = {}
    for group_name, group_data in groups.items():
        if not group_name or not isinstance(group_data, dict):
            continue

        port = _to_int(group_data.get("port"))
        if port is None:
            print(f"[runtime_config] ignore group without valid port: {group_name}")
            continue

        controller = _to_int(group_data.get("controller"))
        normalized = {"port": port}
        if controller is not None:
            normalized["controller"] = controller

        result[str(group_name)] = normalized

    return result


def load_group_config() -> dict:
    data = _load_yaml(GROUP_NODES_FILE)
    group_config = _normalize_group_config(data)
    return group_config or dict(DEFAULT_GROUP_CONFIG)


def reload_group_config() -> dict:
    global _GROUP_CONFIG_CACHE, _GROUP_CONFIG_MTIME
    _GROUP_CONFIG_CACHE = load_group_config()
    try:
        _GROUP_CONFIG_MTIME = GROUP_NODES_FILE.stat().st_mtime
    except OSError:
        _GROUP_CONFIG_MTIME = None
    return _GROUP_CONFIG_CACHE


def _get_group_config() -> dict:
    global _GROUP_CONFIG_CACHE, _GROUP_CONFIG_MTIME

    try:
        current_mtime = GROUP_NODES_FILE.stat().st_mtime
    except OSError:
        current_mtime = None

    if _GROUP_CONFIG_CACHE is None or current_mtime != _GROUP_CONFIG_MTIME:
        _GROUP_CONFIG_CACHE = load_group_config()
        _GROUP_CONFIG_MTIME = current_mtime

    return _GROUP_CONFIG_CACHE


def get_group_port_map() -> dict:
    return {
        group_name: group_data["port"]
        for group_name, group_data in _get_group_config().items()
        if "port" in group_data
    }


def get_group_controller_map() -> dict:
    return {
        group_name: group_data["controller"]
        for group_name, group_data in _get_group_config().items()
        if "controller" in group_data
    }


def get_valid_group_names() -> set[str]:
    return set(get_group_port_map().keys())
