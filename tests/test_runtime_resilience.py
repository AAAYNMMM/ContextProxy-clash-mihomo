import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from backend import group_health, mihomo_paths, port_manager


class RuntimePortAllocationTests(unittest.TestCase):
    def test_only_group_ports_are_reassigned_and_persisted(self):
        settings = {
            "proxy": {"listen_host": "127.0.0.1", "listen_port": 18000, "receiver_port": 17890},
            "mihomo": {"exe": "", "mixed_port": 7899, "controller_port": 9090},
            "latency_test": {"timeout_ms": 5000, "test_url": "https://example.test/health"},
            "ui": {},
            "logging": {},
        }
        groups = {
            "groups": {
                "Proxy": {"port": 7890, "controller": 9090, "nodes": []},
                "AI": {"port": 7891, "controller": 9091, "nodes": []},
            }
        }
        occupied = {7890, 7891}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings_file = root / "app_settings.yaml"
            groups_file = root / "group_nodes.yaml"
            settings_file.write_text(yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8")
            groups_file.write_text(yaml.safe_dump(groups, allow_unicode=True, sort_keys=False), encoding="utf-8")

            with (
                patch.object(port_manager, "GROUP_NODES_FILE", groups_file),
                patch.object(port_manager, "load_app_settings", return_value=copy.deepcopy(settings)),
                patch.object(port_manager, "is_port_available", side_effect=lambda port: port not in occupied),
                patch("backend.runtime_config.reload_group_config"),
            ):
                ok, error, changes = port_manager.prepare_mihomo_runtime_ports(
                    write_settings=True,
                    check_system=True,
                )

            self.assertTrue(ok)
            self.assertIsNone(error)
            self.assertGreaterEqual(len(changes), 4)

            saved_settings = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
            saved_groups = yaml.safe_load(groups_file.read_text(encoding="utf-8"))
            self.assertEqual(saved_settings, settings)
            self.assertEqual(saved_groups["groups"]["Proxy"]["port"], 7892)
            self.assertEqual(saved_groups["groups"]["AI"]["port"], 7893)
            self.assertNotIn("controller", saved_groups["groups"]["Proxy"])
            self.assertNotIn("controller", saved_groups["groups"]["AI"])

    def test_fixed_extension_port_conflict_is_reported_without_reassignment(self):
        settings = {
            "proxy": {"listen_host": "127.0.0.1", "listen_port": 18000, "receiver_port": 17890},
            "mihomo": {"exe": "", "mixed_port": 7899, "controller_port": 9090},
            "latency_test": {"timeout_ms": 5000, "test_url": "https://example.test/health"},
            "ui": {},
            "logging": {},
        }
        groups = {"groups": {"Proxy": {"port": 7890, "nodes": []}}}

        with tempfile.TemporaryDirectory() as temp_dir:
            groups_file = Path(temp_dir) / "group_nodes.yaml"
            groups_file.write_text(yaml.safe_dump(groups, allow_unicode=True, sort_keys=False), encoding="utf-8")

            with (
                patch.object(port_manager, "GROUP_NODES_FILE", groups_file),
                patch.object(port_manager, "load_app_settings", return_value=copy.deepcopy(settings)),
                patch.object(port_manager, "is_port_available", side_effect=lambda port: port != 17890),
            ):
                ok, error, changes = port_manager.prepare_mihomo_runtime_ports(
                    write_settings=True,
                    check_system=True,
                )

            self.assertFalse(ok)
            self.assertIn("Tab 上报接收端口 17890 被占用", error)
            self.assertEqual(changes, [])
            self.assertEqual(
                yaml.safe_load(groups_file.read_text(encoding="utf-8")),
                groups,
            )

    def test_group_port_conflicting_with_extension_port_is_reassigned(self):
        settings = {
            "proxy": {"listen_host": "127.0.0.1", "listen_port": 18000, "receiver_port": 17890},
            "mihomo": {"exe": "", "mixed_port": 7899, "controller_port": 9090},
            "latency_test": {"timeout_ms": 5000, "test_url": "https://example.test/health"},
            "ui": {},
            "logging": {},
        }
        groups = {"groups": {"Proxy": {"port": 17890, "nodes": []}}}

        with tempfile.TemporaryDirectory() as temp_dir:
            groups_file = Path(temp_dir) / "group_nodes.yaml"
            groups_file.write_text(yaml.safe_dump(groups, allow_unicode=True, sort_keys=False), encoding="utf-8")

            with (
                patch.object(port_manager, "GROUP_NODES_FILE", groups_file),
                patch.object(port_manager, "load_app_settings", return_value=copy.deepcopy(settings)),
                patch.object(port_manager, "is_port_available", return_value=True),
                patch("backend.runtime_config.reload_group_config"),
            ):
                ok, error, changes = port_manager.prepare_mihomo_runtime_ports(
                    write_settings=True,
                    check_system=True,
                )

            self.assertTrue(ok)
            self.assertIsNone(error)
            self.assertEqual(changes, ["分组 Proxy 端口 17890 -> 17891"])
            saved_groups = yaml.safe_load(groups_file.read_text(encoding="utf-8"))
            self.assertEqual(saved_groups["groups"]["Proxy"]["port"], 17891)

    def test_group_port_allocator_wraps_after_upper_bound(self):
        with patch.object(port_manager, "is_port_available", return_value=True):
            port = port_manager.find_available_port(65535, {65535}, check_system=True)

        self.assertEqual(port, 1)


class MihomoPathResolutionTests(unittest.TestCase):
    def test_selected_absolute_path_is_not_forced_under_mihomo_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            selected = root / "custom-mihomo.exe"
            selected.write_bytes(b"test")

            with patch.object(mihomo_paths, "MIHOMO_DIR", str(root / "mihomo")):
                self.assertEqual(mihomo_paths.resolve_mihomo_exe_path(selected), selected)
                self.assertEqual(
                    mihomo_paths.resolve_mihomo_exe_path("legacy.exe"),
                    root / "mihomo" / "legacy.exe",
                )


class ImmediateNodeRecoveryTests(unittest.TestCase):
    def setUp(self):
        group_health._traffic_windows.clear()
        group_health._consecutive_failures.clear()
        group_health._last_recovery_at.clear()
        group_health._healing_groups.clear()
        group_health._recovery_tasks.clear()

    def test_first_high_confidence_failure_requires_recovery(self):
        should_recover, immediate = group_health.should_recover_from_core_metrics(
            recent_total=1,
            recent_fail_count=1,
            consecutive_failures=1,
            last_reason="no_response",
        )

        self.assertTrue(should_recover)
        self.assertTrue(immediate)

    def test_first_infrastructure_failure_does_not_mark_node_bad(self):
        should_recover, immediate = group_health.should_recover_from_core_metrics(
            recent_total=1,
            recent_fail_count=1,
            consecutive_failures=1,
            last_reason="listener_connect_fail",
        )

        self.assertFalse(should_recover)
        self.assertFalse(immediate)

    def test_first_high_confidence_failure_schedules_switch(self):
        with patch.object(group_health, "_schedule_group_recovery") as schedule:
            group_health.record_proxy_connection_result("Proxy", False, "quick_close_low_bytes")

        schedule.assert_called_once_with("Proxy", "traffic:quick_close_low_bytes")

    def test_immediate_failures_still_respect_recovery_cooldown(self):
        async def scenario():
            calls = []

            async def recover(group_name, reason):
                calls.append((group_name, reason))
                group_health._healing_groups.discard(group_name)

            with patch.object(group_health, "recover_group", side_effect=recover):
                group_health._schedule_group_recovery("Proxy", "traffic:no_response")
                await asyncio.sleep(0)
                group_health._schedule_group_recovery("Proxy", "traffic:no_response")
                await asyncio.sleep(0)

            return calls

        self.assertEqual(
            asyncio.run(scenario()),
            [("Proxy", "traffic:no_response")],
        )


if __name__ == "__main__":
    unittest.main()
