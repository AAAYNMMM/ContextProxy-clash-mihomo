import base64
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
import yaml

from backend.atomic_writer import atomic_write_json, atomic_write_yaml
from backend.paths import CONFIG_DIR, PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.group_sync import sync_group_nodes_with_node_pool
from backend.node_normalizer import normalize_proxy_node
from backend.node_pool_manager import rebuild_node_pool


SUB_NAME = "订阅名称"
SUB_URL = "订阅链接"
ADD_SUB_NAME_TO_NODE_NAME = True

OUTPUT_DIR = CONFIG_DIR / "subscriptions"


def refresh_after_subscription_changed() -> str | None:
    """Rebuild persisted subscription state and best-effort apply it at runtime.

    Rebuilding the node pool and group references is part of the subscription
    transaction and must still raise on failure. Runtime application is a
    separate concern: users must be able to see, update, and delete a saved
    subscription even when mihomo is unavailable or rejects the generated
    runtime config.
    """
    from backend.config_apply import apply_core_config_change, apply_mihomo_config_change

    rebuild_node_pool()
    sync_group_nodes_with_node_pool()

    try:
        apply_mihomo_config_change("subscription_changed")
        ok, error, _payload = apply_core_config_change()
        if not ok:
            raise RuntimeError(error or "Go core reload 失败")
    except Exception as exc:
        warning = str(exc) or "运行配置应用失败"
        print(f"[subscription] persisted state synced, runtime apply deferred: {warning}")
        return warning

    print("[subscription] node pool, group nodes and mihomo config synced")
    return None


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


def _proxy_list_from_yaml_data(data) -> list:
    """Return a proxy list from common Clash/Mihomo subscription shapes."""
    if isinstance(data, dict):
        proxies = data.get("proxies", [])
        return proxies if isinstance(proxies, list) else []

    # Some provider exports are a plain YAML list of proxy objects.  Accepting
    # that shape costs nothing and keeps the importer protocol-agnostic.
    if isinstance(data, list):
        return data

    return []


def extract_proxies_from_yaml(text: str, sub_name: str) -> list[dict]:
    """Extract Clash/Mihomo proxies while preserving every node field.

    Mihomo is responsible for protocol support.  ContextProxy should only
    require the fields needed to identify a proxy in the UI/config: `name` and
    `type`.  Do not require `server`/`port`, because some mihomo proxy types or
    future protocols may not use the same shape, and mihomo's own config check
    is the source of truth for protocol validity.
    """
    try:
        data = yaml.safe_load(text)
    except Exception as exc:
        print(f"[subscription] YAML parse failed: {exc}")
        return []

    proxies = _proxy_list_from_yaml_data(data)
    if not proxies:
        return []

    result = []
    skipped = 0
    for node in proxies:
        if not isinstance(node, dict):
            skipped += 1
            continue

        name = str(node.get("name", "")).strip()
        node_type = str(node.get("type", "")).strip()

        if not name or not node_type:
            skipped += 1
            continue

        result.append(add_subscription_prefix_to_node(node, sub_name))

    if skipped:
        print(f"[subscription] skipped invalid proxy entries: {skipped}")

    return result


def decode_subscription(url: str, sub_name: str) -> list[dict]:
    raw_text = fetch_subscription(url)
    decoded_text = try_base64_decode(raw_text)

    # Prefer mihomo/Clash YAML passthrough.  This is what makes protocol support
    # future-proof: every proxy object under `proxies` is preserved and handed
    # back to mihomo instead of being reduced to a fixed field schema.
    nodes = extract_proxies_from_yaml(decoded_text, sub_name)
    if nodes:
        return nodes

    print("[subscription] no Clash/Mihomo YAML proxies were extracted")
    print("[subscription] to support all mihomo protocols, use subscriptions that expose a proxies YAML list")
    print("[subscription] URI line subscriptions are not converted by ContextProxy")
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

    atomic_write_json(json_file, json_data)
    atomic_write_yaml(yaml_file, yaml_data)

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
