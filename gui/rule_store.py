from pathlib import Path

from backend.paths import APP_PROCESSES_FILE, GROUPS_DOMAINS_FILE
from gui.config_store import load_group_nodes_config


DOMAIN_RULES_FILE = GROUPS_DOMAINS_FILE
PROCESS_RULES_FILE = APP_PROCESSES_FILE
FALLBACK_GROUPS = ["AI", "Media", "Proxy"]


def _load_rule_file(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        return []

    rules = []
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or "," not in line:
                continue

            group, value = line.split(",", 1)
            group = group.strip()
            value = value.strip()
            if group and value:
                rules.append((group, value))

    return rules


def _save_rule_file(path: Path, rules: list[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        for group, value in rules:
            file.write(f"{group},{value}\n")


def load_domain_rules() -> list[tuple[str, str]]:
    return _load_rule_file(DOMAIN_RULES_FILE)


def save_domain_rules(rules: list[tuple[str, str]]) -> None:
    _save_rule_file(DOMAIN_RULES_FILE, rules)


def load_process_rules() -> list[tuple[str, str]]:
    return _load_rule_file(PROCESS_RULES_FILE)


def save_process_rules(rules: list[tuple[str, str]]) -> None:
    _save_rule_file(PROCESS_RULES_FILE, rules)


def load_group_names() -> list[str]:
    data, _error = load_group_nodes_config()
    groups = data.get("groups", {})

    if not isinstance(groups, dict) or not groups:
        return FALLBACK_GROUPS.copy()

    return [str(group_name) for group_name in groups.keys()]
