import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import base64
import json
import re
from datetime import datetime

import requests
import yaml

from backend.node_pool_manager import rebuild_node_pool
from backend.group_sync import sync_group_nodes_with_node_pool
from backend.mihomo_config_generator import generate_all_configs
from backend.node_normalizer import normalize_proxy_node

# ============================================================
# 鎵嬪姩鎿嶄綔鍖猴細鐜板湪鍏堟敼杩欓噷锛屽悗闈?GUI 灏辨敼杩欎簺鍙橀噺
# ============================================================

SUB_NAME = "鏈哄満鍚嶇О"
SUB_URL = "璁㈤槄杩炴帴"

# 鑺傜偣鍚嶅墠缂€鏍煎紡锛?# True  鈫?鏈哄満A | 棣欐腐01
# False 鈫?棣欐腐01
ADD_SUB_NAME_TO_NODE_NAME = True


# ============================================================
# 璺緞閰嶇疆
# ============================================================

OUTPUT_DIR = PROJECT_ROOT / "config" / "subscriptions"


# ============================================================
# 宸ュ叿鍑芥暟
# ============================================================
def refresh_after_subscription_changed():

    rebuild_node_pool()
    sync_group_nodes_with_node_pool()
    generate_all_configs()
    print("[subscription] node pool, group nodes and mihomo config synced")

def safe_filename(name: str) -> str:
    """
    鎶婅闃呭悕杞崲鎴愬畨鍏ㄦ枃浠跺悕銆?    """
    name = name.strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name or "subscription"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fix_base64_padding(s: str) -> str:
    s = s.strip().replace("\n", "").replace("\r", "")
    return s + "=" * (-len(s) % 4)


def try_base64_decode(text: str) -> str:
    try:
        decoded = base64.b64decode(fix_base64_padding(text)).decode(
            "utf-8",
            errors="ignore",
        )

        if "proxies:" in decoded or "://" in decoded:
            return decoded

        return text

    except Exception:
        return text


def fetch_subscription(url: str) -> str:
    headers = {
        "User-Agent": "ClashMetaForAndroid/2.10.1",
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    return response.text


# ============================================================
# 鑺傜偣鎻愬彇
# ============================================================

def add_subscription_prefix_to_node(node: dict, sub_name: str) -> dict:
    """
    缁欒妭鐐瑰悕鍔犺闃呭悕鍓嶇紑锛岄伩鍏嶅涓闃呰妭鐐归噸鍚嶃€?    """
    new_node = dict(node)

    old_name = str(new_node.get("name", "")).strip()

    if not old_name:
        old_name = "Unnamed"

    prefix = f"{sub_name} | "

    if ADD_SUB_NAME_TO_NODE_NAME and not old_name.startswith(prefix):
        new_node["name"] = f"{prefix}{old_name}"
    else:
        new_node["name"] = old_name

    return normalize_proxy_node(new_node)


def extract_proxies_from_yaml(text: str, sub_name: str) -> list[dict]:
    """
    鍙粠 Clash/Mihomo YAML 涓彁鍙?proxies銆?    涓嶄繚鐣?rules / proxy-groups / dns / rule-providers 绛夊唴瀹广€?    """
    try:
        data = yaml.safe_load(text)
    except Exception as e:
        print(f"[閿欒] YAML 瑙ｆ瀽澶辫触: {e}")
        return []

    if not isinstance(data, dict):
        return []

    proxies = data.get("proxies", [])

    if not isinstance(proxies, list):
        return []

    result = []

    for node in proxies:
        if not isinstance(node, dict):
            continue

        name = str(node.get("name", "")).strip()
        node_type = str(node.get("type", "")).strip()
        server = str(node.get("server", "")).strip()
        port = node.get("port")

        if not name or not node_type or not server or not port:
            continue

        result.append(add_subscription_prefix_to_node(node, sub_name))

    return result


def decode_subscription(url: str, sub_name: str) -> list[dict]:
    raw_text = fetch_subscription(url)
    decoded_text = try_base64_decode(raw_text)

    if "proxies:" in decoded_text:
        return extract_proxies_from_yaml(decoded_text, sub_name)

    print("[璀﹀憡] 褰撳墠鑴氭湰涓昏鏀寔 Clash/Mihomo YAML 璁㈤槄")
    print("[璀﹀憡] 杩欎釜璁㈤槄鍙兘鏄?vmess:// / ss:// / trojan:// / vless:// 琛屾牸寮忥紝鏆傛湭杞崲")
    return []


# ============================================================
# 淇濆瓨
# ============================================================

def save_nodes(sub_name: str, nodes: list[dict]):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    file_base = safe_filename(sub_name)

    json_file = OUTPUT_DIR / f"{file_base}_nodes.json"
    yaml_file = OUTPUT_DIR / f"{file_base}_nodes.yaml"

    json_data = {
        "subscription_name": sub_name,
        "updated_at": now_str(),
        "node_count": len(nodes),
        "proxies": nodes,
    }

    yaml_data = {
        "proxies": nodes,
    }

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    with open(yaml_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            yaml_data,
            f,
            allow_unicode=True,
            sort_keys=False,
        )

    print("瑙ｆ瀽瀹屾垚")
    print(f"璁㈤槄鍚嶇О: {sub_name}")
    print(f"鑺傜偣鏁伴噺: {len(nodes)}")
    print(f"JSON 淇濆瓨鍒? {json_file}")
    print(f"YAML 淇濆瓨鍒? {yaml_file}")


# ============================================================
# VSCode 鐩存帴杩愯鍏ュ彛
# ============================================================

if __name__ == "__main__":
    if not SUB_URL or SUB_URL == "YOUR_SUBSCRIPTION_URL":
        print("[error] please fill SUB_URL first")
    else:
        nodes = decode_subscription(SUB_URL, SUB_NAME)

        if not nodes:
            print("[璁㈤槄] 娌℃湁鎻愬彇鍒拌妭鐐癸紝涓嶆洿鏂版棫鏂囦欢")
        else:
            save_nodes(SUB_NAME, nodes)
            refresh_after_subscription_changed()
