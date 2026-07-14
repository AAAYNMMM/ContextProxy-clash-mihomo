from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from backend.activity_bus import write_log
from backend.app_settings import clear_app_settings_cache, get_default_app_settings
from backend.atomic_writer import atomic_write_text, atomic_write_yaml
from backend.paths import APP_PROCESSES_FILE, CONFIG_DIR, GROUPS_DOMAINS_FILE


APP_SETTINGS_FILE = CONFIG_DIR / "app_settings.yaml"
GROUP_NODES_FILE = CONFIG_DIR / "group_nodes.yaml"


@dataclass
class ApplyResult:
    changed_groups: set[str] = field(default_factory=set)
    changed_items: set[str] = field(default_factory=set)
    core_payload: dict | None = None
    mihomo_applied: bool = False


def _snapshot_file(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _restore_file(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        try:
            path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        return
    from backend.atomic_writer import atomic_write_bytes

    atomic_write_bytes(path, snapshot)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rule_text(rules: Iterable[tuple[str, str]]) -> str:
    return "".join(f"{group},{value}\n" for group, value in rules)


def _write_rule_file_internal(path: Path, rules: list[tuple[str, str]]) -> None:
    atomic_write_text(path, _rule_text(rules))


def _settings_for_save(settings: dict) -> dict:
    merged = deepcopy(get_default_app_settings())
    settings = dict(settings or {})
    legacy_ui = settings.get("ui")
    if isinstance(legacy_ui, dict) and "auto_manage_system_proxy" not in legacy_ui:
        enable_on_start = legacy_ui.get("enable_system_proxy_on_start", True)
        disable_on_stop = legacy_ui.get("disable_system_proxy_on_stop", True)
        settings["ui"] = {
            **legacy_ui,
            "auto_manage_system_proxy": (
                enable_on_start if isinstance(enable_on_start, bool) else True
            )
            and (
                disable_on_stop if isinstance(disable_on_stop, bool) else True
            ),
        }
    for section, defaults in merged.items():
        incoming = settings.get(section, {})
        if not isinstance(incoming, dict):
            continue
        for key in defaults.keys():
            if key in incoming:
                merged[section][key] = incoming[key]
    merged["updated_at"] = _now_str()
    return merged


def _is_port(value) -> bool:
    return isinstance(value, int) and 1 <= value <= 65535


def validate_app_settings(settings: dict) -> tuple[bool, str | None]:
    proxy = settings.get("proxy", {})
    if not str(proxy.get("listen_host", "")).strip():
        return False, "本地监听地址不能为空"
    if not _is_port(proxy.get("listen_port")):
        return False, "本地代理端口必须是 1-65535"
    if not _is_port(proxy.get("receiver_port")):
        return False, "Tab 上报接收端口必须是 1-65535"

    mihomo = settings.get("mihomo", {})
    if not _is_port(mihomo.get("controller_port")):
        return False, "mihomo controller 端口必须是 1-65535"
    if not _is_port(mihomo.get("mixed_port")):
        return False, "mihomo mixed-port 必须是 1-65535"

    seen_ports = {}
    for label, port in (
        ("本地代理", proxy.get("listen_port")),
        ("Tab 上报接收", proxy.get("receiver_port")),
        ("mihomo controller", mihomo.get("controller_port")),
        ("mihomo mixed-port", mihomo.get("mixed_port")),
    ):
        existing_label = seen_ports.get(port)
        if existing_label:
            return False, f"{label} 与 {existing_label} 端口不能相同"
        seen_ports[port] = label

    latency_test = settings.get("latency_test", {})
    if not isinstance(latency_test.get("timeout_ms"), int) or latency_test["timeout_ms"] < 1000:
        return False, "延迟测试超时必须大于等于 1000 ms"
    if not str(latency_test.get("test_url", "")).strip():
        return False, "测试 URL 不能为空"
    return True, None


def save_app_settings_file_internal(settings: dict) -> tuple[bool, str | None]:
    data = _settings_for_save(settings)
    valid, error = validate_app_settings(data)
    if not valid:
        return False, error
    try:
        atomic_write_yaml(APP_SETTINGS_FILE, data)
        clear_app_settings_cache()
    except Exception as exc:
        return False, str(exc)
    return True, None


def _settings_need_proxy_restart(previous_settings: dict, current_settings: dict) -> bool:
    watched_keys = [
        ("proxy", "listen_host"),
        ("proxy", "listen_port"),
        ("proxy", "receiver_port"),
        ("mihomo", "exe"),
        ("mihomo", "mixed_port"),
        ("mihomo", "controller_port"),
    ]
    for section, key in watched_keys:
        if (previous_settings.get(section, {}) or {}).get(key) != (current_settings.get(section, {}) or {}).get(key):
            return True
    return False


def _settings_need_mihomo_apply(previous_settings: dict, current_settings: dict) -> bool:
    watched_keys = ["exe", "mixed_port", "controller_port"]
    previous_mihomo = previous_settings.get("mihomo", {}) or {}
    current_mihomo = current_settings.get("mihomo", {}) or {}
    return any(previous_mihomo.get(key) != current_mihomo.get(key) for key in watched_keys)


def apply_core_config_change() -> tuple[bool, str | None, dict | None]:
    from backend.core_config import generate_contextproxy_core_config
    from backend.core_launcher import is_core_running, reload_core_config_checked

    generated = generate_contextproxy_core_config()
    if not is_core_running():
        return True, None, generated
    return reload_core_config_checked(generated_config=generated)


def _regenerate_core_config_best_effort() -> None:
    try:
        from backend.core_config import generate_contextproxy_core_config

        generate_contextproxy_core_config()
    except Exception as exc:
        write_log("core", f"failed to regenerate core config after rollback: {exc}", "WARN")


def _regenerate_mihomo_config_best_effort() -> None:
    try:
        from backend.mihomo_config_generator import generate_all_configs

        generate_all_configs()
    except Exception as exc:
        write_log("mihomo", f"failed to regenerate mihomo config after rollback: {exc}", "WARN")


def apply_mihomo_config_change(reason: str = "config_change") -> dict:
    from backend.atomic_writer import atomic_write_bytes
    from backend.mihomo_config_generator import MAIN_CONFIG_FILE, generate_all_configs
    from backend import mihomo_launcher

    write_log("mihomo", f"apply mihomo config change: reason={reason}")
    result = generate_all_configs() or {}
    changed = bool(result.get("changed", True))
    backup_path = Path(str(result.get("backup_path") or MAIN_CONFIG_FILE.with_name(MAIN_CONFIG_FILE.name + ".bak")))

    if not changed:
        write_log(
            "mihomo",
            f"mihomo config unchanged, skip restart: reason={reason} config={result.get('config_path') or MAIN_CONFIG_FILE}",
        )
        return result

    if not mihomo_launcher.is_process_running(mihomo_launcher.mihomo_process):
        write_log("mihomo", f"mihomo not running, generated config only: reason={reason}")
        return result

    try:
        mihomo_launcher.restart_mihomo_core()
        write_log("mihomo", f"mihomo config applied by restart: reason={reason}")
        return result
    except Exception as restart_error:
        write_log(
            "mihomo",
            f"mihomo restart failed after config change: reason={reason} error={restart_error}",
            "ERROR",
        )
        rollback_error = None
        if backup_path.is_file():
            try:
                atomic_write_bytes(MAIN_CONFIG_FILE, backup_path.read_bytes())
                write_log("mihomo", f"mihomo config rollback restored: {backup_path} -> {MAIN_CONFIG_FILE}", "WARN")
                try:
                    mihomo_launcher.restart_mihomo_core()
                    write_log("mihomo", f"mihomo rollback restart succeeded: reason={reason}", "WARN")
                except Exception as rollback_restart_error:
                    rollback_error = rollback_restart_error
                    write_log(
                        "mihomo",
                        f"mihomo rollback restart failed: reason={reason} error={rollback_restart_error}",
                        "ERROR",
                    )
            except Exception as restore_error:
                rollback_error = restore_error
                write_log(
                    "mihomo",
                    f"mihomo config rollback restore failed: reason={reason} error={restore_error}",
                    "ERROR",
                )
        else:
            rollback_error = FileNotFoundError(f"mihomo backup not found: {backup_path}")
            write_log("mihomo", f"mihomo rollback skipped, backup not found: {backup_path}", "ERROR")

        if rollback_error is None:
            raise RuntimeError(f"mihomo 新配置应用失败，已回滚旧配置：{restart_error}") from restart_error
        raise RuntimeError(
            f"mihomo 新配置应用失败，且回滚失败：apply_error={restart_error}; rollback_error={rollback_error}"
        ) from restart_error


def save_domain_rules_and_apply(
    rules: list[tuple[str, str]],
    old_rules: list[tuple[str, str]] | None = None,
) -> ApplyResult:
    snapshot = _snapshot_file(GROUPS_DOMAINS_FILE)
    try:
        _write_rule_file_internal(GROUPS_DOMAINS_FILE, rules)
        ok, error, payload = apply_core_config_change()
        if not ok:
            raise RuntimeError(error or "Go core reload 失败，域名规则尚未生效")

        affected_rules = list(old_rules or []) + list(rules)
        changed_groups = {group for group, _pattern in affected_rules}
        changed_items = {pattern for _group, pattern in affected_rules}
        from backend.core_launcher import is_core_running

        if changed_groups and is_core_running():
            from backend.connection_closer import close_changed_groups

            close_changed_groups(changed_groups)
        return ApplyResult(changed_groups=changed_groups, changed_items=changed_items, core_payload=payload)
    except Exception:
        _restore_file(GROUPS_DOMAINS_FILE, snapshot)
        _regenerate_core_config_best_effort()
        raise


def save_process_rules_and_apply(
    rules: list[tuple[str, str]],
    old_rules: list[tuple[str, str]] | None = None,
) -> ApplyResult:
    snapshot = _snapshot_file(APP_PROCESSES_FILE)
    try:
        _write_rule_file_internal(APP_PROCESSES_FILE, rules)
        ok, error, payload = apply_core_config_change()
        if not ok:
            raise RuntimeError(error or "Go core reload 失败，进程规则尚未生效")

        affected_rules = list(old_rules or []) + list(rules)
        changed_groups = {group for group, _process in affected_rules}
        changed_items = {process for _group, process in affected_rules}
        from backend.core_launcher import is_core_running

        if changed_groups and is_core_running():
            from backend.connection_closer import close_changed_groups

            close_changed_groups(changed_groups)
        return ApplyResult(changed_groups=changed_groups, changed_items=changed_items, core_payload=payload)
    except Exception:
        _restore_file(APP_PROCESSES_FILE, snapshot)
        _regenerate_core_config_best_effort()
        raise


def save_app_settings_and_apply(settings: dict, previous_settings: dict | None = None) -> ApplyResult:
    from backend.core_launcher import is_core_running

    current_settings = _settings_for_save(settings)
    previous_settings = previous_settings or {}
    if is_core_running() and _settings_need_proxy_restart(previous_settings, current_settings):
        raise RuntimeError("代理运行中不能修改监听端口或 mihomo 端口，请先停止代理后再保存。")

    snapshot = _snapshot_file(APP_SETTINGS_FILE)
    try:
        ok, error = save_app_settings_file_internal(current_settings)
        if not ok:
            raise RuntimeError(error or "保存设置失败")
        if _settings_need_mihomo_apply(previous_settings, current_settings):
            apply_mihomo_config_change("settings_change")
        ok, error, payload = apply_core_config_change()
        if not ok:
            raise RuntimeError(error or "Go core reload 失败，设置尚未生效")
        return ApplyResult(core_payload=payload)
    except Exception:
        _restore_file(APP_SETTINGS_FILE, snapshot)
        clear_app_settings_cache()
        _regenerate_core_config_best_effort()
        _regenerate_mihomo_config_best_effort()
        raise


def save_group_nodes_and_apply(
    data: dict,
    *,
    allow_running: bool = False,
    reason: str = "group_nodes_change",
) -> ApplyResult:
    from backend.core_launcher import is_core_running
    from backend.port_manager import prepare_mihomo_runtime_ports

    if is_core_running() and not allow_running:
        raise RuntimeError("代理运行中不能修改分组配置，请先停止代理。")

    snapshot = _snapshot_file(GROUP_NODES_FILE)
    data = deepcopy(data)
    data["updated_at"] = _now_str()
    if "groups" not in data or not isinstance(data["groups"], dict):
        data["groups"] = {}

    try:
        atomic_write_yaml(GROUP_NODES_FILE, data)
        if not is_core_running():
            ok, error, _changes = prepare_mihomo_runtime_ports(write_settings=True, check_system=True)
            if not ok:
                raise RuntimeError(error or "mihomo 端口准备失败")
        apply_mihomo_config_change(reason)
        ok, error, payload = apply_core_config_change()
        if not ok:
            raise RuntimeError(error or "Go core reload 失败，分组配置尚未生效")
        return ApplyResult(core_payload=payload, mihomo_applied=True)
    except Exception:
        _restore_file(GROUP_NODES_FILE, snapshot)
        _regenerate_core_config_best_effort()
        _regenerate_mihomo_config_best_effort()
        raise
