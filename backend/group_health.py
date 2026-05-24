import asyncio
import socket
import time
from collections import defaultdict, deque

from backend.activity_bus import emit_activity, write_log
from backend.runtime_config import get_group_port_map


TRAFFIC_WINDOW_SIZE = 20
TRAFFIC_FAIL_THRESHOLD = 8
TRAFFIC_FAIL_RATE_THRESHOLD = 0.40
TRAFFIC_CONSECUTIVE_FAIL_THRESHOLD = 5
LISTENER_FAIL_THRESHOLD = 3
CONTROLLER_FAIL_THRESHOLD = 3
MONITOR_INTERVAL_SECONDS = 30
RECOVERY_COOLDOWN_SECONDS = 60

_traffic_windows: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=TRAFFIC_WINDOW_SIZE))
_consecutive_failures: dict[str, int] = defaultdict(int)
_listener_failures: dict[str, int] = defaultdict(int)
_node_failures: dict[str, int] = defaultdict(int)
_controller_failures = 0
_healing_groups: set[str] = set()
_recovery_tasks: set[asyncio.Task] = set()
_last_recovery_at: dict[str, float] = {}
_monitor_task = None
_running = False


def record_proxy_connection_result(group_name: str, success: bool, reason: str = ""):
    group_name = str(group_name or "").strip()
    if not group_name or group_name.lower() == "direct":
        return

    window = _traffic_windows[group_name]
    window.append(bool(success))
    if success:
        _consecutive_failures[group_name] = 0
        return

    _consecutive_failures[group_name] += 1
    write_log(
        "tcp",
        f"group traffic failure: group={group_name}, reason={reason or '-'}, "
        f"consecutive={_consecutive_failures[group_name]}",
        "WARN",
    )

    fail_count = window.count(False)
    fail_rate = fail_count / max(1, len(window))
    if (
        len(window) >= TRAFFIC_WINDOW_SIZE
        and fail_count >= TRAFFIC_FAIL_THRESHOLD
        and fail_rate >= TRAFFIC_FAIL_RATE_THRESHOLD
    ) or _consecutive_failures[group_name] >= TRAFFIC_CONSECUTIVE_FAIL_THRESHOLD:
        _schedule_group_recovery(group_name, f"traffic:{reason or 'unknown'}")


def _schedule_group_recovery(group_name: str, reason: str):
    now = time.monotonic()
    if group_name in _healing_groups:
        return
    if now - _last_recovery_at.get(group_name, 0) < RECOVERY_COOLDOWN_SECONDS:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        write_log("group_health", f"cannot schedule recovery outside event loop: {group_name}", "WARN")
        return

    _healing_groups.add(group_name)
    _last_recovery_at[group_name] = now
    task = loop.create_task(recover_group(group_name, reason), name=f"group_recovery:{group_name}")
    _recovery_tasks.add(task)
    task.add_done_callback(_recovery_tasks.discard)


def _listener_available(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1.5):
            return True
    except OSError:
        return False


async def start_group_health_monitor():
    global _monitor_task, _running
    if _running:
        return
    _running = True
    _monitor_task = asyncio.current_task()
    write_log("group_health", f"started, interval={MONITOR_INTERVAL_SECONDS}s")

    while _running:
        await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
        await check_all_groups_once()


def stop_group_health_monitor():
    global _monitor_task, _running
    _running = False
    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()
    _monitor_task = None

    for task in list(_recovery_tasks):
        if not task.done():
            task.cancel()
    _recovery_tasks.clear()
    _healing_groups.clear()


async def check_all_groups_once():
    global _controller_failures

    from backend.mihomo_controller import is_controller_available

    if not is_controller_available():
        _controller_failures += 1
        write_log("group_health", f"controller failed {_controller_failures}/{CONTROLLER_FAIL_THRESHOLD}", "WARN")
        if _controller_failures >= CONTROLLER_FAIL_THRESHOLD:
            await restart_mihomo_core_safely("controller unavailable")
            _controller_failures = 0
        return

    _controller_failures = 0
    port_map = get_group_port_map()
    failed_listeners = []

    for group_name, port in port_map.items():
        if _listener_available(port):
            _listener_failures[group_name] = 0
        else:
            _listener_failures[group_name] += 1
            failed_listeners.append(group_name)
            write_log(
                "group_health",
                f"{group_name} listener failed {_listener_failures[group_name]}/{LISTENER_FAIL_THRESHOLD}: {port}",
                "WARN",
            )

        node_ok = await asyncio.to_thread(_current_node_healthy, group_name)
        if node_ok is True:
            _node_failures[group_name] = 0
        elif node_ok is False:
            _node_failures[group_name] += 1
            write_log(
                "group_health",
                f"{group_name} current node failed {_node_failures[group_name]}/{LISTENER_FAIL_THRESHOLD}",
                "WARN",
            )

        if (
            _listener_failures[group_name] >= LISTENER_FAIL_THRESHOLD
            or _node_failures[group_name] >= LISTENER_FAIL_THRESHOLD
        ):
            _schedule_group_recovery(group_name, "periodic health check")

    if len(failed_listeners) >= 2 and all(_listener_failures[group] >= LISTENER_FAIL_THRESHOLD for group in failed_listeners):
        await restart_mihomo_core_safely(f"multiple listeners unavailable: {failed_listeners}")


def _current_node_healthy(group_name: str) -> bool | None:
    try:
        from backend.app_settings import get_mihomo_controller_port
        from backend.auto_selector import get_current_node_for_group, is_current_node_healthy

        node_name = get_current_node_for_group(group_name)
        if not node_name:
            return None
        return is_current_node_healthy(get_mihomo_controller_port(), node_name)
    except Exception as exc:
        write_log("group_health", f"{group_name} current node check failed: {exc}", "WARN")
        return None


async def recover_group(group_name: str, reason: str):
    try:
        write_log("group_health", f"{group_name} recovery started: {reason}", "WARN")

        from backend.connection_closer import close_changed_groups
        from backend.mihomo_controller import close_all_mihomo_connections, close_mihomo_connections_by_group

        await asyncio.to_thread(close_changed_groups, {group_name})
        closed_precise = await asyncio.to_thread(close_mihomo_connections_by_group, group_name)
        if not closed_precise:
            await asyncio.to_thread(close_all_mihomo_connections)

        listener_port = get_group_port_map().get(group_name)
        if listener_port and not _listener_available(listener_port):
            await restart_mihomo_core_safely(f"{group_name} listener unavailable after soft recovery")
            _reset_group_stats(group_name)
            return

        if await asyncio.to_thread(_current_node_healthy, group_name):
            emit_activity(
                "已自动优化连接状态",
                "INFO",
                key=f"group-health:optimized:{group_name}",
                ttl=120,
            )
            _reset_group_stats(group_name)
            return

        from backend.auto_selector import get_current_node_for_group, select_best_node_for_group

        previous_node = get_current_node_for_group(group_name)
        selected_node = await asyncio.to_thread(select_best_node_for_group, group_name)
        if selected_node and selected_node != previous_node:
            await asyncio.to_thread(close_changed_groups, {group_name})
            emit_activity(
                f"{group_name} 已自动切换节点：{selected_node}",
                "INFO",
                key=f"group-health:switch:{group_name}:{selected_node}",
                ttl=120,
            )
            _reset_group_stats(group_name)
            return

        if selected_node:
            emit_activity(
                "已自动优化连接状态",
                "INFO",
                key=f"group-health:optimized:{group_name}",
                ttl=120,
            )
            _reset_group_stats(group_name)
            return

        emit_activity(
            f"{group_name} 暂无可用节点",
            "WARN",
            key=f"group-health:no-node:{group_name}",
            ttl=180,
        )
    except Exception as exc:
        write_log("group_health", f"{group_name} recovery failed: {exc}", "ERROR")
    finally:
        _healing_groups.discard(group_name)


async def restart_mihomo_core_safely(reason: str):
    write_log("group_health", f"restart mihomo core requested: {reason}", "WARN")
    try:
        from backend.mihomo_launcher import restart_mihomo_core
        from backend.auto_selector import initialize_selected_nodes_without_delay

        await asyncio.to_thread(restart_mihomo_core)
        await asyncio.sleep(2)
        await asyncio.to_thread(initialize_selected_nodes_without_delay)
        emit_activity(
            "代理核心已自动恢复",
            "INFO",
            key="group-health:core-recovered",
            ttl=120,
        )
    except Exception as exc:
        write_log("group_health", f"restart mihomo core failed: {exc}", "ERROR")
        emit_activity(
            "代理服务异常，请重新启动代理",
            "ERROR",
            key="group-health:core-restart-failed",
            ttl=120,
        )
        raise


def _reset_group_stats(group_name: str):
    _traffic_windows[group_name].clear()
    _consecutive_failures[group_name] = 0
    _listener_failures[group_name] = 0
    _node_failures[group_name] = 0
