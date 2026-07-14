import asyncio
import socket
import sys
import threading
import time
import traceback
from concurrent.futures import TimeoutError as FutureTimeoutError

from backend.app_settings import get_mihomo_controller_port
from backend.auto_selector import start_auto_selector, stop_auto_selector
from backend.core_config import get_core_listen_settings
from backend.core_launcher import core_health_ok, is_core_available, is_core_running, start_core, stop_core
from backend.local_http import local_get
from backend.mihomo_launcher import launch_mihomo_all, stop_all_mihomo
from backend.activity_bus import write_log
from backend.runtime_snapshot import clear_core_metrics, update_core_metrics
from backend.group_health import start_group_health_monitor, stop_group_health_monitor



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
        self.core_mode = False
        self._last_core_traffic_trigger: dict[str, tuple[int, int, int, int, str]] = {}

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

        if self.core_mode and not is_core_running():
            self._set_failed("contextproxy core exited")
            return False

        return True

    def is_starting(self) -> bool:
        return self.get_state() == "starting"

    def get_state(self) -> str:
        with self._state_lock:
            return self.state

    def _is_stopping_or_stopped(self) -> bool:
        return self.get_state() in {"stopping", "stopped", "failed"}

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
        write_log("backend_service", message, "INFO")

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
            self.core_mode = is_core_available()
            if not self.core_mode:
                raise FileNotFoundError("core/contextproxy-core.exe 缺失，无法启动 Go core 数据面")
            self.log("[BackendService] using Go core data plane")

            step_start = time.perf_counter()
            self.log("[BackendService] starting mihomo")
            launch_mihomo_all()
            self.log(f"[BackendService] mihomo started in {time.perf_counter() - step_start:.2f}s")

            step_start = time.perf_counter()
            self._create_task(start_group_health_monitor(), "group_health")
            start_core()
            self._create_task(self._monitor_core_process(), "contextproxy_core_monitor")
            self._create_task(self._watch_core_metrics(), "contextproxy_core_metrics")
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
        self.core_mode = False
        self._last_core_traffic_trigger: dict[str, tuple[int, int, int, int, str]] = {}
        self._set_state(final_state)
        self.stopped_event.set()

    async def _cleanup_services(self):
        self.log("[BackendService] cleanup services")

        try:
            stop_group_health_monitor()
        except Exception as exc:
            self.log(f"[BackendService] group health stop failed: {exc}")

        try:
            stop_auto_selector()
        except Exception as exc:
            self.log(f"[BackendService] auto selector stop failed: {exc}")

        await self._cancel_tasks_by_name({"contextproxy_core_monitor", "contextproxy_core_metrics"})

        try:
            stop_core()
        except Exception as exc:
            self.log(f"[BackendService] core stop failed: {exc}")

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
        clear_core_metrics()

        try:
            stop_all_mihomo()
        except Exception as exc:
            self.log(f"[BackendService] mihomo stop failed: {exc}")

    def _create_task(self, coro, name: str):
        task = self.loop.create_task(coro, name=name)
        task.add_done_callback(self._on_task_done)
        self.tasks.append(task)
        return task

    async def _cancel_tasks_by_name(self, names: set[str]):
        current_task = asyncio.current_task()
        tasks = [
            task
            for task in self.tasks
            if task is not current_task and not task.done() and task.get_name() in names
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _on_task_done(self, task: asyncio.Task):
        if task.cancelled():
            return

        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return

        if self._is_stopping_or_stopped():
            if exc is not None:
                self.log(f"[BackendService] {task.get_name()} ended during shutdown: {exc}")
            return

        if exc is None:
            self.last_error = f"{task.get_name()} exited unexpectedly"
            self.log(f"[BackendService] {self.last_error}")
            if self.loop and self.loop.is_running():
                self.loop.create_task(self._stop_after_failure())
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
        listen_host, listen_port, api_host, api_port = get_core_listen_settings()
        controller_port = get_mihomo_controller_port()

        waiting = []
        if not self._can_connect(listen_host, listen_port):
            waiting.append(f"tcp proxy port {listen_port}")
        if not self._can_connect(api_host, api_port):
            waiting.append(f"core api port {api_port}")
        if not core_health_ok(timeout=0.5):
            waiting.append("contextproxy core /health")
        if not self._controller_ready(controller_port):
            waiting.append(f"mihomo controller {controller_port}")
        return waiting

    async def _monitor_core_process(self):
        failed_health_count = 0
        while True:
            await asyncio.sleep(2)
            if self._is_stopping_or_stopped():
                self.log("[BackendService] contextproxy core monitor stopped because backend is stopping")
                return

            if not is_core_running():
                if self._is_stopping_or_stopped():
                    self.log("[BackendService] contextproxy core monitor saw core exit during shutdown")
                    return
                raise RuntimeError("contextproxy core process exited")

            health_ok = await asyncio.to_thread(core_health_ok, timeout=0.8)
            if self._is_stopping_or_stopped():
                self.log("[BackendService] contextproxy core monitor stopped because backend is stopping")
                return

            if health_ok:
                failed_health_count = 0
                continue
            failed_health_count += 1
            self.log(f"[BackendService] contextproxy core health failed {failed_health_count}/3")
            if failed_health_count >= 3:
                if self._is_stopping_or_stopped():
                    self.log("[BackendService] contextproxy core health failed during shutdown")
                    return
                raise RuntimeError("contextproxy core /health unavailable")

    async def _watch_core_metrics(self):
        from backend.group_health import schedule_group_recovery, should_recover_from_core_metrics

        while True:
            await asyncio.sleep(2)
            if not self.core_mode or not is_core_running():
                continue

            try:
                _proxy_host, _proxy_port, api_host, api_port = get_core_listen_settings()
                response = await asyncio.to_thread(
                    local_get,
                    f"http://{api_host}:{api_port}/metrics",
                    timeout=1,
                )
                if response.status_code != 200:
                    continue
                data = response.json()
                if isinstance(data, dict):
                    update_core_metrics(data)
                traffic = data.get("traffic", {}) if isinstance(data, dict) else {}
                if not isinstance(traffic, dict):
                    continue

                active_groups = set()
                for group_name, stats in traffic.items():
                    if not isinstance(stats, dict):
                        continue
                    group_key = str(group_name)
                    active_groups.add(group_key)
                    recent_total = int(stats.get("recent_total") or 0)
                    recent_fail = int(stats.get("recent_fail_count") or 0)
                    consecutive = int(stats.get("consecutive_failures") or 0)
                    updated_at = int(stats.get("updated_at") or 0)
                    last_reason = str(stats.get("last_reason") or "")
                    fail_rate = recent_fail / max(1, recent_total)
                    should_recover, immediate_node_failure = should_recover_from_core_metrics(
                        recent_total,
                        recent_fail,
                        consecutive,
                        last_reason,
                    )

                    if not should_recover:
                        if consecutive == 0 or recent_fail == 0:
                            self._last_core_traffic_trigger.pop(group_key, None)
                        continue

                    signature = (updated_at, recent_total, recent_fail, consecutive, last_reason)
                    if self._last_core_traffic_trigger.get(group_key) == signature:
                        continue
                    self._last_core_traffic_trigger[group_key] = signature

                    self.log(
                        "[BackendService] group real traffic failure: "
                        f"group={group_name}, recent_total={recent_total}, "
                        f"recent_fail_count={recent_fail}, "
                        f"recent_failure_rate={fail_rate:.2f}, "
                        f"consecutive_failures={consecutive}, last_reason={last_reason or '-'}, "
                        f"immediate_node_failure={immediate_node_failure}, updated_at={updated_at}"
                    )
                    schedule_group_recovery(
                        group_key,
                        f"go_core_metrics:{last_reason or 'threshold'}",
                    )

                for group_key in list(self._last_core_traffic_trigger):
                    if group_key not in active_groups:
                        self._last_core_traffic_trigger.pop(group_key, None)
            except Exception as exc:
                self.log(f"[BackendService] core metrics read failed: {exc}")

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
