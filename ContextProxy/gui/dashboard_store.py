from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GROUP_NODES_FILE = PROJECT_ROOT / "config" / "group_nodes.yaml"
NODE_POOL_FILE = PROJECT_ROOT / "config" / "node_pool.yaml"
SUBSCRIPTIONS_DIR = PROJECT_ROOT / "config" / "subscriptions"
DOMAIN_RULES_FILE = PROJECT_ROOT / "groups_domains.txt"
PROCESS_RULES_FILE = PROJECT_ROOT / "app_processes.txt"


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


def get_dashboard_stats() -> dict[str, int]:
    return {
        "groups": count_groups(),
        "nodes": count_nodes(),
        "subscriptions": count_subscriptions(),
        "rules": count_rules(),
    }
