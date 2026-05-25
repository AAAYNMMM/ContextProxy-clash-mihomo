import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import quote

import yaml

from backend.app_settings import get_latency_test_settings, get_mihomo_controller_port
from backend.local_http import local_get
from backend.paths import CONFIG_DIR


NODE_POOL_FILE = CONFIG_DIR / "node_pool.yaml"
MAX_WORKERS = 12
DELAY_TEST_LOCK = threading.Lock()
DELAY_TEST_CANCEL_EVENT = threading.Event()
PRIMARY_HEALTH_CHECK_URL = "https://www.gstatic.com/generate_204"


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
    latency_settings = get_latency_test_settings()
    test_url = str(latency_settings.get("test_url") or PRIMARY_HEALTH_CHECK_URL)
    if test_url in {
        "http://www.gstatic.com/generate_204",
        "http://www.google.com/generate_204",
    }:
        return PRIMARY_HEALTH_CHECK_URL
    return test_url


def _timeout_ms() -> int:
    latency_settings = get_latency_test_settings()
    try:
        return int(latency_settings.get("timeout_ms") or 5000)
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


def test_node_delay_via_main_controller(
    node_name: str,
    controller_port: int | None = None,
    node: dict | None = None,
    allow_retry: bool = True,
    test_url: str | None = None,
) -> dict:
    controller_port = controller_port or get_mihomo_controller_port()
    timeout_ms = _node_timeout_ms(node)
    encoded_name = quote(str(node_name), safe="")
    url = _controller_url(controller_port, f"/proxies/{encoded_name}/delay")
    last_error = None

    attempts = _node_attempts(node) if allow_retry else 1
    for attempt in range(attempts):
        try:
            response = local_get(
                url,
                params={"url": test_url or _test_url(), "timeout": str(timeout_ms)},
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
            if attempt + 1 < attempts:
                time.sleep(0.35)

    return {
        "delay": None,
        "status": "failed",
        "error": str(last_error),
    }

def cancel_delay_test():
    DELAY_TEST_CANCEL_EVENT.set()


def test_all_node_delays_via_main_controller(cancel_event: threading.Event | None = None) -> dict:
    if not DELAY_TEST_LOCK.acquire(blocking=False):
        return {
            "ok": False,
            "error": "delay test is already running",
            "results": {},
        }

    cancel_event = cancel_event or DELAY_TEST_CANCEL_EVENT
    cancel_event.clear()

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
        executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        future_map = {
            executor.submit(test_node_delay_via_main_controller, node_name, controller_port, node, False): node_name
            for node_name, node in node_items
        }
        pending = set(future_map.keys())

        try:
            while pending and not cancel_event.is_set():
                done, pending = wait(pending, timeout=0.3, return_when=FIRST_COMPLETED)
                for future in done:
                    node_name = future_map[future]
                    try:
                        results[node_name] = future.result()
                    except Exception as exc:
                        results[node_name] = {
                            "delay": None,
                            "status": "failed",
                            "error": str(exc),
                        }

            if cancel_event.is_set():
                for future in pending:
                    future.cancel()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        return {
            "ok": True,
            "error": None,
            "cancelled": cancel_event.is_set(),
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
