import asyncio
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import yaml

from backend.app_settings import get_auto_select_settings, get_mihomo_controller_port
from backend.delay_tester import is_main_controller_available, test_node_delay_via_main_controller
from backend.local_http import local_put


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GROUP_NODES_FILE = PROJECT_ROOT / "config" / "group_nodes.yaml"
RUNTIME_SELECTED_NODES_FILE = PROJECT_ROOT / "config" / "runtime_selected_nodes.yaml"

MONITOR_INTERVAL_SECONDS = 20
MAX_FAIL_COUNT = 2

_selected_nodes: dict[str, str] = {}
_selected_delays: dict[str, int | None] = {}
_fail_counts: dict[str, int] = {}
_monitor_task = None
_running = False


def _auto_select_settings() -> dict:
    return get_auto_select_settings()


def _monitor_interval_seconds() -> int:
    return int(_auto_select_settings().get("check_interval") or MONITOR_INTERVAL_SECONDS)


def _max_fail_count() -> int:
    return int(_auto_select_settings().get("max_fail_count") or MAX_FAIL_COUNT)


def _load_yaml(path: Path, default):
    if not path.is_file():
        return default

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except Exception as exc:
        print(f"[auto-select] read failed: {path} | {exc}")
        return default

    return data if data is not None else default


def _save_yaml(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False)


def _load_group_nodes() -> dict:
    data = _load_yaml(GROUP_NODES_FILE, {})
    groups = data.get("groups", {}) if isinstance(data, dict) else {}
    return groups if isinstance(groups, dict) else {}


def _load_runtime_selected_nodes():
    global _selected_nodes, _selected_delays

    data = _load_yaml(RUNTIME_SELECTED_NODES_FILE, {})
    selected_nodes = {}
    selected_delays = {}

    selected = data.get("selected", {}) if isinstance(data, dict) else {}
    if isinstance(selected, dict):
        for group_name, entry in selected.items():
            if not isinstance(entry, dict):
                continue
            node_name = entry.get("node")
            if node_name:
                group_name = str(group_name)
                selected_nodes[group_name] = str(node_name)
                selected_delays[group_name] = entry.get("delay")

    simple_selected = data.get("selected_nodes", {}) if isinstance(data, dict) else {}
    if isinstance(simple_selected, dict):
        for group_name, node_name in simple_selected.items():
            if node_name and str(group_name) not in selected_nodes:
                selected_nodes[str(group_name)] = str(node_name)
                selected_delays[str(group_name)] = None

    _selected_nodes = selected_nodes
    _selected_delays = selected_delays


def _save_runtime_selected_nodes():
    selected = {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for group_name, node_name in _selected_nodes.items():
        selected[group_name] = {
            "node": node_name,
            "delay": _selected_delays.get(group_name),
            "updated_at": now,
        }

    _save_yaml(
        RUNTIME_SELECTED_NODES_FILE,
        {
            "selected": selected,
            "selected_nodes": dict(_selected_nodes),
        },
    )


def _group_nodes(group_name: str) -> list[str]:
    group_data = _load_group_nodes().get(group_name, {})
    if not isinstance(group_data, dict):
        return []

    nodes = group_data.get("nodes", [])
    if not isinstance(nodes, list):
        return []

    return [str(node_name) for node_name in nodes if str(node_name).strip()]


def start_auto_selector():
    global _monitor_task, _running

    if _running:
        print("[auto-select] already running")
        return

    _running = True
    _load_runtime_selected_nodes()
    select_best_node_for_all_groups()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        print("[auto-select] no running event loop; startup selection only")
        return

    _monitor_task = loop.create_task(monitor_current_nodes())
    print("[auto-select] monitor started")


def stop_auto_selector():
    global _monitor_task, _running

    _running = False

    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()

    _monitor_task = None
    print("[auto-select] stopped")


def select_best_node_for_all_groups():
    groups = _load_group_nodes()

    for group_name, group_data in groups.items():
        if not isinstance(group_data, dict):
            continue

        select_best_node_for_group(str(group_name))


def select_best_node_for_group(group_name):
    controller_port = get_mihomo_controller_port()
    if not is_main_controller_available(controller_port):
        print(f"[auto-select] main controller unavailable, skip group: {group_name}")
        return None

    nodes = _group_nodes(group_name)
    if not nodes:
        print(f"[auto-select] {group_name} has no nodes, skip")
        return None

    print(f"[auto-select] {group_name} testing {len(nodes)} nodes")

    best_node = None
    best_delay = None

    for node_name in nodes:
        delay = test_node_delay(controller_port, node_name)
        if delay is None:
            continue

        if best_delay is None or delay < best_delay:
            best_node = node_name
            best_delay = delay

    if not best_node:
        current = _selected_nodes.get(group_name)
        if current:
            print(f"[auto-select] {group_name} no available node, keep current: {current}")
        else:
            print(f"[auto-select] {group_name} no available node, keep current")
        return None

    if switch_group_node(controller_port, group_name, best_node):
        _selected_nodes[group_name] = best_node
        _selected_delays[group_name] = best_delay
        _fail_counts[group_name] = 0
        _save_runtime_selected_nodes()
        print(f"[auto-select] {group_name} selected node: {best_node}, delay {best_delay}ms")
        return best_node

    return None


async def monitor_current_nodes():
    while _running:
        await asyncio.sleep(_monitor_interval_seconds())

        groups = _load_group_nodes()
        controller_port = get_mihomo_controller_port()
        if not is_main_controller_available(controller_port):
            print("[auto-select] main controller unavailable, skip health check")
            continue

        for group_name in groups.keys():
            group_name = str(group_name)
            current_node = _selected_nodes.get(group_name)
            valid_nodes = set(_group_nodes(group_name))

            if not current_node or current_node not in valid_nodes:
                print(f"[auto-select] {group_name} has no current selection, selecting")
                select_best_node_for_group(group_name)
                continue

            delay = test_node_delay(controller_port, current_node)
            if delay is not None:
                _fail_counts[group_name] = 0
                print(f"[auto-select] {group_name} current node ok: {current_node}")
                continue

            fail_count = _fail_counts.get(group_name, 0) + 1
            _fail_counts[group_name] = fail_count

            max_fail_count = _max_fail_count()
            if fail_count < max_fail_count:
                print(f"[auto-select] {group_name} current node failed {fail_count}/{max_fail_count}: {current_node}")
                continue

            print(f"[auto-select] {group_name} current node failed continuously, reselecting")
            previous_node = _selected_nodes.get(group_name)
            selected_node = select_best_node_for_group(group_name)

            if selected_node and selected_node != previous_node:
                try:
                    from backend.connection_closer import close_changed_groups

                    close_changed_groups({group_name})
                except Exception as exc:
                    print(f"[auto-select] {group_name} close old connections failed: {exc}")


def _delay_from_result(result: dict | None):
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None

    try:
        delay = int(result.get("delay"))
    except (TypeError, ValueError):
        return None

    return delay if delay >= 0 else None


def test_node_delay(controller_port, node_name):
    return _delay_from_result(test_node_delay_via_main_controller(node_name, controller_port))


def switch_group_node(controller_port, group_name, node_name):
    encoded_group = quote(str(group_name), safe="")
    url = f"http://127.0.0.1:{controller_port}/proxies/{encoded_group}"

    try:
        response = local_put(url, json={"name": node_name}, timeout=3)
    except Exception as exc:
        print(f"[auto-select] {group_name} switch node exception: {exc}")
        return False

    if response.status_code in (200, 204):
        return True

    print(
        f"[auto-select] {group_name} switch node failed: "
        f"status={response.status_code}, body={response.text[:200]}"
    )
    return False
