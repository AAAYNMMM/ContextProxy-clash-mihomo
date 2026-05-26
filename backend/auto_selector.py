from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import yaml

from backend.activity_bus import emit_activity, write_log
from backend.atomic_writer import atomic_write_yaml
from backend.app_settings import get_mihomo_controller_port
from backend.delay_tester import (
    is_main_controller_available,
    test_node_delay_via_main_controller,
)
from backend.local_http import local_put
from backend.paths import CONFIG_DIR


GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"
RUNTIME_SELECTED_NODES_FILE = CONFIG_DIR / "runtime_selected_nodes.yaml"

BAD_NODE_TTL_SECONDS = 10 * 60

_selected_nodes: dict[str, str] = {}
_selected_delays: dict[str, int | None] = {}
_bad_nodes: dict[str, dict[str, float]] = {}
_running = False


def _log(message: str, level: str = "INFO"):
    write_log("auto_select", message, level)


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
    atomic_write_yaml(path, data)


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


def get_current_node_for_group(group_name: str) -> str | None:
    return _selected_nodes.get(group_name)


def start_auto_selector():
    global _running

    if _running:
        _log("already running")
        return

    _running = True
    _load_runtime_selected_nodes()
    initialize_selected_nodes_without_delay()
    _log("started without periodic delay health checks")


def stop_auto_selector():
    global _running

    _running = False
    _log("stopped")


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
            changed = True
            _log(f"{group_name} use first node: {selected_node}")

        if controller_available:
            switch_group_node(controller_port, group_name, selected_node)

    if changed:
        _save_runtime_selected_nodes()


def _prune_bad_nodes(group_name: str | None = None):
    now = time_monotonic()
    groups = [group_name] if group_name else list(_bad_nodes.keys())
    for name in groups:
        entries = _bad_nodes.get(str(name), {})
        expired = [node for node, expires_at in entries.items() if expires_at <= now]
        for node in expired:
            entries.pop(node, None)
        if not entries:
            _bad_nodes.pop(str(name), None)


def time_monotonic() -> float:
    import time

    return time.monotonic()


def mark_current_node_bad(group_name: str, reason: str = "real_traffic_failure", ttl: int = BAD_NODE_TTL_SECONDS) -> str | None:
    node_name = _selected_nodes.get(str(group_name))
    if not node_name:
        return None
    mark_node_bad(group_name, node_name, reason=reason, ttl=ttl)
    return node_name


def mark_node_bad(group_name: str, node_name: str, reason: str = "real_traffic_failure", ttl: int = BAD_NODE_TTL_SECONDS):
    group_name = str(group_name)
    node_name = str(node_name)
    expires_at = time_monotonic() + max(1, int(ttl))
    _bad_nodes.setdefault(group_name, {})[node_name] = expires_at
    _log(f"{group_name} mark bad node: {node_name}, ttl={ttl}s, reason={reason}", "WARN")


def _bad_node_names(group_name: str) -> set[str]:
    _prune_bad_nodes(group_name)
    return set(_bad_nodes.get(str(group_name), {}).keys())


def select_next_node_for_group(group_name: str, reason: str = "real_traffic_failure") -> str | None:
    """Select a replacement node without running delay checks.

    This is used by real-traffic recovery. It intentionally relies on the
    configured group order and temporary bad-node exclusions instead of
    periodic delay probing.
    """
    group_name = str(group_name)
    controller_port = get_mihomo_controller_port()
    if not is_main_controller_available(controller_port):
        _log(f"main controller unavailable, skip group: {group_name}", "WARN")
        return None

    nodes = _group_nodes(group_name)
    if not nodes:
        _log(f"{group_name} has no nodes, skip")
        return None

    current = _selected_nodes.get(group_name)
    excluded_bad = _bad_node_names(group_name)
    candidates = [node for node in nodes if node not in excluded_bad]
    if current in candidates and len(candidates) > 1:
        candidates = [node for node in candidates if node != current]

    if not candidates:
        if current in nodes:
            _log(
                f"{group_name} no replacement candidate, keep current: {current}, "
                f"excluded_bad_nodes={sorted(excluded_bad)}",
                "WARN",
            )
            emit_activity(
                f"{group_name} 暂无其他可用候选节点，保持当前节点",
                "WARN",
                key=f"auto-select:keep-current:{group_name}",
                ttl=180,
            )
            return current
        candidates = nodes

    selected_node = candidates[0]
    previous_node = current
    if switch_group_node(controller_port, group_name, selected_node):
        _selected_nodes[group_name] = selected_node
        _selected_delays[group_name] = None
        _save_runtime_selected_nodes()
        _log(
            f"{group_name} selected replacement: old={previous_node}, new={selected_node}, "
            f"excluded_bad_nodes={sorted(excluded_bad)}, selection_basis=group_order, reason={reason}",
            "WARN",
        )
        if selected_node != previous_node:
            emit_activity(
                f"{group_name} 已根据真实流量故障切换节点：{selected_node}",
                "INFO",
                key=f"auto-select:traffic-switch:{group_name}:{selected_node}",
                ttl=120,
            )
        return selected_node

    return None


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
        emit_activity(
            f"{group_name} 暂无可用节点",
            "WARN",
            key=f"auto-select:no-node:{group_name}",
            ttl=120,
        )
        return None

    previous_node = _selected_nodes.get(group_name)
    if switch_group_node(controller_port, group_name, best_node):
        _selected_nodes[group_name] = best_node
        _selected_delays[group_name] = best_delay
        _save_runtime_selected_nodes()
        _log(f"{group_name} selected node: {best_node}, delay {best_delay}ms")
        if best_node != previous_node:
            emit_activity(
                f"{group_name} 已自动切换节点：{best_node}",
                "INFO",
                key=f"auto-select:switch:{group_name}:{best_node}",
                ttl=30,
            )
        return best_node

    return None


def _delay_from_result(result: dict | None):
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None

    try:
        delay = int(result.get("delay"))
    except (TypeError, ValueError):
        return None

    return delay if delay >= 0 else None


def test_node_delay(controller_port, node_name, test_url: str | None = None, allow_retry: bool = True):
    return _delay_from_result(
        test_node_delay_via_main_controller(
            node_name,
            controller_port,
            allow_retry=allow_retry,
            test_url=test_url,
        )
    )


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
