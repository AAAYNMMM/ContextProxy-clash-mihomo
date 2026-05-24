import sys
import subprocess
from datetime import datetime
from pathlib import Path

import yaml


from backend.paths import CONFIG_DIR, MIHOMO_DIR, PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app_settings import get_mihomo_settings, get_proxy_settings
from backend.group_sync import sync_group_nodes_with_node_pool
from backend.mihomo_paths import get_mihomo_exe_path, mihomo_exe_missing_message
from backend.node_normalizer import normalize_proxy_node
from backend.process_utils import open_log_file, run_hidden


NODE_POOL_FILE = CONFIG_DIR / "node_pool.yaml"
GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"
MAIN_CONFIG_FILE = MIHOMO_DIR / "config.yaml"
TEMP_CONFIG_FILE = MIHOMO_DIR / "config.yaml.tmp"

ACTION = "generate"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_yaml(path: Path, default):
    if not path.exists():
        return default

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except Exception as exc:
        print(f"[generator] read failed: {path} | {exc}")
        return default

    return data if data is not None else default


def save_yaml(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False)


def load_node_pool() -> dict:
    data = load_yaml(NODE_POOL_FILE, {})
    nodes = data.get("nodes", {}) if isinstance(data, dict) else {}
    return nodes if isinstance(nodes, dict) else {}


def load_group_nodes() -> dict:
    data = load_yaml(GROUP_NODES_FILE, {})
    groups = data.get("groups", {}) if isinstance(data, dict) else {}
    return groups if isinstance(groups, dict) else {}


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _node_name(fallback_name: str, node: dict) -> str:
    return str(node.get("name") or fallback_name).strip()


def _all_proxies(node_pool: dict) -> tuple[list[dict], set[str]]:
    proxies = []
    names = set()

    for fallback_name, node in node_pool.items():
        if not isinstance(node, dict):
            continue

        proxy = normalize_proxy_node(dict(node))
        name = _node_name(str(fallback_name), proxy)
        if not name:
            continue

        proxy["name"] = name
        proxies.append(proxy)
        names.add(name)

    return proxies, names


def _reserved_runtime_ports(groups: dict) -> set[int]:
    proxy_settings = get_proxy_settings()
    reserved = set()

    for value in (
        proxy_settings.get("listen_port"),
        proxy_settings.get("receiver_port"),
    ):
        port = _to_int(value)
        if port:
            reserved.add(port)

    for group_data in groups.values():
        if not isinstance(group_data, dict):
            continue
        port = _to_int(group_data.get("port"))
        if port:
            reserved.add(port)

    return reserved


def _validate_mihomo_ports(groups: dict, mixed_port: int, controller_port: int):
    if mixed_port == controller_port:
        raise ValueError("mihomo mixed_port and controller_port cannot be the same")

    reserved = _reserved_runtime_ports(groups)
    if mixed_port in reserved:
        raise ValueError(f"mihomo mixed_port conflicts with an existing port: {mixed_port}")
    if controller_port in reserved:
        raise ValueError(f"mihomo controller_port conflicts with an existing port: {controller_port}")

    seen = set()
    for group_name, group_data in groups.items():
        if not isinstance(group_data, dict):
            continue

        port = _to_int(group_data.get("port"))
        if port is None:
            raise ValueError(f"{group_name} missing valid listener port")
        if port in seen:
            raise ValueError(f"duplicate listener port: {port}")
        seen.add(port)


def _group_proxy_names(group_name: str, group_data: dict, node_names: set[str]) -> list[str]:
    selected_nodes = group_data.get("nodes", [])
    if not isinstance(selected_nodes, list):
        selected_nodes = []

    result = []
    missing = []
    for node_name in selected_nodes:
        node_name = str(node_name).strip()
        if not node_name:
            continue
        if node_name in node_names:
            result.append(node_name)
        else:
            missing.append(node_name)

    if missing:
        print(f"[generator] {group_name} ignored missing nodes: {missing}")

    if not result:
        print(f"[generator] {group_name} has no valid nodes; using REJECT")
        return ["REJECT"]

    return result


def build_mihomo_config() -> dict:
    node_pool = load_node_pool()
    groups = load_group_nodes()
    if not groups:
        raise ValueError("group_nodes.yaml has no groups")

    proxies, node_names = _all_proxies(node_pool)
    mihomo_settings = get_mihomo_settings()
    mixed_port = _to_int(mihomo_settings.get("mixed_port")) or 7899
    controller_port = _to_int(mihomo_settings.get("controller_port")) or 9090
    _validate_mihomo_ports(groups, mixed_port, controller_port)

    proxy_groups = []
    listeners = []

    for group_name, group_data in groups.items():
        if not isinstance(group_data, dict):
            continue

        listener_port = _to_int(group_data.get("port"))
        if listener_port is None:
            raise ValueError(f"{group_name} missing valid listener port")

        proxy_groups.append(
            {
                "name": str(group_name),
                "type": "select",
                "proxies": _group_proxy_names(str(group_name), group_data, node_names),
            }
        )
        listeners.append(
            {
                "name": f"{group_name}-in",
                "type": "mixed",
                "listen": "127.0.0.1",
                "port": listener_port,
                "proxy": str(group_name),
            }
        )

    if "Proxy" not in {group["name"] for group in proxy_groups}:
        raise ValueError("Proxy group is required")

    # Keep every node referenced by at least one proxy-group. Some mihomo
    # protocol implementations initialize and test nodes more reliably when
    # they are part of a group, while routing still uses the user groups only.
    if node_names:
        proxy_groups.append(
            {
                "name": "ContextProxy_NodePool",
                "type": "select",
                "proxies": sorted(node_names),
            }
        )

    return {
        "mixed-port": mixed_port,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "ipv6": False,
        "unified-delay": True,
        "tcp-concurrent": True,
        "global-client-fingerprint": "chrome",
        "external-controller": f"127.0.0.1:{controller_port}",
        "profile": {
            "store-selected": False,
            "store-fake-ip": False,
        },
        "proxies": proxies,
        "proxy-groups": proxy_groups,
        "listeners": listeners,
        "rules": [
            "MATCH,Proxy",
        ],
    }


def _cleanup_old_group_configs():
    for config_file in MIHOMO_DIR.glob("config-*.yaml"):
        try:
            config_file.unlink()
            print(f"[generator] removed old group config: {config_file.name}")
        except Exception as exc:
            print(f"[generator] failed to remove old group config {config_file.name}: {exc}")


def _check_mihomo_config(config_path: Path):
    mihomo_exe_file = get_mihomo_exe_path()
    if not mihomo_exe_file.is_file():
        raise FileNotFoundError(mihomo_exe_missing_message(mihomo_exe_file))

    try:
        result = run_hidden(
            [str(mihomo_exe_file), "-t", "-f", str(config_path)],
            cwd=MIHOMO_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"mihomo config check timeout: {exc}") from exc

    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
    with open_log_file("mihomo_check.log") as log_file:
        log_file.write(
            f"\n[check] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
            f"{mihomo_exe_file} -t -f {config_path}\n"
        )
        if output:
            log_file.write(output + "\n")
        log_file.write(f"returncode={result.returncode}\n")

    if result.returncode != 0:
        raise RuntimeError(f"mihomo config check failed: {output or result.returncode}")

    if output:
        print(f"[generator] mihomo config check passed: {output}")
    else:
        print("[generator] mihomo config check passed")


def check_group_nodes():
    node_pool = load_node_pool()
    groups = load_group_nodes()
    ok = True

    if not node_pool:
        print("[generator] node_pool.yaml is empty")
        ok = False

    if not groups:
        print("[generator] group_nodes.yaml has no groups")
        return False

    for group_name, group_data in groups.items():
        nodes = group_data.get("nodes", []) if isinstance(group_data, dict) else []
        print(f"[check] group={group_name}, nodes={len(nodes) if isinstance(nodes, list) else 0}")
        if isinstance(nodes, list):
            for node_name in nodes:
                if node_name not in node_pool:
                    print(f"[check] missing node: {group_name} -> {node_name}")
                    ok = False

    return ok


def generate_all_configs():
    config = build_mihomo_config()
    save_yaml(TEMP_CONFIG_FILE, config)
    try:
        _check_mihomo_config(TEMP_CONFIG_FILE)
        TEMP_CONFIG_FILE.replace(MAIN_CONFIG_FILE)
    except Exception:
        try:
            TEMP_CONFIG_FILE.unlink()
        except Exception:
            pass
        raise
    _cleanup_old_group_configs()
    print(f"[generator] generated {MAIN_CONFIG_FILE}")
    return {"config.yaml"}


def run_action():
    if ACTION == "generate":
        sync_group_nodes_with_node_pool()
        generate_all_configs()
    elif ACTION == "check":
        check_group_nodes()
    else:
        print(f"[error] unknown ACTION: {ACTION}")


if __name__ == "__main__":
    run_action()
