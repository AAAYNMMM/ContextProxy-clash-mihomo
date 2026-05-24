import asyncio
import socket
import sys
import threading
import time
import traceback
from datetime import datetime
from concurrent.futures import TimeoutError as FutureTimeoutError

from backend.app_process_rules import load_app_process_file, start_app_process_watcher
from backend.app_settings import get_mihomo_controller_port, get_proxy_settings
from backend.auto_selector import start_auto_selector, stop_auto_selector
from backend.batch_processor import init_request_queue, load_domain_file, start_batch_processor
from backend.local_http import local_get
from backend.mihomo_launcher import launch_mihomo_all, stop_all_mihomo
from backend.paths import APP_PROCESSES_FILE, GROUPS_DOMAINS_FILE, LOGS_DIR
from backend.process_cache import start_process_cache_watcher, stop_process_cache_watcher
from backend.group_health import start_group_health_monitor, stop_group_health_monitor
from backend.receiver import start_receiver, stop_receiver
from backend.tcp_proxy import async_stop_tcp_proxy, start_tcp_proxy, stop_tcp_proxy


LOG_FILE = LOGS_DIR / "backend_service.log"


class BackendService:
    VALID_STATES = {"stopped", "starting", "running", "stopping", "failed"}

    def __init__(self):
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.tasks: list[asyncio.Task] = []
        self.last_error: str | None = None
        self._start_future = None

        self.state = "stopped"
        self.running = False
        self.starting = False
        self.stopping = False

        self.started_event = threading.Event()
        self.stopped_event = threading.Event()
        self._loop_ready_event = threading.Event()
        self._state_lock = threading.RLock()
        self._log_lock = threading.Lock()

    def start(self) -> tuple[bool, str | None]:
        with self._state_lock:
            if self.state == "starting":
                return False, "proxy is starting"
            if self.state == "running" and self.is_running():
                return False, "proxy is already running"

            self.last_error = None
            self.started_event.clear()
            self.stopped_event.clear()
            self._set_state("starting")

        self._ensure_loop_thread()
        if not self.loop or not self.loop.is_running():
            self._set_failed("backend event loop is not running")
            return False, self.last_error

        future = asyncio.run_coroutine_threadsafe(self._async_start(), self.loop)
        self._start_future = future
        try:
            ok, error = future.result(timeout=25)
        except FutureTimeoutError:
            future.cancel()
            self._set_failed("backend service start timeout")
            self.log("[BackendService] state=failed: backend service start timeout")
            try:
                asyncio.run_coroutine_threadsafe(self._async_stop(final_state="failed"), self.loop).result(timeout=8)
            except Exception as exc:
                self.log(f"[BackendService] cleanup after start timeout failed: {exc}")
            self._stop_loop_thread()
            self._start_future = None
            return False, self.last_error
        except Exception as exc:
            self._set_failed(str(exc))
            self.log(f"[BackendService] state=failed: {exc}\n{traceback.format_exc()}")
            self._stop_loop_thread()
            self._start_future = None
            return False, self.last_error

        self._start_future = None
        if not ok:
            self._stop_loop_thread()
            return False, error or self.last_error or "backend service start failed"

        return True, None

    def stop(self) -> tuple[bool, str | None]:
        with self._state_lock:
            if self.state == "stopped" and not self._thread_alive():
                self.running = False
                self.starting = False
                self.stopping = False
                return False, "proxy is not running"

            self._set_state("stopping")

        loop = self.loop
        if loop and loop.is_running():
            try:
                if self._start_future is not None and not self._start_future.done():
                    self._start_future.cancel()
                future = asyncio.run_coroutine_threadsafe(self._async_stop(final_state="stopped"), loop)
                future.result(timeout=10)
            except FutureTimeoutError:
                self.last_error = "backend service stop timeout"
                self.log("[BackendService] stop timeout; force cleanup")
                try:
                    stop_all_mihomo()
                except Exception as exc:
                    self.log(f"[BackendService] force mihomo cleanup failed: {exc}")
                return False, self.last_error
            except Exception as exc:
                self.last_error = f"backend service stop failed: {exc}"
                self.log(f"[BackendService] stop failed: {exc}\n{traceback.format_exc()}")
                try:
                    stop_all_mihomo()
                except Exception as cleanup_exc:
                    self.log(f"[BackendService] mihomo cleanup failed: {cleanup_exc}")
                return False, self.last_error

        self._stop_loop_thread()
        self._set_state("stopped")
        return True, None

    def is_running(self) -> bool:
        with self._state_lock:
            if self.state == "starting":
                return False
            if self.state != "running":
                return False

        if not self._thread_alive():
            self._set_failed("backend thread exited")
            return False

        return True

    def is_starting(self) -> bool:
        return self.get_state() == "starting"

    def get_state(self) -> str:
        with self._state_lock:
            return self.state

    def cleanup_residue(self):
        state = self.get_state()
        if state == "starting":
            return
        if state == "running" and self.is_running():
            return

        if self._thread_alive():
            self.stop()
            return

        try:
            stop_all_mihomo()
        except Exception as exc:
            self.log(f"[BackendService] residual mihomo cleanup failed: {exc}")

    def log(self, message: str):
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._log_lock:
            with open(LOG_FILE, "a", encoding="utf-8") as file:
                file.write(f"{timestamp} {message}\n")

    def _ensure_loop_thread(self):
        if self._thread_alive() and self.loop and self.loop.is_running():
            return

        self._loop_ready_event.clear()
        self.thread = threading.Thread(target=self._thread_main, name="ContextProxyBackend", daemon=False)
        self.thread.start()
        self._loop_ready_event.wait(timeout=5)

    def _thread_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _thread_main(self):
        if sys.platform.startswith("win"):
            self.loop = asyncio.SelectorEventLoop()
        else:
            self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._loop_ready_event.set()

        try:
            self.loop.run_forever()
        except BaseException as exc:
            self._set_failed(str(exc))
            self.log(f"[BackendService] loop thread error: {exc}\n{traceback.format_exc()}")
        finally:
            self._cancel_pending_tasks_before_close()
            try:
                self.loop.close()
            except Exception:
                pass

    def _stop_loop_thread(self):
        loop = self.loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

        if self.thread and self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=5)

        self.thread = None
        self.loop = None

    def _cancel_pending_tasks_before_close(self):
        if not self.loop:
            return

        pending = [task for task in asyncio.all_tasks(self.loop) if not task.done()]
        for task in pending:
            task.cancel()

        if pending:
            try:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass

    def _set_state(self, state: str):
        if state not in self.VALID_STATES:
            raise ValueError(f"invalid backend state: {state}")

        with self._state_lock:
            self.state = state
            self.starting = state == "starting"
            self.running = state == "running"
            self.stopping = state == "stopping"

        self.log(f"[BackendService] state={state}")

    def _set_failed(self, error: str | None):
        with self._state_lock:
            self.last_error = error or "backend service failed"
        self._set_state("failed")
        self.started_event.set()

    async def _async_start(self) -> tuple[bool, str | None]:
        self._set_state("starting")
        total_start = time.perf_counter()
        try:
            step_start = time.perf_counter()
            self.log("[BackendService] loading rules")
            init_request_queue()
            load_domain_file(str(GROUPS_DOMAINS_FILE))
            load_app_process_file(str(APP_PROCESSES_FILE))
            self.log(f"[BackendService] rules ready in {time.perf_counter() - step_start:.2f}s")

            step_start = time.perf_counter()
            self.log("[BackendService] starting mihomo")
            launch_mihomo_all()
            self.log(f"[BackendService] mihomo started in {time.perf_counter() - step_start:.2f}s")

            step_start = time.perf_counter()
            self._create_task(start_batch_processor(), "batch_processor")
            self._create_task(start_app_process_watcher(), "app_process_watcher")
            self._create_task(start_process_cache_watcher(), "process_cache")
            self._create_task(start_group_health_monitor(), "group_health")
            self._create_task(start_tcp_proxy(), "tcp_proxy")
            self._create_task(start_receiver(), "receiver")
            self.log(f"[BackendService] async tasks scheduled in {time.perf_counter() - step_start:.2f}s")

            step_start = time.perf_counter()
            ok, error = await self._wait_until_healthy(timeout=10)
            if not ok:
                raise RuntimeError(error or "backend health check failed")
            self.log(f"[BackendService] health ready in {time.perf_counter() - step_start:.2f}s")

            step_start = time.perf_counter()
            self.log("[BackendService] starting auto selector")
            start_auto_selector()
            self.log(f"[BackendService] auto selector started in {time.perf_counter() - step_start:.2f}s")

            self._set_state("running")
            self.log(f"[BackendService] startup completed in {time.perf_counter() - total_start:.2f}s")
            self.last_error = None
            self.started_event.set()
            return True, None

        except asyncio.CancelledError:
            message = "backend start task cancelled"
            self.last_error = message
            self.log(f"[BackendService] {message}")
            await self._cleanup_services()
            if self.get_state() == "stopping":
                self._set_state("stopped")
            else:
                self._set_failed(message)
            return False, message

        except Exception as exc:
            error = str(exc)
            self.last_error = error
            self.log(f"[BackendService] state=failed: {error}\n{traceback.format_exc()}")
            await self._cleanup_services()
            self._set_failed(error)
            return False, error

    async def _async_stop(self, final_state: str = "stopped"):
        self._set_state("stopping")
        await self._cleanup_services()
        self._set_state(final_state)
        self.stopped_event.set()

    async def _cleanup_services(self):
        self.log("[BackendService] cleanup services")

        try:
            stop_process_cache_watcher()
        except Exception as exc:
            self.log(f"[BackendService] process cache stop failed: {exc}")

        try:
            stop_group_health_monitor()
        except Exception as exc:
            self.log(f"[BackendService] group health stop failed: {exc}")

        try:
            stop_auto_selector()
        except Exception as exc:
            self.log(f"[BackendService] auto selector stop failed: {exc}")

        try:
            stop_tcp_proxy()
        except Exception as exc:
            self.log(f"[BackendService] tcp_proxy stop failed: {exc}")

        try:
            await stop_receiver()
        except Exception as exc:
            self.log(f"[BackendService] receiver stop failed: {exc}")

        try:
            await async_stop_tcp_proxy()
        except Exception as exc:
            self.log(f"[BackendService] tcp_proxy async stop failed: {exc}")

        for _ in range(10):
            pending_business_tasks = [task for task in self.tasks if not task.done()]
            if not pending_business_tasks:
                break
            await asyncio.sleep(0.1)

        current_task = asyncio.current_task()
        tasks = [task for task in self.tasks if task is not current_task and not task.done()]
        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self.tasks.clear()

        try:
            stop_all_mihomo()
        except Exception as exc:
            self.log(f"[BackendService] mihomo stop failed: {exc}")

    def _create_task(self, coro, name: str):
        task = self.loop.create_task(coro, name=name)
        task.add_done_callback(self._on_task_done)
        self.tasks.append(task)
        return task

    def _on_task_done(self, task: asyncio.Task):
        if self.get_state() in {"stopping", "stopped"} or task.cancelled():
            return

        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return

        if exc is None:
            return

        self.last_error = f"{task.get_name()} exited with error: {exc}"
        self.log(f"[BackendService] {self.last_error}")
        if self.loop and self.loop.is_running():
            self.loop.create_task(self._stop_after_failure())

    async def _stop_after_failure(self):
        await self._cleanup_services()
        self._set_failed(self.last_error)

    async def _wait_until_healthy(self, timeout: float) -> tuple[bool, str | None]:
        deadline = time.monotonic() + timeout
        last_waiting = None
        while time.monotonic() < deadline:
            failed_task_error = self._first_failed_task_error()
            if failed_task_error:
                return False, failed_task_error

            waiting = self._health_waiting_items()
            if not waiting:
                return True, None

            waiting_text = ", ".join(waiting)
            if waiting_text != last_waiting:
                self.log(f"[BackendService] waiting {waiting_text}")
                last_waiting = waiting_text

            await asyncio.sleep(0.2)

        return False, f"health check timeout: {last_waiting or 'services'} not ready"

    def _first_failed_task_error(self) -> str | None:
        for task in self.tasks:
            if not task.done() or task.cancelled():
                continue
            exc = task.exception()
            if exc:
                return f"{task.get_name()} start failed: {exc}"
        return None

    def _health_check_once(self) -> bool:
        return not self._health_waiting_items()

    def _health_waiting_items(self) -> list[str]:
        proxy_settings = get_proxy_settings()
        listen_host = str(proxy_settings.get("listen_host") or "127.0.0.1")
        listen_port = int(proxy_settings.get("listen_port") or 18000)
        receiver_port = int(proxy_settings.get("receiver_port") or 17890)
        controller_port = get_mihomo_controller_port()

        waiting = []
        if not self._can_connect(listen_host, listen_port):
            waiting.append(f"tcp proxy port {listen_port}")
        if not self._can_connect("127.0.0.1", receiver_port):
            waiting.append(f"receiver port {receiver_port}")
        if not self._controller_ready(controller_port):
            waiting.append(f"mihomo controller {controller_port}")
        return waiting

    @staticmethod
    def _can_connect(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            return False

    @staticmethod
    def _controller_ready(port: int) -> bool:
        try:
            response = local_get(f"http://127.0.0.1:{port}/proxies", timeout=0.5)
            return response.status_code == 200
        except Exception:
            return False
