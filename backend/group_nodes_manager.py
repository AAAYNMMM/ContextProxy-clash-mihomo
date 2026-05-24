from pathlib import Path
from datetime import datetime
import yaml

from backend.paths import CONFIG_DIR


# ============================================================
# 鎵嬪姩鎿嶄綔鍖猴細鐜板湪鍏堟敼杩欓噷锛屽悗闈?GUI 灏辨敼杩欎簺鍙橀噺
# ============================================================

ACTION = "add"
# 鍙€夛細
# "init"       鍒濆鍖?group_nodes.yaml
# "list"       鏌ョ湅褰撳墠鍒嗙粍
# "add"        缁欐煇涓垎缁勬坊鍔犺妭鐐?
# "remove"     浠庢煇涓垎缁勭Щ闄よ妭鐐?
# "clear"      娓呯┖鏌愪釜鍒嗙粍鑺傜偣

TARGET_GROUP = "Media"

# 娣诲姞/鍒犻櫎鑺傜偣鏃讹紝鎶婅妭鐐瑰畬鏁?name 鍐欒繘杩欓噷
TARGET_NODES = [
    "鑹績浜?| 馃嚫馃嚞鏂板姞鍧￠珮閫?7|BGP|CTCU",
    "鑹績浜?| 馃嚫馃嚞鏂板姞鍧￠珮閫?8|BGP|CTCU",
    "鑹績浜?| 馃嚫馃嚞鏂板姞鍧￠珮閫?6|BGP|CTCU",
]


# ============================================================
# 璺緞閰嶇疆
# ============================================================

GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"
NODE_POOL_FILE = CONFIG_DIR / "node_pool.yaml"


# ============================================================
# 榛樿鍒嗙粍
# ============================================================

DEFAULT_GROUPS = {
    "AI": {
        "port": 7891,
        "controller": 9090,
        "nodes": [],
    },
    "Proxy": {
        "port": 7890,
        "controller": 9093,
        "nodes": [],
    },
    "Media": {
        "port": 7892,
        "controller": 9091,
        "nodes": [],
    },
}


# ============================================================
# 宸ュ叿鍑芥暟
# ============================================================

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
        print(f"[鍒嗙粍] 璇诲彇澶辫触: {path} | {e}")
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
    nodes = data.get("nodes", {})

    if not isinstance(nodes, dict):
        return set()

    return set(nodes.keys())


# ============================================================
# 鍔熻兘
# ============================================================

def init_group_nodes():
    if GROUP_NODES_FILE.exists():
        print(f"[鍒嗙粍] group_nodes.yaml 宸插瓨鍦紝涓嶈鐩? {GROUP_NODES_FILE}")
        return

    data = {
        "updated_at": now_str(),
        "groups": DEFAULT_GROUPS,
    }

    save_yaml(GROUP_NODES_FILE, data)

    print("[鍒嗙粍] 宸插垵濮嬪寲 group_nodes.yaml")
    print(f"[鍒嗙粍] 淇濆瓨浣嶇疆: {GROUP_NODES_FILE}")


def list_groups():
    data = load_group_nodes()
    groups = data.get("groups", {})

    print(f"[鍒嗙粍] 閰嶇疆鏂囦欢: {GROUP_NODES_FILE}")

    for group_name, group_data in groups.items():
        nodes = group_data.get("nodes", [])
        port = group_data.get("port")
        controller = group_data.get("controller")

        print()
        print(f"[{group_name}] port={port}, controller={controller}, nodes={len(nodes)}")

        for node in nodes:
            print(f"  - {node}")


def add_nodes_to_group(group_name: str, node_names: list[str]):
    data = load_group_nodes()
    groups = data.get("groups", {})

    if group_name not in groups:
        print(f"[鍒嗙粍] 涓嶅瓨鍦ㄥ垎缁? {group_name}")
        return

    node_pool_names = load_node_pool_names()

    if not node_pool_names:
        print("[鍒嗙粍] node_pool.yaml 涓虹┖鎴栦笉瀛樺湪锛岃鍏堣繍琛?node_pool_manager.py")
        return

    current_nodes = groups[group_name].setdefault("nodes", [])

    added = 0
    skipped = 0

    for node_name in node_names:
        node_name = node_name.strip()

        if not node_name:
            continue

        if node_name not in node_pool_names:
            print(f"[鍒嗙粍] 鑺傜偣姹犱笉瀛樺湪锛岃烦杩? {node_name}")
            skipped += 1
            continue

        if node_name in current_nodes:
            print(f"[鍒嗙粍] 宸插瓨鍦紝璺宠繃: {node_name}")
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
        print(f"[鍒嗙粍] 涓嶅瓨鍦ㄥ垎缁? {group_name}")
        return

    current_nodes = groups[group_name].setdefault("nodes", [])

    before = len(current_nodes)

    remove_set = {name.strip() for name in node_names if name.strip()}

    groups[group_name]["nodes"] = [
        node for node in current_nodes
        if node not in remove_set
    ]

    after = len(groups[group_name]["nodes"])

    save_group_nodes(data)

    print(f"[group] removed {before - after} nodes from {group_name}")


def clear_group(group_name: str):
    data = load_group_nodes()
    groups = data.get("groups", {})

    if group_name not in groups:
        print(f"[鍒嗙粍] 涓嶅瓨鍦ㄥ垎缁? {group_name}")
        return

    count = len(groups[group_name].get("nodes", []))
    groups[group_name]["nodes"] = []

    save_group_nodes(data)

    print(f"[group] cleared {group_name}, removed {count} nodes")


# ============================================================
# VSCode 鐩存帴杩愯鍏ュ彛
# ============================================================

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
        print(f"[閿欒] 鏈煡 ACTION: {ACTION}")


if __name__ == "__main__":
    run_action()
