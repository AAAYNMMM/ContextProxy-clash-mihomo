from pathlib import Path
from datetime import datetime
import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent

NODE_POOL_FILE = PROJECT_ROOT / "config" / "node_pool.yaml"
GROUP_NODES_FILE = PROJECT_ROOT / "config" / "group_nodes.yaml"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_yaml(path: Path, default):
    if not path.exists():
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if data is not None else default
    except Exception as e:
        print(f"[鍚屾] 璇诲彇澶辫触: {path} | {e}")
        return default


def save_yaml(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            allow_unicode=True,
            sort_keys=False,
        )


def sync_group_nodes_with_node_pool():
    """
    鏍规嵁 node_pool.yaml 鑷姩娓呯悊 group_nodes.yaml銆?
    濡傛灉鏌愪釜鍒嗙粍寮曠敤浜嗗凡缁忎笉瀛樺湪鐨勮妭鐐癸紝鐩存帴鍒犻櫎杩欎釜寮曠敤銆?
    杩斿洖锛氬彂鐢熻妭鐐瑰紩鐢ㄥ彉鍖栫殑鍒嗙粍闆嗗悎銆?    """
    node_pool_data = load_yaml(NODE_POOL_FILE, {})
    group_nodes_data = load_yaml(GROUP_NODES_FILE, {})

    node_pool = node_pool_data.get("nodes", {})
    groups = group_nodes_data.get("groups", {})

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
        nodes = group_data.get("nodes", [])

        if not isinstance(nodes, list):
            group_data["nodes"] = []
            changed_groups.add(group_name)
            continue

        before = len(nodes)

        group_data["nodes"] = [
            node_name for node_name in nodes
            if node_name in valid_node_names
        ]

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
