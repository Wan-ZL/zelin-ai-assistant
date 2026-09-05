"""server/ 设置页「后台雷达」行与目录字段按钮（CONTRACT §48.7 / §68.1）：

- GET /api/radars：每源 label / interval_s（读模板 StartInterval，不手抄）/ loaded（launchctl 注入）
  / plist_installed；非 darwin 的 loaded = null；
- POST /api/radars/reinstall {source}：= `bash install.sh --reinstall-agent <label>`（install_runner 注入，
  server 绝不写 plist）；退出 3 → 409（源关着）、4 → 409（未 pin 解释器）、其它非零 → 500 带尾巴；
  四闸 + 字段白名单 + 非 darwin 501；label 与 act/launchd 模板文件名逐字一致；
- settings 目录：obsidian_raw / default_target_repo / maintainer_repo_path 投影 add-only `path` + `path_exists`
  （空值 null、目录在 true、不在 false），其它字段不带这两键；
- POST /api/folders/open {key} / create {key}：路径由 server 从 effective 值读（客户端只传 key），
  空 400、open 不在 404、非 darwin 501、create 幂等 + default_target_repo 的 git init（runner 注入）、
  mkdir 失败 500 `could not create the folder`。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, get_json, post_json,
                                      start_server, write_text)

from server import folders, paths, radars, repair, settings_catalog
from server.errors import (ApiError, ConflictError, InvalidFieldError, NotFoundError,
                           NotImplementedError501, UnknownFieldError)

_WIN = sys.platform.startswith("win")


class _ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-radars-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        self.user_home = Path(self.tmp.name) / "user"
        self.user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.user_home), "USERPROFILE": str(self.user_home)})
        env.start()
        self.addCleanup(env.stop)
        _httpd, self.port = start_server(self, self.home)

    def _overrides(self, **kv):
        write_text(self.home / "state" / "settings_overrides.json", json.dumps(kv))


class RadarsSnapshotTestCase(_ServerCase):
    def test_labels_have_templates_and_intervals_come_from_them(self):
        for source, label in radars.RADAR_LABELS.items():
            with self.subTest(source=source):
                self.assertTrue(radars.template_path(label).is_file(), label)
                self.assertIsInstance(radars.interval_s(label), int)
        # truth = 模板：gmail 5 分钟、slack 3 分钟（原生「已安装，每 N 分钟自动运行」的 N）
        self.assertEqual(radars.interval_s(radars.RADAR_LABELS["gmail"]), 300)
        self.assertEqual(radars.interval_s(radars.RADAR_LABELS["slack"]), 180)
        self.assertIsNone(radars.interval_s("com.zelin.aiassistant.nosuch"))

    def test_snapshot_asks_launchd_per_label(self):
        calls = []

        def run(argv):
            calls.append(argv)
            return (0 if argv[-1].endswith("gmailradar") else 113), ""
        snap = radars.snapshot(self.home, runner=run, platform="darwin")
        gm, sl = snap["radars"]["gmail"], snap["radars"]["slack"]
        self.assertTrue(gm["loaded"])
        self.assertFalse(sl["loaded"])
        self.assertEqual(gm["label"], radars.RADAR_LABELS["gmail"])
        self.assertEqual([c[:2] for c in calls], [["/bin/launchctl", "print"]] * 2)
        self.assertFalse(gm["plist_installed"])
        la = self.user_home / "Library" / "LaunchAgents"
        la.mkdir(parents=True)
        (la / (radars.RADAR_LABELS["gmail"] + ".plist")).write_text("<plist/>", encoding="utf-8")
        self.assertTrue(radars.snapshot(self.home, runner=run, platform="darwin")["radars"]["gmail"]["plist_installed"])

    def test_non_darwin_reports_unknown_without_calling_launchctl(self):
        calls = []
        snap = radars.snapshot(self.home, runner=lambda argv: calls.append(argv) or (0, ""), platform="linux")
        self.assertEqual(calls, [])
        self.assertIsNone(snap["radars"]["gmail"]["loaded"])
        self.assertEqual(snap["radars"]["gmail"]["interval_s"], 300)

    def test_route_is_token_light_get(self):
        with mock.patch.object(repair, "default_runner", lambda argv: (0, "")), \
                mock.patch.object(radars.sys, "platform", "darwin"):
            status, obj = get_json(self.port, "/api/radars")
        self.assertEqual(status, 200)
        self.assertEqual(set(obj["radars"]), {"gmail", "slack"})
        self.assertTrue(obj["radars"]["slack"]["loaded"])


class RadarsReinstallTestCase(_ServerCase):
    def _install(self, rc=0, out="reinstalled"):
        calls = []

        def run(argv):
            calls.append(argv)
            return rc, out
        return run, calls

    def test_happy_path_runs_install_sh_reinstall_agent(self):
        install, calls = self._install()
        out = radars.reinstall(self.home, {"source": "slack"}, runner=lambda argv: (0, ""),
                               install_runner=install, platform="darwin")
        self.assertEqual(out, {"ok": True, "source": "slack", "label": radars.RADAR_LABELS["slack"], "loaded": True})
        self.assertEqual(calls, [["bash", str(paths.repo_root() / "install.sh"), "--reinstall-agent",
                                  radars.RADAR_LABELS["slack"]]])

    def test_switched_off_source_is_409(self):
        install, _calls = self._install(rc=3, out="switched off")
        with self.assertRaises(ConflictError) as ctx:
            radars.reinstall(self.home, {"source": "gmail"}, install_runner=install, platform="darwin")
        self.assertIn("switched off", ctx.exception.message)

    def test_unpinned_interpreter_is_409_pointing_at_install(self):
        install, _calls = self._install(rc=4, out="no pinned daemon interpreter")
        with self.assertRaises(ConflictError) as ctx:
            radars.reinstall(self.home, {"source": "gmail"}, install_runner=install, platform="darwin")
        self.assertEqual(ctx.exception.details["fix"], "bash install.sh")

    def test_other_failures_are_500_with_the_tail(self):
        install, _calls = self._install(rc=1, out="x" * 1000 + "  [ERR ] failed to load")
        with self.assertRaises(ApiError) as ctx:
            radars.reinstall(self.home, {"source": "gmail"}, install_runner=install, platform="darwin")
        self.assertIn("failed to load", ctx.exception.message)
        self.assertLess(len(ctx.exception.message), 600)
        self.assertEqual(ctx.exception.details["rc"], 1)

    def test_gates(self):
        install, calls = self._install()
        with self.assertRaises(UnknownFieldError):
            radars.reinstall(self.home, {"source": "gmail", "label": "x"}, install_runner=install, platform="darwin")
        with self.assertRaises(InvalidFieldError):
            radars.reinstall(self.home, {"source": "obsidian"}, install_runner=install, platform="darwin")
        with self.assertRaises(InvalidFieldError):
            radars.reinstall(self.home, {}, install_runner=install, platform="darwin")
        with self.assertRaises(NotImplementedError501):
            radars.reinstall(self.home, {"source": "gmail"}, install_runner=install, platform="linux")
        self.assertEqual(calls, [], "no gate may reach install.sh")

    def test_route_requires_write_gates_and_uses_injected_runners(self):
        install, calls = self._install()
        with mock.patch.object(radars, "_default_install_runner", install), \
                mock.patch.object(repair, "default_runner", lambda argv: (0, "")), \
                mock.patch.object(radars.sys, "platform", "darwin"):
            status, obj = post_json(self.port, "/api/radars/reinstall", {"source": "gmail"})
            self.assertEqual(status, 200, obj)
            self.assertTrue(obj["ok"])
            self.assertEqual(len(calls), 1)
            status, obj = post_json(self.port, "/api/radars/reinstall", {"source": "nope"})
            self.assertEqual(status, 400)
            assert_envelope(self, obj, "INVALID_FIELD")


class CatalogPathFieldsTestCase(_ServerCase):
    def _field(self, section, key):
        status, obj = get_json(self.port, "/api/settings/" + section)
        self.assertEqual(status, 200)
        return next(f for f in obj["fields"] if f["key"] == key)

    def test_path_fields_project_path_and_path_exists(self):
        missing = Path(self.tmp.name) / "nowhere"
        present = Path(self.tmp.name) / "vault" / "2 - raw"
        present.mkdir(parents=True)
        self._overrides(obsidian_raw=str(present), default_target_repo=str(missing))
        raw = self._field("obsidian", "obsidian_raw")
        self.assertEqual((raw["path"], raw["path_exists"]), ("dir", True))
        repo = self._field("approval", "default_target_repo")
        self.assertEqual((repo["path"], repo["path_exists"]), ("dir", False))
        maint = self._field("maintainer", "maintainer_repo_path")
        self.assertEqual((maint["path"], maint["path_exists"]), ("dir", None))   # 空值 = 无从判断
        gmail = self._field("gmail", "gmail_address")
        self.assertNotIn("path", gmail)
        self.assertNotIn("path_exists", gmail)

    def test_tilde_is_expanded(self):
        (self.user_home / "Notes").mkdir()
        self._overrides(obsidian_raw="~/Notes")
        self.assertTrue(self._field("obsidian", "obsidian_raw")["path_exists"])
        self.assertIsNone(settings_catalog.path_exists(""))
        self.assertIsNone(settings_catalog.path_exists(None))


class FoldersTestCase(_ServerCase):
    def test_open_uses_the_saved_effective_path(self):
        target = Path(self.tmp.name) / "work" / "bench"
        target.mkdir(parents=True)
        self._overrides(default_target_repo=str(target))
        opened = []
        out = folders.open_folder(self.home, {"key": "default_target_repo"}, opener=opened.append, platform="darwin")
        self.assertEqual(opened, [target])
        self.assertEqual(out, {"ok": True, "key": "default_target_repo", "path": str(target)})
        # 笔记库那一把键开的是 vault 根（raw 的父目录；§68.1 追记）——判例在 tests/test_server_folders_open_vault_root.py

    def test_open_gates(self):
        opened = []
        with self.assertRaises(UnknownFieldError):
            folders.open_folder(self.home, {"key": "obsidian_raw", "path": "/etc"}, opener=opened.append, platform="darwin")
        with self.assertRaises(InvalidFieldError):
            folders.open_folder(self.home, {"key": "gmail_address"}, opener=opened.append, platform="darwin")
        with self.assertRaises(NotImplementedError501):
            folders.open_folder(self.home, {"key": "obsidian_raw"}, opener=opened.append, platform="linux")
        # 空值（obsidian_raw 默认 ""）→ 400；不存在的目录 → 404
        with self.assertRaises(InvalidFieldError):
            folders.open_folder(self.home, {"key": "obsidian_raw"}, opener=opened.append, platform="darwin")
        self._overrides(default_target_repo=str(Path(self.tmp.name) / "nowhere"))
        with self.assertRaises(NotFoundError):
            folders.open_folder(self.home, {"key": "default_target_repo"}, opener=opened.append, platform="darwin")
        self.assertEqual(opened, [])

    def test_create_makes_the_folder_and_git_inits_the_workbench_only(self):
        vault = Path(self.tmp.name) / "vault" / "2 - raw"
        repo = Path(self.tmp.name) / "work" / "bench"
        self._overrides(obsidian_raw=str(vault), default_target_repo=str(repo))
        gits = []

        def run(argv):
            gits.append(argv)
            return 0
        out = folders.create_folder(self.home, {"key": "obsidian_raw"}, runner=run)
        self.assertTrue(vault.is_dir())
        self.assertEqual(out, {"ok": True, "key": "obsidian_raw", "path": str(vault), "created": True, "git_init": None})
        out = folders.create_folder(self.home, {"key": "default_target_repo"}, runner=run)
        self.assertTrue(repo.is_dir())
        self.assertEqual((out["created"], out["git_init"]), (True, "done"))
        self.assertEqual(gits, [["git", "-C", str(repo), "init", "-q"]])
        # 幂等：已在 = created:false；.git 已在 = 不再 init
        (repo / ".git").mkdir()
        out = folders.create_folder(self.home, {"key": "default_target_repo"}, runner=run)
        self.assertEqual((out["created"], out["git_init"]), (False, "skipped"))
        self.assertEqual(len(gits), 1)

    def test_create_reports_git_init_failure_without_failing(self):
        repo = Path(self.tmp.name) / "bench2"
        self._overrides(default_target_repo=str(repo))
        out = folders.create_folder(self.home, {"key": "default_target_repo"}, runner=lambda argv: 128)
        self.assertEqual((out["created"], out["git_init"]), (True, "failed"))

    @unittest.skipIf(_WIN, "POSIX file semantics")
    def test_mkdir_failure_is_500_with_the_native_sentence(self):
        blocker = Path(self.tmp.name) / "file"
        blocker.write_text("x", encoding="utf-8")
        self._overrides(obsidian_raw=str(blocker / "child"))
        with self.assertRaises(ApiError) as ctx:
            folders.create_folder(self.home, {"key": "obsidian_raw"}, runner=lambda argv: 0)
        self.assertTrue(ctx.exception.message.startswith("could not create the folder: "))

    def test_routes_are_write_gated(self):
        target = Path(self.tmp.name) / "v" / "2 - raw"
        self._overrides(obsidian_raw=str(target))
        with mock.patch.object(folders, "_default_runner", lambda argv: 0):
            status, obj = post_json(self.port, "/api/folders/create", {"key": "obsidian_raw"})
        self.assertEqual(status, 200, obj)
        self.assertTrue(target.is_dir())    # create 建的是 raw 目录本身（含根）
        opened = []
        with mock.patch.object(folders, "_default_opener", opened.append), \
                mock.patch.object(folders.sys, "platform", "darwin"):
            status, obj = post_json(self.port, "/api/folders/open", {"key": "obsidian_raw"})
        self.assertEqual(status, 200, obj)
        self.assertEqual(opened, [target.parent])    # 打开落到 vault 根（§68.1 追记）
        status, obj = post_json(self.port, "/api/folders/open", {"key": "obsidian_raw", "path": "/"})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")


if __name__ == "__main__":
    unittest.main()
