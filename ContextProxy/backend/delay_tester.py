import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import yaml

from backend.app_settings import get_auto_select_settings, get_mihomo_controller_port
from backend.local_http import local_get


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NODE_POOL_FILE = PROJECT_ROOT / "config" / "node_pool.yaml"
MAX_WORKERS = 12
DELAY_TEST_LOCK = threading.Lock()


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def _load_node_pool() -> dict:
    data = _load_yaml(NODE_POOL_FILE)
    nodes = data.get("nodes", {}) if isinstance(data, dict) else {}
    return nodes if isinstance(nodes, dict) else {}


def _test_url() -> str:
    auto_settings = get_auto_select_settings()
    return str(auto_settings.get("test_url") or "http://www.gstatic.com/generate_204")


def _timeout_ms() -> int:
    auto_settings = get_auto_select_settings()
    try:
        return int(auto_settings.get("delay_timeout_ms") or 5000)
    except (TypeError, ValueError):
        return 5000


def _controller_url(controller_port: int, path: str = "/proxies") -> str:
    return f"http://127.0.0.1:{controller_port}{path}"


def is_main_controller_available(controller_port: int | None = None) -> bool:
    controller_port = controller_port or get_mihomo_controller_port()
    try:
        response = local_get(_controller_url(controller_port), timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def _is_vless_grpc_reality(node: dict | None) -> bool:
    if not isinstance(node, dict):
        return False
    return (
        str(node.get("type", "")).lower() == "vless"
        and str(node.get("network", "")).lower() == "grpc"
        and bool(node.get("reality-opts"))
    )


def _node_timeout_ms(node: dict | None = None) -> int:
    timeout_ms = _timeout_ms()
    if _is_vless_grpc_reality(node):
        return max(timeout_ms, 15000)
    return timeout_ms


def _node_attempts(node: dict | None = None) -> int:
    return 2 if _is_vless_grpc_reality(node) else 1


def test_node_delay_via_main_controller(node_name: str, controller_port: int | None = None, node: dict | None = None) -> dict:
    controller_port = controller_port or get_mihomo_controller_port()
    timeout_ms = _node_timeout_ms(node)
    encoded_name = quote(str(node_name), safe="")
    url = _controller_url(controller_port, f"/proxies/{encoded_name}/delay")
    last_error = None

    for attempt in range(_node_attempts(node)):
        try:
            response = local_get(
                url,
                params={"url": _test_url(), "timeout": str(timeout_ms)},
                timeout=(timeout_ms / 1000) + 3,
            )
            response.raise_for_status()
            data = response.json()
            delay = int(data.get("delay"))
            return {
                "delay": delay,
                "status": "ok",
                "error": None,
            }
        except Exception as exc:
            last_error = exc
            if attempt + 1 < _node_attempts(node):
                time.sleep(0.35)

    return {
        "delay": None,
        "status": "failed",
        "error": str(last_error),
    }

def test_all_node_delays_via_main_controller() -> dict:
    if not DELAY_TEST_LOCK.acquire(blocking=False):
        return {
            "ok": False,
            "error": "delay test is already running",
            "results": {},
        }

    try:
        nodes = _load_node_pool()
        if not nodes:
            return {
                "ok": False,
                "error": "node_pool.yaml is empty; no nodes to test",
                "results": {},
            }

        controller_port = get_mihomo_controller_port()
        if not is_main_controller_available(controller_port):
            return {
                "ok": False,
                "error": "mihomo controller 不可用，请先启动代理",
                "results": {},
            }

        node_items = [
            (str(node.get("name") or fallback_name), node)
            for fallback_name, node in nodes.items()
            if isinstance(node, dict)
        ]

        results = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_map = {
                executor.submit(test_node_delay_via_main_controller, node_name, controller_port, node): node_name
                for node_name, node in node_items
            }

            for future in as_completed(future_map):
                node_name = future_map[future]
                try:
                    results[node_name] = future.result()
                except Exception as exc:
                    results[node_name] = {
                        "delay": None,
                        "status": "failed",
                        "error": str(exc),
                    }

        return {
            "ok": True,
            "error": None,
            "results": results,
        }
    finally:
        DELAY_TEST_LOCK.release()


def test_node_delay(controller_port, node_name):
    result = test_node_delay_via_main_controller(node_name, controller_port)
    if result.get("status") == "ok":
        return int(result.get("delay"))
    return None


def test_all_node_delays() -> dict:
    return test_all_node_delays_via_main_controller()
