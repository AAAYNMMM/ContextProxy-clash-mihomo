import asyncio
import fnmatch
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from backend.config import SPECIAL_GROUPS, DEFAULT_PROXY, DIRECT
from backend.runtime_config import get_valid_group_names, reload_group_config
from backend.activity_bus import emit_activity, emit_routing_event, write_log

request_queue = None


def init_request_queue():
    """Create a fresh request queue bound to the current event loop.

    The GUI runs backend services in an internal asyncio loop.  A module-level
    asyncio.Queue created during import can be bound to a different loop after
    stop/start, so the queue must be initialized inside the active backend loop.
    """
    global request_queue
    request_queue = asyncio.Queue()
    return request_queue


def get_request_queue():
    global request_queue
    if request_queue is None:
        request_queue = asyncio.Queue()
    return request_queue

# requestHost -> {"group": final_group, "created_at": time.monotonic()}
REQUEST_GROUP_MAP = {}

DOMAIN_RULES = []
DOMAIN_RULE_INDEX = {"exact": {}, "suffix": [], "wildcard": []}
DOMAIN_RULE_VERSION = 0
DOMAIN_DECISION_CACHE = {}
DOMAIN_CACHE_TTL = 8.0
DOMAIN_DIRECT_CACHE_TTL = 3.0
DOMAIN_FILE_PATH = None
DOMAIN_FILE_LAST_MODIFY = 0
DOMAIN_FILE_CHECK_INTERVAL = 5

CLOSE_CHANGED_GROUP_CONNECTIONS_ON_RELOAD = True


def domain_match(host: str, pattern: str) -> bool:
    host = host.lower().strip()
    pattern = pattern.lower().strip()

    if fnmatch.fnmatch(host, pattern):
        return True

    if pattern.startswith("*."):
        root_pattern = pattern[2:]
        if fnmatch.fnmatch(host, root_pattern):
            return True

    return False


def _is_exact_pattern(pattern: str) -> bool:
    return not any(char in pattern for char in "*?[]")


def _build_domain_rule_index(rules: list[tuple[str, str]]) -> dict:
    exact = {}
    suffix = []
    wildcard = []

    for group, pattern in rules:
        pattern = pattern.lower().strip()
        if _is_exact_pattern(pattern):
            exact.setdefault(pattern, group)
        elif pattern.startswith("*.") and _is_exact_pattern(pattern[2:]):
            suffix.append((pattern[2:], group))
        else:
            wildcard.append((pattern, group))

    return {
        "exact": exact,
        "suffix": suffix,
        "wildcard": wildcard,
    }


def clear_domain_decision_cache():
    DOMAIN_DECISION_CACHE.clear()


def _cached_domain_decision(tab_host: str, request_host: str):
    key = (DOMAIN_RULE_VERSION, tab_host, request_host)
    entry = DOMAIN_DECISION_CACHE.get(key)
    if not entry:
        return None

    group, created_at, ttl = entry
    if time.monotonic() - created_at > ttl:
        DOMAIN_DECISION_CACHE.pop(key, None)
        return None

    return group


def _store_domain_decision(tab_host: str, request_host: str, group: str):
    ttl = DOMAIN_DIRECT_CACHE_TTL if group == DIRECT else DOMAIN_CACHE_TTL
    key = (DOMAIN_RULE_VERSION, tab_host, request_host)
    DOMAIN_DECISION_CACHE[key] = (group, time.monotonic(), ttl)


def _match_domain_rule(host: str) -> str | None:
    host = host.lower().strip()
    if not host:
        return None

    exact_group = DOMAIN_RULE_INDEX.get("exact", {}).get(host)
    if exact_group:
        return exact_group

    for suffix_root, group in DOMAIN_RULE_INDEX.get("suffix", []):
        if host == suffix_root or host.endswith("." + suffix_root):
            return group

    for pattern, group in DOMAIN_RULE_INDEX.get("wildcard", []):
        if domain_match(host, pattern):
            return group

    return None


def load_domain_file(path: str):
    global DOMAIN_FILE_PATH

    DOMAIN_FILE_PATH = Path(path)

    if not DOMAIN_FILE_PATH.is_file():
        emit_activity(f"域名规则文件不存在：{DOMAIN_FILE_PATH}", "WARN", key="domain-rule-missing", ttl=60)
        return

    _reload_domain_file()


def _read_domain_rules():
    rules = []
    reload_group_config()
    valid_groups = get_valid_group_names()

    with open(DOMAIN_FILE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            if "," not in line:
                continue

            group, pattern = line.split(",", 1)

            group = group.strip()
            pattern = pattern.strip().lower()

            if not group or not pattern:
                continue

            # 鍙鍙栧綋鍓嶅凡缁忓瓨鍦ㄧ殑浠ｇ悊鍒嗙粍
            if group not in valid_groups:
                write_log("rules", f"ignore missing group: {group}, pattern={pattern}", "WARN")
                continue

            rules.append((group, pattern))
    return rules


def _build_group_rule_map(rules: list[tuple[str, str]]) -> dict[str, set[str]]:
    result = defaultdict(set)

    for group, pattern in rules:
        result[group].add(pattern)

    return dict(result)


def _get_changed_groups(
    old_rules: list[tuple[str, str]],
    new_rules: list[tuple[str, str]],
) -> set[str]:
    old_map = _build_group_rule_map(old_rules)
    new_map = _build_group_rule_map(new_rules)

    all_groups = set(old_map.keys()) | set(new_map.keys())
    changed = set()

    for group in all_groups:
        if old_map.get(group, set()) != new_map.get(group, set()):
            changed.add(group)

    return changed




def _get_changed_patterns(
    old_rules: list[tuple[str, str]],
    new_rules: list[tuple[str, str]],
) -> set[str]:
    changed_rule_tuples = set(old_rules) ^ set(new_rules)
    return {pattern for _group, pattern in changed_rule_tuples if pattern}


def reload_domain_file_now():
    """Reload domain rules immediately after GUI saves the rule file.

    The file watcher also reloads changes, but it polls every few seconds.
    Calling this from the GUI save path updates DOMAIN_RULES immediately and
    closes stale connections before browsers/apps can keep reusing them.
    """
    if not DOMAIN_FILE_PATH:
        return set(), set()

    if not DOMAIN_FILE_PATH.is_file():
        return set(), set()

    return _reload_domain_file()


def _reload_domain_file():
    global DOMAIN_RULES, DOMAIN_RULE_INDEX, DOMAIN_RULE_VERSION, DOMAIN_FILE_LAST_MODIFY

    old_rules = DOMAIN_RULES
    try:
        new_rules = _read_domain_rules()
    except Exception as exc:
        write_log("rules", f"domain rules reload failed, keeping old rules: {exc}", "WARN")
        return set(), set()

    changed_groups = _get_changed_groups(old_rules, new_rules)

    DOMAIN_RULES = new_rules
    DOMAIN_RULE_INDEX = _build_domain_rule_index(new_rules)
    DOMAIN_RULE_VERSION += 1
    DOMAIN_FILE_LAST_MODIFY = DOMAIN_FILE_PATH.stat().st_mtime

    REQUEST_GROUP_MAP.clear()
    clear_domain_decision_cache()

    write_log("rules", f"domain rules loaded, count={len(DOMAIN_RULES)}")
    write_log("rules", "cleared old routing caches")

    changed_patterns = _get_changed_patterns(old_rules, new_rules)

    if old_rules and (changed_groups or changed_patterns):
        if changed_groups:
            emit_activity(f"域名规则变更，受影响分组：{', '.join(sorted(changed_groups))}", "INFO", key="domain-rule-groups", ttl=2)
        if changed_patterns:
            write_log("rules", f"domain rule changes affect patterns: {sorted(changed_patterns)}")

        if CLOSE_CHANGED_GROUP_CONNECTIONS_ON_RELOAD:
            try:
                from backend.connection_closer import close_changed_groups
                from backend.tcp_proxy import close_connections_by_domain_patterns

                close_changed_groups(changed_groups)
                close_connections_by_domain_patterns(changed_patterns)
            except Exception as e:
                emit_activity(f"域名规则变更断连失败：{e}", "WARN", key="domain-rule-close-failed", ttl=10)

    return changed_groups, changed_patterns


async def _watch_domain_file():
    global DOMAIN_FILE_LAST_MODIFY

    while True:
        await asyncio.sleep(DOMAIN_FILE_CHECK_INTERVAL)

        if not DOMAIN_FILE_PATH:
            continue

        if not DOMAIN_FILE_PATH.is_file():
            continue

        mtime = DOMAIN_FILE_PATH.stat().st_mtime

        if mtime > DOMAIN_FILE_LAST_MODIFY:
            write_log("rules", "domain rules changed, reloading")
            _reload_domain_file()


def decide_group(tabHost: str, requestHost: str):
    tab_host = tabHost.lower().strip()
    request_host = requestHost.lower().strip()

    cached_group = _cached_domain_decision(tab_host, request_host)
    if cached_group:
        return cached_group

    group = _match_domain_rule(tab_host) or DIRECT
    _store_domain_decision(tab_host, request_host, group)
    return group


def resolve_final_group(groups: set[str]):
    if len(groups) == 1:
        return next(iter(groups))

    non_direct_groups = groups - {DIRECT}

    if len(non_direct_groups) == 1:
        return next(iter(non_direct_groups))

    special_hits = [g for g in non_direct_groups if g in SPECIAL_GROUPS]

    if len(special_hits) >= 2:
        return DEFAULT_PROXY

    if DEFAULT_PROXY in non_direct_groups:
        return DEFAULT_PROXY

    if len(special_hits) == 1:
        return special_hits[0]

    return DIRECT


async def start_batch_processor():
    queue = get_request_queue()
    watcher_task = asyncio.create_task(_watch_domain_file(), name="domain_rule_watcher")

    try:
        while True:
            batch = []

            try:
                report = await queue.get()
                batch.append(report)

                await asyncio.sleep(0.01)

                while not queue.empty():
                    batch.append(queue.get_nowait())

            except asyncio.CancelledError:
                raise
            except Exception as e:
                write_log("batch", f"batch processing error: {e}", "WARN")
                continue

            host_buckets = defaultdict(set)
            host_tab_map = {}

            for r in batch:
                group = decide_group(r.tabHost, r.requestHost)

                host_buckets[r.requestHost].add(group)

                if r.requestHost not in host_tab_map:
                    host_tab_map[r.requestHost] = r.tabHost

            for request_host, groups in host_buckets.items():
                final_group = resolve_final_group(groups)
                groups_list = list(groups)

                tab_host = host_tab_map.get(request_host, "")
                emit_routing_event(
                    "tab",
                    request_host=request_host,
                    final_group=final_group,
                    tab_host=tab_host,
                    ttl=10,
                )

                REQUEST_GROUP_MAP[request_host] = {
                    "group": final_group,
                    "created_at": time.monotonic(),
                }
    finally:
        watcher_task.cancel()
        await asyncio.gather(watcher_task, return_exceptions=True)
