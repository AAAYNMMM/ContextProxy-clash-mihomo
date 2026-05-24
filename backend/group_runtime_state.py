import time


# group_name -> marked_at
RESTARTING_GROUPS = {}

# 兜底超时时间，防止异常情况下某个分组永久处于变更状态
GROUP_RESTART_HOLD_SECONDS = 3.0


def mark_group_restarting(group_name: str):
    if not group_name:
        return

    RESTARTING_GROUPS[group_name] = time.monotonic()


def mark_groups_restarting(group_names):
    for group_name in group_names:
        mark_group_restarting(group_name)


def unmark_group_restarting(group_name: str):
    if not group_name:
        return

    RESTARTING_GROUPS.pop(group_name, None)


def unmark_groups_restarting(group_names):
    for group_name in group_names:
        unmark_group_restarting(group_name)


def is_group_restarting(group_name: str) -> bool:
    if not group_name:
        return False

    marked_at = RESTARTING_GROUPS.get(group_name)

    if marked_at is None:
        return False

    # 兜底：如果 connection_closer 异常退出，避免永久拒绝该组连接
    if time.monotonic() - marked_at > GROUP_RESTART_HOLD_SECONDS:
        RESTARTING_GROUPS.pop(group_name, None)
        return False

    return True
