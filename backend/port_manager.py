import socket
from datetime import datetime
from pathlib import Path

from backend.app_settings import load_app_settings
from backend.atomic_writer import atomic_write_yaml
from backend.paths import CONFIG_DIR


GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"

FIXED_RUNTIME_PORTS = (
    ("本地代理端口", "proxy", "listen_port"),
    ("Tab 上报接收端口", "proxy", "receiver_port"),
    ("mihomo mixed-port", "mihomo", "mixed_port"),
    ("mihomo controller", "mihomo", "controller_port"),
)


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
    """Return the next usable port, wrapping after 65535 when needed."""
    reserved_ports = set(reserved_ports or set())
    try:
        start = int(start_port or 1)
    except (TypeError, ValueError):
        start = 1
    if not _is_valid_port(start):
        start = 1

    for candidate in range(start, 65536):
        if candidate in reserved_ports:
            continue
        if check_system and not is_port_available(candidate):
            continue
        return candidate

    for candidate in range(1, start):
        if candidate in reserved_ports:
            continue
        if check_system and not is_port_available(candidate):
            continue
        return candidate

    raise ValueError("没有可用端口")


def _validate_fixed_runtime_ports(settings: dict, check_system: bool) -> tuple[bool, str | None, set[int]]:
    """Validate ports users configure directly; these are never auto-reassigned."""
    reserved_ports: set[int] = set()
    owners: dict[int, str] = {}

    for label, section, key in FIXED_RUNTIME_PORTS:
        section_data = settings.get(section, {})
        section_data = section_data if isinstance(section_data, dict) else {}
        port = _to_int(section_data.get(key))
        if not _is_valid_port(port):
            return False, f"{label}必须是 1-65535", set()

        existing_label = owners.get(port)
        if existing_label:
            return False, f"{label} {port} 与 {existing_label} 相同，请在设置中修改后再启动代理", set()

        if check_system and not is_port_available(port):
            return False, f"{label} {port} 被占用，请在设置中修改后再启动代理", set()

        owners[port] = label
        reserved_ports.add(port)

    return True, None, reserved_ports


def _fixed_port_owners(settings: dict) -> dict[int, str]:
    owners: dict[int, str] = {}
    for label, section, key in FIXED_RUNTIME_PORTS:
        section_data = settings.get(section, {})
        section_data = section_data if isinstance(section_data, dict) else {}
        port = _to_int(section_data.get(key))
        if _is_valid_port(port):
            owners[port] = label
    return owners


def collect_reserved_ports(
    include_proxy: bool = True,
    include_mihomo: bool = False,
    include_group_listeners: bool = True,
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

    if include_group_listeners:
        for group_data in _load_group_map().values():
            if not isinstance(group_data, dict):
                continue
            port = _to_int(group_data.get("port"))
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
    fixed_ok, fixed_error, _fixed_ports = _validate_fixed_runtime_ports(settings, check_system)
    if not fixed_ok:
        return False, fixed_error, set()

    fixed_owners = _fixed_port_owners(settings)
    seen: dict[int, str] = {}
    listener_ports: set[int] = set()
    for group_name, group_data in groups.items():
        if not isinstance(group_data, dict):
            return False, f"分组 {group_name} 配置格式无效", set()

        port = _to_int(group_data.get("port"))
        if not _is_valid_port(port):
            return False, f"分组 {group_name} 的端口必须是 1-65535", set()

        fixed_owner = fixed_owners.get(port)
        if fixed_owner:
            return False, f"分组 {group_name} 的端口 {port} 与 {fixed_owner} 冲突", set()

        existing_group = seen.get(port)
        if existing_group:
            return False, f"分组 {group_name} 的端口 {port} 与分组 {existing_group} 重复", set()

        if check_system and not is_port_available(port):
            return False, f"分组 {group_name} 的端口 {port} 被占用", set()

        seen[port] = str(group_name)
        listener_ports.add(port)

    return True, None, listener_ports


def _assign_runtime_port(
    preferred_port,
    fallback_port: int,
    reserved_ports: set[int],
    *,
    check_system: bool,
) -> tuple[int, bool]:
    """Keep a usable preferred group port, or reserve the next available one."""
    preferred = _to_int(preferred_port)
    start_port = preferred if _is_valid_port(preferred) else fallback_port
    if (
        _is_valid_port(preferred)
        and preferred not in reserved_ports
        and (not check_system or is_port_available(preferred))
    ):
        reserved_ports.add(preferred)
        return preferred, False

    allocated = find_available_port(start_port, reserved_ports, check_system=check_system)
    reserved_ports.add(allocated)
    return allocated, allocated != preferred


def prepare_mihomo_runtime_ports(
    write_settings: bool = True,
    check_system: bool = True,
) -> tuple[bool, str | None, list[str]]:
    """Check fixed ports and auto-assign only group listener ports.

    The ContextProxy listener, Tab receiver, and mihomo's two global ports are
    explicit user settings.  Silently changing any of them breaks external
    clients, especially the browser extension, so conflicts fail closed.  A
    group listener is internal to the generated mihomo configuration and can
    safely move to the next available port.
    """
    settings = load_app_settings()
    fixed_ok, fixed_error, reserved_ports = _validate_fixed_runtime_ports(settings, check_system)
    if not fixed_ok:
        return False, fixed_error, []

    group_document = _load_yaml(GROUP_NODES_FILE)
    groups = group_document.get("groups", {}) if isinstance(group_document, dict) else {}
    if not isinstance(groups, dict):
        return False, "group_nodes.yaml groups 格式无效", []

    changes: list[str] = []
    groups_changed = False
    for group_name, group_data in groups.items():
        if not isinstance(group_data, dict):
            return False, f"分组 {group_name} 配置格式无效", changes

        # A single global external-controller is generated for mihomo.  Older
        # per-group controller values were never consumed at runtime.
        if "controller" in group_data:
            group_data.pop("controller", None)
            groups_changed = True
            changes.append(f"分组 {group_name} 已移除无效 external-controller 配置")

        previous = _to_int(group_data.get("port"))
        try:
            port, changed = _assign_runtime_port(
                previous,
                7890,
                reserved_ports,
                check_system=check_system,
            )
        except ValueError as exc:
            return False, f"分组 {group_name} 端口分配失败：{exc}", changes

        if changed:
            changes.append(f"分组 {group_name} 端口 {previous if previous is not None else '无效'} -> {port}")
            group_data["port"] = port
            groups_changed = True

    if write_settings and groups_changed:
        group_document["groups"] = groups
        group_document["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ok, save_error = _save_yaml(GROUP_NODES_FILE, group_document)
        if not ok:
            return False, f"保存 group_nodes.yaml 失败：{save_error}", changes

        from backend.runtime_config import reload_group_config

        reload_group_config()

    return True, None, changes


def get_mihomo_mixed_port() -> int:
    return load_app_settings()["mihomo"]["mixed_port"]


def get_mihomo_controller_port() -> int:
    return load_app_settings()["mihomo"]["controller_port"]
