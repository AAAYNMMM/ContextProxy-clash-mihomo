import asyncio
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import yaml

from backend.app_settings import get_auto_select_settings, get_mihomo_controller_port
from backend.delay_tester import is_main_controller_available, test_node_delay_via_main_controller
from backend.local_http import local_put
from backend.paths import CONFIG_DIR
from backend.activity_bus import write_log


GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"
RUNTIME_SELECTED_NODES_FILE = CONFIG_DIR / "runtime_selected_nodes.yaml"

MONITOR_INTERVAL_SECONDS = 20
MAX_FAIL_COUNT = 2

_selected_nodes: dict[str, str] = {}
_selected_delays: dict[str, int | None] = {}
_fail_counts: dict[str, int] = {}
_monitor_task = None
_running = False


def _log(message: str, level: str = "INFO"):
    write_log("auto_select", message, level)


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
        _log(f"read failed: {path} | {exc}", "WARN")
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
        _log("already running")
        return

    _running = True
    _load_runtime_selected_nodes()
    initialize_selected_nodes_without_delay()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _log("no running event loop; startup selection only", "WARN")
        return

    _monitor_task = loop.create_task(monitor_current_nodes())
    _log("monitor started")


def stop_auto_selector():
    global _monitor_task, _running

    _running = False

    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()

    _monitor_task = None
    _log("stopped")


def select_best_node_for_all_groups():
    groups = _load_group_nodes()

    for group_name, group_data in groups.items():
        if not isinstance(group_data, dict):
            continue

        select_best_node_for_group(str(group_name))


def initialize_selected_nodes_without_delay():
    groups = _load_group_nodes()
    controller_port = get_mihomo_controller_port()
    controller_available = is_main_controller_available(controller_port)
    changed = False

    for group_name, group_data in groups.items():
        group_name = str(group_name)
        if not isinstance(group_data, dict):
            continue

        nodes = _group_nodes(group_name)
        current_node = _selected_nodes.get(group_name)

        if not nodes:
            if current_node:
                _selected_nodes.pop(group_name, None)
                _selected_delays.pop(group_name, None)
                _fail_counts.pop(group_name, None)
                changed = True
            _log(f"{group_name} has no nodes at startup")
            continue

        if current_node in nodes:
            selected_node = current_node
            _log(f"{group_name} keep previous node: {selected_node}")
        else:
            selected_node = nodes[0]
            _selected_nodes[group_name] = selected_node
            _selected_delays[group_name] = None
            _fail_counts[group_name] = 0
            changed = True
            _log(f"{group_name} use first node: {selected_node}")

        if controller_available:
            switch_group_node(controller_port, group_name, selected_node)

    if changed:
        _save_runtime_selected_nodes()


def select_best_node_for_group(group_name):
    controller_port = get_mihomo_controller_port()
    if not is_main_controller_available(controller_port):
        _log(f"main controller unavailable, skip group: {group_name}", "WARN")
        return None

    nodes = _group_nodes(group_name)
    if not nodes:
        _log(f"{group_name} has no nodes, skip")
        return None

    _log(f"{group_name} testing {len(nodes)} nodes")

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
            _log(f"{group_name} no available node, keep current: {current}", "WARN")
        else:
            _log(f"{group_name} no available node, keep current", "WARN")
        return None

    if switch_group_node(controller_port, group_name, best_node):
        _selected_nodes[group_name] = best_node
        _selected_delays[group_name] = best_delay
        _fail_counts[group_name] = 0
        _save_runtime_selected_nodes()
        _log(f"{group_name} selected node: {best_node}, delay {best_delay}ms")
        return best_node

    return None


async def monitor_current_nodes():
    while _running:
        await asyncio.sleep(_monitor_interval_seconds())

        groups = _load_group_nodes()
        controller_port = get_mihomo_controller_port()
        if not is_main_controller_available(controller_port):
            _log("main controller unavailable, skip health check", "WARN")
            continue

        for group_name in groups.keys():
            group_name = str(group_name)
            current_node = _selected_nodes.get(group_name)
            valid_nodes = set(_group_nodes(group_name))

            if not current_node or current_node not in valid_nodes:
                _log(f"{group_name} has no current selection, using first node")
                nodes = _group_nodes(group_name)
                if nodes:
                    selected_node = nodes[0]
                    _selected_nodes[group_name] = selected_node
                    _selected_delays[group_name] = None
                    _fail_counts[group_name] = 0
                    switch_group_node(controller_port, group_name, selected_node)
                    _save_runtime_selected_nodes()
                continue

            delay = test_node_delay(controller_port, current_node)
            if delay is not None:
                _fail_counts[group_name] = 0
                _log(f"{group_name} current node ok: {current_node}", "DEBUG")
                continue

            fail_count = _fail_counts.get(group_name, 0) + 1
            _fail_counts[group_name] = fail_count

            max_fail_count = _max_fail_count()
            if fail_count < max_fail_count:
                _log(f"{group_name} current node failed {fail_count}/{max_fail_count}: {current_node}", "WARN")
                continue

            _log(f"{group_name} current node failed continuously, reselecting", "WARN")
            previous_node = _selected_nodes.get(group_name)
            selected_node = select_best_node_for_group(group_name)

            if selected_node and selected_node != previous_node:
                try:
                    from backend.connection_closer import close_changed_groups

                    close_changed_groups({group_name})
                except Exception as exc:
                    _log(f"{group_name} close old connections failed: {exc}", "WARN")


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
        _log(f"{group_name} switch node exception: {exc}", "WARN")
        return False

    if response.status_code in (200, 204):
        return True

    _log(
        f"{group_name} switch node failed: "
        f"status={response.status_code}, body={response.text[:200]}",
        "WARN",
    )
    return False
