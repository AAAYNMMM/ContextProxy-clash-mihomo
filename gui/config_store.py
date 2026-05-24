from datetime import datetime
from pathlib import Path
import sys

from backend.paths import CONFIG_DIR, PROJECT_ROOT

NODE_POOL_FILE = CONFIG_DIR / "node_pool.yaml"
GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"


def _load_yaml(path: Path) -> tuple[dict, str | None]:
    if not path.is_file():
        return {}, f"file not found: {path}"

    try:
        import yaml
    except ImportError:
        return {}, "PyYAML is not installed"

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
    except Exception as exc:
        return {}, str(exc)

    if not isinstance(data, dict):
        return {}, f"invalid yaml root: {path}"

    return data, None


def _save_yaml(path: Path, data: dict) -> tuple[bool, str | None]:
    try:
        import yaml
    except ImportError:
        return False, "PyYAML is not installed"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as file:
            yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False)
    except Exception as exc:
        return False, str(exc)

    return True, None


def load_node_pool() -> tuple[dict, str | None]:
    data, error = _load_yaml(NODE_POOL_FILE)
    if error:
        return {}, error

    nodes = data.get("nodes", {})
    if not isinstance(nodes, dict):
        return {}, "node_pool.yaml nodes is not a dict"

    return nodes, None


def load_group_nodes_config() -> tuple[dict, str | None]:
    data, error = _load_yaml(GROUP_NODES_FILE)
    if error:
        return {"groups": {}}, error

    groups = data.get("groups", {})
    if not isinstance(groups, dict):
        data["groups"] = {}

    return data, None


def save_group_nodes_config(data: dict) -> tuple[bool, str | None]:
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if "groups" not in data or not isinstance(data["groups"], dict):
        data["groups"] = {}
    return _save_yaml(GROUP_NODES_FILE, data)


def generate_mihomo_configs() -> tuple[bool, str | None]:
    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        from backend.mihomo_config_generator import generate_all_configs

        generate_all_configs()
    except Exception as exc:
        return False, str(exc)

    return True, None


def extract_subscription_source(node_name: str) -> str:
    separator = " | "
    if separator not in node_name:
        return "\u672a\u77e5"

    source = node_name.split(separator, 1)[0].strip()
    return source or "\u672a\u77e5"
