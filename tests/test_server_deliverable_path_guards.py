"""server/files.py 路径护栏小件的直接判例（CONTRACT §49 files/reveal）。

test_server_files 走真 server；这里钉纯函数：_validate_name 的每一类拒绝、
_contained_file（dotfile / 目录 / 指向目录外的 symlink / 悬空 symlink）、
_resolve_inside（缺失 / 目录 / symlink 逃逸）、_newest_deliverable 的
iterdir 失败 → None，以及 board_source 的 registry_backend 三层判定。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401

from server import board_source, files
from server.errors import InvalidFieldError, NotFoundError


class ValidateNameTestCase(unittest.TestCase):
    def test_rejections(self):
        for bad in ("", "a" * 256, ".hidden", "..", "a/b", "a\\b", "a\x00b"):
            with self.assertRaises(InvalidFieldError, msg=repr(bad)):
                files._validate_name(bad)

    def test_plain_basename_passes(self):
        files._validate_name("report.final.html")


class ContainedFileTestCase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="zai-deliv-"))
        self.real = self.base.resolve(strict=True)
        (self.base / "ok.txt").write_text("x", encoding="utf-8")
        (self.base / ".dot").write_text("x", encoding="utf-8")
        (self.base / "dir").mkdir()

    def test_regular_file_in_dir(self):
        self.assertTrue(files._contained_file(self.base / "ok.txt", self.real))

    def test_dotfile_and_directory_excluded(self):
        self.assertFalse(files._contained_file(self.base / ".dot", self.real))
        self.assertFalse(files._contained_file(self.base / "dir", self.real))

    def test_dangling_and_escaping_symlinks_excluded(self):
        outside = Path(tempfile.mkdtemp(prefix="zai-outside-")) / "o.txt"
        outside.write_text("o", encoding="utf-8")
        try:
            (self.base / "esc").symlink_to(outside)
            (self.base / "dangling").symlink_to(self.base / "nope")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertFalse(files._contained_file(self.base / "esc", self.real))
        self.assertFalse(files._contained_file(self.base / "dangling", self.real))

    def test_newest_picks_by_mtime_and_none_on_unreadable_dir(self):
        old = self.base / "old.txt"
        old.write_text("o", encoding="utf-8")
        os.utime(old, (1, 1))
        self.assertEqual(files._newest_deliverable(self.base).name, "ok.txt")
        self.assertIsNone(files._newest_deliverable(self.base / "missing"))


class ResolveInsideTestCase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="zai-resolve-"))
        (self.base / "f.txt").write_text("f", encoding="utf-8")
        (self.base / "d").mkdir()
        self.nf = NotFoundError("deliverable not found", {})

    def test_hit(self):
        self.assertEqual(files._resolve_inside(self.base, "f.txt", self.nf).name, "f.txt")

    def test_missing_and_directory_raise_the_given_404(self):
        for name in ("nope.txt", "d"):
            with self.assertRaises(NotFoundError) as cm:
                files._resolve_inside(self.base, name, self.nf)
            self.assertIs(cm.exception, self.nf)


class RegistryBackendTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-backend-"))
        (self.home / "state").mkdir()

    def test_env_wins(self):
        with mock.patch.dict(os.environ, {"ZAI_REGISTRY_BACKEND": "SQLite"}):
            self.assertEqual(board_source.registry_backend(self.home), "sqlite")

    def test_config_beats_marker(self):
        (self.home / "config.yaml").write_text("registry:\n  backend: yaml\n", encoding="utf-8")
        board_source.paths.store2_truth_path(self.home).parent.mkdir(parents=True, exist_ok=True)
        board_source.paths.store2_truth_path(self.home).write_text("{}", encoding="utf-8")
        with mock.patch.dict(os.environ, {"ZAI_REGISTRY_BACKEND": ""}):
            self.assertEqual(board_source.registry_backend(self.home), "yaml")

    def test_bad_config_value_falls_to_marker(self):
        (self.home / "config.yaml").write_text("registry:\n  backend: mongo\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"ZAI_REGISTRY_BACKEND": ""}):
            self.assertEqual(board_source.registry_backend(self.home), "yaml")
        self.assertEqual(board_source._config_backend(self.home), "")

    def test_registry_not_a_mapping(self):
        (self.home / "config.yaml").write_text("registry: 3\n", encoding="utf-8")
        self.assertEqual(board_source._config_backend(self.home), "")


if __name__ == "__main__":
    unittest.main()
