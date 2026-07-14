import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import yaml

from backend import config_apply
from backend import subscription_manager
from gui import subscription_store


class SubscriptionStoreTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.subscriptions_dir = self.root / "subscriptions"
        self.subscriptions_dir.mkdir()
        self.meta_file = self.subscriptions_dir / "subscriptions.yaml"
        self.node_pool_file = self.root / "node_pool.yaml"
        self.group_nodes_file = self.root / "group_nodes.yaml"

        self.stack = ExitStack()
        self.stack.enter_context(patch.object(subscription_store, "SUBSCRIPTIONS_DIR", self.subscriptions_dir))
        self.stack.enter_context(patch.object(subscription_store, "SUBSCRIPTIONS_META_FILE", self.meta_file))
        self.stack.enter_context(patch.object(subscription_store, "NODE_POOL_FILE", self.node_pool_file))
        self.stack.enter_context(patch.object(subscription_store, "GROUP_NODES_FILE", self.group_nodes_file))

    def tearDown(self):
        self.stack.close()
        self.temp_dir.cleanup()

    @staticmethod
    def _write_yaml(path: Path, data) -> None:
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def _write_subscription_files(self, name: str, nodes: list[dict]) -> None:
        file_base = subscription_manager.safe_filename(name)
        self._write_yaml(self.subscriptions_dir / f"{file_base}_nodes.yaml", {"proxies": nodes})
        (self.subscriptions_dir / f"{file_base}_nodes.json").write_text(
            json.dumps(
                {
                    "subscription_name": name,
                    "updated_at": "2026-07-14 12:00:00",
                    "node_count": len(nodes),
                    "proxies": nodes,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _seed_derived_files(self) -> None:
        old_node = {"name": "旧订阅 | 旧节点", "type": "ss", "server": "old.example", "port": 443}
        self._write_yaml(
            self.node_pool_file,
            {"node_count": 1, "nodes": {old_node["name"]: old_node}},
        )
        self._write_yaml(
            self.group_nodes_file,
            {"groups": {"Proxy": {"port": 7890, "controller": 9090, "nodes": [old_node["name"]]}},},
        )

    def test_runtime_apply_warning_keeps_update_visible(self):
        self._write_yaml(self.meta_file, {"subscriptions": {}})
        self._seed_derived_files()
        new_node = {"name": "新订阅 | 新节点", "type": "ss", "server": "new.example", "port": 443}

        def save_nodes(name, nodes):
            self._write_subscription_files(name, nodes)

        def sync_with_runtime_warning():
            self._write_yaml(self.node_pool_file, {"node_count": 1, "nodes": {new_node["name"]: new_node}})
            self._write_yaml(
                self.group_nodes_file,
                {"groups": {"Proxy": {"port": 7890, "controller": 9090, "nodes": []}}},
            )
            return "mihomo config check failed"

        with (
            patch.object(subscription_manager, "decode_subscription", return_value=[new_node]),
            patch.object(subscription_manager, "save_nodes", side_effect=save_nodes),
            patch.object(
                subscription_manager,
                "refresh_after_subscription_changed",
                side_effect=sync_with_runtime_warning,
            ),
        ):
            ok, warning = subscription_store.update_subscription_from_gui("新订阅", "https://example.test/sub")

        self.assertTrue(ok)
        self.assertIn("运行配置暂未应用", warning)
        self.assertTrue((self.subscriptions_dir / "新订阅_nodes.yaml").exists())
        self.assertTrue((self.subscriptions_dir / "新订阅_nodes.json").exists())
        self.assertEqual(subscription_store.list_subscriptions()[0]["name"], "新订阅")

    def test_persisted_state_sync_failure_still_rolls_back_update(self):
        self._write_yaml(self.meta_file, {"subscriptions": {}})
        self._seed_derived_files()
        before = {
            path: path.read_bytes()
            for path in (self.meta_file, self.node_pool_file, self.group_nodes_file)
        }
        node = {"name": "测试 | 节点", "type": "ss", "server": "node.example", "port": 443}

        def save_nodes(name, nodes):
            self._write_subscription_files(name, nodes)

        with (
            patch.object(subscription_manager, "decode_subscription", return_value=[node]),
            patch.object(subscription_manager, "save_nodes", side_effect=save_nodes),
            patch.object(
                subscription_manager,
                "refresh_after_subscription_changed",
                side_effect=OSError("node pool write failed"),
            ),
        ):
            ok, error = subscription_store.update_subscription_from_gui("测试", "https://example.test/sub")

        self.assertFalse(ok)
        self.assertIn("node pool write failed", error)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse((self.subscriptions_dir / "测试_nodes.yaml").exists())
        self.assertFalse((self.subscriptions_dir / "测试_nodes.json").exists())

    def test_success_commits_metadata_before_refresh_and_lists_subscription(self):
        self._write_yaml(self.meta_file, {"subscriptions": {}})
        self._seed_derived_files()
        node = {"name": "测试 | 节点", "type": "ss", "server": "node.example", "port": 443}

        def save_nodes(name, nodes):
            self._write_subscription_files(name, nodes)

        def refresh():
            entry = subscription_store.load_subscription_meta()["subscriptions"]["测试"]
            self.assertEqual(entry["url"], "https://example.test/sub")
            self._write_yaml(self.node_pool_file, {"node_count": 1, "nodes": {node["name"]: node}})

        with (
            patch.object(subscription_manager, "decode_subscription", return_value=[node]),
            patch.object(subscription_manager, "save_nodes", side_effect=save_nodes),
            patch.object(subscription_manager, "refresh_after_subscription_changed", side_effect=refresh),
        ):
            ok, error = subscription_store.update_subscription_from_gui("测试", "https://example.test/sub")

        self.assertTrue(ok)
        self.assertIsNone(error)
        rows = subscription_store.list_subscriptions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "测试")
        self.assertEqual(rows[0]["node_count"], 1)
        self.assertEqual(rows[0]["url"], "https://example.test/sub")

    def test_sanitized_filename_uses_sidecar_original_name_and_metadata(self):
        name = "My Subscription / A"
        url = "https://example.test/sub"
        node = {"name": "测试 | 节点", "type": "ss", "server": "node.example", "port": 443}
        self._write_subscription_files(name, [node])
        self._write_yaml(
            self.meta_file,
            {"subscriptions": {name: {"name": name, "url": url, "node_count": 1}}},
        )

        rows = subscription_store.list_subscriptions()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], name)
        self.assertEqual(rows[0]["url"], url)

        with patch.object(subscription_manager, "refresh_after_subscription_changed", return_value=None):
            ok, warning, _messages = subscription_store.delete_subscription_from_gui(name)

        self.assertTrue(ok)
        self.assertIsNone(warning)
        self.assertEqual(subscription_store.list_subscriptions(), [])

    def test_runtime_apply_warning_keeps_delete_visible(self):
        node = {"name": "测试 | 节点", "type": "ss", "server": "node.example", "port": 443}
        self._write_subscription_files("测试", [node])
        self._write_yaml(
            self.meta_file,
            {"subscriptions": {"测试": {"name": "测试", "url": "https://example.test/sub", "node_count": 1}}},
        )
        self._write_yaml(self.node_pool_file, {"node_count": 1, "nodes": {node["name"]: node}})
        self._write_yaml(
            self.group_nodes_file,
            {"groups": {"Proxy": {"port": 7890, "controller": 9090, "nodes": [node["name"]]}}},
        )
        def sync_with_runtime_warning():
            self._write_yaml(self.node_pool_file, {"node_count": 0, "nodes": {}})
            self._write_yaml(
                self.group_nodes_file,
                {"groups": {"Proxy": {"port": 7890, "controller": 9090, "nodes": []}}},
            )
            return "runtime apply failed"

        with patch.object(
            subscription_manager,
            "refresh_after_subscription_changed",
            side_effect=sync_with_runtime_warning,
        ):
            ok, warning, messages = subscription_store.delete_subscription_from_gui("测试")

        self.assertTrue(ok)
        self.assertIn("runtime apply failed", warning)
        self.assertEqual(len(messages), 2)
        self.assertEqual(subscription_store.list_subscriptions(), [])
        self.assertEqual(yaml.safe_load(self.node_pool_file.read_text(encoding="utf-8"))["nodes"], {})

    def test_missing_mihomo_warning_explains_that_data_was_saved(self):
        message = subscription_store._runtime_apply_warning(
            "订阅数据已保存",
            "mihomo executable not found: test.exe",
        )

        self.assertIn("订阅数据已保存", message)
        self.assertIn("未找到 mihomo 核心", message)

    def test_refresh_treats_runtime_apply_failure_as_warning(self):
        with (
            patch.object(subscription_manager, "rebuild_node_pool") as rebuild,
            patch.object(subscription_manager, "sync_group_nodes_with_node_pool") as sync,
            patch.object(
                config_apply,
                "apply_mihomo_config_change",
                side_effect=FileNotFoundError("mihomo executable not found"),
            ),
            patch.object(config_apply, "apply_core_config_change") as apply_core,
        ):
            warning = subscription_manager.refresh_after_subscription_changed()

        rebuild.assert_called_once_with()
        sync.assert_called_once_with()
        apply_core.assert_not_called()
        self.assertIn("mihomo executable not found", warning)


if __name__ == "__main__":
    unittest.main()
