import json
import sys
from datetime import datetime
from pathlib import Path

from backend.paths import CONFIG_DIR, PROJECT_ROOT

SUBSCRIPTIONS_DIR = CONFIG_DIR / "subscriptions"
SUBSCRIPTIONS_META_FILE = SUBSCRIPTIONS_DIR / "subscriptions.yaml"


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


def _read_json_updated_at(path: Path) -> str:
    if not path.is_file():
        return ""

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception:
        return ""

    if not isinstance(data, dict):
        return ""

    return str(data.get("updated_at") or "")


def _subscription_node_files(file_base: str) -> list[Path]:
    return [
        SUBSCRIPTIONS_DIR / f"{file_base}_nodes.yaml",
        SUBSCRIPTIONS_DIR / f"{file_base}_nodes.json",
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
        path.write_bytes(content)



def _friendly_subscription_error(exc: Exception) -> str:
    text = str(exc)
    lower = text.lower()

    if "403" in text:
        return "subscription returned 403; the link may be expired or unauthorized"

    if "404" in text:
        return "璁㈤槄杩斿洖 404锛氶摼鎺ヤ笉瀛樺湪鎴栬闃呭湴鍧€濉啓閿欒"

    if "timed out" in lower or "timeout" in lower or "read timed out" in lower:
        return "璁㈤槄璇锋眰瓒呮椂锛氳妫€鏌ョ綉缁滄垨绋嶅悗閲嶈瘯"

    if "connection" in lower or "failed to establish" in lower or "connection aborted" in lower:
        return "subscription connection failed; check network, proxy, or subscription server"

    if "鏈彁鍙栧埌鑺傜偣" in text or "鑺傜偣" in text and "0" in text:
        return "璁㈤槄瑙ｆ瀽鎴愬姛浣嗘病鏈夋彁鍙栧埌鑺傜偣锛氬彲鑳戒笉鏄?Clash/Mihomo YAML 璁㈤槄锛屾垨璁㈤槄鍐呭涓虹┖"

    return text or "鏈煡閿欒"

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

    SUBSCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    with open(SUBSCRIPTIONS_META_FILE, "w", encoding="utf-8") as file:
        yaml.safe_dump(meta, file, allow_unicode=True, sort_keys=False)


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
    for yaml_file in sorted(SUBSCRIPTIONS_DIR.glob("*_nodes.yaml")):
        sub_name = yaml_file.name.removesuffix("_nodes.yaml")
        seen_names.add(sub_name)
        json_file = SUBSCRIPTIONS_DIR / f"{sub_name}_nodes.json"
        meta_entry = meta_subscriptions.get(sub_name, {})
        meta_entry = meta_entry if isinstance(meta_entry, dict) else {}
        node_count = count_nodes_in_subscription_file(yaml_file)

        rows.append(
            {
                "name": sub_name,
                "node_count": node_count,
                "yaml_file": yaml_file.name,
                "json_file": json_file.name if json_file.exists() else "",
                "updated_at": str(meta_entry.get("updated_at") or _read_json_updated_at(json_file)),
                "status": "\u5df2\u52a0\u8f7d",
                "url": str(meta_entry.get("url") or ""),
            }
        )

    for sub_name, meta_entry in sorted(meta_subscriptions.items()):
        if sub_name in seen_names:
            continue
        meta_entry = meta_entry if isinstance(meta_entry, dict) else {}
        rows.append(
            {
                "name": str(meta_entry.get("name") or sub_name),
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

        files = _subscription_node_files(safe_filename(name))
        snapshot = _snapshot_files(files)

        try:
            save_nodes(name, nodes)
            refresh_after_subscription_changed()
            update_subscription_meta(name, url, len(nodes))
        except Exception:
            _restore_snapshot(snapshot)
            raise
    except Exception as exc:
        return False, _friendly_subscription_error(exc)

    return True, None


def delete_subscription_from_gui(name: str) -> tuple[bool, str | None, list[str]]:
    name = name.strip()
    if not name:
        return False, "\u8bf7\u5148\u9009\u62e9\u8981\u5220\u9664\u7684\u8ba2\u9605", []

    _ensure_project_root_on_path()

    try:
        from backend.subscription_manager import refresh_after_subscription_changed, safe_filename

        file_base = safe_filename(name)
        files = _subscription_node_files(file_base)

        messages = []
        for file_path in files:
            if file_path.exists():
                file_path.unlink()
                messages.append(f"\u5df2\u5220\u9664: {file_path.name}")
            else:
                messages.append(f"\u6587\u4ef6\u4e0d\u5b58\u5728: {file_path.name}")

        refresh_after_subscription_changed()
        remove_subscription_meta(name)
    except Exception as exc:
        return False, str(exc), []

    return True, None, messages
