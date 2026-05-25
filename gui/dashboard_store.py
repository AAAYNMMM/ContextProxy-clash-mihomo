from pathlib import Path

from backend.paths import APP_PROCESSES_FILE, CONFIG_DIR, GROUPS_DOMAINS_FILE

GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"
NODE_POOL_FILE = CONFIG_DIR / "node_pool.yaml"
SUBSCRIPTIONS_DIR = CONFIG_DIR / "subscriptions"
DOMAIN_RULES_FILE = GROUPS_DOMAINS_FILE
PROCESS_RULES_FILE = APP_PROCESSES_FILE


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


def _count_rule_file(path: Path) -> int:
    if not path.is_file():
        return 0

    count = 0
    try:
        with open(path, "r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if line and not line.startswith("#") and "," in line:
                    count += 1
    except Exception:
        return 0

    return count


def count_groups() -> int:
    data = _load_yaml(GROUP_NODES_FILE)
    groups = data.get("groups", {})
    return len(groups) if isinstance(groups, dict) else 0


def count_nodes() -> int:
    data = _load_yaml(NODE_POOL_FILE)
    node_count = data.get("node_count")
    if isinstance(node_count, int):
        return node_count

    nodes = data.get("nodes", {})
    return len(nodes) if isinstance(nodes, dict) else 0


def count_subscriptions() -> int:
    if not SUBSCRIPTIONS_DIR.is_dir():
        return 0
    return len(list(SUBSCRIPTIONS_DIR.glob("*_nodes.yaml")))


def count_rules() -> int:
    return _count_rule_file(DOMAIN_RULES_FILE) + _count_rule_file(PROCESS_RULES_FILE)


def count_active_connections() -> int:
    try:
        from backend.core_config import get_core_listen_settings
        from backend.core_launcher import is_core_running
        from backend.local_http import local_get

        if is_core_running():
            _proxy_host, _proxy_port, api_host, api_port = get_core_listen_settings()
            response = local_get(f"http://{api_host}:{api_port}/metrics", timeout=0.3)
            if response.status_code == 200:
                data = response.json()
                return int(data.get("active") or 0)
    except Exception:
        pass

    return 0


def get_core_events(after_id: int | None = 0, limit: int = 100) -> dict:
    try:
        from backend.core_config import get_core_listen_settings
        from backend.core_launcher import is_core_running
        from backend.local_http import local_get

        if not is_core_running():
            return {"boot_id": None, "events": []}
        _proxy_host, _proxy_port, api_host, api_port = get_core_listen_settings()
        params = {"limit": int(limit or 100)}
        if after_id is not None:
            params["after_id"] = int(after_id or 0)
        response = local_get(
            f"http://{api_host}:{api_port}/events",
            params=params,
            timeout=0.5,
        )
        if response.status_code != 200:
            return {"boot_id": None, "events": []}
        data = response.json()
        boot_id = data.get("boot_id") if isinstance(data, dict) else None
        events = data.get("events", []) if isinstance(data, dict) else []
        return {"boot_id": boot_id, "events": events if isinstance(events, list) else []}
    except Exception:
        return {"boot_id": None, "events": []}


def get_dashboard_stats() -> dict[str, int]:
    return {
        "groups": count_groups(),
        "nodes": count_nodes(),
        "subscriptions": count_subscriptions(),
        "rules": count_rules(),
        "active_connections": count_active_connections(),
    }
