from datetime import datetime
from pathlib import Path

import yaml

from backend.atomic_writer import atomic_write_yaml
from backend.paths import CONFIG_DIR


NODE_POOL_FILE = CONFIG_DIR / "node_pool.yaml"
GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"


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
        print(f"[sync] failed to read {path}: {exc}")
        return default


def save_yaml(path: Path, data):
    atomic_write_yaml(path, data)


def sync_group_nodes_with_node_pool():
    """
    Remove group node references that no longer exist in node_pool.yaml.

    Returns a set of group names whose node list changed.
    """
    node_pool_data = load_yaml(NODE_POOL_FILE, {})
    group_nodes_data = load_yaml(GROUP_NODES_FILE, {})

    node_pool = node_pool_data.get("nodes", {}) if isinstance(node_pool_data, dict) else {}
    groups = group_nodes_data.get("groups", {}) if isinstance(group_nodes_data, dict) else {}

    if not isinstance(node_pool, dict):
        print("[sync] node_pool.yaml format invalid, skip sync")
        return set()

    if not isinstance(groups, dict):
        print("[sync] group_nodes.yaml format invalid, skip sync")
        return set()

    valid_node_names = set(node_pool.keys())
    total_removed = 0
    changed_groups = set()

    for group_name, group_data in groups.items():
        nodes = group_data.get("nodes", []) if isinstance(group_data, dict) else []

        if not isinstance(nodes, list):
            group_data["nodes"] = []
            changed_groups.add(group_name)
            continue

        before = len(nodes)
        group_data["nodes"] = [node_name for node_name in nodes if node_name in valid_node_names]

        removed = before - len(group_data["nodes"])
        total_removed += removed

        if removed:
            changed_groups.add(group_name)
            print(f"[sync] {group_name} removed {removed} invalid nodes")

    group_nodes_data["updated_at"] = now_str()
    save_yaml(GROUP_NODES_FILE, group_nodes_data)

    print(f"[sync] group_nodes.yaml synced, removed invalid nodes total={total_removed}")
    return changed_groups


if __name__ == "__main__":
    sync_group_nodes_with_node_pool()
