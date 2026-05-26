from backend.runtime_config import (
    DEFAULT_GROUP_CONFIG,
    get_group_port_map,
)
from backend.app_settings import get_mihomo_controller_port, get_proxy_settings
from backend.paths import MIHOMO_DIR as MIHOMO_DIR_PATH, PROJECT_ROOT


MIHOMO_DIR = str(MIHOMO_DIR_PATH)
MIHOMO_EXE = "mihomo-windows-amd64-compatible.exe"

DEFAULT_GROUP_PORT_MAP = {
    group: data["port"]
    for group, data in DEFAULT_GROUP_CONFIG.items()
}

DEFAULT_GROUP_CONTROLLER_MAP = {
    group: get_mihomo_controller_port()
    for group in DEFAULT_GROUP_CONFIG
}

# Compatibility values for older imports. Runtime paths should call fresh
# helpers instead of relying on these module-level snapshots.
GROUP_PORT_MAP = get_group_port_map()
GROUP_CONTROLLER_MAP = {
    group: get_mihomo_controller_port()
    for group in GROUP_PORT_MAP
}

SPECIAL_GROUPS = ["AI", "Media"]

FIXED_DOMAINS = []

DEFAULT_PROXY = "Proxy"
DIRECT = "Direct"

_PROXY_SETTINGS = get_proxy_settings()

TCP_LISTEN_HOST = _PROXY_SETTINGS["listen_host"]
TCP_LISTEN_PORT = _PROXY_SETTINGS["listen_port"]
RECEIVER_PORT = _PROXY_SETTINGS["receiver_port"]
