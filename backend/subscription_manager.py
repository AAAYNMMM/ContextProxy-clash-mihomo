import base64
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
import yaml

from backend.paths import CONFIG_DIR, PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.group_sync import sync_group_nodes_with_node_pool
from backend.mihomo_config_generator import generate_all_configs
from backend.node_normalizer import normalize_proxy_node
from backend.node_pool_manager import rebuild_node_pool


SUB_NAME = "订阅名称"
SUB_URL = "订阅链接"
ADD_SUB_NAME_TO_NODE_NAME = True

OUTPUT_DIR = CONFIG_DIR / "subscriptions"


def refresh_after_subscription_changed():
    rebuild_node_pool()
    sync_group_nodes_with_node_pool()
    generate_all_configs()
    print("[subscription] node pool, group nodes and mihomo config synced")


def safe_filename(name: str) -> str:
    """Return a filesystem-safe subscription file base name."""
    name = name.strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name or "subscription"


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fix_base64_padding(value: str) -> str:
    value = value.strip().replace("\n", "").replace("\r", "")
    return value + "=" * (-len(value) % 4)


def try_base64_decode(text: str) -> str:
    try:
        decoded = base64.b64decode(fix_base64_padding(text)).decode("utf-8", errors="ignore")
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


def add_subscription_prefix_to_node(node: dict, sub_name: str) -> dict:
    """Prefix node names with the subscription name to avoid duplicate names."""
    new_node = dict(node)
    old_name = str(new_node.get("name", "")).strip() or "Unnamed"
    prefix = f"{sub_name} | "

    if ADD_SUB_NAME_TO_NODE_NAME and not old_name.startswith(prefix):
        new_node["name"] = f"{prefix}{old_name}"
    else:
        new_node["name"] = old_name

    return normalize_proxy_node(new_node)


def extract_proxies_from_yaml(text: str, sub_name: str) -> list[dict]:
    """Extract Clash/Mihomo proxies while preserving node fields."""
    try:
        data = yaml.safe_load(text)
    except Exception as exc:
        print(f"[subscription] YAML parse failed: {exc}")
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

    print("[subscription] current parser mainly supports Clash/Mihomo YAML subscriptions")
    print("[subscription] vmess://, ss://, trojan:// and vless:// line subscriptions are not converted yet")
    return []


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

    with open(json_file, "w", encoding="utf-8") as file:
        json.dump(json_data, file, ensure_ascii=False, indent=2)

    with open(yaml_file, "w", encoding="utf-8") as file:
        yaml.safe_dump(yaml_data, file, allow_unicode=True, sort_keys=False)

    print("[subscription] saved nodes")
    print(f"[subscription] name: {sub_name}")
    print(f"[subscription] node count: {len(nodes)}")
    print(f"[subscription] JSON saved to: {json_file}")
    print(f"[subscription] YAML saved to: {yaml_file}")


if __name__ == "__main__":
    if not SUB_URL or SUB_URL == "YOUR_SUBSCRIPTION_URL":
        print("[error] please fill SUB_URL first")
    else:
        nodes = decode_subscription(SUB_URL, SUB_NAME)

        if not nodes:
            print("[subscription] no nodes extracted; old files were not changed")
        else:
            save_nodes(SUB_NAME, nodes)
            refresh_after_subscription_changed()
