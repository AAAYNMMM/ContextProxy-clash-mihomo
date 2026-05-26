import time

from backend.activity_bus import write_log


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
    from backend.mihomo_controller import close_mihomo_connections_by_groups
    from backend.core_launcher import close_core_connections, pause_core_groups, resume_core_groups

    write_log("connections", f"closing changed groups: {sorted(changed_groups)}")

    mark_groups_restarting(changed_groups)

    try:
        pause_core_groups(groups=changed_groups, hold_ms=int(HOLD_AFTER_CLOSE_SECONDS * 1000))
        close_core_connections(groups=changed_groups)
        close_mihomo_connections_by_groups(changed_groups)

        if HOLD_AFTER_CLOSE_SECONDS > 0:
            time.sleep(HOLD_AFTER_CLOSE_SECONDS)
    finally:
        resume_core_groups(groups=changed_groups)
        unmark_groups_restarting(changed_groups)

    write_log("connections", f"changed groups handled: {sorted(changed_groups)}")
