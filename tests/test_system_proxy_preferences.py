import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import yaml
from PySide6.QtWidgets import QApplication

from backend import app_settings, config_apply
from gui import settings_store
from gui.main_window import ProxyActionWorker


class SystemProxyPreferenceMigrationTests(unittest.TestCase):
    def test_gui_settings_merge_combines_legacy_preferences(self):
        settings = settings_store._merge_defaults(
            {
                "ui": {
                    "enable_system_proxy_on_start": True,
                    "disable_system_proxy_on_stop": False,
                }
            }
        )

        self.assertFalse(settings["ui"]["auto_manage_system_proxy"])

    def test_explicit_unified_preference_overrides_legacy_values(self):
        settings = settings_store._merge_defaults(
            {
                "ui": {
                    "auto_manage_system_proxy": True,
                    "enable_system_proxy_on_start": False,
                    "disable_system_proxy_on_stop": False,
                }
            }
        )

        self.assertTrue(settings["ui"]["auto_manage_system_proxy"])

    def test_backend_settings_loader_migrates_legacy_preferences(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "app_settings.yaml"
            settings_file.write_text(
                yaml.safe_dump(
                    {
                        "ui": {
                            "enable_system_proxy_on_start": False,
                            "disable_system_proxy_on_stop": True,
                        }
                    },
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )

            with patch.object(app_settings, "APP_SETTINGS_FILE", settings_file):
                settings = app_settings._load_app_settings_uncached()

        self.assertFalse(settings["ui"]["auto_manage_system_proxy"])

    def test_settings_save_normalizes_legacy_preferences(self):
        settings = config_apply._settings_for_save(
            {
                "ui": {
                    "enable_system_proxy_on_start": True,
                    "disable_system_proxy_on_stop": False,
                }
            }
        )

        self.assertFalse(settings["ui"]["auto_manage_system_proxy"])
        self.assertNotIn("enable_system_proxy_on_start", settings["ui"])
        self.assertNotIn("disable_system_proxy_on_stop", settings["ui"])


class ProxyActionWorkerPreferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _run_worker(worker: ProxyActionWorker) -> list[tuple[str, bool, str]]:
        results = []
        worker.finished.connect(
            lambda action, ok, error: results.append((action, ok, error))
        )
        worker.run()
        return results

    def test_disabled_unified_preference_does_not_change_system_proxy(self):
        settings = {"ui": {"auto_manage_system_proxy": False}}
        worker = ProxyActionWorker("start")

        with (
            patch("gui.main_window.load_app_settings", return_value=settings),
            patch("gui.main_window.start_proxy_process", return_value=(True, None)) as start,
            patch("gui.main_window.enable_system_proxy") as enable,
        ):
            results = self._run_worker(worker)

        start.assert_called_once_with()
        enable.assert_not_called()
        self.assertEqual(results, [("start", True, "")])

    def test_enabled_unified_preference_enables_system_proxy_on_start(self):
        settings = {
            "ui": {"auto_manage_system_proxy": True},
            "proxy": {"listen_host": "127.0.0.1", "listen_port": 18000},
        }
        worker = ProxyActionWorker("start")

        with (
            patch("gui.main_window.load_app_settings", return_value=settings),
            patch("gui.main_window.start_proxy_process", return_value=(True, None)),
            patch(
                "gui.main_window.enable_system_proxy",
                return_value=(True, "系统代理已启用"),
            ) as enable,
        ):
            results = self._run_worker(worker)

        enable.assert_called_once_with("127.0.0.1", 18000)
        self.assertEqual(results, [("start", True, "")])

    def test_enabled_unified_preference_disables_system_proxy_on_stop(self):
        settings = {
            "ui": {"auto_manage_system_proxy": True},
            "proxy": {"listen_host": "127.0.0.1", "listen_port": 18000},
        }
        worker = ProxyActionWorker("stop")

        with (
            patch("gui.main_window.load_app_settings", return_value=settings),
            patch(
                "gui.main_window.disable_system_proxy_if_contextproxy",
                return_value=(True, "系统代理已关闭"),
            ) as disable,
            patch("gui.main_window.stop_proxy_process", return_value=(True, None)) as stop,
        ):
            results = self._run_worker(worker)

        disable.assert_called_once_with("127.0.0.1", 18000)
        stop.assert_called_once_with()
        self.assertEqual(results, [("stop", True, "")])


if __name__ == "__main__":
    unittest.main()
