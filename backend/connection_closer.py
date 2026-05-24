import time


HOLD_AFTER_CLOSE_SECONDS = 0.8


def close_changed_groups(changed_groups):
    """
    Close connections for groups whose rules or selected node changed.

    This function only freezes affected groups briefly, closes frontend proxy
    connections, and asks mihomo controllers to close their internal
    connections. It does not restart mihomo and does not stop main.py.
    """
    if not changed_groups:
        return

    changed_groups = set(changed_groups)

    from backend.group_runtime_state import mark_groups_restarting, unmark_groups_restarting
    from backend.tcp_proxy import close_connections_by_groups
    from backend.mihomo_controller import close_mihomo_connections_by_groups

    print(f"[connections] closing changed groups: {sorted(changed_groups)}")

    mark_groups_restarting(changed_groups)

    try:
        close_connections_by_groups(changed_groups)
        close_mihomo_connections_by_groups(changed_groups)

        if HOLD_AFTER_CLOSE_SECONDS > 0:
            time.sleep(HOLD_AFTER_CLOSE_SECONDS)
    finally:
        unmark_groups_restarting(changed_groups)

    print(f"[connections] changed groups handled: {sorted(changed_groups)}")
