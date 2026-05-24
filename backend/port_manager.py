import socket
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from backend.app_settings import get_default_app_settings, load_app_settings
from backend.paths import CONFIG_DIR


APP_SETTINGS_FILE = CONFIG_DIR / "app_settings.yaml"
GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"


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


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_valid_port(port: int | None) -> bool:
    return isinstance(port, int) and 1 <= port <= 65535


def _load_group_map() -> dict:
    data = _load_yaml(GROUP_NODES_FILE)
    groups = data.get("groups", {}) if isinstance(data, dict) else {}
    return groups if isinstance(groups, dict) else {}


def _settings_for_save(settings: dict) -> dict:
    merged = deepcopy(get_default_app_settings())
    for section, defaults in merged.items():
        incoming = settings.get(section, {})
        if not isinstance(incoming, dict):
            continue
        for key in defaults.keys():
            if key in incoming:
                merged[section][key] = incoming[key]
    merged["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return merged


def is_port_available(port: int) -> bool:
    if not _is_valid_port(port):
        return False

    for host in ("127.0.0.1", "0.0.0.0"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        finally:
            sock.close()

    return True


def find_available_port(start_port: int, reserved_ports: set[int] | None = None, check_system: bool = True) -> int:
    reserved_ports = set(reserved_ports or set())
    try:
        port = int(start_port or 1)
    except (TypeError, ValueError):
        port = 1
    port = max(1, port)

    while port <= 65535:
        if port in reserved_ports:
            port += 1
            continue
        if check_system and not is_port_available(port):
            port += 1
            continue
        return port

    raise ValueError("没有可用端口")


def collect_reserved_ports(
    include_proxy: bool = True,
    include_mihomo: bool = False,
    include_group_listeners: bool = True,
    include_group_controllers: bool = False,
) -> set[int]:
    settings = load_app_settings()
    ports: set[int] = set()

    if include_proxy:
        for key in ("listen_port", "receiver_port"):
            port = _to_int(settings.get("proxy", {}).get(key))
            if _is_valid_port(port):
                ports.add(port)

    if include_mihomo:
        for key in ("mixed_port", "controller_port"):
            port = _to_int(settings.get("mihomo", {}).get(key))
            if _is_valid_port(port):
                ports.add(port)

    groups = _load_group_map()
    for group_data in groups.values():
        if not isinstance(group_data, dict):
            continue

        if include_group_listeners:
            port = _to_int(group_data.get("port"))
            if _is_valid_port(port):
                ports.add(port)

        if include_group_controllers:
            port = _to_int(group_data.get("controller"))
            if _is_valid_port(port):
                ports.add(port)

    return ports


def validate_group_listener_ports(
    settings: dict | None = None,
    groups: dict | None = None,
    check_system: bool = True,
) -> tuple[bool, str | None, set[int]]:
    settings = settings or load_app_settings()
    groups = groups if groups is not None else _load_group_map()
    proxy_settings = settings.get("proxy", {})

    proxy_reserved = set()
    for label, key in (("本地代理", "listen_port"), ("Tab 上报接收", "receiver_port")):
        port = _to_int(proxy_settings.get(key))
        if not _is_valid_port(port):
            return False, f"{label}端口必须是 1-65535", set()
        proxy_reserved.add(port)

    seen: dict[int, str] = {}
    listener_ports: set[int] = set()
    for group_name, group_data in groups.items():
        if not isinstance(group_data, dict):
            continue

        port = _to_int(group_data.get("port"))
        if not _is_valid_port(port):
            return False, f"分组 {group_name} 的端口必须是 1-65535", set()

        if port in proxy_reserved:
            return False, f"分组 {group_name} 的端口 {port} 与本地代理或 Tab 上报端口冲突", set()

        existing_group = seen.get(port)
        if existing_group:
            return False, f"分组 {group_name} 的端口 {port} 与分组 {existing_group} 重复", set()

        if check_system and not is_port_available(port):
            return False, f"分组 {group_name} 的端口 {port} 被占用，请修改端口后再启动代理", set()

        seen[port] = str(group_name)
        listener_ports.add(port)

    return True, None, listener_ports


def prepare_mihomo_runtime_ports(
    write_settings: bool = True,
    check_system: bool = True,
) -> tuple[bool, str | None, list[str]]:
    settings = load_app_settings()
    groups = _load_group_map()
    changes: list[str] = []

    proxy_settings = settings.get("proxy", {})
    proxy_ports: list[tuple[str, int]] = []
    for label, key in (("本地代理", "listen_port"), ("Tab 上报接收", "receiver_port")):
        port = _to_int(proxy_settings.get(key))
        if not _is_valid_port(port):
            return False, f"{label}端口必须是 1-65535", changes
        proxy_ports.append((label, port))

    if proxy_ports[0][1] == proxy_ports[1][1]:
        return False, "本地代理端口和 Tab 上报接收端口不能相同", changes

    if check_system:
        for label, port in proxy_ports:
            if not is_port_available(port):
                return False, f"{label}端口 {port} 被占用，请修改设置后再启动代理", changes

    ok, error, listener_ports = validate_group_listener_ports(settings, groups, check_system=check_system)
    if not ok:
        return False, error, changes

    defaults = get_default_app_settings()
    mihomo_settings = settings.setdefault("mihomo", {})
    reserved_ports = {port for _label, port in proxy_ports}
    reserved_ports.update(listener_ports)

    mixed_start = _to_int(mihomo_settings.get("mixed_port")) or defaults["mihomo"]["mixed_port"]
    controller_start = _to_int(mihomo_settings.get("controller_port")) or defaults["mihomo"]["controller_port"]
    mixed_port = mixed_start
    mixed_reserved_ports = set(reserved_ports)
    if _is_valid_port(controller_start):
        mixed_reserved_ports.add(controller_start)
    if (
        not _is_valid_port(mixed_port)
        or mixed_port in reserved_ports
        or (check_system and not is_port_available(mixed_port))
    ):
        try:
            mixed_port = find_available_port(mixed_start, mixed_reserved_ports, check_system=check_system)
        except ValueError as exc:
            return False, f"mihomo mixed-port 分配失败：{exc}", changes
        changes.append(f"mihomo mixed-port {mixed_start} -> {mixed_port}")
        mihomo_settings["mixed_port"] = mixed_port

    reserved_ports.add(mixed_port)

    controller_port = controller_start
    if (
        not _is_valid_port(controller_port)
        or controller_port in reserved_ports
        or (check_system and not is_port_available(controller_port))
    ):
        try:
            controller_port = find_available_port(controller_start, reserved_ports, check_system=check_system)
        except ValueError as exc:
            return False, f"mihomo controller 端口分配失败：{exc}", changes
        changes.append(f"mihomo controller {controller_start} -> {controller_port}")
        mihomo_settings["controller_port"] = controller_port

    if changes and write_settings:
        ok, save_error = _save_yaml(APP_SETTINGS_FILE, _settings_for_save(settings))
        if not ok:
            return False, f"保存 app_settings.yaml 失败：{save_error}", changes

    return True, None, changes


def get_mihomo_mixed_port() -> int:
    return load_app_settings()["mihomo"]["mixed_port"]


def get_mihomo_controller_port() -> int:
    return load_app_settings()["mihomo"]["controller_port"]
