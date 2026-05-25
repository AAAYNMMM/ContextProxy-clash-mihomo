from pathlib import Path
from datetime import datetime
import copy
import re
import socket
import time

from PySide6.QtCore import QEvent, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QInputDialog,
    QMenu,
    QStackedWidget,
    QStyle,
    QSystemTrayIcon,
    QTabBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.config_store import (
    extract_subscription_source,
    generate_mihomo_configs,
    load_group_nodes_config,
    load_node_pool,
    save_group_nodes_config,
)
from gui.loading_overlay import LoadingOverlay
from gui.auto_selector_store import (
    get_selected_node_for_group,
    load_runtime_selected_nodes,
)
from gui.dashboard_store import count_active_connections, get_core_events, get_dashboard_stats
from gui.notification import show_error_animation, show_success_animation, show_warning_animation
from gui.process_manager import (
    cleanup_proxy_residue,
    count_mihomo_processes,
    get_backend_error,
    get_proxy_status,
    get_proxy_state,
    is_proxy_running,
    is_proxy_starting,
    start_proxy_process,
    stop_proxy_process,
)
from gui.rule_store import (
    load_domain_rules,
    load_group_names,
    load_process_rules,
    save_domain_rules,
    save_process_rules,
)
from gui.subscription_store import (
    delete_subscription_from_gui,
    get_subscription_url,
    list_subscriptions,
    update_subscription_from_gui,
)
from gui.settings_store import load_app_settings, save_app_settings
from gui.system_proxy_manager import (
    disable_system_proxy_if_contextproxy,
    enable_system_proxy,
    is_contextproxy_system_proxy,
)
from gui.theme import APP_QSS, DANGER, MUTED, PRIMARY, SUCCESS, TEXT
from gui.widgets.proxy_switch import ProxySwitch
from backend.port_manager import prepare_mihomo_runtime_ports
from backend.delay_tester import cancel_delay_test, test_all_node_delays_via_main_controller
from backend.activity_bus import set_activity_callback, write_log
from backend.paths import PROJECT_ROOT


class DelayTestWorker(QObject):
    finished = Signal(dict)

    def run(self):
        try:
            self.finished.emit(test_all_node_delays_via_main_controller())
        except Exception as exc:
            self.finished.emit({"ok": False, "error": str(exc), "results": {}})


class ProxyActionWorker(QObject):
    finished = Signal(str, bool, str)

    def __init__(self, action: str):
        super().__init__()
        self.action = action

    def run(self):
        try:
            if self.action == "start":
                write_log("lifecycle", "start_proxy_process called, reason=proxy_switch_or_tray_start")
                ok, error = start_proxy_process()
                if ok and load_app_settings().get("ui", {}).get("enable_system_proxy_on_start", True):
                    proxy_settings = load_app_settings().get("proxy", {})
                    host = str(proxy_settings.get("listen_host") or "127.0.0.1")
                    try:
                        port = int(proxy_settings.get("listen_port") or 18000)
                    except (TypeError, ValueError):
                        port = 18000
                    write_log("lifecycle", f"enable_system_proxy called, reason=start_proxy_worker, endpoint={host}:{port}")
                    proxy_ok, proxy_message = enable_system_proxy(host, port)
                    if not proxy_ok:
                        write_log("lifecycle", "stop_proxy_process called, reason=start_proxy_system_proxy_enable_failed")
                        stop_proxy_process()
                        ok = False
                        error = proxy_message or "系统代理启用失败"
            else:
                if load_app_settings().get("ui", {}).get("disable_system_proxy_on_stop", True):
                    proxy_settings = load_app_settings().get("proxy", {})
                    host = str(proxy_settings.get("listen_host") or "127.0.0.1")
                    try:
                        port = int(proxy_settings.get("listen_port") or 18000)
                    except (TypeError, ValueError):
                        port = 18000
                    write_log("lifecycle", f"disable_system_proxy called, reason=stop_proxy_worker, endpoint={host}:{port}")
                    proxy_ok, proxy_message = disable_system_proxy_if_contextproxy(host, port)
                    if not proxy_ok:
                        self.finished.emit(self.action, False, proxy_message or "系统代理关闭失败")
                        return
                write_log("lifecycle", "stop_proxy_process called, reason=proxy_switch_or_tray_stop")
                ok, error = stop_proxy_process()
            self.finished.emit(self.action, ok, error or "")
        except Exception as exc:
            self.finished.emit(self.action, False, str(exc))


class GuiTaskWorker(QObject):
    finished = Signal(bool, object, str)

    def __init__(self, task_func):
        super().__init__()
        self.task_func = task_func

    def run(self):
        try:
            self.finished.emit(True, self.task_func(), "")
        except Exception as exc:
            self.finished.emit(False, None, str(exc))


class MainWindow(QMainWindow):
    activity_signal = Signal(str, str)

    def __init__(self):
        super().__init__()

        self.setWindowTitle("ContextProxy")
        self.resize(1220, 760)
        self.setStyleSheet(APP_QSS)

        self.nav = QListWidget()
        self.pages = QStackedWidget()
        self.activity_log = QTextEdit()
        self.subscription_name_input = None
        self.subscription_url_input = None
        self.subscription_table = None
        self.subscription_urls = {}
        self.node_pool_table = None
        self.node_delay_test_button = None
        self.node_latency_cache = {}
        self.delay_test_thread = None
        self.delay_test_worker = None
        self.proxy_action_thread = None
        self.proxy_action_worker = None
        self.loading_overlay = None
        self.busy = False
        self.current_task_name = None
        self._busy_started_at = 0.0
        self.gui_task_thread = None
        self.gui_task_worker = None
        self.gui_task_on_success = None
        self.gui_task_on_error = None
        self.gui_task_success_message = None
        self.gui_task_error_message = None
        self.group_nodes_config = {"groups": {}}
        self.node_pool_nodes = {}
        self._active_notifications = []
        self.group_list = None
        self.group_name_input = None
        self.group_port_input = None
        self.group_controller_input = None
        self.strategy_combo = None
        self.current_node_input = None
        self.group_node_table = None
        self.current_group_name = None
        self.rule_group_names = []
        self.domain_rule_table = None
        self.domain_group_combo = None
        self.domain_pattern_input = None
        self.process_rule_table = None
        self.process_group_combo = None
        self.process_name_input = None
        self.proxy_status_value_label = None
        self.proxy_status_sub_label = None
        self.proxy_switch = None
        self.proxy_switch_label = None
        self.group_readonly_hint = None
        self.group_new_button = None
        self.group_delete_button = None
        self.group_save_button = None
        self.group_reselect_button = None
        self.dashboard_stat_labels = {}
        self.setting_inputs = {}
        self.setting_checks = {}
        self._auto_selector_log_snapshot = {}
        self.tray_icon = None
        self.tray_menu = None
        self.tray_show_action = None
        self.tray_start_action = None
        self.tray_stop_action = None
        self.tray_quit_action = None
        self._tray_notice_shown = False
        self._force_real_close = False
        self._last_backend_error_seen = None
        self._residue_cleanup_done = False
        self._node_pool_last_loaded_count = None
        self._last_core_event_id = 0
        self._last_core_boot_id = None
        self._routing_activity_seen = {}
        self._max_recent_activities = int(
            load_app_settings().get("logging", {}).get("max_recent_activities") or 200
        )

        self._build_layout()
        self.loading_overlay = LoadingOverlay(self)
        self._setup_tray_icon()
        self.activity_signal.connect(self._append_activity_log)
        set_activity_callback(lambda message, level: self.activity_signal.emit(message, level))
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._poll_proxy_runtime_state)
        self.status_timer.start(2000)
        QTimer.singleShot(0, self._check_group_ports_on_app_start)
        QTimer.singleShot(0, self._cleanup_stale_system_proxy_on_start)
        QTimer.singleShot(0, self._apply_startup_ui_settings)

    def _project_root(self) -> Path:
        return PROJECT_ROOT

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.loading_overlay and self.loading_overlay.isVisible():
            self.loading_overlay.setGeometry(self.rect())

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() != QEvent.WindowStateChange:
            return
        if self._force_real_close:
            return
        if not load_app_settings().get("ui", {}).get("close_to_tray", True):
            return
        if self.windowState() & Qt.WindowMinimized:
            QTimer.singleShot(0, self._hide_to_tray_from_minimize)

    def _log_lifecycle_event(self, message: str, level: str = "INFO"):
        write_log("lifecycle", message, level)

    def _hide_to_tray_from_minimize(self):
        if self._force_real_close:
            return
        if not load_app_settings().get("ui", {}).get("close_to_tray", True):
            return
        self.hide()
        self._update_tray_status()
        self._log_lifecycle_event("window minimized to tray; backend/service/proxy unchanged")
        self._append_activity_log("[INFO] 窗口已最小化到系统托盘")

    def start_busy(self, message="正在处理...", task_name: str | None = None):
        self.busy = True
        self.current_task_name = task_name or message
        self._busy_started_at = time.monotonic()
        if self.loading_overlay:
            self.loading_overlay.setGeometry(self.rect())
            self.loading_overlay.start(message)

    def finish_busy(self, success=True, message=None):
        self.busy = False
        self.current_task_name = None
        if self.loading_overlay:
            self.loading_overlay.stop()

        if message:
            if success:
                self.notify_success(message)
            else:
                self.notify_error(message)

    def run_gui_task(
        self,
        task_func,
        on_success=None,
        on_error=None,
        success_message=None,
        error_message=None,
        loading_message="正在处理...",
        task_name=None,
    ):
        if self.busy:
            self.notify_info("任务处理中，请稍候")
            return False

        self.start_busy(loading_message, task_name or loading_message)
        self.gui_task_thread = QThread(self)
        self.gui_task_worker = GuiTaskWorker(task_func)
        self.gui_task_on_success = on_success
        self.gui_task_on_error = on_error
        self.gui_task_success_message = success_message
        self.gui_task_error_message = error_message

        self.gui_task_worker.moveToThread(self.gui_task_thread)
        self.gui_task_thread.started.connect(self.gui_task_worker.run)
        self.gui_task_worker.finished.connect(self._on_gui_task_finished)
        self.gui_task_worker.finished.connect(self.gui_task_thread.quit)
        self.gui_task_worker.finished.connect(self.gui_task_worker.deleteLater)
        self.gui_task_thread.finished.connect(self.gui_task_thread.deleteLater)
        self.gui_task_thread.finished.connect(self._cleanup_gui_task_thread)
        self.gui_task_thread.start()
        return True

    def _on_gui_task_finished(self, ok: bool, result, error: str):
        elapsed_ms = int((time.monotonic() - self._busy_started_at) * 1000)
        delay_ms = max(0, 200 - elapsed_ms)
        QTimer.singleShot(delay_ms, lambda: self._complete_gui_task(ok, result, error))

    def _complete_gui_task(self, ok: bool, result, error: str):
        try:
            if ok:
                self.finish_busy(True)
                if self.gui_task_on_success:
                    self.gui_task_on_success(result)
                if self.gui_task_success_message:
                    self.notify_success(self.gui_task_success_message)
                return

            message = self.gui_task_error_message or "任务执行失败"
            if error:
                message = f"{message}：{error}"
            self.finish_busy(False)
            if self.gui_task_on_error:
                self.gui_task_on_error(error)
            self.notify_error(message)
        except Exception as exc:
            self.finish_busy(False, f"任务完成处理失败：{exc}")
        finally:
            self.gui_task_on_success = None
            self.gui_task_on_error = None
            self.gui_task_success_message = None
            self.gui_task_error_message = None

    def _cleanup_gui_task_thread(self):
        self.gui_task_thread = None
        self.gui_task_worker = None

    def _apply_startup_ui_settings(self):
        ui_settings = load_app_settings().get("ui", {})

        if ui_settings.get("start_minimized", False):
            self.showMinimized()

        if ui_settings.get("auto_start_proxy", False) and not is_proxy_running():
            self._start_proxy_from_gui()

    def _node_pool_path(self) -> Path:
        return self._project_root() / "config" / "node_pool.yaml"

    def _build_layout(self):
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_sidebar())
        root_layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

        self._add_page("\u4eea\u8868\u76d8", self._dashboard_page())
        self._add_page("\u8ba2\u9605\u7ba1\u7406", self._subscription_page())
        self._add_page("\u8282\u70b9\u6c60", self._node_pool_page())
        self._add_page("\u5206\u7ec4\u7ba1\u7406", self._group_page())
        self._add_page("\u89c4\u5219\u7ba1\u7406", self._rules_page())
        self._add_page("\u8bbe\u7f6e", self._settings_page())

        self.nav.currentRowChanged.connect(self._on_navigation_changed)
        self.nav.setCurrentRow(0)

    def _build_sidebar(self):
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(168)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        brand = QLabel("ContextProxy")
        brand.setObjectName("Brand")
        brand.setFixedHeight(58)

        self.nav.setObjectName("Navigation")
        layout.addWidget(brand)
        layout.addWidget(self.nav, 1)
        return sidebar

    def _add_page(self, title: str, page: QWidget):
        self.nav.addItem(QListWidgetItem(title))
        self.pages.addWidget(page)

    def _on_navigation_changed(self, row: int):
        self.pages.setCurrentIndex(row)

        item = self.nav.item(row) if row >= 0 else None
        if item and item.text() == "\u4eea\u8868\u76d8":
            self.refresh_dashboard()
        if item and item.text() == "\u5206\u7ec4\u7ba1\u7406":
            self.refresh_group_management_page()
        if item and item.text() == "\u89c4\u5219\u7ba1\u7406":
            self.refresh_rule_group_options()

    def _page_shell(self, title: str, hint: str):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 22, 24, 24)
        layout.setSpacing(16)

        title_label = QLabel(title)
        title_label.setObjectName("PageTitle")
        hint_label = QLabel(hint)
        hint_label.setObjectName("PageHint")

        layout.addWidget(title_label)
        layout.addWidget(hint_label)
        return page, layout

    def _card(self):
        card = QFrame()
        card.setObjectName("Card")
        card.setFrameShape(QFrame.StyledPanel)
        return card

    def _dashboard_page(self):
        page, layout = self._page_shell(
            "\u4eea\u8868\u76d8",
            "\u67e5\u770b\u672c\u5730\u4ee3\u7406\u72b6\u6001\u3001\u7edf\u8ba1\u6570\u636e\u548c\u6700\u8fd1\u6d3b\u52a8",
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)

        stats = get_dashboard_stats()
        proxy_host, proxy_port = self._context_proxy_endpoint()
        proxy_endpoint = f"{proxy_host}:{proxy_port}"
        cards = [
            ("\u8fd0\u884c\u72b6\u6001", get_proxy_status(), "\u7b49\u5f85\u542f\u52a8", SUCCESS if is_proxy_running() else MUTED),
            ("\u672c\u5730\u4ee3\u7406", proxy_endpoint, "HTTP / SOCKS5", TEXT),
            ("\u5f53\u524d\u6a21\u5f0f", "Context \u5206\u6d41", "\u667a\u80fd\u57df\u540d + \u7ec4\u95f4\u5206\u6d41", TEXT),
            ("\u6d3b\u52a8\u8fde\u63a5", str(stats.get("active_connections", 0)), "\u5f53\u524d\u6d3b\u8dc3\u6570", TEXT),
            ("\u5206\u7ec4\u603b\u6570", str(stats["groups"]), "", TEXT),
            ("\u8282\u70b9\u603b\u6570", str(stats["nodes"]), "", TEXT),
            ("\u8ba2\u9605\u603b\u6570", str(stats["subscriptions"]), "", TEXT),
            ("\u89c4\u5219\u603b\u6570", str(stats["rules"]), "", TEXT),
        ]

        for index, (label, value, subtext, color) in enumerate(cards):
            grid.addWidget(self._stat_card(label, value, subtext, color), index // 4, index % 4)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.proxy_switch_label = QLabel("代理运行中" if is_proxy_running() else "代理已停止")
        self.proxy_switch_label.setObjectName("SectionTitle")
        self.proxy_switch = ProxySwitch()
        self.proxy_switch.set_running(is_proxy_running())
        self.proxy_switch.clicked.connect(self._toggle_proxy_from_switch)
        actions.addWidget(self.proxy_switch_label)
        actions.addWidget(self.proxy_switch)

        layout.addLayout(grid)
        layout.addLayout(actions)

        section_title = QLabel("\u6700\u8fd1\u6d3b\u52a8")
        section_title.setObjectName("SectionTitle")
        layout.addWidget(section_title)

        self.activity_log.setReadOnly(True)
        self.activity_log.setMinimumHeight(280)
        self.activity_log.setText(
            "\n".join(
                [
                    "[INFO] GUI \u5df2\u542f\u52a8",
                    f"[INFO] \u672c\u5730\u4ee3\u7406\u5730\u5740\uff1a{proxy_endpoint}",
                ]
            )
        )
        layout.addWidget(self.activity_log, 1)
        self.refresh_dashboard()
        return page

    def _append_activity_log(self, message: str, level: str = "INFO"):
        if self.activity_log:
            timestamp = datetime.now().strftime("%H:%M:%S")
            if message.startswith("["):
                self.activity_log.append(f"{timestamp}  {message}")
            else:
                self.activity_log.append(f"{timestamp}  [{level}] {message}")
            self._trim_activity_log()

    def _trim_activity_log(self):
        max_count = max(1, int(self._max_recent_activities or 200))

        document = self.activity_log.document()
        while document.blockCount() > max_count:
            cursor = QTextCursor(document.firstBlock())
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def notify_success(self, message: str):
        self._append_activity_log(message, "INFO")
        if self.isVisible():
            self._remember_notification(show_success_animation(self))

    def notify_info(self, message: str):
        self._append_activity_log(message, "INFO")

    def notify_error(self, message: str):
        self._append_activity_log(message, "ERROR")
        if self.isVisible():
            self._remember_notification(show_error_animation(self))

    def notify_warning(self, message: str):
        self._append_activity_log(message, "WARN")
        if self.isVisible():
            self._remember_notification(show_warning_animation(self))

    def _remember_notification(self, toast):
        self._active_notifications.append(toast)
        toast.destroyed.connect(lambda _obj=None, item=toast: self._forget_notification(item))

    def _forget_notification(self, toast):
        if toast in self._active_notifications:
            self._active_notifications.remove(toast)

    def refresh_dashboard(self):
        stats = get_dashboard_stats()
        state = get_proxy_state()
        if state == "starting":
            self._set_proxy_starting_status()
        elif state == "stopping":
            self._set_proxy_stopping_status()
        else:
            self._set_proxy_status(is_proxy_running())
        proxy_host, proxy_port = self._context_proxy_endpoint()
        self._set_stat_value("\u672c\u5730\u4ee3\u7406", f"{proxy_host}:{proxy_port}")
        self._set_stat_value("\u5206\u7ec4\u603b\u6570", str(stats["groups"]))
        self._set_stat_value("\u8282\u70b9\u603b\u6570", str(stats["nodes"]))
        self._set_stat_value("\u8ba2\u9605\u603b\u6570", str(stats["subscriptions"]))
        self._set_stat_value("\u89c4\u5219\u603b\u6570", str(stats["rules"]))
        self._refresh_active_connection_count()
        self._append_auto_selector_status_logs()

    def _refresh_active_connection_count(self):
        active_count = count_active_connections() if is_proxy_running() else 0
        self._set_stat_value("\u6d3b\u52a8\u8fde\u63a5", str(active_count))

    def _poll_proxy_runtime_state(self):
        if self.proxy_action_thread:
            return

        state = get_proxy_state()
        if state == "starting":
            self._set_proxy_starting_status()
            self._refresh_active_connection_count()
            return

        if state == "stopping":
            self._set_proxy_stopping_status()
            self._refresh_active_connection_count()
            return

        running = is_proxy_running()
        self._set_proxy_status(running)
        self._refresh_active_connection_count()

        if running:
            self._poll_core_route_events()
            self._residue_cleanup_done = False
            return

        if state not in {"stopped", "failed"}:
            return

        error = get_backend_error()
        if error and error != self._last_backend_error_seen:
            self._last_backend_error_seen = error
            self.notify_error(f"后端服务已停止：{error}")

        if not self._residue_cleanup_done:
            self._log_lifecycle_event(f"runtime poll observed backend state={state}; cleanup allowed", "WARN")
            self._disable_system_proxy_on_exit()
            cleanup_proxy_residue()
            self._residue_cleanup_done = True

    def _poll_core_route_events(self):
        payload = get_core_events(self._last_core_event_id, limit=80)
        boot_id = payload.get("boot_id")
        if boot_id and boot_id != self._last_core_boot_id:
            self.reset_core_event_cursor(boot_id)
            payload = get_core_events(None, limit=80)
            boot_id = payload.get("boot_id") or boot_id
            self._last_core_boot_id = boot_id

        events = payload.get("events", [])
        if not events:
            return

        now = time.monotonic()
        for event in events:
            try:
                event_id = int(event.get("id") or 0)
            except Exception:
                event_id = 0
            if event_id > self._last_core_event_id:
                self._last_core_event_id = event_id

            if event.get("action") != "route":
                continue

            message = self._format_core_route_event(event)
            if not message:
                continue

            key = (
                str(event.get("source") or ""),
                str(event.get("tab_host") or ""),
                str(event.get("process_name") or ""),
                str(event.get("request_host") or ""),
                str(event.get("final_group") or ""),
            )
            ttl = 20 if str(event.get("final_group") or "").lower() == "direct" else 8
            last_seen = self._routing_activity_seen.get(key, 0)
            if now - last_seen < ttl:
                continue
            self._routing_activity_seen[key] = now
            self._append_activity_log(message, "INFO")

        if len(self._routing_activity_seen) > 300:
            cutoff = now - 60
            self._routing_activity_seen = {
                key: ts for key, ts in self._routing_activity_seen.items() if ts >= cutoff
            }

    def reset_core_event_cursor(self, boot_id=None):
        self._last_core_event_id = 0
        self._last_core_boot_id = boot_id
        self._routing_activity_seen.clear()

    def _format_core_route_event(self, event: dict) -> str:
        source = str(event.get("source") or "").lower()
        reason = str(event.get("source_reason") or "")
        tab_host = str(event.get("tab_host") or "").strip()
        request_host = str(event.get("request_host") or "").strip()
        process_name = str(event.get("process_name") or "").strip()
        final_group = str(event.get("final_group") or "").strip()
        matched_pattern = str(event.get("matched_pattern") or "").strip()

        if not request_host or not final_group:
            return ""

        if source == "tab":
            suffix = f"（{matched_pattern}）" if matched_pattern else ""
            return f"Tab 分流：{tab_host or '-'} / {request_host} -> {final_group}{suffix}"
        if source == "process":
            suffix = f"（{matched_pattern}）" if matched_pattern else ""
            return f"App 分流：{process_name or '-'} / {request_host} -> {final_group}{suffix}"
        if source == "domain":
            suffix = f"（{matched_pattern}）" if matched_pattern else ""
            return f"域名分流：{request_host} -> {final_group}{suffix}"
        if final_group.lower() == "direct":
            if reason == "tab_report_no_rule" and tab_host:
                return f"直连：{tab_host} / {request_host} -> Direct"
            return f"直连：{request_host} -> Direct"
        return f"分流：{request_host} -> {final_group}"

    def _append_auto_selector_status_logs(self):
        selected_nodes = load_runtime_selected_nodes()
        for group_name, entry in selected_nodes.items():
            if isinstance(entry, dict) and entry.get("node"):
                node_name = entry["node"]
                if self._auto_selector_log_snapshot.get(group_name) != node_name:
                    self._append_activity_log(f"[INFO]  {group_name} \u5f53\u524d\u9009\u62e9\u8282\u70b9\uff1a{node_name}")
                    self._auto_selector_log_snapshot[group_name] = node_name

    def _set_stat_value(self, label: str, value: str):
        value_label = self.dashboard_stat_labels.get(label)
        if value_label:
            value_label.setText(value)

    def _stat_card(self, label: str, value: str, subtext: str, color: str):
        card = self._card()
        card.setMinimumHeight(98)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(16, 14, 16, 14)
        inner.setSpacing(6)

        label_widget = QLabel(label)
        label_widget.setObjectName("Muted")
        value_widget = QLabel(value)
        value_widget.setStyleSheet(f"font-size: 24px; font-weight: 800; color: {color};")
        sub_widget = QLabel(subtext)
        sub_widget.setObjectName("Muted")

        if label == "\u8fd0\u884c\u72b6\u6001":
            self.proxy_status_value_label = value_widget
            self.proxy_status_sub_label = sub_widget
        self.dashboard_stat_labels[label] = value_widget

        inner.addWidget(label_widget)
        inner.addWidget(value_widget)
        inner.addWidget(sub_widget)
        return card

    def _set_proxy_status(self, running: bool):
        if self.proxy_status_value_label:
            text = "\u8fd0\u884c\u4e2d" if running else "\u5df2\u505c\u6b62"
            color = SUCCESS if running else MUTED
            self.proxy_status_value_label.setText(text)
            self.proxy_status_value_label.setStyleSheet(
                f"font-size: 24px; font-weight: 800; color: {color};"
            )

        if self.proxy_status_sub_label:
            self.proxy_status_sub_label.setText("\u540e\u7aef\u670d\u52a1\u5185\u7f6e\u8fd0\u884c\u4e2d" if running else "\u540e\u7aef\u670d\u52a1\u5df2\u505c\u6b62")
        self._update_proxy_switch(running, enabled=True)
        self._update_group_edit_state()
        self._update_tray_status()

    def _set_proxy_starting_status(self):
        if self.proxy_status_value_label:
            self.proxy_status_value_label.setText("\u542f\u52a8\u4e2d")
            self.proxy_status_value_label.setStyleSheet(
                f"font-size: 24px; font-weight: 800; color: {PRIMARY};"
            )

        if self.proxy_status_sub_label:
            self.proxy_status_sub_label.setText("\u540e\u7aef\u670d\u52a1\u542f\u52a8\u4e2d")
        if self.proxy_switch:
            self.proxy_switch.setEnabled(False)

    def _set_proxy_stopping_status(self):
        if self.proxy_status_value_label:
            self.proxy_status_value_label.setText("\u505c\u6b62\u4e2d")
            self.proxy_status_value_label.setStyleSheet(
                f"font-size: 24px; font-weight: 800; color: {PRIMARY};"
            )

        if self.proxy_status_sub_label:
            self.proxy_status_sub_label.setText("\u540e\u7aef\u670d\u52a1\u505c\u6b62\u4e2d")
        if self.proxy_switch:
            self.proxy_switch.setEnabled(False)

    def _update_proxy_switch(self, running: bool | None = None, enabled: bool = True):
        running = is_proxy_running() if running is None else running
        if self.proxy_switch:
            self.proxy_switch.setEnabled(enabled)
            self.proxy_switch.set_running(running)
        if self.proxy_switch_label:
            self.proxy_switch_label.setText("代理运行中" if running else "代理已停止")

    def _toggle_proxy_from_switch(self):
        if self.busy:
            return
        if is_proxy_starting() or get_proxy_state() == "stopping":
            return

        if is_proxy_running():
            self._stop_proxy_from_gui()
        else:
            self._start_proxy_from_gui()

    def _start_proxy_from_gui(self):
        if self.proxy_action_thread or self.busy:
            return

        if self.proxy_switch:
            self.proxy_switch.setEnabled(False)
        if not self._validate_group_ports_before_start("\u542f\u52a8\u4ee3\u7406\u524d\u68c0\u67e5", show_message=True):
            self.refresh_dashboard()
            self._update_proxy_switch(False, enabled=True)
            return

        self.start_busy("正在启动代理...", "启动代理")
        self._set_proxy_starting_status()
        self._append_activity_log("\u4ee3\u7406\u542f\u52a8\u4e2d")
        self._run_proxy_action("start")

    def _stop_proxy_from_gui(self):
        if self.proxy_action_thread or self.busy:
            return

        self.start_busy("正在停止代理...", "停止代理")
        if self.proxy_switch:
            self.proxy_switch.setEnabled(False)
        self._append_activity_log("\u4ee3\u7406\u505c\u6b62\u4e2d")

        self._run_proxy_action("stop")

    def _run_proxy_action(self, action: str):
        self.proxy_action_thread = QThread(self)
        self.proxy_action_worker = ProxyActionWorker(action)
        self.proxy_action_worker.moveToThread(self.proxy_action_thread)
        self.proxy_action_thread.started.connect(self.proxy_action_worker.run)
        self.proxy_action_worker.finished.connect(self._on_proxy_action_finished)
        self.proxy_action_worker.finished.connect(self.proxy_action_thread.quit)
        self.proxy_action_worker.finished.connect(self.proxy_action_worker.deleteLater)
        self.proxy_action_thread.finished.connect(self.proxy_action_thread.deleteLater)
        self.proxy_action_thread.finished.connect(self._cleanup_proxy_action_thread)
        self.proxy_action_thread.start()

    def _on_proxy_action_finished(self, action: str, ok: bool, error: str):
        if action == "start":
            if not ok:
                self._log_lifecycle_event("cleanup_proxy_residue called, reason=start_proxy_failed", "WARN")
                cleanup_proxy_residue()
                self._disable_system_proxy_on_exit()
                self.refresh_dashboard()
                self._update_tray_status()
                self._update_proxy_switch(False, enabled=True)
                self.finish_busy(False, error or "代理启动失败")
                return

            self._residue_cleanup_done = False
            self.reset_core_event_cursor()
            self._set_proxy_status(True)
            self.finish_busy(True, "代理已启动")
            self._append_activity_log("后端服务：内置运行中")
            QTimer.singleShot(1500, self._append_mihomo_process_count)
            return

        if not ok:
            self.refresh_dashboard()
            self._update_tray_status()
            self._update_proxy_switch(True, enabled=True)
            self.finish_busy(False, error or "代理停止失败")
            return

        self._set_proxy_status(False)
        self.reset_core_event_cursor()
        self.finish_busy(True, "代理已停止")

    def _cleanup_proxy_action_thread(self):
        self.proxy_action_thread = None
        self.proxy_action_worker = None

    def _context_proxy_endpoint(self) -> tuple[str, int]:
        proxy_settings = load_app_settings().get("proxy", {})
        host = str(proxy_settings.get("listen_host") or "127.0.0.1")
        try:
            port = int(proxy_settings.get("listen_port") or 18000)
        except (TypeError, ValueError):
            port = 18000
        return host, port

    def _reserved_system_ports(self) -> set[int]:
        reserved_ports = {18000, 17890}
        settings = load_app_settings()
        proxy_settings = settings.get("proxy", {})
        mihomo_settings = settings.get("mihomo", {})

        for key in ("listen_port", "receiver_port"):
            try:
                reserved_ports.add(int(proxy_settings.get(key)))
            except (TypeError, ValueError):
                continue

        for key in ("controller_port", "mixed_port"):
            try:
                reserved_ports.add(int(mihomo_settings.get(key)))
            except (TypeError, ValueError):
                continue

        return reserved_ports

    def _enable_system_proxy_if_configured(self) -> bool:
        if not load_app_settings().get("ui", {}).get("enable_system_proxy_on_start", True):
            return True

        host, port = self._context_proxy_endpoint()
        self._log_lifecycle_event(f"enable_system_proxy called, reason=start_proxy, endpoint={host}:{port}")
        ok, message = enable_system_proxy(host, port)
        if not ok:
            self.notify_error(f"启用系统代理失败：{message}")
            return False

        self._append_activity_log("\u7cfb\u7edf\u4ee3\u7406\u5df2\u542f\u7528")
        return True

    def _disable_system_proxy_if_configured(self) -> bool:
        if not load_app_settings().get("ui", {}).get("disable_system_proxy_on_stop", True):
            return True

        host, port = self._context_proxy_endpoint()
        self._log_lifecycle_event(f"disable_system_proxy called, reason=stop_proxy, endpoint={host}:{port}")
        if is_contextproxy_system_proxy("127.0.0.1", 18000):
            ok, message = disable_system_proxy_if_contextproxy("127.0.0.1", 18000)
            if not ok:
                self.notify_error(f"关闭系统代理失败：{message}")
                return False

            if message:
                self._append_activity_log(message)
            return True

        ok, message = disable_system_proxy_if_contextproxy(host, port)
        if not ok:
            self.notify_error(f"关闭系统代理失败：{message}")
            return False

        if message:
            self._append_activity_log(message)
        return True

    def _cleanup_stale_system_proxy_on_start(self):
        host, port = self._context_proxy_endpoint()
        if is_proxy_running():
            return

        if is_contextproxy_system_proxy("127.0.0.1", 18000):
            self._log_lifecycle_event("disable_system_proxy called, reason=startup_stale_proxy_cleanup, endpoint=127.0.0.1:18000")
            ok, _message = disable_system_proxy_if_contextproxy("127.0.0.1", 18000)
            if ok:
                self._append_activity_log("[INFO] \u5df2\u6e05\u7406\u4e0a\u6b21\u6b8b\u7559\u7684\u7cfb\u7edf\u4ee3\u7406")
            return

        if not is_contextproxy_system_proxy(host, port):
            return

        self._log_lifecycle_event(f"disable_system_proxy called, reason=startup_stale_proxy_cleanup, endpoint={host}:{port}")
        ok, _message = disable_system_proxy_if_contextproxy(host, port)
        if ok:
            self._append_activity_log("[INFO] \u5df2\u6e05\u7406\u4e0a\u6b21\u6b8b\u7559\u7684\u7cfb\u7edf\u4ee3\u7406")

    def _disable_system_proxy_on_exit(self):
        if not load_app_settings().get("ui", {}).get("disable_system_proxy_on_stop", True):
            return

        if is_contextproxy_system_proxy("127.0.0.1", 18000):
            self._log_lifecycle_event("disable_system_proxy called, reason=exit_or_backend_stopped, endpoint=127.0.0.1:18000")
            disable_system_proxy_if_contextproxy("127.0.0.1", 18000)
            return

        host, port = self._context_proxy_endpoint()
        self._log_lifecycle_event(f"disable_system_proxy called, reason=exit_or_backend_stopped, endpoint={host}:{port}")
        disable_system_proxy_if_contextproxy(host, port)

    def _append_mihomo_process_count(self):
        if is_proxy_running():
            self._append_activity_log(f"mihomo \u5b9e\u4f8b\u6570\uff1a{count_mihomo_processes()}")
        self.refresh_dashboard()


    def _make_tray_icon(self, running: bool) -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        base_color = QColor(PRIMARY if running else MUTED)
        status_color = QColor(SUCCESS if running else DANGER)

        painter.setPen(Qt.NoPen)
        painter.setBrush(base_color)
        painter.drawRoundedRect(6, 6, 52, 52, 12, 12)

        painter.setPen(QPen(QColor("white"), 2))
        font = QFont("Microsoft YaHei UI", 15)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "CP")

        painter.setPen(QPen(QColor("white"), 3))
        painter.setBrush(status_color)
        painter.drawEllipse(42, 42, 16, 16)
        painter.end()

        return QIcon(pixmap)

    def _setup_tray_icon(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._append_activity_log("[WARN] 系统托盘不可用，关闭窗口时将只能最小化")
            return

        self.tray_icon = QSystemTrayIcon(self._make_tray_icon(is_proxy_running()), self)
        self.tray_icon.setToolTip("ContextProxy")

        self.tray_menu = QMenu(self)
        self.tray_show_action = QAction("显示主窗口", self)
        self.tray_start_action = QAction("启动代理", self)
        self.tray_stop_action = QAction("停止代理", self)
        self.tray_quit_action = QAction("退出", self)

        self.tray_show_action.triggered.connect(self._show_main_window_from_tray)
        self.tray_start_action.triggered.connect(self._start_proxy_from_tray)
        self.tray_stop_action.triggered.connect(self._stop_proxy_from_tray)
        self.tray_quit_action.triggered.connect(self._quit_from_tray)

        self.tray_menu.addAction(self.tray_show_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.tray_start_action)
        self.tray_menu.addAction(self.tray_stop_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.tray_quit_action)

        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._on_tray_icon_activated)
        self.tray_icon.show()
        self._update_tray_status()

    def _update_tray_status(self):
        if not self.tray_icon:
            return

        running = is_proxy_running()
        self.tray_icon.setIcon(self._make_tray_icon(running))
        self.tray_icon.setToolTip("ContextProxy - " + ("运行中" if running else "已停止"))

        if self.tray_start_action:
            self.tray_start_action.setEnabled(not running)
        if self.tray_stop_action:
            self.tray_stop_action.setEnabled(running)

    def _on_tray_icon_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_main_window_from_tray()

    def _show_main_window_from_tray(self):
        self._log_lifecycle_event("tray show window")
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()
        self.refresh_dashboard()

    def _start_proxy_from_tray(self):
        self._log_lifecycle_event("tray start proxy requested")
        self._start_proxy_from_gui()
        self._update_tray_status()

    def _stop_proxy_from_tray(self):
        self._log_lifecycle_event("tray stop proxy requested")
        self._stop_proxy_from_gui()
        self._update_tray_status()

    def _quit_from_tray(self):
        if self.busy:
            self.notify_info("任务处理中，请稍候")
            return

        self._log_lifecycle_event("tray exit requested")
        self._force_real_close = True

        if is_proxy_running():
            result = QMessageBox.question(
                self,
                "ContextProxy",
                "代理正在运行，是否停止代理并退出？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if result == QMessageBox.Cancel:
                self._force_real_close = False
                return
            if result == QMessageBox.Yes:
                self._disable_system_proxy_on_exit()
                self._log_lifecycle_event("stop_proxy_process called, reason=tray_exit")
                ok, error = stop_proxy_process()
                if not ok:
                    self.notify_error(error or "代理停止失败")
                    self._force_real_close = False
                    return
        else:
            self._disable_system_proxy_on_exit()

        if self.tray_icon:
            self.tray_icon.hide()

        app = QApplication.instance()
        if app:
            app.quit()
        else:
            self.close()

    def _subscription_page(self):
        page, layout = self._page_shell(
            "\u8ba2\u9605\u7ba1\u7406",
            "\u6dfb\u52a0\u3001\u66f4\u65b0\u548c\u5220\u9664\u8ba2\u9605\uff0c\u6210\u529f\u540e\u81ea\u52a8\u540c\u6b65\u8282\u70b9\u6c60\u548c\u914d\u7f6e",
        )

        editor = self._card()
        editor_layout = QGridLayout(editor)
        editor_layout.setContentsMargins(16, 16, 16, 16)
        editor_layout.setHorizontalSpacing(12)
        editor_layout.setVerticalSpacing(10)

        self.subscription_name_input = QLineEdit()
        self.subscription_name_input.setPlaceholderText("\u8ba2\u9605\u540d\u79f0")
        self.subscription_url_input = QLineEdit()
        self.subscription_url_input.setPlaceholderText("\u8ba2\u9605\u94fe\u63a5")

        update_button = QPushButton("\u6dfb\u52a0 / \u66f4\u65b0\u8ba2\u9605")
        delete_button = QPushButton("\u5220\u9664\u8ba2\u9605")
        refresh_button = QPushButton("\u5237\u65b0\u5217\u8868")
        update_button.setObjectName("PrimaryButton")
        delete_button.setObjectName("DangerButton")
        update_button.clicked.connect(self._update_subscription_from_inputs)
        delete_button.clicked.connect(self._delete_selected_subscription)
        refresh_button.clicked.connect(self._refresh_subscription_table)

        editor_layout.addWidget(QLabel("\u8ba2\u9605\u540d\u79f0"), 0, 0)
        editor_layout.addWidget(self.subscription_name_input, 0, 1)
        editor_layout.addWidget(QLabel("\u8ba2\u9605\u94fe\u63a5"), 1, 0)
        editor_layout.addWidget(self.subscription_url_input, 1, 1, 1, 3)
        editor_layout.addWidget(update_button, 0, 2)
        editor_layout.addWidget(delete_button, 0, 3)
        editor_layout.addWidget(refresh_button, 0, 4)
        layout.addWidget(editor)

        self.subscription_table = QTableWidget()
        self._setup_table(self.subscription_table)
        headers = [
            "\u8ba2\u9605\u540d\u79f0",
            "\u8282\u70b9\u6570\u91cf",
            "YAML \u6587\u4ef6",
            "JSON \u6587\u4ef6",
            "\u66f4\u65b0\u65f6\u95f4",
            "\u72b6\u6001",
        ]
        self.subscription_table.setColumnCount(len(headers))
        self.subscription_table.setHorizontalHeaderLabels(headers)
        self.subscription_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.subscription_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.subscription_table.itemSelectionChanged.connect(self._sync_selected_subscription_to_editor)

        layout.addWidget(self.subscription_table, 1)
        self._refresh_subscription_table()
        return page

    def _refresh_subscription_table(self):
        if not self.subscription_table:
            return

        rows = list_subscriptions()
        self.subscription_urls = {row["name"]: row.get("url", "") for row in rows}
        self.subscription_table.setRowCount(len(rows))

        for row_index, row in enumerate(rows):
            values = [
                row["name"],
                str(row["node_count"]),
                row["yaml_file"],
                row["json_file"],
                row["updated_at"],
                row["status"],
            ]

            self.subscription_table.setRowHeight(row_index, 34)
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column_index in {1, 5}:
                    item.setTextAlignment(Qt.AlignCenter)
                if column_index == 5:
                    item.setForeground(QColor(SUCCESS))
                self.subscription_table.setItem(row_index, column_index, item)

    def _sync_selected_subscription_to_editor(self):
        if not self.subscription_table or not self.subscription_name_input:
            return

        row = self.subscription_table.currentRow()
        if row < 0:
            return

        name_item = self.subscription_table.item(row, 0)
        if name_item:
            name = name_item.text()
            self.subscription_name_input.setText(name)
            if self.subscription_url_input:
                self.subscription_url_input.setText(self.subscription_urls.get(name) or get_subscription_url(name))

    def _selected_subscription_name(self) -> str:
        if not self.subscription_table:
            return ""

        row = self.subscription_table.currentRow()
        if row < 0:
            return self.subscription_name_input.text().strip() if self.subscription_name_input else ""

        item = self.subscription_table.item(row, 0)
        return item.text().strip() if item else ""

    def _update_subscription_from_inputs(self):
        if self.busy:
            return
        name = self.subscription_name_input.text().strip() if self.subscription_name_input else ""
        url = self.subscription_url_input.text().strip() if self.subscription_url_input else ""

        def task():
            ok, error = update_subscription_from_gui(name, url)
            if not ok:
                raise RuntimeError(error or "未知错误")
            return name

        def on_success(result_name):
            self._refresh_subscription_table()
            self._refresh_node_pool_table()
            self.refresh_group_management_page()
            self.refresh_dashboard()
            self.notify_success(f"订阅更新成功：{result_name}")

        self.run_gui_task(
            task,
            on_success=on_success,
            error_message="订阅更新失败",
            loading_message="正在更新订阅...",
            task_name="更新订阅",
        )

    def _delete_selected_subscription(self):
        if self.busy:
            return
        name = self._selected_subscription_name()

        def task():
            ok, error, messages = delete_subscription_from_gui(name)
            if not ok:
                raise RuntimeError(error or "未知错误")
            return name, messages

        def on_success(result):
            result_name, messages = result
            self._refresh_subscription_table()
            self._refresh_node_pool_table()
            self.refresh_group_management_page()
            self.refresh_dashboard()
            self.notify_success(f"订阅已删除，节点池和配置已同步：{result_name}")
            for message in messages:
                self._append_activity_log(message)

        self.run_gui_task(
            task,
            on_success=on_success,
            error_message="删除订阅失败",
            loading_message="正在删除订阅...",
            task_name="删除订阅",
        )

    def _node_pool_page(self):
        page, layout = self._page_shell(
            "\u8282\u70b9\u6c60",
            "\u67e5\u770b\u6240\u6709\u8ba2\u9605\u8282\u70b9\uff0c\u5e76\u8fdb\u884c\u5ef6\u8fdf\u6d4b\u8bd5",
        )

        top = QHBoxLayout()
        tabs = QTabBar()
        tabs.setExpanding(False)
        tabs.addTab("\u5168\u90e8")
        top.addWidget(tabs, 1)

        test_button = QPushButton("\u6d4b\u8bd5\u5ef6\u8fdf")
        test_button.setObjectName("PrimaryButton")
        test_button.clicked.connect(self._start_node_delay_test)
        self.node_delay_test_button = test_button
        top.addWidget(test_button)
        layout.addLayout(top)

        table = QTableWidget()
        self.node_pool_table = table
        self._setup_table(table)
        headers = [
            "\u5e8f\u53f7",
            "\u72b6\u6001",
            "\u7c7b\u578b",
            "\u8282\u70b9\u540d\u79f0",
            "\u5730\u5740",
            "\u7aef\u53e3",
            "TLS",
            "\u8ba2\u9605\u6765\u6e90",
            "\u5ef6\u8fdf(ms)",
        ]
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)

        rows = self._load_node_pool_rows()
        self._fill_table(table, rows)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(table, 1)
        return page

    def _refresh_node_pool_table(self):
        if not self.node_pool_table:
            return

        rows = self._load_node_pool_rows()
        self._fill_table(self.node_pool_table, rows)
        self.node_pool_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

    def _load_node_pool_rows(self) -> list[tuple[str, ...]]:
        path = self._node_pool_path()

        if not path.is_file():
            self._append_activity_log(
                f"[WARN]  \u672a\u627e\u5230\u8282\u70b9\u6c60\u6587\u4ef6\uff1a{path}"
            )
            return []

        try:
            import yaml
        except ImportError:
            self._append_activity_log("[WARN]  \u672a\u5b89\u88c5 PyYAML\uff0c\u65e0\u6cd5\u8bfb\u53d6 node_pool.yaml")
            return []

        try:
            with open(path, "r", encoding="utf-8") as file:
                data = yaml.safe_load(file) or {}
        except Exception as exc:
            self._append_activity_log(f"[WARN]  \u8bfb\u53d6\u8282\u70b9\u6c60\u5931\u8d25\uff1a{exc}")
            return []

        nodes = data.get("nodes", {})
        if not isinstance(nodes, dict) or not nodes:
            if self._node_pool_last_loaded_count != 0:
                self._append_activity_log("[WARN]  node_pool.yaml \u4e3a\u7a7a\uff0c\u8282\u70b9\u6c60\u8868\u683c\u672a\u586b\u5145")
                self._node_pool_last_loaded_count = 0
            return []

        rows = []
        for index, (fallback_name, node) in enumerate(nodes.items(), start=1):
            if not isinstance(node, dict):
                continue
            rows.append(self._node_to_table_row(index, fallback_name, node))

        if rows and self._node_pool_last_loaded_count != len(rows):
            self._append_activity_log(f"[INFO]  \u5df2\u52a0\u8f7d\u8282\u70b9\u6c60\uff1a{len(rows)} \u4e2a\u8282\u70b9")
            self._node_pool_last_loaded_count = len(rows)
        elif not rows:
            if self._node_pool_last_loaded_count != 0:
                self._append_activity_log("[WARN]  node_pool.yaml \u672a\u5305\u542b\u53ef\u5c55\u793a\u7684\u8282\u70b9")
                self._node_pool_last_loaded_count = 0

        return rows

    def _node_to_table_row(self, index: int, fallback_name: str, node: dict) -> tuple[str, ...]:
        name = str(node.get("name") or fallback_name)
        node_type = str(node.get("type") or "")
        server = str(node.get("server") or "")
        port = str(node.get("port") or "")
        tls = "\u662f" if bool(node.get("tls")) else "\u5426"
        source = self._subscription_source_from_name(name)
        latency_text = self._latency_text_for_node(name)
        status_text = self._latency_status_for_node(name)

        return (
            str(index),
            status_text,
            node_type,
            name,
            server,
            port,
            tls,
            source,
            latency_text,
        )

    def _latency_text_for_node(self, node_name: str) -> str:
        result = self.node_latency_cache.get(node_name)
        if not isinstance(result, dict):
            return "-"

        if result.get("status") == "ok" and result.get("delay") is not None:
            return str(result.get("delay"))

        if result.get("status") == "failed":
            return "\u5931\u8d25"

        return "-"

    def _latency_status_for_node(self, node_name: str) -> str:
        result = self.node_latency_cache.get(node_name)
        if not isinstance(result, dict):
            return "\u672a\u6d4b"

        if result.get("status") == "ok":
            return "\u53ef\u7528"

        if result.get("status") == "failed":
            return "\u5931\u8d25"

        return "\u672a\u6d4b"

    def _subscription_source_from_name(self, node_name: str) -> str:
        separator = " | "
        if separator not in node_name:
            return "\u672a\u77e5"
        source = node_name.split(separator, 1)[0].strip()
        return source or "\u672a\u77e5"

    def _group_page(self):
        page, layout = self._page_shell(
            "\u5206\u7ec4\u7ba1\u7406",
            "\u7ba1\u7406\u5206\u7ec4\u8282\u70b9\uff0c\u4fdd\u5b58\u540e\u81ea\u52a8\u5e94\u7528\u914d\u7f6e",
        )

        body = QHBoxLayout()
        body.setSpacing(16)
        layout.addLayout(body, 1)

        left_card = self._card()
        left_card.setFixedWidth(230)
        left = QVBoxLayout(left_card)
        left.setContentsMargins(16, 16, 16, 16)
        left.setSpacing(12)

        title = QLabel("\u5206\u7ec4\u5217\u8868")
        title.setObjectName("SectionTitle")
        left.addWidget(title)

        self._load_group_management_data()

        self.group_list = QListWidget()
        self.group_list.setObjectName("GroupList")
        self.group_list.currentRowChanged.connect(self._on_group_selection_changed)
        self._populate_group_list()
        left.addWidget(self.group_list, 1)

        self.group_new_button = QPushButton("\u65b0\u5efa\u5206\u7ec4")
        self.group_delete_button = QPushButton("\u5220\u9664\u5206\u7ec4")
        self.group_save_button = QPushButton("\u4fdd\u5b58\u5e76\u5e94\u7528")
        self.group_delete_button.setObjectName("DangerButton")
        self.group_save_button.setObjectName("PrimaryButton")
        self.group_new_button.clicked.connect(self._create_group_from_dialog)
        self.group_delete_button.clicked.connect(self._delete_current_group)
        self.group_save_button.clicked.connect(self._save_current_group_nodes)
        for button in (self.group_new_button, self.group_delete_button, self.group_save_button):
            left.addWidget(button)

        body.addWidget(left_card)
        body.addWidget(self._group_editor(), 1)
        self._select_initial_group()
        self._update_group_edit_state()
        return page

    def _group_editor(self):
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        title = QLabel("\u7f16\u8f91\u5206\u7ec4")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        self.group_readonly_hint = QLabel("代理运行中，分组配置暂不可修改。请先停止代理后再编辑分组。")
        self.group_readonly_hint.setObjectName("Muted")
        self.group_readonly_hint.setVisible(False)
        layout.addWidget(self.group_readonly_hint)

        form = QGridLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)

        self.group_name_input = QLineEdit("")
        self.group_name_input.setReadOnly(True)
        self.group_port_input = QLineEdit("")
        self.group_port_input.setPlaceholderText("7891")
        self.group_controller_input = QLineEdit("")
        self.group_controller_input.setPlaceholderText("9090")
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems(["\u81ea\u52a8\u9009\u62e9"])
        self.current_node_input = QLineEdit("\u6682\u65e0\u9009\u62e9")
        self.current_node_input.setReadOnly(True)
        self.group_reselect_button = QPushButton("\u91cd\u65b0\u81ea\u52a8\u9009\u62e9")
        self.group_reselect_button.clicked.connect(self._reselect_current_group)

        form.addWidget(QLabel("\u5206\u7ec4\u540d\u79f0"), 0, 0)
        form.addWidget(QLabel("\u672c\u5730\u4ee3\u7406\u7aef\u53e3"), 0, 1)
        form.addWidget(QLabel("external-controller"), 0, 2)
        form.addWidget(self.group_name_input, 1, 0)
        form.addWidget(self.group_port_input, 1, 1)
        form.addWidget(self.group_controller_input, 1, 2)
        form.addWidget(QLabel("\u9009\u62e9\u7b56\u7565"), 2, 0, 1, 3)
        form.addWidget(self.strategy_combo, 3, 0, 1, 3)
        form.addWidget(QLabel("\u5f53\u524d\u9009\u62e9\u8282\u70b9"), 4, 0, 1, 3)
        form.addWidget(self.current_node_input, 5, 0, 1, 2)
        form.addWidget(self.group_reselect_button, 5, 2)
        layout.addLayout(form)

        self.group_node_table = QTableWidget()
        self._setup_table(self.group_node_table)
        headers = [
            "\u52fe\u9009",
            "\u7c7b\u578b",
            "\u8282\u70b9\u540d\u79f0",
            "\u5730\u5740",
            "\u5ef6\u8fdf(ms)",
            "\u8ba2\u9605\u6765\u6e90",
        ]
        self.group_node_table.setColumnCount(len(headers))
        self.group_node_table.setHorizontalHeaderLabels(headers)
        self.group_node_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.group_node_table, 1)
        return card

    def _load_group_management_data(self):
        self.group_nodes_config, group_error = load_group_nodes_config()
        if group_error:
            self._append_activity_log(f"[WARN]  \u8bfb\u53d6 group_nodes.yaml \u5931\u8d25\uff1a{group_error}")

        self.node_pool_nodes, node_error = load_node_pool()
        if node_error:
            self._append_activity_log(f"[WARN]  \u8bfb\u53d6 node_pool.yaml \u5931\u8d25\uff1a{node_error}")

    def refresh_group_management_page(self):
        if not self.group_list or not self.group_node_table:
            return

        previous_group = self.current_group_name
        self._load_group_management_data()
        self._populate_group_list()
        self._select_group_after_refresh(previous_group)
        self._update_group_edit_state()

    def _refresh_after_group_structure_changed(self, preferred_group: str | None):
        self._load_group_management_data()
        self._populate_group_list()
        self._select_group_after_refresh(preferred_group)
        self.refresh_rule_group_options()
        self.refresh_dashboard()

    def _group_map(self) -> dict:
        groups = self.group_nodes_config.get("groups", {})
        return groups if isinstance(groups, dict) else {}

    def _update_group_edit_state(self):
        readonly = is_proxy_running()
        for button in (
            self.group_new_button,
            self.group_delete_button,
            self.group_save_button,
        ):
            if button:
                button.setEnabled(not readonly)

        # “重新自动选择”只是对正在运行的 mihomo 切换当前分组节点，
        # 不会修改 group_nodes.yaml / 端口 / 分组配置，所以代理运行中应保持可用。
        if self.group_reselect_button:
            self.group_reselect_button.setEnabled(bool(self.current_group_name) and readonly)

        if self.group_readonly_hint:
            if readonly:
                self.group_readonly_hint.setText(
                    "代理运行中，分组配置暂不可修改；可以使用“重新自动选择”切换当前分组节点。"
                )
            self.group_readonly_hint.setVisible(readonly)

        if self.group_name_input:
            self.group_name_input.setReadOnly(True)
        if self.group_port_input:
            self.group_port_input.setReadOnly(readonly)
        if self.group_controller_input:
            self.group_controller_input.setReadOnly(readonly)
        if self.strategy_combo:
            self.strategy_combo.setEnabled(not readonly)

        if self.group_node_table:
            for row_index in range(self.group_node_table.rowCount()):
                checkbox = self.group_node_table.cellWidget(row_index, 0)
                if isinstance(checkbox, QCheckBox):
                    checkbox.setEnabled(not readonly)

    def _used_group_ports(self, excluded_group: str | None = None) -> set[int]:
        used_ports = self._reserved_system_ports()

        for group_name, group_data in self._group_map().items():
            if excluded_group and group_name == excluded_group:
                continue

            if not isinstance(group_data, dict):
                continue

            for key in ("port", "controller"):
                value = group_data.get(key)
                try:
                    if value not in (None, ""):
                        used_ports.add(int(value))
                except (TypeError, ValueError):
                    continue

        return used_ports

    def _is_port_available(self, port: int) -> bool:
        for host in ("127.0.0.1", "0.0.0.0"):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind((host, port))
            except OSError:
                return False
            finally:
                sock.close()

        return True

    def _allocate_available_port(self, start_port: int, used_ports: set[int], check_system: bool = True) -> int:
        port = start_port
        while port <= 65535:
            if port in used_ports:
                port += 1
                continue
            if check_system and not self._is_port_available(port):
                port += 1
                continue
            break

        if port > 65535:
            raise ValueError("\u6ca1\u6709\u53ef\u7528\u7aef\u53e3")

        used_ports.add(port)
        return port

    def _iter_config_port_entries(self):
        for group_name, group_data in self._group_map().items():
            if not isinstance(group_data, dict):
                continue
            for key in ("port", "controller"):
                yield str(group_name), key, group_data.get(key)

    def _validate_group_ports_config_only(self, current_group: str | None = None) -> tuple[bool, str | None]:
        settings = load_app_settings()
        proxy_settings = settings.get("proxy", {})
        mihomo_settings = settings.get("mihomo", {})
        listener_reserved_ports = {18000, 17890}
        controller_reserved_ports = {18000, 17890}

        for key in ("listen_port", "receiver_port"):
            try:
                port = int(proxy_settings.get(key))
            except (TypeError, ValueError):
                continue
            listener_reserved_ports.add(port)
            controller_reserved_ports.add(port)

        for key in ("controller_port", "mixed_port"):
            try:
                listener_reserved_ports.add(int(mihomo_settings.get(key)))
            except (TypeError, ValueError):
                continue

        listener_reserved_text = " / ".join(str(port) for port in sorted(listener_reserved_ports))
        controller_reserved_text = " / ".join(str(port) for port in sorted(controller_reserved_ports))
        seen = {}

        for group_name, key, raw_value in self._iter_config_port_entries():
            try:
                port = int(raw_value)
            except (TypeError, ValueError):
                return False, f"{group_name} {key} 必须是数字"

            if port < 1 or port > 65535:
                return False, f"{group_name} {key} 范围必须是 1-65535"

            if key == "port" and port in listener_reserved_ports:
                return False, f"{group_name} listener 端口不能使用保留端口 {listener_reserved_text}"

            if key == "controller" and port in controller_reserved_ports:
                return False, f"{group_name} external-controller 不能使用保留端口 {controller_reserved_text}"

            owner = seen.get(port)
            if owner:
                return False, f"{group_name} {key} 与 {owner[0]} {owner[1]} 端口重复"

            seen[port] = (group_name, key)

        for group_name, group_data in self._group_map().items():
            if not isinstance(group_data, dict):
                continue
            try:
                port = int(group_data.get("port"))
                controller = int(group_data.get("controller"))
            except (TypeError, ValueError):
                continue
            if port == controller:
                return False, f"{group_name} 本地代理端口和 external-controller 不能相同"

        _ = current_group
        return True, None

    def _validate_ports_before_proxy_start(self) -> tuple[bool, str | None]:
        if is_proxy_running():
            self._append_activity_log("[INFO]  代理已运行，跳过启动前系统端口占用检查")
            return True, None

        ok, error, changes = prepare_mihomo_runtime_ports(write_settings=True, check_system=True)
        if not ok:
            return False, error

        for change in changes:
            self._append_activity_log(f"[INFO]  启动代理前检查：{change}")

        return True, None

    def _validate_group_ports_before_start(self, reason: str, show_message: bool = False) -> bool:
        self._load_group_management_data()
        ok, error = self._validate_ports_before_proxy_start()
        if not ok:
            message = error or "端口检查失败"
            if show_message:
                self.notify_error(message)
            else:
                self._append_activity_log(f"[WARN]  {reason}：{message}")
            return False

        self._append_activity_log(f"[INFO]  {reason}：分组端口检查通过")
        return True

    def _prepare_mihomo_ports_before_config_generation(self) -> bool:
        if is_proxy_running():
            return True

        ok, error, changes = prepare_mihomo_runtime_ports(write_settings=True, check_system=True)
        if not ok:
            self.notify_error(error or "mihomo 端口准备失败")
            return False

        for change in changes:
            self._append_activity_log(f"[INFO]  生成配置前检查：{change}")

        return True

    def _check_group_ports_on_app_start(self):
        self._load_group_management_data()
        ok, error = self._validate_group_ports_config_only()
        if ok:
            self._append_activity_log("[INFO]  启动软件时检查：分组端口配置检查通过")
        else:
            self._append_activity_log(f"[WARN]  启动软件时检查：{error}")

    def _is_valid_group_name(self, group_name: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9_\-\u4e00-\u9fff]+", group_name))

    def _populate_group_list(self):
        if not self.group_list:
            return

        self.group_list.blockSignals(True)
        self.group_list.clear()
        for group_name in self._group_map().keys():
            group_name = str(group_name)
            item = QListWidgetItem(f"{group_name}")
            item.setData(Qt.UserRole, group_name)
            item.setForeground(QColor(PRIMARY))
            self.group_list.addItem(item)
        self.group_list.blockSignals(False)

    def _select_initial_group(self):
        if self.group_list and self.group_list.count() > 0:
            self.group_list.setCurrentRow(0)
        else:
            self._show_group("")

    def _select_group_after_refresh(self, preferred_group: str | None):
        if not self.group_list or self.group_list.count() == 0:
            self._show_group("")
            return

        target_row = -1
        if preferred_group:
            for row in range(self.group_list.count()):
                item = self.group_list.item(row)
                if item and item.data(Qt.UserRole) == preferred_group:
                    target_row = row
                    break

        if target_row < 0:
            target_row = 0

        self.group_list.setCurrentRow(target_row)
        item = self.group_list.item(target_row)
        self._show_group(item.data(Qt.UserRole) if item else "")

    def _on_group_selection_changed(self, row: int):
        if not self.group_list or row < 0:
            self._show_group("")
            return

        item = self.group_list.item(row)
        self._show_group(item.data(Qt.UserRole) if item else "")

    def _show_group(self, group_name: str):
        self.current_group_name = group_name or None

        if self.group_name_input:
            self.group_name_input.setText(group_name)

        if self.strategy_combo:
            self.strategy_combo.setCurrentText("\u81ea\u52a8\u9009\u62e9")

        if not group_name:
            if self.group_port_input:
                self.group_port_input.clear()
            if self.group_controller_input:
                self.group_controller_input.clear()
            if self.current_node_input:
                self.current_node_input.setText("\u6682\u65e0\u9009\u62e9")
            if self.group_node_table:
                self.group_node_table.setRowCount(0)
            return

        group_data = self._current_group_data(group_name)
        nodes = self._current_group_nodes(group_name)

        if self.group_port_input:
            self.group_port_input.setText(str(group_data.get("port") or ""))

        if self.group_controller_input:
            self.group_controller_input.setText(str(group_data.get("controller") or ""))

        if self.current_node_input:
            self.current_node_input.setText(self._selected_node_display(group_name, nodes))

        self._populate_group_node_table(set(nodes))

    def _selected_node_display(self, group_name: str, group_nodes: list[str]) -> str:
        selected_node = get_selected_node_for_group(group_name)
        if not selected_node:
            return "\u6682\u65e0\u9009\u62e9"

        if selected_node not in set(group_nodes):
            return "\u8282\u70b9\u5df2\u5931\u6548"

        return selected_node

    def refresh_selected_node_display(self, group_name: str | None = None):
        target_group = group_name or self.current_group_name
        if not target_group or not self.current_node_input:
            return

        self.current_node_input.setText(
            self._selected_node_display(target_group, self._current_group_nodes(target_group))
        )

    def _current_group_data(self, group_name: str) -> dict:
        group_data = self._group_map().get(group_name, {})
        return group_data if isinstance(group_data, dict) else {}

    def _current_group_nodes(self, group_name: str) -> list[str]:
        group_data = self._current_group_data(group_name)
        nodes = group_data.get("nodes", [])
        if not isinstance(nodes, list):
            return []

        return [str(node_name) for node_name in nodes]

    def _populate_group_node_table(self, selected_nodes: set[str]):
        if not self.group_node_table:
            return

        table = self.group_node_table
        table.setRowCount(len(self.node_pool_nodes))

        for row_index, (fallback_name, node) in enumerate(self.node_pool_nodes.items()):
            node = node if isinstance(node, dict) else {}
            node_name = str(node.get("name") or fallback_name)
            checkbox = QCheckBox()
            checkbox.setChecked(node_name in selected_nodes)
            checkbox.setEnabled(not is_proxy_running())
            table.setCellWidget(row_index, 0, checkbox)
            table.setRowHeight(row_index, 34)

            row = (
                str(node.get("type") or ""),
                node_name,
                str(node.get("server") or ""),
                self._latency_text_for_node(node_name),
                extract_subscription_source(node_name),
            )

            for column_index, value in enumerate(row, start=1):
                item = QTableWidgetItem(value)
                if column_index == 4:
                    item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_index, column_index, item)

    def _checked_group_nodes(self) -> list[str]:
        checked = []

        if not self.group_node_table:
            return checked

        for row_index in range(self.group_node_table.rowCount()):
            checkbox = self.group_node_table.cellWidget(row_index, 0)
            node_item = self.group_node_table.item(row_index, 2)

            if isinstance(checkbox, QCheckBox) and checkbox.isChecked() and node_item:
                checked.append(node_item.text())

        return checked

    def _validate_current_group_ports(self) -> tuple[bool, str | None, int | None, int | None, list[str]]:
        port_text = self.group_port_input.text().strip() if self.group_port_input else ""
        controller_text = self.group_controller_input.text().strip() if self.group_controller_input else ""
        settings = load_app_settings()
        proxy_settings = settings.get("proxy", {})
        mihomo_settings = settings.get("mihomo", {})
        listener_reserved_ports = {18000, 17890}
        controller_reserved_ports = {18000, 17890}

        for key in ("listen_port", "receiver_port"):
            try:
                value = int(proxy_settings.get(key))
            except (TypeError, ValueError):
                continue
            listener_reserved_ports.add(value)
            controller_reserved_ports.add(value)

        for key in ("controller_port", "mixed_port"):
            try:
                listener_reserved_ports.add(int(mihomo_settings.get(key)))
            except (TypeError, ValueError):
                continue

        listener_reserved_text = " / ".join(str(port) for port in sorted(listener_reserved_ports))
        controller_reserved_text = " / ".join(str(port) for port in sorted(controller_reserved_ports))

        if not port_text.isdigit():
            return False, "\u672c\u5730\u4ee3\u7406\u7aef\u53e3\u5fc5\u987b\u662f\u6570\u5b57", None, None, []

        if not controller_text.isdigit():
            return False, "external-controller \u5fc5\u987b\u662f\u6570\u5b57", None, None, []

        port = int(port_text)
        controller = int(controller_text)

        if port < 1 or port > 65535:
            return False, "\u672c\u5730\u4ee3\u7406\u7aef\u53e3\u8303\u56f4\u5fc5\u987b\u662f 1-65535", None, None, []

        if controller < 1 or controller > 65535:
            return False, "external-controller \u8303\u56f4\u5fc5\u987b\u662f 1-65535", None, None, []

        if port == controller:
            return False, "\u672c\u5730\u4ee3\u7406\u7aef\u53e3\u548c external-controller \u4e0d\u80fd\u76f8\u540c", None, None, []

        if port in listener_reserved_ports:
            return False, f"\u672c\u5730\u4ee3\u7406\u7aef\u53e3\u4e0d\u80fd\u4f7f\u7528\u4fdd\u7559\u7aef\u53e3 {listener_reserved_text}", None, None, []

        if controller in controller_reserved_ports:
            return False, f"external-controller \u4e0d\u80fd\u4f7f\u7528\u4fdd\u7559\u7aef\u53e3 {controller_reserved_text}", None, None, []

        for group_name, group_data in self._group_map().items():
            if group_name == self.current_group_name or not isinstance(group_data, dict):
                continue

            other_port = group_data.get("port")
            other_controller = group_data.get("controller")

            try:
                other_port = int(other_port) if other_port not in (None, "") else None
            except (TypeError, ValueError):
                other_port = None

            try:
                other_controller = int(other_controller) if other_controller not in (None, "") else None
            except (TypeError, ValueError):
                other_controller = None

            if other_port == port or other_controller == port:
                return False, f"\u672c\u5730\u4ee3\u7406\u7aef\u53e3\u4e0e\u5206\u7ec4 {group_name} \u91cd\u590d", None, None, []

            if other_controller == controller or other_port == controller:
                return False, f"external-controller \u4e0e\u5206\u7ec4 {group_name} \u91cd\u590d", None, None, []

        return True, None, port, controller, []

    def _save_current_group_nodes(self):
        if self.busy:
            return
        if is_proxy_running():
            self._append_activity_log("[WARN] 代理运行中，无法修改分组配置")
            self._update_group_edit_state()
            return

        if not self.current_group_name:
            self.notify_info("请先选择一个分组")
            return

        groups = self._group_map()
        group_data = groups.get(self.current_group_name)
        if not isinstance(group_data, dict):
            self.notify_warning("当前分组不存在")
            return

        valid, validation_error, port, controller, adjusted_ports = self._validate_current_group_ports()
        if not valid:
            self.notify_warning(validation_error or "端口校验失败")
            return

        config_to_save = copy.deepcopy(self.group_nodes_config)
        save_groups = config_to_save.get("groups", {})
        save_group_data = save_groups.get(self.current_group_name)
        if not isinstance(save_group_data, dict):
            self.notify_warning("当前分组不存在")
            return
        checked_nodes = self._checked_group_nodes()
        save_group_data["nodes"] = checked_nodes
        save_group_data["port"] = port
        save_group_data["controller"] = controller
        current_group_name = self.current_group_name
        _ = adjusted_ports

        def task():
            ok, error = save_group_nodes_config(config_to_save)
            if not ok:
                raise RuntimeError(error or "保存失败")

            if not is_proxy_running():
                ok, error, _changes = prepare_mihomo_runtime_ports(write_settings=True, check_system=True)
                if not ok:
                    raise RuntimeError(error or "mihomo 端口准备失败")

            generated, generate_error = generate_mihomo_configs()
            if not generated:
                raise RuntimeError(generate_error or "配置生成失败")
            return config_to_save, current_group_name, len(checked_nodes)

        def on_success(result):
            saved_config, group_name, node_count = result
            self.group_nodes_config = saved_config
            self._show_group(group_name)
            self.refresh_dashboard()
            self._append_activity_log(
                f"[INFO]  分组 {group_name} 已保存，配置已自动生成，节点数 {node_count}"
            )
            self.notify_success("分组已保存，配置已自动生成")

        self.run_gui_task(
            task,
            on_success=on_success,
            error_message="分组保存失败",
            loading_message="正在保存分组...",
            task_name="保存分组",
        )

    def _create_group_from_dialog(self):
        if self.busy:
            return
        if is_proxy_running():
            self._append_activity_log("[WARN] 代理运行中，无法修改分组配置")
            self._update_group_edit_state()
            return

        group_name, accepted = QInputDialog.getText(
            self,
            "ContextProxy",
            "\u8bf7\u8f93\u5165\u5206\u7ec4\u540d\u79f0",
        )

        if not accepted:
            return

        group_name = group_name.strip()
        if not group_name:
            self.notify_warning("分组名称不能为空")
            return

        if not self._is_valid_group_name(group_name):
            self.notify_warning("分组名称只允许中文、英文、数字、下划线和短横线")
            return

        groups = self._group_map()
        if group_name in groups:
            self.notify_info("分组名称已存在")
            return

        try:
            used_ports = self._used_group_ports()
            check_system_ports = not is_proxy_running()
            port = self._allocate_available_port(7894, used_ports, check_system=check_system_ports)
            controller = self._allocate_available_port(9094, used_ports, check_system=check_system_ports)
        except ValueError as exc:
            self.notify_error(str(exc))
            return

        config_to_save = copy.deepcopy(self.group_nodes_config)
        if "groups" not in config_to_save or not isinstance(config_to_save["groups"], dict):
            config_to_save["groups"] = {}

        config_to_save["groups"][group_name] = {
            "port": port,
            "controller": controller,
            "nodes": [],
        }

        def task():
            ok, error = save_group_nodes_config(config_to_save)
            if not ok:
                raise RuntimeError(error or "创建失败")

            ok, error, _changes = prepare_mihomo_runtime_ports(write_settings=True, check_system=True)
            if not ok:
                raise RuntimeError(error or "mihomo 端口准备失败")

            generated, generate_error = generate_mihomo_configs()
            if not generated:
                raise RuntimeError(generate_error or "配置生成失败")
            return config_to_save

        def on_success(saved_config):
            self.group_nodes_config = saved_config
            self._refresh_after_group_structure_changed(group_name)
            self._append_activity_log(f"[INFO]  分组 {group_name} 已创建，port={port}, controller={controller}")
            self.notify_success("分组已创建，请勾选节点后保存并应用")

        self.run_gui_task(
            task,
            on_success=on_success,
            error_message="创建分组失败",
            loading_message="正在创建分组...",
            task_name="创建分组",
        )

    def _delete_current_group(self):
        if self.busy:
            return
        if is_proxy_running():
            self._append_activity_log("[WARN] 代理运行中，无法修改分组配置")
            self._update_group_edit_state()
            return

        if not self.current_group_name:
            self.notify_info("请先选择分组")
            return

        default_groups = {"Proxy"}
        if self.current_group_name in default_groups:
            self.notify_warning("Proxy 默认分组暂时不允许删除")
            return

        result = QMessageBox.question(
            self,
            "ContextProxy",
            f"\u786e\u5b9a\u5220\u9664\u5206\u7ec4 {self.current_group_name} \u5417\uff1f",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if result != QMessageBox.Yes:
            return

        group_name = self.current_group_name
        config_to_save = copy.deepcopy(self.group_nodes_config)
        groups = config_to_save.get("groups", {})
        if group_name not in groups:
            self.notify_warning("当前分组不存在")
            return

        groups.pop(group_name, None)

        def task():
            ok, error = save_group_nodes_config(config_to_save)
            if not ok:
                raise RuntimeError(error or "删除失败")

            ok, error, _changes = prepare_mihomo_runtime_ports(write_settings=True, check_system=True)
            if not ok:
                raise RuntimeError(error or "mihomo 端口准备失败")

            generated, generate_error = generate_mihomo_configs()
            if not generated:
                raise RuntimeError(generate_error or "配置生成失败")
            return config_to_save

        def on_success(saved_config):
            self.group_nodes_config = saved_config
            self._refresh_after_group_structure_changed(None)
            self._append_activity_log(f"[INFO]  分组 {group_name} 已删除")
            self.notify_success("分组已删除。如有该分组相关规则，请到规则管理中清理。")

        self.run_gui_task(
            task,
            on_success=on_success,
            error_message="删除分组失败",
            loading_message="正在删除分组...",
            task_name="删除分组",
        )

    def _reselect_current_group(self):
        if self.busy:
            return
        if not self.current_group_name:
            self.notify_info("请先选择一个分组")
            return

        if not is_proxy_running():
            self.notify_warning("请先启动代理后再重新自动选择")
            self._update_group_edit_state()
            return

        group_name = self.current_group_name
        previous_node = self.current_node_input.text().strip() if self.current_node_input else ""
        self._append_activity_log(f"[INFO] {group_name} 开始重新自动选择")

        def task():
            from backend.auto_selector import select_best_node_for_group

            selected_node = select_best_node_for_group(group_name)
            if selected_node and selected_node != previous_node:
                from backend.connection_closer import close_changed_groups

                close_changed_groups({group_name})
            return selected_node

        def on_success(selected_node):
            self.refresh_selected_node_display(group_name)
            if not selected_node:
                self.notify_warning("没有可用节点，保持当前选择")
                return
            self._append_activity_log(f"[INFO] {group_name} 当前选择节点：{selected_node}")
            self.notify_success("已重新自动选择")

        self.run_gui_task(
            task,
            on_success=on_success,
            error_message="重新自动选择失败",
            loading_message="正在重新选择节点...",
            task_name="重新自动选择",
        )

    def _rules_page(self):
        page, layout = self._page_shell(
            "\u89c4\u5219\u7ba1\u7406",
            "\u7ef4\u62a4\u57df\u540d\u548c\u5e94\u7528\u8fdb\u7a0b\u5206\u6d41\u89c4\u5219",
        )

        self.rule_group_names = load_group_names()

        tabs = QTabWidget()
        tabs.addTab(self._domain_rules_page(), "\u57df\u540d\u89c4\u5219")
        tabs.addTab(self._process_rules_page(), "\u8fdb\u7a0b\u89c4\u5219")
        tabs.setCurrentIndex(1)
        layout.addWidget(tabs, 1)
        return page

    def _domain_rules_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        self.domain_rule_table = QTableWidget()
        self._setup_table(self.domain_rule_table)
        self.domain_rule_table.setColumnCount(2)
        self.domain_rule_table.setHorizontalHeaderLabels(["\u5206\u7ec4", "\u5339\u914d\u89c4\u5219"])
        self.domain_rule_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.domain_rule_table.itemSelectionChanged.connect(self._sync_selected_domain_rule_to_editor)
        self._fill_simple_rule_table(self.domain_rule_table, load_domain_rules())
        layout.addWidget(self.domain_rule_table, 1)

        editor = self._rule_editor_card(
            "\u7f16\u8f91 / \u65b0\u589e \u57df\u540d\u89c4\u5219",
            is_process=False,
        )
        layout.addWidget(editor)
        return page

    def _process_rules_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        self.process_rule_table = QTableWidget()
        self._setup_table(self.process_rule_table)
        self.process_rule_table.setColumnCount(2)
        self.process_rule_table.setHorizontalHeaderLabels([
            "\u5206\u7ec4",
            "\u5e94\u7528\u7a0b\u5e8f\uff08\u8fdb\u7a0b\u540d\uff09",
        ])
        self.process_rule_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.process_rule_table.itemSelectionChanged.connect(self._sync_selected_process_rule_to_editor)
        self._fill_simple_rule_table(self.process_rule_table, load_process_rules())
        layout.addWidget(self.process_rule_table, 1)

        layout.addWidget(self._rule_editor_card("\u7f16\u8f91 / \u65b0\u589e \u8fdb\u7a0b\u89c4\u5219", is_process=True))
        return page

    def _rule_editor_card(self, title_text: str, is_process: bool):
        editor = self._card()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(16, 16, 16, 16)
        editor_layout.setSpacing(14)

        title = QLabel(title_text)
        title.setObjectName("SectionTitle")
        editor_layout.addWidget(title)

        form = QGridLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        group_combo = QComboBox()
        group_combo.addItems(self.rule_group_names)
        value_input = QLineEdit()

        form.addWidget(QLabel("\u9009\u62e9\u5206\u7ec4"), 0, 0)
        form.addWidget(group_combo, 0, 1)

        if is_process:
            choose_app = QPushButton("\u4ece\u6587\u4ef6\u9009\u62e9 App...")
            choose_app.clicked.connect(self._choose_process_exe)
            value_input.setPlaceholderText("chrome.exe")
            form.addWidget(QLabel("\u5e94\u7528\u7a0b\u5e8f\u540d"), 1, 0)
            form.addWidget(choose_app, 1, 1)
            form.addWidget(value_input, 1, 2)
            self.process_group_combo = group_combo
            self.process_name_input = value_input
        else:
            value_input.setPlaceholderText("*.example.com")
            form.addWidget(QLabel("\u5339\u914d\u89c4\u5219"), 1, 0)
            form.addWidget(value_input, 1, 1, 1, 2)
            self.domain_group_combo = group_combo
            self.domain_pattern_input = value_input

        editor_layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        add_button = QPushButton("添加规则")
        update_button = QPushButton("修改规则")
        delete_button = QPushButton("删除规则")
        save_button = QPushButton("保存规则")
        add_button.setObjectName("PrimaryButton")
        delete_button.setObjectName("DangerButton")

        if is_process:
            add_button.clicked.connect(self._add_process_rule)
            update_button.clicked.connect(self._update_process_rule)
            delete_button.clicked.connect(lambda: self._delete_selected_rule(self.process_rule_table))
            save_button.clicked.connect(self._save_process_rules)
        else:
            add_button.clicked.connect(self._add_domain_rule)
            update_button.clicked.connect(self._update_domain_rule)
            delete_button.clicked.connect(lambda: self._delete_selected_rule(self.domain_rule_table))
            save_button.clicked.connect(self._save_domain_rules)

        for button in (add_button, update_button, delete_button, save_button):
            buttons.addWidget(button)
        editor_layout.addLayout(buttons)
        return editor

    def _fill_simple_rule_table(self, table: QTableWidget, rules: list[tuple[str, str]]):
        table.setRowCount(len(rules))
        for row_index, (group, value) in enumerate(rules):
            table.setRowHeight(row_index, 34)
            table.setItem(row_index, 0, QTableWidgetItem(group))
            table.setItem(row_index, 1, QTableWidgetItem(value))

    def _rules_from_table(self, table: QTableWidget) -> list[tuple[str, str]]:
        rules = []
        for row_index in range(table.rowCount()):
            group_item = table.item(row_index, 0)
            value_item = table.item(row_index, 1)
            group = group_item.text().strip() if group_item else ""
            value = value_item.text().strip() if value_item else ""
            if group and value:
                rules.append((group, value))
        return rules

    def _set_combo_text(self, combo: QComboBox, value: str):
        index = combo.findText(value)
        if index < 0:
            combo.addItem(value)
            index = combo.findText(value)
        combo.setCurrentIndex(index)

    def _sync_selected_domain_rule_to_editor(self):
        self._sync_selected_rule_to_editor(
            self.domain_rule_table,
            self.domain_group_combo,
            self.domain_pattern_input,
        )

    def _sync_selected_process_rule_to_editor(self):
        self._sync_selected_rule_to_editor(
            self.process_rule_table,
            self.process_group_combo,
            self.process_name_input,
        )

    def _sync_selected_rule_to_editor(self, table: QTableWidget, combo: QComboBox, input_box: QLineEdit):
        if not table or not combo or not input_box:
            return

        row = table.currentRow()
        if row < 0:
            return

        group_item = table.item(row, 0)
        value_item = table.item(row, 1)
        if group_item:
            self._set_combo_text(combo, group_item.text())
        if value_item:
            input_box.setText(value_item.text())

    def _choose_process_exe(self):
        file_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "\u9009\u62e9 App",
            "",
            "Executable (*.exe);;All Files (*)",
        )
        if not file_path:
            return

        if self.process_name_input:
            self.process_name_input.setText(Path(file_path).name)

    def _add_domain_rule(self):
        if not self.domain_group_combo or not self.domain_pattern_input:
            return

        group = self.domain_group_combo.currentText().strip()
        pattern = self.domain_pattern_input.text().strip()
        self._add_rule_to_table(self.domain_rule_table, group, pattern, "\u5339\u914d\u89c4\u5219\u4e0d\u80fd\u4e3a\u7a7a")

    def _add_process_rule(self):
        if not self.process_group_combo or not self.process_name_input:
            return

        group = self.process_group_combo.currentText().strip()
        process_name = self.process_name_input.text().strip()
        self._add_rule_to_table(self.process_rule_table, group, process_name, "\u8fdb\u7a0b\u540d\u4e0d\u80fd\u4e3a\u7a7a")

    def _update_domain_rule(self):
        if not self.domain_group_combo or not self.domain_pattern_input:
            return

        self._update_selected_rule(
            self.domain_rule_table,
            self.domain_group_combo.currentText().strip(),
            self.domain_pattern_input.text().strip(),
            "匹配规则不能为空",
        )

    def _update_process_rule(self):
        if not self.process_group_combo or not self.process_name_input:
            return

        self._update_selected_rule(
            self.process_rule_table,
            self.process_group_combo.currentText().strip(),
            self.process_name_input.text().strip(),
            "进程名不能为空",
        )

    def _update_selected_rule(self, table: QTableWidget, group: str, value: str, empty_value_message: str):
        if not table:
            return

        row = table.currentRow()
        if row < 0:
            self.notify_info("请先选择要修改的规则")
            return

        if not group:
            self.notify_warning("分组不能为空")
            return

        if not value:
            self.notify_warning(empty_value_message)
            return

        for row_index in range(table.rowCount()):
            if row_index == row:
                continue

            group_item = table.item(row_index, 0)
            value_item = table.item(row_index, 1)
            existing_group = group_item.text().strip() if group_item else ""
            existing_value = value_item.text().strip() if value_item else ""
            if existing_group == group and existing_value == value:
                self.notify_info("规则已存在")
                return

        table.setItem(row, 0, QTableWidgetItem(group))
        table.setItem(row, 1, QTableWidgetItem(value))
        table.selectRow(row)
        self.notify_success("规则已修改")

    def _add_rule_to_table(self, table: QTableWidget, group: str, value: str, empty_value_message: str):
        if not group:
            self.notify_warning("分组不能为空")
            return

        if not value:
            self.notify_warning(empty_value_message)
            return

        existing = set(self._rules_from_table(table))
        if (group, value) in existing:
            self.notify_info("规则已存在")
            return

        row = table.rowCount()
        table.insertRow(row)
        table.setRowHeight(row, 34)
        table.setItem(row, 0, QTableWidgetItem(group))
        table.setItem(row, 1, QTableWidgetItem(value))
        table.selectRow(row)
        self.notify_success("规则已添加")

    def _delete_selected_rule(self, table: QTableWidget):
        if not table:
            return

        row = table.currentRow()
        if row < 0:
            self.notify_info("请先选择要删除的规则")
            return

        table.removeRow(row)
        self.notify_success("规则已删除")

    def refresh_rule_group_options(self):
        self.rule_group_names = load_group_names()

        for combo in (self.domain_group_combo, self.process_group_combo):
            if not combo:
                continue

            current_text = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(self.rule_group_names)
            combo.blockSignals(False)

            if current_text:
                index = combo.findText(current_text)
                if index >= 0:
                    combo.setCurrentIndex(index)
                elif combo.count() > 0:
                    combo.setCurrentIndex(0)

    def _missing_rule_groups(self, rules: list[tuple[str, str]]) -> list[str]:
        known = set(self.rule_group_names)
        return sorted({group for group, _value in rules if group not in known})

    def _confirm_missing_groups(self, rules: list[tuple[str, str]]) -> bool:
        missing = self._missing_rule_groups(rules)
        if not missing:
            return True

        message = (
            "\u4ee5\u4e0b\u5206\u7ec4\u4e0d\u5b58\u5728\uff0c\u4ecd\u7136\u4fdd\u5b58\u5417\uff1f\n"
            + "\n".join(missing)
        )
        result = QMessageBox.question(
            self,
            "ContextProxy",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return result == QMessageBox.Yes

    def _save_domain_rules(self):
        if self.busy:
            return
        old_rules = load_domain_rules()
        rules = self._rules_from_table(self.domain_rule_table)
        if not self._confirm_missing_groups(rules):
            return

        def task():
            save_domain_rules(rules)
            changed_groups, changed_patterns = self._reload_domain_rules_immediately(old_rules, rules)
            return changed_groups, changed_patterns

        def on_success(result):
            changed_groups, changed_patterns = result
            self._fill_simple_rule_table(self.domain_rule_table, load_domain_rules())
            self._append_activity_log(
                f"[INFO] 域名规则已保存，已立即断开受影响连接，"
                f"groups={sorted(changed_groups)}, patterns={sorted(changed_patterns)}"
            )
            self.notify_success("域名规则已保存")

        self.run_gui_task(
            task,
            on_success=on_success,
            error_message="域名规则保存失败",
            loading_message="正在保存规则...",
            task_name="保存域名规则",
        )

    def _save_process_rules(self):
        if self.busy:
            return
        old_rules = load_process_rules()
        rules = self._rules_from_table(self.process_rule_table)
        if not self._confirm_missing_groups(rules):
            return

        def task():
            save_process_rules(rules)
            changed_groups, changed_patterns = self._reload_process_rules_immediately(old_rules, rules)
            return changed_groups, changed_patterns

        def on_success(result):
            changed_groups, changed_patterns = result
            self._fill_simple_rule_table(self.process_rule_table, load_process_rules())
            self._append_activity_log(
                f"[INFO] 进程规则已保存，已立即断开受影响连接，"
                f"groups={sorted(changed_groups)}, processes={sorted(changed_patterns)}"
            )
            self.notify_success("进程规则已保存")

        self.run_gui_task(
            task,
            on_success=on_success,
            error_message="进程规则保存失败",
            loading_message="正在保存规则...",
            task_name="保存进程规则",
        )

    def _reload_domain_rules_immediately(
        self,
        old_rules: list[tuple[str, str]] | None = None,
        new_rules: list[tuple[str, str]] | None = None,
    ) -> tuple[set[str], set[str]]:
        try:
            from backend.core_launcher import reload_core_config
            from backend.connection_closer import close_changed_groups

            reload_core_config()
            current_rules = new_rules if new_rules is not None else load_domain_rules()
            old_rules = old_rules or []

            # Close both old and new groups. When a rule moves from Proxy -> AI
            # -> Media, the old persistent HTTP/2/WebSocket connections are
            # still attached to the old group and must be closed too; closing
            # only the new group leaves stale browser links alive after several
            # consecutive rule edits.
            affected_rules = list(old_rules) + list(current_rules)
            changed_groups = {group for group, _pattern in affected_rules}
            if changed_groups:
                close_changed_groups(changed_groups)
            return changed_groups, {pattern for _group, pattern in affected_rules}
        except Exception as exc:
            _ = exc
            return set(), set()

    def _reload_process_rules_immediately(
        self,
        old_rules: list[tuple[str, str]] | None = None,
        new_rules: list[tuple[str, str]] | None = None,
    ) -> tuple[set[str], set[str]]:
        try:
            from backend.core_launcher import reload_core_config
            from backend.connection_closer import close_changed_groups

            reload_core_config()
            current_rules = new_rules if new_rules is not None else load_process_rules()
            old_rules = old_rules or []

            affected_rules = list(old_rules) + list(current_rules)
            changed_groups = {group for group, _process in affected_rules}
            if changed_groups:
                close_changed_groups(changed_groups)
            return changed_groups, {process for _group, process in affected_rules}
        except Exception as exc:
            _ = exc
            return set(), set()


    def _settings_page(self):
        page, layout = self._page_shell(
            "\u8bbe\u7f6e",
            "\u7ba1\u7406\u672c\u5730\u4ee3\u7406\u3001\u81ea\u52a8\u9009\u62e9\u548c\u754c\u9762\u884c\u4e3a\u8bbe\u7f6e",
        )

        settings = load_app_settings()
        self.setting_inputs = {}
        self.setting_checks = {}

        layout.addWidget(
            self._settings_form_card(
                "\u4ee3\u7406\u8bbe\u7f6e",
                [
                    ("proxy.listen_host", "\u672c\u5730\u76d1\u542c\u5730\u5740"),
                    ("proxy.listen_port", "\u672c\u5730\u4ee3\u7406\u7aef\u53e3"),
                    ("proxy.receiver_port", "Tab \u4e0a\u62a5\u63a5\u6536\u7aef\u53e3"),
                ],
                settings,
            )
        )

        layout.addWidget(
            self._settings_form_card(
                "\u8282\u70b9\u6c60\u624b\u52a8\u6d4b\u901f",
                [
                    ("latency_test.timeout_ms", "\u624b\u52a8\u6d4b\u901f\u8d85\u65f6\uff08ms\uff09"),
                    ("latency_test.test_url", "\u624b\u52a8\u6d4b\u901f URL"),
                ],
                settings,
            )
        )

        behavior_card = self._card()
        behavior_layout = QVBoxLayout(behavior_card)
        behavior_layout.setContentsMargins(16, 16, 16, 16)
        behavior_layout.setSpacing(12)
        behavior_title = QLabel("\u754c\u9762 / \u6258\u76d8\u884c\u4e3a")
        behavior_title.setObjectName("SectionTitle")
        behavior_layout.addWidget(behavior_title)

        for key, label_text in [
            ("ui.close_to_tray", "\u5173\u95ed\u7a97\u53e3\u6700\u5c0f\u5316\u5230\u6258\u76d8"),
            ("ui.start_minimized", "\u542f\u52a8\u65f6\u6700\u5c0f\u5316"),
            ("ui.auto_start_proxy", "\u542f\u52a8 GUI \u540e\u81ea\u52a8\u542f\u52a8\u4ee3\u7406"),
            ("ui.enable_system_proxy_on_start", "\u542f\u52a8\u4ee3\u7406\u65f6\u542f\u7528\u7cfb\u7edf\u4ee3\u7406"),
            ("ui.disable_system_proxy_on_stop", "\u505c\u6b62\u4ee3\u7406\u65f6\u5173\u95ed\u7cfb\u7edf\u4ee3\u7406"),
        ]:
            checkbox = QCheckBox(label_text)
            checkbox.setChecked(bool(self._setting_value(settings, key)))
            self.setting_checks[key] = checkbox
            behavior_layout.addWidget(checkbox)

        layout.addWidget(behavior_card)

        hint = QLabel("\u90e8\u5206\u8bbe\u7f6e\u9700\u8981\u91cd\u542f\u4ee3\u7406\u540e\u751f\u6548")
        hint.setObjectName("Muted")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        save_button = QPushButton("\u4fdd\u5b58\u8bbe\u7f6e")
        save_button.setObjectName("PrimaryButton")
        save_button.clicked.connect(self._save_app_settings_from_inputs)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return page

    def _settings_form_card(self, title_text: str, fields: list[tuple[str, str]], settings: dict):
        card = self._card()
        inner = QVBoxLayout(card)
        inner.setContentsMargins(16, 16, 16, 16)
        inner.setSpacing(12)

        title = QLabel(title_text)
        title.setObjectName("SectionTitle")
        inner.addWidget(title)

        form = QGridLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        for row, (key, label_text) in enumerate(fields):
            input_box = QLineEdit(str(self._setting_value(settings, key)))
            self.setting_inputs[key] = input_box
            form.addWidget(QLabel(label_text), row, 0)
            form.addWidget(input_box, row, 1)

        inner.addLayout(form)
        return card

    def _setting_value(self, settings: dict, dotted_key: str):
        section, key = dotted_key.split(".", 1)
        section_data = settings.get(section, {})
        return section_data.get(key, "") if isinstance(section_data, dict) else ""

    def _read_int_setting(self, key: str, label: str) -> int | None:
        input_box = self.setting_inputs.get(key)
        value = input_box.text().strip() if input_box else ""
        if not value.isdigit():
            self.notify_warning(f"{label}必须是数字")
            return None
        return int(value)

    def _save_app_settings_from_inputs(self):
        if self.busy:
            return
        previous_settings = load_app_settings()
        listen_port = self._read_int_setting("proxy.listen_port", "\u672c\u5730\u4ee3\u7406\u7aef\u53e3")
        receiver_port = self._read_int_setting("proxy.receiver_port", "Tab \u4e0a\u62a5\u63a5\u6536\u7aef\u53e3")
        delay_timeout_ms = self._read_int_setting("latency_test.timeout_ms", "\u624b\u52a8\u6d4b\u901f\u8d85\u65f6")

        if None in (listen_port, receiver_port, delay_timeout_ms):
            return

        settings = {
            "proxy": {
                "listen_host": self.setting_inputs["proxy.listen_host"].text().strip(),
                "listen_port": listen_port,
                "receiver_port": receiver_port,
            },
            "latency_test": {
                "timeout_ms": delay_timeout_ms,
                "test_url": self.setting_inputs["latency_test.test_url"].text().strip(),
            },
            "mihomo": previous_settings.get("mihomo", {}),
            "ui": {
                "close_to_tray": self.setting_checks["ui.close_to_tray"].isChecked(),
                "start_minimized": self.setting_checks["ui.start_minimized"].isChecked(),
                "auto_start_proxy": self.setting_checks["ui.auto_start_proxy"].isChecked(),
                "enable_system_proxy_on_start": self.setting_checks["ui.enable_system_proxy_on_start"].isChecked(),
                "disable_system_proxy_on_stop": self.setting_checks["ui.disable_system_proxy_on_stop"].isChecked(),
            },
            "logging": previous_settings.get("logging", {}),
        }

        message = "设置已保存"
        if self._settings_need_proxy_restart(previous_settings, settings):
            message += "，部分设置需要重启代理后生效"

        def task():
            ok, error = save_app_settings(settings)
            if not ok:
                raise RuntimeError(error or "保存失败")
            return message

        def on_success(success_message):
            self._max_recent_activities = int(
                load_app_settings().get("logging", {}).get("max_recent_activities") or 200
            )
            self.notify_success(success_message)

        self.run_gui_task(
            task,
            on_success=on_success,
            error_message="设置保存失败",
            loading_message="正在保存设置...",
            task_name="保存设置",
        )

    def _settings_need_proxy_restart(self, previous_settings: dict, current_settings: dict) -> bool:
        watched_keys = [
            ("proxy", "listen_port"),
            ("proxy", "receiver_port"),
        ]

        for section, key in watched_keys:
            previous_section = previous_settings.get(section, {})
            current_section = current_settings.get(section, {})
            if previous_section.get(key) != current_section.get(key):
                return True

        return False


    def _setup_table(self, table: QTableWidget):
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(34)
        table.setShowGrid(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def _fill_table(self, table: QTableWidget, rows: list[tuple[str, ...]]):
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            table.setRowHeight(row_index, 34)
            for column_index, value in enumerate(row):
                item = QTableWidgetItem(value)

                if value in {"\u6d3b\u52a8", "\u53ef\u7528"}:
                    item.setForeground(QColor(SUCCESS if value == "\u6d3b\u52a8" else PRIMARY))
                    item.setTextAlignment(Qt.AlignCenter)

                if value == "\u672a\u6d4b":
                    item.setForeground(QColor(MUTED))
                    item.setTextAlignment(Qt.AlignCenter)

                if value == "\u5931\u8d25":
                    item.setForeground(QColor(DANGER))
                    item.setTextAlignment(Qt.AlignCenter)

                if value == "\u2713":
                    item.setForeground(QColor(SUCCESS))
                    item.setTextAlignment(Qt.AlignCenter)

                if value == "\u00d7":
                    item.setForeground(QColor(DANGER))
                    item.setTextAlignment(Qt.AlignCenter)

                if value in {"\u662f", "\u5426"}:
                    item.setTextAlignment(Qt.AlignCenter)

                if column_index == table.columnCount() - 1 and value.isdigit():
                    item.setForeground(QColor(SUCCESS if int(value) < 150 else DANGER))
                    item.setTextAlignment(Qt.AlignCenter)

                table.setItem(row_index, column_index, item)


    def _start_node_delay_test(self):
        if self.busy:
            return
        if self.delay_test_thread and self.delay_test_thread.isRunning():
            cancel_delay_test()
            if self.node_delay_test_button:
                self.node_delay_test_button.setEnabled(False)
                self.node_delay_test_button.setText("正在取消...")
            self.notify_info("节点池延迟测试正在取消")
            return

        self.start_busy("正在测试延迟...", "节点池测试延迟")
        if self.node_delay_test_button:
            self.node_delay_test_button.setEnabled(True)
            self.node_delay_test_button.setText("取消测速")

        self._append_activity_log("[INFO] \u8282\u70b9\u6c60\u5ef6\u8fdf\u6d4b\u8bd5\u5df2\u5f00\u59cb")

        self.delay_test_thread = QThread(self)
        self.delay_test_worker = DelayTestWorker()
        self.delay_test_worker.moveToThread(self.delay_test_thread)
        self.delay_test_thread.started.connect(self.delay_test_worker.run)
        self.delay_test_worker.finished.connect(self._finish_node_delay_test)
        self.delay_test_worker.finished.connect(self.delay_test_thread.quit)
        self.delay_test_worker.finished.connect(self.delay_test_worker.deleteLater)
        self.delay_test_thread.finished.connect(self._cleanup_node_delay_thread)
        self.delay_test_thread.finished.connect(self.delay_test_thread.deleteLater)
        self.delay_test_thread.start()

    def _finish_node_delay_test(self, result: dict):
        if self.node_delay_test_button:
            self.node_delay_test_button.setEnabled(True)
            self.node_delay_test_button.setText("\u6d4b\u8bd5\u5ef6\u8fdf")

        if not isinstance(result, dict) or not result.get("ok"):
            error = result.get("error") if isinstance(result, dict) else None
            error_text = error or "\u672a\u77e5\u9519\u8bef"
            self.finish_busy(False, f"\u8282\u70b9\u6c60\u5ef6\u8fdf\u6d4b\u8bd5\u5931\u8d25\uff1a{error_text}")
            return

        results = result.get("results", {})
        self.node_latency_cache = results if isinstance(results, dict) else {}
        ok_count = sum(
            1
            for item in self.node_latency_cache.values()
            if isinstance(item, dict) and item.get("status") == "ok"
        )
        total_count = len(self.node_latency_cache)

        self._refresh_node_pool_table()
        if self.current_group_name:
            self._populate_group_node_table(set(self._current_group_nodes(self.current_group_name)))

        if result.get("cancelled"):
            self.finish_busy(True)
            self.notify_info(f"节点池延迟测试已取消：已完成 {total_count} 个，{ok_count} 个可用")
        else:
            self.finish_busy(True, f"\u8282\u70b9\u6c60\u5ef6\u8fdf\u6d4b\u8bd5\u5b8c\u6210\uff1a{ok_count}/{total_count} \u53ef\u7528")

    def _cleanup_node_delay_thread(self):
        self.delay_test_thread = None
        self.delay_test_worker = None

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
            self.notify_info("任务处理中，请稍候")
            return

        if self._force_real_close:
            self._disable_system_proxy_on_exit()
            if is_proxy_running() or get_proxy_state() in {"starting", "stopping", "failed"}:
                try:
                    self._log_lifecycle_event("stop_proxy_process called, reason=real_close_event")
                    stop_proxy_process()
                except Exception as exc:
                    self._append_activity_log(f"[WARN] 退出时停止后端失败：{exc}")
            else:
                cleanup_proxy_residue()
            if self.tray_icon:
                self.tray_icon.hide()
            event.accept()
            return

        if load_app_settings().get("ui", {}).get("close_to_tray", True):
            event.ignore()
            self.hide()
            self._update_tray_status()
            self._log_lifecycle_event("window close ignored, hidden to tray; backend/service/proxy unchanged")
            self._append_activity_log("[INFO] 窗口已最小化到系统托盘")
            return

        if not is_proxy_running():
            self._disable_system_proxy_on_exit()
            if self.tray_icon:
                self.tray_icon.hide()
            event.accept()
            return

        result = QMessageBox.question(
            self,
            "ContextProxy",
            "代理正在运行，是否停止代理并退出？",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )

        if result == QMessageBox.Cancel:
            event.ignore()
            return

        if result == QMessageBox.Yes:
            self._disable_system_proxy_on_exit()

            self._log_lifecycle_event("stop_proxy_process called, reason=close_event_exit_confirmed")
            ok, error = stop_proxy_process()
            if not ok:
                self.notify_error(error or "代理停止失败")
                event.ignore()
                return

        if self.tray_icon:
            self.tray_icon.hide()
        event.accept()
