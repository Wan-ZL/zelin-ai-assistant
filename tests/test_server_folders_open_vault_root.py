"""笔记库「打开」落到 vault 根（CONTRACT §68.1 追记「vault 根」；原生 Settings.swift:768 ``openInFinder(vaultRoot)``，
vaultRoot = effective ``obsidian_raw`` 的 ``deletingLastPathComponent``）：

- POST /api/folders/open {key:"obsidian_raw"} 开的是 raw 目录的**父目录**（web 框里显示的就是它），回执 path 也是根；
- 叶子不叫 ``2 - raw``（config.yaml 手工自定义）仍取父目录——原生 loadVault 同式，不看叶子；
- 根不存在 → 404（raw 更不会在）；raw 不在但根在 → 仍开根（原生 Open 按钮不看 vaultMissing）；
- 相对的一段名没有目录部分 → 没有根可开，原样按存值判；
- create 不变：仍 ``mkdir -p`` raw 目录本身（含根）；``default_target_repo`` 的打开仍是存值本身。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import post_json, start_server, write_text

from server import folders
from server.errors import NotFoundError


class OpenVaultRootTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-vault-open-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.user_home = Path(self.tmp.name) / "user"
        self.user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.user_home), "USERPROFILE": str(self.user_home)})
        env.start()
        self.addCleanup(env.stop)

    def _overrides(self, **kv):
        write_text(self.home / "state" / "settings_overrides.json", json.dumps(kv))

    def _open(self, key):
        opened = []
        out = folders.open_folder(self.home, {"key": key}, opener=opened.append, platform="darwin")
        return opened, out

    def test_open_reveals_the_vault_root_not_the_raw_dir(self):
        raw = Path(self.tmp.name) / "Vault" / "2 - raw"
        raw.mkdir(parents=True)
        self._overrides(obsidian_raw=str(raw))
        opened, out = self._open("obsidian_raw")
        self.assertEqual(opened, [raw.parent])
        self.assertEqual(out, {"ok": True, "key": "obsidian_raw", "path": str(raw.parent)})

    def test_tilde_root_is_expanded(self):
        (self.user_home / "Notes" / "2 - raw").mkdir(parents=True)
        self._overrides(obsidian_raw="~/Notes/2 - raw")
        opened, out = self._open("obsidian_raw")
        self.assertEqual(opened, [self.user_home / "Notes"])
        self.assertEqual(out["path"], str(self.user_home / "Notes"))

    def test_custom_leaf_still_opens_the_parent(self):
        raw = Path(self.tmp.name) / "Custom" / "inbox"
        raw.mkdir(parents=True)
        self._overrides(obsidian_raw=str(raw))
        opened, _out = self._open("obsidian_raw")
        self.assertEqual(opened, [raw.parent])

    def test_missing_raw_dir_still_opens_an_existing_root(self):
        root = Path(self.tmp.name) / "Vault"
        root.mkdir()
        self._overrides(obsidian_raw=str(root / "2 - raw"))
        opened, _out = self._open("obsidian_raw")
        self.assertEqual(opened, [root])

    def test_missing_root_is_404(self):
        self._overrides(obsidian_raw=str(Path(self.tmp.name) / "nowhere" / "2 - raw"))
        with self.assertRaises(NotFoundError) as ctx:
            self._open("obsidian_raw")
        self.assertEqual(ctx.exception.details["path"], str(Path(self.tmp.name) / "nowhere"))

    def test_bare_relative_name_has_no_root_to_open(self):
        self.assertEqual(folders.open_target("obsidian_raw", Path("2 - raw")), Path("2 - raw"))
        self.assertEqual(folders.open_target("obsidian_raw", Path("/2 - raw")), Path("/"))
        self.assertEqual(folders.open_target("default_target_repo", Path("/a/b")), Path("/a/b"))

    def test_create_still_makes_the_raw_dir_itself(self):
        raw = Path(self.tmp.name) / "Vault" / "2 - raw"
        self._overrides(obsidian_raw=str(raw))
        out = folders.create_folder(self.home, {"key": "obsidian_raw"}, runner=lambda argv: 0)
        self.assertTrue(raw.is_dir())
        self.assertEqual(out["path"], str(raw))

    def test_route_returns_the_root(self):
        raw = Path(self.tmp.name) / "Vault" / "2 - raw"
        raw.mkdir(parents=True)
        self._overrides(obsidian_raw=str(raw))
        _httpd, port = start_server(self, self.home)
        opened = []
        with mock.patch.object(folders, "_default_opener", opened.append), \
                mock.patch.object(folders.sys, "platform", "darwin"):
            status, obj = post_json(port, "/api/folders/open", {"key": "obsidian_raw"})
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj["path"], str(raw.parent))
        self.assertEqual(opened, [raw.parent])


if __name__ == "__main__":
    unittest.main()
