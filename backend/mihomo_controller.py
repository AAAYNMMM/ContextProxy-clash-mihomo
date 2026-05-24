from urllib.parse import quote

from backend.app_settings import get_mihomo_controller_port
from backend.local_http import local_delete, local_get
from backend.runtime_config import get_group_port_map


def get_controller_port(group_name: str | None = None):
    _ = group_name
    return get_mihomo_controller_port()


def _controller_url(path: str) -> str:
    return f"http://127.0.0.1:{get_mihomo_controller_port()}{path}"


def _load_connections() -> list[dict] | None:
    try:
        response = local_get(_controller_url("/connections"), timeout=3)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        print(f"[mihomo] read connections failed: {exc}")
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


def close_mihomo_connections_by_group(group_name: str):
    if group_name not in get_group_port_map():
        print(f"[mihomo] group does not exist, skip closing connections: {group_name}")
        return

    connections = _load_connections()
    if connections is None:
        print(f"[mihomo] cannot inspect connections, skip mihomo internal close for {group_name}")
        return

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
        print(f"[mihomo] no accurately matched internal connections for group: {group_name}")
        return

    closed = 0
    for connection_id in matched_ids:
        try:
            response = local_delete(_controller_url(f"/connections/{quote(connection_id, safe='')}"), timeout=3)
            if response.status_code in (200, 204):
                closed += 1
            else:
                print(
                    f"[mihomo] failed to close connection {connection_id} for {group_name}: "
                    f"status={response.status_code}, body={response.text[:200]}"
                )
        except Exception as exc:
            print(f"[mihomo] close connection {connection_id} error for {group_name}: {exc}")

    print(f"[mihomo] closed {closed}/{len(matched_ids)} internal connections for {group_name}")


def close_mihomo_connections_by_groups(group_names):
    for group_name in sorted(set(group_names)):
        close_mihomo_connections_by_group(group_name)


def close_all_mihomo_connections():
    try:
        response = local_delete(_controller_url("/connections"), timeout=3)
        if response.status_code in (200, 204):
            print("[mihomo] closed all internal connections")
        else:
            print(
                "[mihomo] failed to close all internal connections: "
                f"status={response.status_code}, body={response.text[:200]}"
            )
    except Exception as exc:
        print(f"[mihomo] close all internal connections error: {exc}")
