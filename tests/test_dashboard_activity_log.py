import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPlainTextEdit

from gui.main_window import MainWindow


class DashboardActivityLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_scroll_activity_log_to_latest(self):
        activity_log = QPlainTextEdit()
        scroll_bar = activity_log.verticalScrollBar()
        scroll_bar.setRange(0, 12)
        scroll_bar.setValue(0)

        MainWindow._scroll_activity_log_to_latest(
            SimpleNamespace(activity_log=activity_log)
        )

        self.assertEqual(scroll_bar.value(), scroll_bar.maximum())


if __name__ == "__main__":
    unittest.main()
