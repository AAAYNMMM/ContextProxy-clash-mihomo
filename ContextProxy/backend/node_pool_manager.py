from pathlib import Path
from datetime import datetime
import yaml

from backend.node_normalizer import normalize_proxy_node


# ============================================================
# 璺緞閰嶇疆
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUBSCRIPTIONS_DIR = PROJECT_ROOT / "config" / "subscriptions"
NODE_POOL_FILE = PROJECT_ROOT / "config" / "node_pool.yaml"


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
        print(f"[鑺傜偣姹燷 璇诲彇澶辫触: {path} | {e}")
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


def load_subscription_nodes() -> list[dict]:
    """
    璇诲彇 config/subscriptions/*_nodes.yaml 閲岀殑鎵€鏈?proxies銆?
    """
    if not SUBSCRIPTIONS_DIR.exists():
        print(f"[鑺傜偣姹燷 璁㈤槄鑺傜偣鐩綍涓嶅瓨鍦? {SUBSCRIPTIONS_DIR}")
        return []

    all_nodes = []

    files = sorted(SUBSCRIPTIONS_DIR.glob("*_nodes.yaml"))

    if not files:
        print("[鑺傜偣姹燷 娌℃湁鎵惧埌浠讳綍璁㈤槄鑺傜偣鏂囦欢")
        return []

    for file in files:
        data = load_yaml(file, {})

        proxies = data.get("proxies", [])

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


# ============================================================
# 鑺傜偣姹犵敓鎴?
# ============================================================

def rebuild_node_pool():
    """
    閲嶆柊鐢熸垚缁熶竴鑺傜偣姹犮€?

    瑙勫垯锛?
    1. 鑺傜偣鍚嶄綔涓哄敮涓€ key
    2. 鍚屽悕鑺傜偣鍚庤鍙栫殑瑕嗙洊鍏堣鍙栫殑
    3. 璁㈤槄鏂囦欢閲屾病鏈夌殑鏃ц妭鐐逛笉浼氫繚鐣?
    """
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
    print(f"[鑺傜偣姹燷 鑺傜偣鏁伴噺: {len(node_map)}")
    print(f"[鑺傜偣姹燷 淇濆瓨浣嶇疆: {NODE_POOL_FILE}")


# ============================================================
# VSCode 鐩存帴杩愯鍏ュ彛
# ============================================================

if __name__ == "__main__":
    rebuild_node_pool()
