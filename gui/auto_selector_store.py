from pathlib import Path

from backend.paths import CONFIG_DIR

RUNTIME_SELECTED_NODES_FILE = CONFIG_DIR / "runtime_selected_nodes.yaml"


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}

    try:
        import yaml
    except ImportError:
        return {}

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def load_runtime_selected_nodes() -> dict:
    data = _load_yaml(RUNTIME_SELECTED_NODES_FILE)
    selected = {}

    rich_selected = data.get("selected", {})
    if isinstance(rich_selected, dict):
        for group_name, entry in rich_selected.items():
            if isinstance(entry, dict):
                node_name = entry.get("node")
                if node_name:
                    selected[str(group_name)] = {
                        "node": str(node_name),
                        "delay": entry.get("delay"),
                        "updated_at": entry.get("updated_at"),
                    }

    simple_selected = data.get("selected_nodes", {})
    if isinstance(simple_selected, dict):
        for group_name, node_name in simple_selected.items():
            if node_name and str(group_name) not in selected:
                selected[str(group_name)] = {
                    "node": str(node_name),
                    "delay": None,
                    "updated_at": None,
                }

    return selected


def get_selected_node_for_group(group_name: str) -> str | None:
    entry = load_runtime_selected_nodes().get(group_name)
    if not isinstance(entry, dict):
        return None

    node_name = entry.get("node")
    return str(node_name) if node_name else None
