"""``POST /api/folders/open`` 落点不在时的祖先回落（CONTRACT §68.1 目录字段 / §68.4 追记 2026-09-05；
parity gap pages-shell-nav-reveal-vault-missing-no-parent-fallback）。

原生 Pages.swift ``.reveal``：``fileExists ? p : deletingLastPathComponent`` → 访达至少把上一级亮出来。
此前 server 对不是目录的落点回 404，依赖检查快速行的「显示」在 vault 还没建时是死路。自本条起：
落点不是目录 → 打开**最近的既有祖先**、回执 add-only ``opened`` + ``missing: true``；落点在 → 回执照旧、
两键不出现（老客户端零改动）；连根都不在才 404。落点本身由 ``open_target`` 决定（``obsidian_raw`` = vault 根，
§68.1 追记 (d)；其它键 = 存值），本文件用 ``default_target_repo`` 钉通用规则、用 ``obsidian_raw`` 钉两条规则的合成。
opener 注入，绝不真 open。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import start_server, write_text

from server import folders
from server.errors import NotFoundError


class OpenMissingFolderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-folders-missing-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        user_home = Path(self.tmp.name) / "user"
        user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(user_home), "USERPROFILE": str(user_home)})
        env.start()
        self.addCleanup(env.stop)
        _httpd, self.port = start_server(self, self.home)
        self.opened = []

    def _overrides(self, **kv):
        write_text(self.home / "state" / "settings_overrides.json", json.dumps(kv))

    def _open(self, key="default_target_repo"):
        return folders.open_folder(self.home, {"key": key}, opener=self.opened.append, platform="darwin")

    def test_existing_folder_receipt_has_no_missing_keys(self):
        target = Path(self.tmp.name) / "work" / "bench"
        target.mkdir(parents=True)
        self._overrides(default_target_repo=str(target))
        out = self._open()
        self.assertEqual(self.opened, [target])
        self.assertNotIn("missing", out)
        self.assertNotIn("opened", out)

    def test_missing_leaf_opens_the_parent_and_says_so(self):
        work = Path(self.tmp.name) / "work"
        work.mkdir()
        target = work / "bench"
        self._overrides(default_target_repo=str(target))
        out = self._open()
        self.assertEqual(self.opened, [work])
        self.assertEqual(out, {"ok": True, "key": "default_target_repo", "path": str(target),
                               "opened": str(work), "missing": True})

    def test_whole_missing_tree_climbs_to_the_nearest_existing_ancestor(self):
        # 原生只上一级（deletingLastPathComponent）——上一级也不在时访达无处可去；这里一路爬到还在的那层
        root = Path(self.tmp.name) / "docs"
        root.mkdir()
        target = root / "projects" / "bench"
        self._overrides(default_target_repo=str(target))
        out = self._open()
        self.assertEqual(self.opened, [root])
        self.assertEqual((out["opened"], out["missing"]), (str(root), True))

    def test_tilde_path_is_expanded_before_climbing(self):
        self._overrides(default_target_repo="~/Work/bench")
        out = self._open()
        self.assertEqual(self.opened, [Path.home()])
        self.assertTrue(out["missing"])

    def test_file_at_the_path_counts_as_not_a_directory(self):
        stray = Path(self.tmp.name) / "bench-file"
        write_text(stray, "not a folder\n")
        self._overrides(default_target_repo=str(stray))
        out = self._open()
        self.assertEqual(self.opened, [stray.parent])
        self.assertTrue(out["missing"])

    def test_vault_root_missing_composes_with_the_root_target(self):
        # obsidian_raw 的落点 = vault 根（§68.1 追记 (d)）；根不在 → 再往上爬；回执 path 仍是根、opened 是祖先
        docs = Path(self.tmp.name) / "Documents"
        docs.mkdir()
        raw = docs / "Obsidian Vault" / "2 - raw"
        self._overrides(obsidian_raw=str(raw))
        out = self._open("obsidian_raw")
        self.assertEqual(self.opened, [docs])
        self.assertEqual(out, {"ok": True, "key": "obsidian_raw", "path": str(raw.parent),
                               "opened": str(docs), "missing": True})

    def test_nearest_existing_ancestor_helper(self):
        here = Path(self.tmp.name)
        self.assertEqual(folders.nearest_existing_ancestor(here / "a" / "b" / "c"), here)
        self.assertEqual(folders.nearest_existing_ancestor(Path("/definitely-not-here-zai")), Path("/"))
        # 相对路径：parents 的尽头是 "."（server 的 cwd，永远 is_dir）——不是用户那棵树的祖先，不许当回落
        self.assertIsNone(folders.nearest_existing_ancestor(Path("Obsidian Vault")))
        self.assertIsNone(folders.nearest_existing_ancestor(Path("a") / "b" / "c"))

    def test_relative_stored_path_is_still_404_not_the_server_cwd(self):
        # 设置字段没有绝对路径校验，config.yaml 也能存相对值；绝不 /usr/bin/open . 然后说「已打开上级目录」
        self._overrides(default_target_repo="Obsidian Vault/2 - raw")
        with self.assertRaises(NotFoundError):
            self._open()
        self.assertEqual(self.opened, [])

    def test_relative_vault_path_is_404_after_root_resolution(self):
        # obsidian_raw 的落点 = 根 "Obsidian Vault"（仍相对）——同样没有祖先可开
        self._overrides(obsidian_raw="Obsidian Vault/2 - raw")
        with self.assertRaises(NotFoundError):
            self._open("obsidian_raw")
        self._overrides(obsidian_raw="2 - raw")    # 一段名：open_target 原样、仍相对
        with self.assertRaises(NotFoundError):
            self._open("obsidian_raw")
        self.assertEqual(self.opened, [])


if __name__ == "__main__":
    unittest.main()
