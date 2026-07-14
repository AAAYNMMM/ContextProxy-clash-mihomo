import json
import sys
from datetime import datetime
from pathlib import Path

from backend.atomic_writer import atomic_write_bytes, atomic_write_yaml
from backend.paths import CONFIG_DIR, PROJECT_ROOT

SUBSCRIPTIONS_DIR = CONFIG_DIR / "subscriptions"
SUBSCRIPTIONS_META_FILE = SUBSCRIPTIONS_DIR / "subscriptions.yaml"
NODE_POOL_FILE = CONFIG_DIR / "node_pool.yaml"
GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"


def _ensure_project_root_on_path():
    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def count_nodes_in_subscription_file(path: Path) -> int:
    if not path.is_file():
        return 0

    try:
        import yaml
    except ImportError:
        return 0

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
    except Exception:
        return 0

    proxies = data.get("proxies", []) if isinstance(data, dict) else []
    return len(proxies) if isinstance(proxies, list) else 0


def _read_json_metadata(path: Path) -> dict:
    if not path.is_file():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}

def _subscription_node_files(file_base: str) -> list[Path]:
    return [
        SUBSCRIPTIONS_DIR / f"{file_base}_nodes.yaml",
        SUBSCRIPTIONS_DIR / f"{file_base}_nodes.json",
    ]


def _subscription_state_files(file_base: str) -> list[Path]:
    """Files that must move together for a subscription mutation."""
    return [
        *_subscription_node_files(file_base),
        SUBSCRIPTIONS_META_FILE,
        NODE_POOL_FILE,
        GROUP_NODES_FILE,
    ]


def _snapshot_files(paths: list[Path]) -> dict[Path, bytes | None]:
    snapshot = {}
    for path in paths:
        snapshot[path] = path.read_bytes() if path.exists() else None
    return snapshot


def _restore_snapshot(snapshot: dict[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        if content is None:
            if path.exists():
                path.unlink()
            continue

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, content)



def _friendly_subscription_error(exc: Exception) -> str:
    text = str(exc)
    lower = text.lower()

    if "mihomo executable not found" in lower:
        return (
            "未找到 mihomo 核心，订阅更新未生效并已恢复原配置；"
            "请检查设置中的 mihomo 可执行文件"
        )

    if "mihomo config check failed" in lower:
        return f"mihomo 配置校验失败，订阅更新未生效并已恢复原配置：{text}"

    if "403" in text:
        return "订阅返回 403：链接可能已过期或无权限访问"

    if "404" in text:
        return "订阅返回 404：链接不存在或订阅地址填写错误"

    if "timed out" in lower or "timeout" in lower or "read timed out" in lower:
        return "订阅请求超时：请检查网络或稍后重试"

    if "connection" in lower or "failed to establish" in lower or "connection aborted" in lower:
        return "订阅连接失败：请检查网络、代理或订阅服务器"

    if "未提取到节点" in text or "节点" in text and "0" in text or "no nodes" in lower:
        return "订阅解析成功但没有提取到节点：可能不是 Clash/Mihomo YAML 订阅，或订阅内容为空"

    return text or "未知错误"


def _runtime_apply_warning(action: str, error: str | None) -> str | None:
    if not error:
        return None

    text = str(error)
    lower = text.lower()
    if "mihomo executable not found" in lower:
        detail = "未找到 mihomo 核心，请检查设置中的 mihomo 可执行文件"
    elif "mihomo config check failed" in lower:
        detail = f"mihomo 配置校验失败：{text}"
    else:
        detail = text
    return f"{action}，但运行配置暂未应用：{detail}"

def load_subscription_meta() -> dict:
    if not SUBSCRIPTIONS_META_FILE.is_file():
        return {"subscriptions": {}}

    try:
        import yaml
    except ImportError:
        return {"subscriptions": {}}

    try:
        with open(SUBSCRIPTIONS_META_FILE, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
    except Exception:
        return {"subscriptions": {}}

    if not isinstance(data, dict):
        return {"subscriptions": {}}

    subscriptions = data.get("subscriptions", {})
    if not isinstance(subscriptions, dict):
        data["subscriptions"] = {}

    return data


def save_subscription_meta(meta: dict) -> None:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is not installed") from exc

    if "subscriptions" not in meta or not isinstance(meta["subscriptions"], dict):
        meta["subscriptions"] = {}

    atomic_write_yaml(SUBSCRIPTIONS_META_FILE, meta)


def update_subscription_meta(name: str, url: str, node_count: int) -> None:
    meta = load_subscription_meta()
    meta["subscriptions"][name] = {
        "name": name,
        "url": url,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "node_count": node_count,
    }
    save_subscription_meta(meta)


def remove_subscription_meta(name: str) -> None:
    meta = load_subscription_meta()
    meta.get("subscriptions", {}).pop(name, None)
    save_subscription_meta(meta)


def get_subscription_url(name: str) -> str:
    meta = load_subscription_meta()
    entry = meta.get("subscriptions", {}).get(name, {})
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("url") or "")


def list_subscriptions() -> list[dict]:
    SUBSCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)

    meta = load_subscription_meta()
    meta_subscriptions = meta.get("subscriptions", {})
    if not isinstance(meta_subscriptions, dict):
        meta_subscriptions = {}

    rows = []
    seen_names = set()
    seen_metadata_keys = set()
    for yaml_file in sorted(SUBSCRIPTIONS_DIR.glob("*_nodes.yaml")):
        file_base = yaml_file.name.removesuffix("_nodes.yaml")
        json_file = SUBSCRIPTIONS_DIR / f"{file_base}_nodes.json"
        sidecar_metadata = _read_json_metadata(json_file)
        sub_name = str(sidecar_metadata.get("subscription_name") or file_base).strip() or file_base

        # Filenames are sanitized (for example, spaces become underscores),
        # while metadata is keyed by the original subscription name.
        meta_entry = meta_subscriptions.get(sub_name, {})
        if not isinstance(meta_entry, dict):
            meta_entry = meta_subscriptions.get(file_base, {})
        meta_entry = meta_entry if isinstance(meta_entry, dict) else {}
        node_count = count_nodes_in_subscription_file(yaml_file)
        seen_names.add(sub_name)
        seen_metadata_keys.update({file_base, sub_name})

        rows.append(
            {
                "name": sub_name,
                "node_count": node_count,
                "yaml_file": yaml_file.name,
                "json_file": json_file.name if json_file.exists() else "",
                "updated_at": str(
                    meta_entry.get("updated_at") or sidecar_metadata.get("updated_at") or ""
                ),
                "status": "\u5df2\u52a0\u8f7d",
                "url": str(meta_entry.get("url") or ""),
            }
        )

    for metadata_key, meta_entry in sorted(meta_subscriptions.items()):
        meta_entry = meta_entry if isinstance(meta_entry, dict) else {}
        sub_name = str(meta_entry.get("name") or metadata_key).strip() or str(metadata_key)
        if str(metadata_key) in seen_metadata_keys or sub_name in seen_names:
            continue
        rows.append(
            {
                "name": sub_name,
                "node_count": int(meta_entry.get("node_count") or 0),
                "yaml_file": "",
                "json_file": "",
                "updated_at": str(meta_entry.get("updated_at") or ""),
                "status": "\u8282\u70b9\u6587\u4ef6\u7f3a\u5931",
                "url": str(meta_entry.get("url") or ""),
            }
        )

    return rows




def update_subscription_from_gui(name: str, url: str) -> tuple[bool, str | None]:
    name = name.strip()
    url = url.strip()

    if not name:
        return False, "\u8ba2\u9605\u540d\u79f0\u4e0d\u80fd\u4e3a\u7a7a"

    if not url:
        return False, "\u8ba2\u9605\u94fe\u63a5\u4e0d\u80fd\u4e3a\u7a7a"

    _ensure_project_root_on_path()

    try:
        from backend.subscription_manager import (
            decode_subscription,
            refresh_after_subscription_changed,
            safe_filename,
            save_nodes,
        )

        nodes = decode_subscription(url, name)
        if not nodes:
            return False, "\u672a\u63d0\u53d6\u5230\u8282\u70b9\uff0c\u65e7\u8ba2\u9605\u6587\u4ef6\u5df2\u4fdd\u7559"

        file_base = safe_filename(name)
        snapshot = _snapshot_files(_subscription_state_files(file_base))

        try:
            save_nodes(name, nodes)
            update_subscription_meta(name, url, len(nodes))
            runtime_error = refresh_after_subscription_changed()
        except Exception:
            _restore_snapshot(snapshot)
            raise
    except Exception as exc:
        return False, _friendly_subscription_error(exc)

    return True, _runtime_apply_warning("订阅数据已保存", runtime_error)


def delete_subscription_from_gui(name: str) -> tuple[bool, str | None, list[str]]:
    name = name.strip()
    if not name:
        return False, "\u8bf7\u5148\u9009\u62e9\u8981\u5220\u9664\u7684\u8ba2\u9605", []

    _ensure_project_root_on_path()

    try:
        from backend.subscription_manager import refresh_after_subscription_changed, safe_filename

        file_base = safe_filename(name)
        files = _subscription_node_files(file_base)
        snapshot = _snapshot_files(_subscription_state_files(file_base))

        try:
            messages = []
            for file_path in files:
                if file_path.exists():
                    file_path.unlink()
                    messages.append(f"\u5df2\u5220\u9664: {file_path.name}")
                else:
                    messages.append(f"\u6587\u4ef6\u4e0d\u5b58\u5728: {file_path.name}")

            remove_subscription_meta(name)
            runtime_error = refresh_after_subscription_changed()
        except Exception:
            _restore_snapshot(snapshot)
            raise
    except Exception as exc:
        return False, str(exc), []

    return True, _runtime_apply_warning("订阅已删除", runtime_error), messages
