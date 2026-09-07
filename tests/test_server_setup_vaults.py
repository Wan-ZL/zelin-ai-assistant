"""``GET /api/setup/vaults``——向导第 5 步的 Obsidian 已注册库列表（CONTRACT §68.5 追记 D51 / §49）。

原生 SetupWizard.swift ``ObsidianVaults.registered`` 的 server 半边：读 obsidian.json 的 ``vaults`` 子树，
只回路径仍是目录的条目（按路径排序、去重），文件缺席 / 坏 JSON / 形状不对 / 超帽一律 ``[]``、永不 500；
路径经 ``registry`` 注入（判例不碰真 HOME），client 不能指定任何路径。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import get_json, start_server

from server import setup


def _write_registry(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc) if not isinstance(doc, str) else doc, encoding="utf-8")


class RegisteredVaultsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-vaults-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.registry = self.root / "obsidian.json"

    def test_missing_registry_is_an_empty_list_not_an_error(self):
        self.assertEqual(setup.registered_vaults(self.registry), [])
        self.assertEqual(setup.vaults_snapshot(self.registry), {"vaults": []})

    def test_corrupt_or_misshapen_registry_is_an_empty_list(self):
        _write_registry(self.registry, "{not json")
        self.assertEqual(setup.registered_vaults(self.registry), [])
        _write_registry(self.registry, ["a", "list"])                      # 顶层不是对象
        self.assertEqual(setup.registered_vaults(self.registry), [])
        _write_registry(self.registry, {"vaults": ["/tmp"]})                 # vaults 不是对象
        self.assertEqual(setup.registered_vaults(self.registry), [])
        _write_registry(self.registry, {"vaults": {"a": "str", "b": {"path": 3}, "c": {"path": ""}, "d": {}}})
        self.assertEqual(setup.registered_vaults(self.registry), [])

    def test_only_existing_directories_survive_sorted_and_deduped(self):
        beta = self.root / "Beta Vault"
        alpha = self.root / "alpha"
        beta.mkdir()
        alpha.mkdir()
        a_file = self.root / "notes.md"
        a_file.write_text("x", encoding="utf-8")
        _write_registry(self.registry, {"vaults": {
            "id1": {"path": str(beta), "ts": 1, "open": True},
            "id2": {"path": str(self.root / "gone")},                       # 不存在的目录被过滤
            "id3": {"path": str(a_file)},                                    # 文件不是库
            "id4": {"path": str(alpha) + "/"},                               # 结尾 / 归一
            "id5": {"path": str(alpha)},                                     # 同一路径登记两次 → 一条
        }})
        self.assertEqual(setup.registered_vaults(self.registry), [
            {"name": "Beta Vault", "path": str(beta)},
            {"name": "alpha", "path": str(alpha)},
        ])

    def test_oversized_registry_is_treated_as_corrupt(self):
        vault = self.root / "v"
        vault.mkdir()
        _write_registry(self.registry, {"vaults": {"a": {"path": str(vault)}}, "pad": "x" * (setup._VAULT_REGISTRY_MAX_BYTES + 1)})
        self.assertEqual(setup.registered_vaults(self.registry), [])

    def test_default_registry_path_is_obsidians_own_file_under_home(self):
        user_home = self.root / "user"
        with mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home)}):
            self.assertEqual(setup.obsidian_registry_path(),
                             user_home / "Library" / "Application Support" / "obsidian" / "obsidian.json")
        self.assertEqual(setup.obsidian_registry_path(user_home),
                         user_home / "Library" / "Application Support" / "obsidian" / "obsidian.json")
        # 默认路径缺席（沙箱 HOME 里没装 Obsidian）→ [] 而不是异常
        with mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home)}):
            self.assertEqual(setup.registered_vaults(), [])


class RouteTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-vaults-route-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    def test_route_is_token_light_and_serves_the_snapshot(self):
        vault = Path(self.tmp.name) / "My Vault"
        vault.mkdir()
        registry = Path(self.tmp.name) / "obsidian.json"
        _write_registry(registry, {"vaults": {"a": {"path": str(vault)}, "b": {"path": str(Path(self.tmp.name) / "nope")}}})
        with mock.patch.object(setup, "obsidian_registry_path", return_value=registry):
            status, obj = get_json(self.port, "/api/setup/vaults")
        self.assertEqual(status, 200)
        self.assertEqual(obj, {"vaults": [{"name": "My Vault", "path": str(vault)}]})

    def test_route_ignores_any_client_supplied_path(self):
        # query 里塞路径不会改读的文件：默认 registry 在沙箱 HOME 下缺席 → []
        with mock.patch.object(setup, "obsidian_registry_path", return_value=Path(self.tmp.name) / "absent.json"):
            status, obj = get_json(self.port, "/api/setup/vaults?registry=/etc/passwd&path=/etc")
        self.assertEqual(status, 200)
        self.assertEqual(obj, {"vaults": []})


if __name__ == "__main__":
    unittest.main()
