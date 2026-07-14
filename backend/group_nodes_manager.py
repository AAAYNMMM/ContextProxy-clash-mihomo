from datetime import datetime
from pathlib import Path

import yaml

from backend.atomic_writer import atomic_write_yaml
from backend.paths import CONFIG_DIR


ACTION = "list"
TARGET_GROUP = "Media"
TARGET_NODES: list[str] = []

GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"
NODE_POOL_FILE = CONFIG_DIR / "node_pool.yaml"

DEFAULT_GROUPS = {
    "Proxy": {
        "port": 7890,
        "nodes": [],
    },
    "AI": {
        "port": 7891,
        "nodes": [],
    },
    "Media": {
        "port": 7892,
        "nodes": [],
    },
}


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
        print(f"[group] failed to read {path}: {exc}")
        return default


def save_yaml(path: Path, data):
    atomic_write_yaml(path, data)


def load_group_nodes():
    data = load_yaml(GROUP_NODES_FILE, None)

    if not data:
        return {
            "updated_at": now_str(),
            "groups": DEFAULT_GROUPS,
        }

    if "groups" not in data:
        data["groups"] = DEFAULT_GROUPS

    return data


def save_group_nodes(data):
    data["updated_at"] = now_str()
    save_yaml(GROUP_NODES_FILE, data)


def load_node_pool_names() -> set[str]:
    data = load_yaml(NODE_POOL_FILE, {})
    nodes = data.get("nodes", {}) if isinstance(data, dict) else {}
    return set(nodes.keys()) if isinstance(nodes, dict) else set()


def init_group_nodes():
    if GROUP_NODES_FILE.exists():
        print(f"[group] group_nodes.yaml already exists, skip: {GROUP_NODES_FILE}")
        return

    data = {
        "updated_at": now_str(),
        "groups": DEFAULT_GROUPS,
    }
    save_yaml(GROUP_NODES_FILE, data)

    print("[group] initialized group_nodes.yaml")
    print(f"[group] saved to: {GROUP_NODES_FILE}")


def list_groups():
    data = load_group_nodes()
    groups = data.get("groups", {})

    print(f"[group] config file: {GROUP_NODES_FILE}")

    for group_name, group_data in groups.items():
        nodes = group_data.get("nodes", [])
        port = group_data.get("port")

        print()
        print(f"[{group_name}] port={port}, nodes={len(nodes)}")

        for node in nodes:
            print(f"  - {node}")


def add_nodes_to_group(group_name: str, node_names: list[str]):
    data = load_group_nodes()
    groups = data.get("groups", {})

    if group_name not in groups:
        print(f"[group] group does not exist: {group_name}")
        return

    node_pool_names = load_node_pool_names()

    if not node_pool_names:
        print("[group] node_pool.yaml is empty or missing; rebuild node pool first")
        return

    current_nodes = groups[group_name].setdefault("nodes", [])
    added = 0
    skipped = 0

    for node_name in node_names:
        node_name = node_name.strip()
        if not node_name:
            continue

        if node_name not in node_pool_names:
            print(f"[group] node not found in node pool, skipped: {node_name}")
            skipped += 1
            continue

        if node_name in current_nodes:
            print(f"[group] node already exists, skipped: {node_name}")
            skipped += 1
            continue

        current_nodes.append(node_name)
        added += 1

    save_group_nodes(data)
    print(f"[group] added {added} nodes to {group_name}, skipped {skipped}")


def remove_nodes_from_group(group_name: str, node_names: list[str]):
    data = load_group_nodes()
    groups = data.get("groups", {})

    if group_name not in groups:
        print(f"[group] group does not exist: {group_name}")
        return

    current_nodes = groups[group_name].setdefault("nodes", [])
    before = len(current_nodes)
    remove_set = {name.strip() for name in node_names if name.strip()}
    groups[group_name]["nodes"] = [node for node in current_nodes if node not in remove_set]
    after = len(groups[group_name]["nodes"])

    save_group_nodes(data)
    print(f"[group] removed {before - after} nodes from {group_name}")


def clear_group(group_name: str):
    data = load_group_nodes()
    groups = data.get("groups", {})

    if group_name not in groups:
        print(f"[group] group does not exist: {group_name}")
        return

    count = len(groups[group_name].get("nodes", []))
    groups[group_name]["nodes"] = []
    save_group_nodes(data)

    print(f"[group] cleared {group_name}, removed {count} nodes")


def run_action():
    if ACTION == "init":
        init_group_nodes()
    elif ACTION == "list":
        list_groups()
    elif ACTION == "add":
        add_nodes_to_group(TARGET_GROUP, TARGET_NODES)
    elif ACTION == "remove":
        remove_nodes_from_group(TARGET_GROUP, TARGET_NODES)
    elif ACTION == "clear":
        clear_group(TARGET_GROUP)
    else:
        print(f"[error] unknown ACTION: {ACTION}")


if __name__ == "__main__":
    run_action()
