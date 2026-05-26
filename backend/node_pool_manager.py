from datetime import datetime
from pathlib import Path

import yaml

from backend.atomic_writer import atomic_write_yaml
from backend.node_normalizer import normalize_proxy_node
from backend.paths import CONFIG_DIR


SUBSCRIPTIONS_DIR = CONFIG_DIR / "subscriptions"
NODE_POOL_FILE = CONFIG_DIR / "node_pool.yaml"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_yaml(path: Path, default):
    if not path.exists():
        return default

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
            return data if data is not None else default
    except Exception as exc:
        print(f"[node_pool] failed to read {path}: {exc}")
        return default


def save_yaml(path: Path, data):
    atomic_write_yaml(path, data)


def load_subscription_nodes() -> list[dict]:
    """Read all proxies from config/subscriptions/*_nodes.yaml."""
    if not SUBSCRIPTIONS_DIR.exists():
        print(f"[node_pool] subscription node directory does not exist: {SUBSCRIPTIONS_DIR}")
        return []

    all_nodes = []
    files = sorted(SUBSCRIPTIONS_DIR.glob("*_nodes.yaml"))

    if not files:
        print("[node_pool] no subscription node files found")
        return []

    for file in files:
        data = load_yaml(file, {})
        proxies = data.get("proxies", []) if isinstance(data, dict) else []
        if not isinstance(proxies, list):
            continue

        for node in proxies:
            if not isinstance(node, dict):
                continue

            name = str(node.get("name", "")).strip()
            if not name:
                continue

            all_nodes.append(node)

    return all_nodes


def rebuild_node_pool():
    """Rebuild config/node_pool.yaml from subscription node files."""
    nodes = load_subscription_nodes()
    node_map = {}

    for node in nodes:
        name = str(node.get("name", "")).strip()
        if not name:
            continue
        node_map[name] = normalize_proxy_node(node)

    output = {
        "updated_at": now_str(),
        "node_count": len(node_map),
        "nodes": node_map,
    }

    save_yaml(NODE_POOL_FILE, output)

    print("[node_pool] generated unified node pool")
    print(f"[node_pool] node count: {len(node_map)}")
    print(f"[node_pool] saved to: {NODE_POOL_FILE}")


if __name__ == "__main__":
    rebuild_node_pool()
