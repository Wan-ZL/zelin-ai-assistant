"""全覆盖跑者的八条 flow: 与临时 HOME 纪律（CONTRACT §58 QA 闸门；§77.7 沙箱纪律）。

判例（假 shell / 假 HTTP，绝不真跑 install.sh、不起 server、不联网）：
  - flow:install_fresh / flow:uninstall_reinstall：只认「exit 0 + plist 落在**临时**
    LaunchAgents」，并且跑在 PATH 前缀假货里（launchctl / open / osascript / crontab
    一个都不许是真的——真跑会写 owner 的 gui domain 与 crontab）；
  - flow:doctor_clean：退出码 = FAIL 条数，非 0 时 evidence 点名失败的检查；
  - flow:card_lifecycle：capture→approve→…→restore 十个动词逐个 200，再由一趟
    actd --once 推车道（车道没动 = MISSING，不许假装过）；
  - flow:settings_roundtrip_all 的单格逻辑（非默认值生成 + PUT/GET/复位）；
  - flow:recaps / flow:pwa / flow:pages_controls 的成败面；
  - 临时 HOME 只在 /tmp 且收尾删除（TempHomes）。
"""
import importlib.util
import json
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "coverage_run_flows_under_test", REPO / "scripts" / "qa" / "coverage_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cr = _load()


class ScriptedShell:
    """按调用顺序回放 (rc, out)，可带副作用（例如「装上了 plist」）。"""

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = []

    def run(self, cmd, cwd=None, env=None, timeout=None, log_name=None):
        self.calls.append({"cmd": list(cmd), "env": dict(env or {}), "log_name": log_name})
        if not self.replies:
            return cr.Proc(0, "")
        rc, out, effect = self.replies.pop(0)
        if effect is not None:
            effect(dict(env or {}))
        return cr.Proc(rc, out)


class ScriptedHttp:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def request(self, method, url, body=None, headers=None, timeout=None):
        payload = json.loads(body) if body else {}
        self.calls.append({"method": method, "url": url, "payload": payload,
                           "headers": dict(headers or {})})
        status, text = self.handler(method, url, payload)
        return cr.Resp(status, text)


class FakeTools:
    def __init__(self, python="/fake/python3", playwright=(None, None)):
        self.python = python
        self.pythonpath = "/fake/repo"
        self._playwright = playwright
        self._server = None

    def playwright_map(self, specs=()):
        return self._playwright

    def server(self):
        return self._server, None if self._server else "no server in this test"


# 本文件建过的每个临时 HOME 台账：tearDownModule 一并删（跑完 /tmp 不许留渣）
_HOMES = []


def temp_homes():
    homes = cr.TempHomes()
    _HOMES.append(homes)
    return homes


def temp_home(slug):
    return temp_homes().make(slug)


def tearDownModule():
    for homes in _HOMES:
        homes.cleanup()
    _HOMES.clear()


def ctx_for(shell=None, http=None, server=None, playwright=(None, None)):
    tools = FakeTools(playwright=playwright)
    tools._server = server
    return cr.FlowCtx(tools, shell, http, temp_homes(), Opts())


class Opts:
    def __init__(self, **kw):
        self.logdir = None
        self.skip_ax = False
        self.unittest_scope = "listed"
        self.unittest_listed_max = 40
        self.__dict__.update(kw)


def _plant_plist(env):
    """假 install.sh 的副作用：在**临时 HOME** 的 LaunchAgents 里放一个 plist。"""
    la = os.path.join(env["HOME"], "Library", "LaunchAgents")
    os.makedirs(la, exist_ok=True)
    Path(la, "com.zelin.aiassistant.actd.plist").write_text("<plist/>", encoding="utf-8")


def _wipe_plists(env):
    la = os.path.join(env["HOME"], "Library", "LaunchAgents")
    for name in os.listdir(la) if os.path.isdir(la) else []:
        os.unlink(os.path.join(la, name))


class TempHomeDisciplineTestCase(unittest.TestCase):
    def test_temp_homes_live_in_tmp_and_are_deleted(self):
        homes = cr.TempHomes()          # 这条判例自己验删除，不进 _HOMES 台账
        home = homes.make("probe")
        self.assertTrue(home.startswith("/tmp/"), home)
        self.assertTrue(os.path.isdir(home))
        homes.cleanup()
        self.assertFalse(os.path.exists(home))
        self.assertEqual(homes.paths, [])

    def test_shims_cover_every_world_touching_binary_and_are_executable(self):
        home = temp_home("shim")
        shim_dir, shim_log = cr.write_shims(home)
        for name in ("launchctl", "open", "osascript", "crontab", "claude", "pgrep", "pkill"):
            path = os.path.join(shim_dir, name)
            self.assertTrue(os.access(path, os.X_OK), name)
        # pgrep / pkill 恒「没找到」：install.sh 才不会去杀 + 重开 owner 正在跑的壳
        for name in ("pgrep", "pkill"):
            text = Path(shim_dir, name).read_text(encoding="utf-8")
            self.assertIn("exit 1", text, name)
            self.assertNotIn("exit 0", text, name)
        env = cr.shim_env(home, shim_dir, shim_log)
        self.assertEqual(env["HOME"], home)
        self.assertTrue(env["PATH"].startswith(shim_dir + ":"))
        # node / npm 的目录故意不在 PATH 上：install.sh 的 UI 步会自己跳过（快且无副作用）
        self.assertNotIn("/opt/homebrew/bin", env["PATH"])
        self.assertEqual(env["ZAA_SHIM_LOG"], shim_log)
        # 壳 bundle 的安装 / 删除都走临时 HOME 下的 Applications/，永不碰 /Applications
        self.assertEqual(env["AIASSISTANT_UI_APPS_DIR"], os.path.join(home, "Applications"))

    def test_claude_stub_prints_a_canned_result_and_never_runs_the_real_agent(self):
        home = temp_home("stub")
        shim_dir, _log = cr.write_shims(home)
        text = Path(shim_dir, "claude").read_text(encoding="utf-8")
        self.assertIn("qa coverage stub", text)
        self.assertNotIn("api.anthropic.com", text)


class InstallFlowTestCase(unittest.TestCase):
    def test_install_fresh_wants_exit_0_and_a_plist_under_the_temp_home(self):
        shell = ScriptedShell([(0, "install done", _plant_plist)])
        verdict = cr.flow_install_fresh(ctx_for(shell=shell))
        self.assertEqual(verdict.state, cr.PRESENT, verdict.reason)
        self.assertIn("com.zelin.aiassistant.actd.plist", verdict.evidence)
        cmd = shell.calls[0]["cmd"]
        self.assertEqual(cmd[:2], ["bash", str(REPO / "install.sh")])
        self.assertIn("--non-interactive", cmd)
        self.assertTrue(shell.calls[0]["env"]["HOME"].startswith("/tmp/"))

    def test_install_fresh_without_a_plist_is_missing(self):
        shell = ScriptedShell([(0, "install done", None)])
        verdict = cr.flow_install_fresh(ctx_for(shell=shell))
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("no plist under", verdict.reason)

    def test_install_fresh_reports_the_installer_exit_code(self):
        shell = ScriptedShell([(3, "boom: dependency missing", None)])
        verdict = cr.flow_install_fresh(ctx_for(shell=shell))
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("rc=3", verdict.reason)

    def test_uninstall_reinstall_fails_when_uninstall_leaves_a_plist(self):
        shell = ScriptedShell([(0, "install", _plant_plist), (0, "uninstall", None),
                               (0, "install again", _plant_plist)])
        verdict = cr.flow_uninstall_reinstall(ctx_for(shell=shell))
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("uninstall left", verdict.reason)

    def test_uninstall_reinstall_present_when_the_round_trip_holds(self):
        shell = ScriptedShell([(0, "install", _plant_plist), (0, "uninstall", _wipe_plists),
                               (0, "install again", _plant_plist)])
        verdict = cr.flow_uninstall_reinstall(ctx_for(shell=shell))
        self.assertEqual(verdict.state, cr.PRESENT, verdict.reason)
        verbs = [c["cmd"][1].split("/")[-1] for c in shell.calls]
        self.assertEqual(verbs, ["install.sh", "uninstall.sh", "install.sh"])
        self.assertIn("--yes", shell.calls[1]["cmd"])


class DoctorFlowTestCase(unittest.TestCase):
    def test_doctor_clean_is_present_only_at_zero_fails(self):
        payload = json.dumps({"checks": [{"name": "deps", "status": "ok"}]})
        shell = ScriptedShell([(0, "seeded", None), (0, payload, None)])
        verdict = cr.flow_doctor_clean(ctx_for(shell=shell))
        self.assertEqual(verdict.state, cr.PRESENT, verdict.reason)
        doctor = [c for c in shell.calls if "act.doctor" in " ".join(c["cmd"])][0]
        self.assertIn("--fast", doctor["cmd"])            # --fast = 不打模型探针（不联网、不用 key）
        self.assertTrue(doctor["env"]["AIASSISTANT_HOME"].startswith("/tmp/"))

    def test_doctor_fails_are_named_in_the_evidence(self):
        payload = json.dumps({"checks": [{"name": "cron ingest chain", "status": "fail"},
                                         {"name": "dashboard", "status": "fail"}]})
        shell = ScriptedShell([(0, "seeded", None), (2, payload, None)])
        verdict = cr.flow_doctor_clean(ctx_for(shell=shell))
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("cron ingest chain", verdict.evidence)
        self.assertIn("dashboard", verdict.evidence)


class CardLifecycleTestCase(unittest.TestCase):
    def _handler(self, board_lane_after="approved", action_status=200):
        state = {"moved": False}

        def handler(method, url, payload):
            if url.endswith("/api/board"):
                lane = board_lane_after if state["moved"] else "needs_approval"
                return 200, json.dumps({lane: [{"id": "P-101"}]})
            if url.endswith("/api/actions"):
                return action_status, json.dumps({"action": payload.get("action"), "id": "P-101"})
            return 404, "{}"

        return handler, state

    def test_every_verb_must_be_accepted_and_the_lane_must_move(self):
        handler, state = self._handler()
        http = ScriptedHttp(handler)
        home = temp_home("lifecycle")
        os.makedirs(os.path.join(home, "state", "inbox"), exist_ok=True)
        Path(home, "state", "inbox", "a.json").write_text("{}", encoding="utf-8")

        def actd(env):
            state["moved"] = True

        shell = ScriptedShell([(0, "actd once", actd)])
        server = cr.DemoServer("http://127.0.0.1:1", home, "tok", proc=None)
        verdict = cr.flow_card_lifecycle(ctx_for(shell=shell, http=http, server=server))
        self.assertEqual(verdict.state, cr.PRESENT, verdict.reason)
        verbs = [c["payload"].get("action") for c in http.calls if c["method"] == "POST"]
        self.assertEqual(verbs, ["capture", "approve", "comment", "stop_to_review", "accept",
                                 "archive", "unarchive", "trash", "restore", "reject"])
        self.assertIn("lane needs_approval→approved", verdict.evidence)
        self.assertIn("X-Zai-Token", http.calls[0]["headers"])

    def test_a_lane_that_never_moves_is_missing(self):
        handler, _state = self._handler()
        http = ScriptedHttp(handler)
        home = temp_home("lifecycle-stuck")
        os.makedirs(os.path.join(home, "state", "inbox"), exist_ok=True)
        Path(home, "state", "inbox", "a.json").write_text("{}", encoding="utf-8")
        shell = ScriptedShell([(0, "actd once", None)])
        server = cr.DemoServer("http://127.0.0.1:1", home, "tok", proc=None)
        verdict = cr.flow_card_lifecycle(ctx_for(shell=shell, http=http, server=server))
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("lane unchanged", verdict.reason)

    def test_a_rejected_verb_stops_the_flow_at_that_verb(self):
        handler, _state = self._handler(action_status=400)
        http = ScriptedHttp(handler)
        verdict = cr.flow_card_lifecycle(ctx_for(
            shell=ScriptedShell(), http=http,
            server=cr.DemoServer("http://127.0.0.1:1", "/tmp/zaa-cov-none", "tok", proc=None)))
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("capture -> 400", verdict.reason)


class SettingsRoundTripTestCase(unittest.TestCase):
    def test_non_default_value_per_kind(self):
        self.assertIs(cr.non_default_value({"kind": "bool", "default": True}), False)
        self.assertEqual(cr.non_default_value(
            {"kind": "enum", "default": "zh", "choices": ["zh", "en"]}), "en")
        self.assertEqual(cr.non_default_value({"kind": "int", "default": 0, "bounds": None}), 1)
        self.assertEqual(cr.non_default_value({"kind": "list", "default": []}),
                         ["qa-coverage-probe"])
        self.assertEqual(cr.non_default_value({"kind": "string", "default": "",
                                               "check": "email"}), "qa.probe@example.com")
        self.assertIsNone(cr.non_default_value({"kind": "string", "default": "",
                                                "check": "unknown-check-kind"}))

    def test_int_at_the_upper_bound_steps_down_instead_of_out_of_range(self):
        self.assertEqual(cr.non_default_value({"kind": "int", "default": 10, "bounds": (0, 10)}), 9)

    def test_round_trip_puts_the_default_back(self):
        writes = []

        def handler(method, url, payload):
            if method == "PUT":
                writes.append(payload)
                return 200, "{}"
            value = writes[-1]["language"] if writes else "zh"
            return 200, json.dumps({"fields": [{"key": "language", "effective": value}]})

        http = ScriptedHttp(handler)
        server = cr.DemoServer("http://127.0.0.1:1", "/tmp/zaa-cov-none", "tok", proc=None)
        field = {"key": "language", "kind": "enum", "default": "zh", "choices": ["zh", "en"]}
        verdict = cr.roundtrip_setting(http, server, "general", "language", field)
        self.assertEqual(verdict.state, cr.PRESENT, verdict.reason)
        self.assertEqual(writes, [{"language": "en"}, {"language": "zh"}])

    def test_effective_that_does_not_reflect_the_write_is_missing(self):
        def handler(method, url, payload):
            if method == "PUT":
                return 200, "{}"
            return 200, json.dumps({"fields": [{"key": "language", "effective": "zh"}]})

        http = ScriptedHttp(handler)
        server = cr.DemoServer("http://127.0.0.1:1", "/tmp/zaa-cov-none", "tok", proc=None)
        field = {"key": "language", "kind": "enum", "default": "zh", "choices": ["zh", "en"]}
        verdict = cr.roundtrip_setting(http, server, "general", "language", field)
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("!= written", verdict.reason)


class RecapsAndPwaTestCase(unittest.TestCase):
    def test_recaps_walks_generate_regenerate_revert_archive_and_history(self):
        seen = []

        def handler(method, url, payload):
            if url.endswith("/api/settings/recap"):
                seen.append(method + " settings")
                return 200, "{}"
            if url.endswith("/api/actions"):
                seen.append(payload.get("action"))
                return 200, "{}"
            if url.endswith("/api/recaps/mark"):
                seen.append("mark")
                return 200, "{}"
            if "/api/recaps/history" in url:
                return 200, json.dumps({"key": "k", "current": None, "entries": [],
                                        "history_cap": 5, "truncated": False})
            return 404, "{}"

        http = ScriptedHttp(handler)
        server = cr.DemoServer("http://127.0.0.1:1", "/tmp/zaa-cov-none", "tok", proc=None)
        verdict = cr.flow_recaps(ctx_for(shell=ScriptedShell(), http=http, server=server))
        self.assertEqual(verdict.state, cr.PRESENT, verdict.reason)
        self.assertEqual(seen.count("recap_generate"), 4)     # 生成 / 重生成 / 形状 / 意图问答
        self.assertIn("recap_revert", seen)
        self.assertIn("recap_slack_draft", seen)
        self.assertIn("mark", seen)

    def test_recap_meeting_key_has_the_contract_shape(self):
        keys = []

        def handler(method, url, payload):
            if payload.get("meeting_key"):
                keys.append(payload["meeting_key"])
            return 200, json.dumps({"entries": [], "history_cap": 5})

        http = ScriptedHttp(handler)
        server = cr.DemoServer("http://127.0.0.1:1", "/tmp/zaa-cov-none", "tok", proc=None)
        cr.flow_recaps(ctx_for(shell=ScriptedShell(), http=http, server=server))
        self.assertTrue(keys)
        for key in keys:
            self.assertRegex(key, r"^meeting:\d{4}-\d{2}-\d{2}T\d{4}-[a-z0-9-]{1,32}$")

    def test_pwa_needs_manifest_and_both_icons(self):
        if not os.path.exists(REPO / "web" / "dist" / "index.html"):
            self.skipTest("web/dist not built in this checkout — the 404 branch needs it")

        def handler(method, url, payload):
            if url.endswith("/icon-512.png"):
                return 404, ""
            if url.endswith(".webmanifest"):
                return 200, '{"icons": []}'
            return 200, "PNG\x00"

        http = ScriptedHttp(handler)
        server = cr.DemoServer("http://127.0.0.1:1", "/tmp/zaa-cov-none", "tok", proc=None)
        verdict = cr.flow_pwa(ctx_for(shell=ScriptedShell(), http=http, server=server))
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("/icon-512.png -> 404", verdict.reason)

    def test_pwa_without_a_build_says_so_instead_of_pretending(self):
        if os.path.exists(REPO / "web" / "dist" / "index.html"):
            self.skipTest("web/dist is built here — the absent-dist branch needs a bare checkout")
        verdict = cr.flow_pwa(ctx_for(
            shell=ScriptedShell(), http=ScriptedHttp(lambda *a: (200, "{}")),
            server=cr.DemoServer("http://127.0.0.1:1", "/tmp/zaa-cov-none", "tok", proc=None)))
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("npm run build", verdict.reason)


class PagesControlsTestCase(unittest.TestCase):
    def test_pages_controls_reads_the_playwright_map_for_the_coverage_spec(self):
        mapping = {(cr.PAGES_SPEC, "rail pages walk"): cr.Hit(True, "passed")}
        verdict = cr.flow_pages_controls(ctx_for(playwright=(mapping, None)))
        self.assertEqual(verdict.state, cr.PRESENT, verdict.reason)
        self.assertIn("1/1 tests passed", verdict.evidence)

    def test_a_failing_page_walk_is_missing(self):
        mapping = {(cr.PAGES_SPEC, "rail pages walk"): cr.Hit(False, "console error")}
        verdict = cr.flow_pages_controls(ctx_for(playwright=(mapping, None)))
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("rail pages walk", verdict.reason)

    def test_no_playwright_report_is_missing_with_the_toolchain_reason(self):
        verdict = cr.flow_pages_controls(ctx_for(playwright=({}, "web/node_modules absent")))
        self.assertEqual((verdict.state, verdict.reason), (cr.MISSING, "web/node_modules absent"))

    def test_every_flow_in_the_brief_is_wired(self):
        self.assertEqual(sorted(cr.FLOWS), sorted([
            "install_fresh", "doctor_clean", "card_lifecycle", "settings_roundtrip_all",
            "recaps", "pwa", "uninstall_reinstall", "pages_controls"]))


if __name__ == "__main__":
    unittest.main()
