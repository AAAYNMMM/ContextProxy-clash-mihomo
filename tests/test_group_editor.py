import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from gui.main_window import MainWindow


class GroupEditorRunningStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _node_table(*, checked: bool = True) -> QTableWidget:
        table = QTableWidget(1, 6)
        check_item = QTableWidgetItem()
        check_item.setFlags(
            (check_item.flags() | Qt.ItemIsUserCheckable)
            & ~Qt.ItemIsEnabled
        )
        check_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        table.setItem(0, 0, check_item)
        table.setItem(0, 2, QTableWidgetItem("节点 A"))
        return table

    def _editor(self, *, port: int = 7893) -> SimpleNamespace:
        table = self._node_table()
        editor = SimpleNamespace(
            busy=False,
            current_group_name="Proxy",
            group_nodes_config={"groups": {"Proxy": {"port": 7893, "nodes": []}}},
            group_new_button=QPushButton(),
            group_delete_button=QPushButton(),
            group_save_button=QPushButton(),
            group_reselect_button=QPushButton(),
            group_readonly_hint=QLabel(),
            group_name_input=QLineEdit("Proxy"),
            group_port_input=QLineEdit(str(port)),
            strategy_combo=QComboBox(),
            group_node_table=table,
            notify_info=Mock(),
            notify_warning=Mock(),
            notify_success=Mock(),
            _append_activity_log=Mock(),
            _load_group_management_data=Mock(),
            _show_group=Mock(),
            refresh_dashboard=Mock(),
        )
        editor._group_map = lambda: editor.group_nodes_config["groups"]
        editor._validate_current_group_ports = lambda: MainWindow._validate_current_group_ports(editor)
        editor._checked_group_nodes = lambda: MainWindow._checked_group_nodes(editor)
        editor._current_group_data = lambda name: editor.group_nodes_config["groups"].get(name, {})

        def run_gui_task(task, *, on_success=None, **_kwargs):
            result = task()
            if on_success:
                on_success(result)
            return True

        editor.run_gui_task = run_gui_task
        return editor

    def test_running_proxy_keeps_node_checkboxes_and_save_available(self):
        editor = self._editor()

        with (
            patch("gui.main_window.is_proxy_running", return_value=True),
            patch("gui.main_window.save_group_nodes_and_apply") as save_group_nodes,
        ):
            MainWindow._update_group_edit_state(editor)
            MainWindow._save_current_group_nodes(editor)

        check_item = editor.group_node_table.item(0, 0)
        self.assertTrue(check_item.flags() & Qt.ItemIsEnabled)
        self.assertTrue(check_item.flags() & Qt.ItemIsUserCheckable)
        self.assertTrue(editor.group_save_button.isEnabled())
        self.assertFalse(editor.group_new_button.isEnabled())
        self.assertFalse(editor.group_delete_button.isEnabled())
        self.assertTrue(editor.group_port_input.isReadOnly())
        self.assertFalse(editor.strategy_combo.isEnabled())
        self.assertTrue(editor.group_reselect_button.isEnabled())

        save_group_nodes.assert_called_once()
        saved_config = save_group_nodes.call_args.args[0]
        self.assertEqual(saved_config["groups"]["Proxy"]["nodes"], ["节点 A"])
        self.assertEqual(saved_config["groups"]["Proxy"]["port"], 7893)
        self.assertTrue(save_group_nodes.call_args.kwargs["allow_running"])
        self.assertEqual(
            save_group_nodes.call_args.kwargs["reason"],
            "group_nodes_save:Proxy",
        )
        editor.notify_success.assert_called_once_with("节点选择已保存并应用")

    def test_running_proxy_rejects_a_group_port_change(self):
        editor = self._editor(port=7894)

        with (
            patch("gui.main_window.is_proxy_running", return_value=True),
            patch("gui.main_window.save_group_nodes_and_apply") as save_group_nodes,
        ):
            MainWindow._save_current_group_nodes(editor)

        save_group_nodes.assert_not_called()
        editor.notify_warning.assert_called_once_with("代理运行中不能修改分组端口")


if __name__ == "__main__":
    unittest.main()
