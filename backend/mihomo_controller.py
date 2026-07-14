from urllib.parse import quote

from backend.app_settings import get_mihomo_controller_port
from backend.activity_bus import write_log
from backend.local_http import local_delete, local_get
from backend.runtime_config import get_group_port_map


def get_controller_port():
    return get_mihomo_controller_port()


def _controller_url(path: str) -> str:
    return f"http://127.0.0.1:{get_mihomo_controller_port()}{path}"


def is_controller_available() -> bool:
    try:
        response = local_get(_controller_url("/proxies"), timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def _load_connections() -> list[dict] | None:
    try:
        response = local_get(_controller_url("/connections"), timeout=3)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        write_log("mihomo_controller", f"read connections failed: {exc}", "WARN")
        return None

    connections = data.get("connections", []) if isinstance(data, dict) else []
    return connections if isinstance(connections, list) else []


def _connection_belongs_to_group(connection: dict, group_name: str) -> bool:
    chains = connection.get("chains", [])
    if isinstance(chains, list) and group_name in {str(item) for item in chains}:
        return True

    rule = connection.get("rule")
    if str(rule) == group_name:
        return True

    metadata = connection.get("metadata", {})
    if isinstance(metadata, dict):
        for key in ("proxy", "proxyGroup", "rulePayload"):
            if str(metadata.get(key) or "") == group_name:
                return True

    return False


def close_mihomo_connections_by_group(group_name: str) -> bool:
    if group_name not in get_group_port_map():
        write_log("mihomo_controller", f"group does not exist, skip close: {group_name}", "WARN")
        return False

    connections = _load_connections()
    if connections is None:
        write_log("mihomo_controller", f"cannot inspect connections for {group_name}", "WARN")
        return False

    matched_ids = []
    for connection in connections:
        if not isinstance(connection, dict):
            continue
        if not _connection_belongs_to_group(connection, group_name):
            continue

        connection_id = connection.get("id")
        if connection_id:
            matched_ids.append(str(connection_id))

    if not matched_ids:
        write_log("mihomo_controller", f"no accurately matched internal connections for {group_name}", "INFO")
        return False

    closed = 0
    for connection_id in matched_ids:
        try:
            response = local_delete(_controller_url(f"/connections/{quote(connection_id, safe='')}"), timeout=3)
            if response.status_code in (200, 204):
                closed += 1
            else:
                write_log(
                    "mihomo_controller",
                    (
                        f"failed to close connection {connection_id} for {group_name}: "
                        f"status={response.status_code}, body={response.text[:200]}"
                    ),
                    "WARN",
                )
        except Exception as exc:
            write_log("mihomo_controller", f"close connection {connection_id} error for {group_name}: {exc}", "WARN")

    write_log("mihomo_controller", f"closed {closed}/{len(matched_ids)} internal connections for {group_name}")
    return closed > 0


def close_mihomo_connections_by_groups(group_names) -> bool:
    closed_any = False
    for group_name in sorted(set(group_names)):
        closed_any = close_mihomo_connections_by_group(group_name) or closed_any
    return closed_any


def close_all_mihomo_connections() -> bool:
    try:
        response = local_delete(_controller_url("/connections"), timeout=3)
        if response.status_code in (200, 204):
            write_log("mihomo_controller", "closed all internal connections")
            return True

        write_log(
            "mihomo_controller",
            (
                "failed to close all internal connections: "
                f"status={response.status_code}, body={response.text[:200]}"
            ),
            "WARN",
        )
    except Exception as exc:
        write_log("mihomo_controller", f"close all internal connections error: {exc}", "WARN")
    return False
