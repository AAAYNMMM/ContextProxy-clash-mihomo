import asyncio
import fnmatch
import time
from collections import defaultdict
from pathlib import Path

from backend.runtime_config import get_valid_group_names, reload_group_config
from backend.activity_bus import emit_activity, write_log

APP_PROCESS_RULES = []
APP_PROCESS_RULE_VERSION = 0
APP_PROCESS_MATCH_CACHE = {}
APP_PROCESS_CACHE_TTL = 8.0
APP_PROCESS_FILE_PATH = None
APP_PROCESS_FILE_LAST_MODIFY = 0
APP_PROCESS_FILE_CHECK_INTERVAL = 5
CLOSE_CHANGED_GROUP_CONNECTIONS_ON_RELOAD = True


def load_app_process_file(path: str):
    global APP_PROCESS_FILE_PATH

    APP_PROCESS_FILE_PATH = Path(path)

    if not APP_PROCESS_FILE_PATH.is_file():
        emit_activity(f"进程规则文件不存在：{APP_PROCESS_FILE_PATH}", "WARN", key="process-rule-missing", ttl=60)
        return

    _reload_app_process_file()


def _read_app_process_rules() -> list[tuple[str, str]]:
    rules = []
    reload_group_config()
    valid_groups = get_valid_group_names()

    with open(APP_PROCESS_FILE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            if "," not in line:
                continue

            group, process_name = line.split(",", 1)

            group = group.strip()
            process_name = process_name.strip().lower()

            if not group or not process_name:
                continue

            if group not in valid_groups:
                write_log("rules", f"ignore missing group: {group}, process={process_name}", "WARN")
                continue

            rules.append((group, process_name))

    return rules


def _build_group_rule_map(rules: list[tuple[str, str]]) -> dict[str, set[str]]:
    result = defaultdict(set)
    for group, process_name in rules:
        result[group].add(process_name)
    return dict(result)


def _get_changed_groups(old_rules: list[tuple[str, str]], new_rules: list[tuple[str, str]]) -> set[str]:
    old_map = _build_group_rule_map(old_rules)
    new_map = _build_group_rule_map(new_rules)
    changed = set()

    for group in set(old_map.keys()) | set(new_map.keys()):
        if old_map.get(group, set()) != new_map.get(group, set()):
            changed.add(group)

    return changed




def _get_changed_process_patterns(old_rules: list[tuple[str, str]], new_rules: list[tuple[str, str]]) -> set[str]:
    changed_rule_tuples = set(old_rules) ^ set(new_rules)
    return {process_name for _group, process_name in changed_rule_tuples if process_name}


def reload_app_process_file_now():
    """Reload App process rules immediately after GUI saves the rule file."""
    if not APP_PROCESS_FILE_PATH:
        return set(), set()

    if not APP_PROCESS_FILE_PATH.is_file():
        return set(), set()

    return _reload_app_process_file()


def _reload_app_process_file():
    global APP_PROCESS_RULES, APP_PROCESS_RULE_VERSION, APP_PROCESS_FILE_LAST_MODIFY

    old_rules = APP_PROCESS_RULES
    try:
        new_rules = _read_app_process_rules()
    except Exception as exc:
        write_log("rules", f"App process rules reload failed, keeping old rules: {exc}", "WARN")
        return set(), set()

    changed_groups = _get_changed_groups(old_rules, new_rules)

    APP_PROCESS_RULES = new_rules
    APP_PROCESS_RULE_VERSION += 1
    APP_PROCESS_MATCH_CACHE.clear()
    APP_PROCESS_FILE_LAST_MODIFY = APP_PROCESS_FILE_PATH.stat().st_mtime

    write_log("rules", f"App process rules loaded, count={len(APP_PROCESS_RULES)}")

    changed_patterns = _get_changed_process_patterns(old_rules, new_rules)

    if old_rules and (changed_groups or changed_patterns):
        if changed_groups:
            emit_activity(f"进程规则变更，受影响分组：{', '.join(sorted(changed_groups))}", "INFO", key="process-rule-groups", ttl=2)
        if changed_patterns:
            write_log("rules", f"App rule changes affect processes: {sorted(changed_patterns)}")

        if CLOSE_CHANGED_GROUP_CONNECTIONS_ON_RELOAD:
            try:
                from backend.connection_closer import close_changed_groups
                from backend.tcp_proxy import close_connections_by_process_patterns

                close_changed_groups(changed_groups)
                close_connections_by_process_patterns(changed_patterns)
            except Exception as exc:
                emit_activity(f"进程规则变更断连失败：{exc}", "WARN", key="process-rule-close-failed", ttl=10)

    return changed_groups, changed_patterns


async def start_app_process_watcher():
    global APP_PROCESS_FILE_LAST_MODIFY

    while True:
        await asyncio.sleep(APP_PROCESS_FILE_CHECK_INTERVAL)

        if not APP_PROCESS_FILE_PATH:
            continue

        if not APP_PROCESS_FILE_PATH.is_file():
            continue

        mtime = APP_PROCESS_FILE_PATH.stat().st_mtime

        if mtime > APP_PROCESS_FILE_LAST_MODIFY:
            write_log("rules", "App process rules changed, reloading")
            _reload_app_process_file()


def match_process_group(process_name: str):
    if not process_name:
        return None

    process_name = process_name.lower().strip()
    cache_key = (APP_PROCESS_RULE_VERSION, process_name)
    cached = APP_PROCESS_MATCH_CACHE.get(cache_key)
    if cached:
        group, created_at = cached
        if time.monotonic() - created_at <= APP_PROCESS_CACHE_TTL:
            return group
        APP_PROCESS_MATCH_CACHE.pop(cache_key, None)

    for group, pattern in APP_PROCESS_RULES:
        if fnmatch.fnmatch(process_name, pattern):
            APP_PROCESS_MATCH_CACHE[cache_key] = (group, time.monotonic())
            return group

    APP_PROCESS_MATCH_CACHE[cache_key] = (None, time.monotonic())
    return None
