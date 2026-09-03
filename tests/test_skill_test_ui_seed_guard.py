"""test-ui skill · seed_guard / runtime 采集判例（全部注入：spawner / fetch / runner，零真进程零网络）：
runtime 不可用 → UNAVAILABLE 带提示；seed 拒绝 → FAIL 且不起 app；app 不 ready → FAIL；demo marker 不见 → FAIL
`seed_guard` 拒绝采集（driver 绝不被调用）；marker 在场 → driver 跑、bundle 落盘、runtime 产物带 seeded_by_skill；
driver rc≠0 → FAIL；parse_runtime 的 schema 形状与 focus walk idx→id；seed_guard 检查在有/无 runtime 产物时的判决；
Launcher 停止杀进程组；wait_ready 超时。

法典：docs/CONTRACT.md §62（demo seed 强制）、§45 精神（真实数据永不进图）；设计 vnext2-plan R2.8。
"""
import json
import os
import tempfile
import unittest

from tests import skill_test_ui_testkit as kit

import checks_ui as checks  # noqa: E402
import inventory_a11y as inv  # noqa: E402
import sensors  # noqa: E402

LAUNCH = {"server": ["{py}", "-m", "server"], "seed": ["{py}", "scripts/demo_seed.py", "{home}", "--scene", "{scene}"],
          "ready": "/api/health", "marker": {"path": "/api/health", "expr": ".demo == true"}, "home_env": "H", "port_env": "P"}
DRIVER_OUTPUT = {"tool": "playwright 1.0", "dims": {"themes": ["light"]},
                 "runs": [{"screen": "board", "scene": "initial", "theme": "light", "viewport": "desktop", "language": "zh", "flags": "default",
                           "emulation": "light", "lang": "zh", "observed_theme": "light",
                           "nodes": [{"idx": 0, "role": "button", "name": "批准", "name_source": "text", "text": "批准", "parent": "window>main:main",
                                      "order": 0, "visible": True, "hidden_by": None, "focusable": True, "bbox": [1, 2, 64, 28],
                                      "computed": {"color": "rgb(0,0,0)"}, "contrast": {"ratio": 12.0, "against": "#ffffffff", "large": False}},
                                     {"idx": 1, "role": "static", "name": "Alice's secret", "name_source": "text", "text": "Alice's secret",
                                      "parent": "window>main:main", "order": 1, "visible": True, "hidden_by": None, "focusable": False}],
                           "landmarks": [{"role": "main", "name": "", "parent": "window", "order": 0, "side": "inside", "bbox": [0, 0, 9, 9], "children": []}],
                           "focus_walk": [0], "overflow": {"scrollWidth": 900, "clientWidth": 900}, "tokens": {"--bg": "rgb(250, 251, 252)"},
                           "geometry": {"layout.lane.width": [320]}, "axe": [], "shot": "/tmp/x.png", "shot_sha256": "0", "masks": [], "masked_ratio": 0.0}]}


class FakeProc(object):
    def __init__(self):
        self.pid, self.killed = 4242, False

    def kill(self):
        self.killed = True


def _runtime_det(tmp):
    det = kit.fake_det(["board.html"], repo=tmp, tools={"node": "/bin/node", "playwright": "/w/pw", "axe": None, "npx": None, "odiff": None},
                       runtime_hint=None)
    det["sides"]["subject"] = kit.side(mode={"structure": "runtime", "tokens": "runtime", "visual": "runtime"}, launch=LAUNCH)
    return det


def _ctx(tmp, det, fetch, runner, spawner=None):
    ctx = checks.make_ctx(tmp, det, sel={"tier": 2}, out=os.path.join(tmp, "out"), runner=runner, spawner=spawner or (lambda a, c, e: FakeProc()),
                          fetch=fetch, ready_timeout=0.05)
    ctx["state"]["subject_source"] = kit.make_inventory([kit.make_item("board", "button", "批准")])
    return ctx


def _driver_runner(output):
    def respond(argv, cwd):
        cfg = json.load(open(argv[2]))
        kit.tc.write_text(cfg["out"], json.dumps(output))
        return kit.lc.RunResult(0, "", "")
    return kit.FakeRunner([("driver.cjs", respond), ("demo_seed.py", (0, "seeded", ""))])


class GuardTestCase(unittest.TestCase):
    def test_unavailable_without_runtime(self):
        res = sensors.check_structure_runtime(checks.make_ctx("/r", kit.fake_det(["b.html"])))
        self.assertEqual(res["status"], "unavailable")
        self.assertIn("playwright", res["summary"])

    def test_seed_refusal_aborts_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            spawned = []
            runner = kit.FakeRunner([("demo_seed.py", (1, "", "MISSING: state/dashboard.json"))])
            ctx = _ctx(tmp, _runtime_det(tmp), lambda url: "{}", runner, spawner=lambda a, c, e: spawned.append(a) or FakeProc())
            res = sensors.check_structure_runtime(ctx)
            self.assertEqual(res["status"], "fail")
            self.assertIn("seed refused", res["summary"])
            self.assertEqual(spawned, [])

    def test_marker_missing_refuses_capture(self):
        """负控制：/api/health 没有 demo: true → FAIL seed_guard，driver 一次都不跑。"""
        with tempfile.TemporaryDirectory() as tmp:
            runner = _driver_runner(DRIVER_OUTPUT)
            ctx = _ctx(tmp, _runtime_det(tmp), lambda url: '{"ok": true}', runner)
            res = sensors.check_structure_runtime(ctx)
            self.assertEqual(res["status"], "fail")
            self.assertIn("seed_guard", res["summary"])
            self.assertFalse(any("driver.cjs" in c for c in runner.commands()))
            self.assertFalse(ctx["state"]["launch"]["marker_seen"])
            self.assertEqual(sensors.check_app_launch(ctx)["status"], "fail")

    def test_not_ready_fails(self):
        def never(url):
            raise OSError("refused")
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _ctx(tmp, _runtime_det(tmp), never, _driver_runner(DRIVER_OUTPUT))
            res = sensors.check_structure_runtime(ctx)
            self.assertEqual(res["status"], "fail")
            self.assertIn("did not become ready", res["summary"])

    def test_capture_happy_path_and_downstream_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = FakeProc()
            det = _runtime_det(tmp)
            det["config"] = {"geometry": {"layout.lane.width": {"screen": "board", "role": "list", "measure": "width"}}}
            ctx = _ctx(tmp, det, lambda url: '{"demo": true}', _driver_runner(DRIVER_OUTPUT), spawner=lambda a, c, e: proc)
            ctx["state"]["subject_tokens"] = kit.make_tokens({"light": {"color.bg": "#fafbfc", "layout.lane.width": "400px"}})
            ctx["state"]["reference_tokens"] = kit.make_tokens({"light": {"color.bg": "#fafbfc", "layout.lane.width": "400px"}})
            ctx["state"]["reference_inventory"] = kit.make_inventory([kit.make_item("board", "button", "批准")], role="reference")
            res = sensors.check_structure_runtime(ctx)
            self.assertEqual(res["status"], "pass", res["summary"])
            bundle = ctx["state"]["runtime"]
            self.assertTrue(bundle["inventory"]["side"]["seed"]["seeded_by_skill"])
            self.assertTrue(ctx["state"]["launch"]["marker_seen"])
            names = {i["id"]: i["name"]["raw"] for i in bundle["inventory"]["items"]}
            self.assertEqual(names["control:board:button:批准"], "批准")
            self.assertEqual(names["control:board:static:alice-s-secret"], "{dynamic}")  # static-name filter
            self.assertEqual(bundle["inventory"]["focus_walk"]["board::light::desktop::zh::rest"], ["control:board:button:批准"])
            self.assertTrue(os.path.exists(os.path.join(tmp, "out", "inventory", "subject-runtime.json")))
            self.assertEqual(sensors.check_seed_guard(ctx)["status"], "pass")
            self.assertEqual(sensors.check_app_launch(ctx)["status"], "pass")
            self.assertEqual(sensors.check_pair_runtime(ctx)["status"], "pass")
            self.assertEqual(sensors.check_tokens_runtime(ctx)["status"], "pass")
            geometry = sensors.check_geometry_runtime(ctx)
            self.assertEqual(geometry["status"], "fail")
            self.assertIn("declared matches", geometry["summary"])
            self.assertEqual(sensors.check_theme_default_observed(ctx)["status"], "pass")
            self.assertEqual(sensors.check_a11y_rules(ctx)["status"], "substituted")  # axe absent
            self.assertEqual(sensors.check_screens_capture(ctx)["status"], "pass")
            self.assertEqual(sensors.check_keyboard_reach(ctx)["status"], "pass")
            self.assertEqual(sensors.check_focus_order(ctx)["status"], "pass")
            self.assertEqual(sensors.check_reflow(ctx)["status"], "pass")
            self.assertEqual(sensors.check_matrix_themes_viewports(ctx)["status"], "substituted")
            self.assertEqual(sensors.check_visual_diff(ctx)["status"], "unavailable")  # no goldens for this machine

    def test_driver_failure_and_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = kit.FakeRunner([("driver.cjs", (1, "", "browser crashed")), ("demo_seed.py", (0, "", ""))])
            ctx = _ctx(tmp, _runtime_det(tmp), lambda url: '{"demo": true}', runner)
            res = sensors.check_structure_runtime(ctx)
            self.assertEqual(res["status"], "fail")
            self.assertIn("driver failed", res["summary"])
            self.assertEqual(sensors.check_seed_guard(ctx)["status"], "pass")  # nothing captured → nothing to guard
        drift = sensors._token_drift({"light": {"color.bg": {"$type": "color", "$value": "#fafbfcff", "var": "--bg"}}},
                                     {"light": {"--bg": "rgb(0, 0, 0)"}})
        self.assertEqual(drift[0]["computed"], "#000000ff")
        self.assertEqual(sensors._token_drift({"light": {"color.bg": {"$type": "color", "$value": "#fafbfcff", "var": "--bg"}}},
                                              {"light": {"--bg": "rgb(250, 251, 252)"}}), [])

    def test_seed_guard_refuses_unseeded_artifacts(self):
        ctx = checks.make_ctx("/r", kit.fake_det(["b.html"]))
        ctx["state"]["runtime"] = {"inventory": kit.make_inventory([], side={"role": "subject", "seed": {"seeded_by_skill": False}})}
        self.assertEqual(sensors.check_seed_guard(ctx)["status"], "fail")


class LauncherTestCase(unittest.TestCase):
    def test_launcher_and_wait_ready(self):
        proc = FakeProc()
        launcher = sensors.Launcher(spawner=lambda argv, cwd, env: proc)
        launcher.start(["x"], "/r", {})
        launcher.stop()  # os.killpg on a fake pid fails → falls back to proc.kill()
        self.assertTrue(proc.killed)
        self.assertIsNone(launcher.proc)
        launcher.stop()  # idempotent
        clock = iter([0.0, 0.1, 0.2, 5.0])
        self.assertFalse(sensors.wait_ready("u", lambda url: (_ for _ in ()).throw(OSError()), 1.0, sleep=lambda s: None, clock=lambda: next(clock)))
        self.assertTrue(sensors.wait_ready("u", lambda url: "ok", 1.0))
        self.assertTrue(0 < sensors.free_port() < 65536)


class ParseRuntimeTestCase(unittest.TestCase):
    def test_parse_runtime_shape(self):
        bundle = inv.parse_runtime(DRIVER_OUTPUT, {"role": "subject", "kind": "url", "locator": "u"})
        inventory = bundle["inventory"]
        self.assertEqual(kit.tc.validate_inventory(inventory), [])
        self.assertEqual(inventory["producer"]["mode"], "runtime")
        item = {i["id"]: i for i in inventory["items"]}["control:board:button:批准"]
        self.assertEqual(item["states"]["light::desktop::zh::rest"]["bbox"], [1, 2, 64, 28])
        self.assertEqual(inventory["landmarks"][0]["id"], "landmark:board:main:main")
        self.assertEqual(inventory["shots"][0]["id"], "shot:board:initial:light:desktop:zh")
        self.assertEqual(bundle["tokens_observed"], {"light": {"--bg": "rgb(250, 251, 252)"}})
        self.assertEqual(bundle["geometry"], {"layout.lane.width": [320]})
        self.assertEqual(bundle["observed_theme"], {"light": "light"})
        self.assertEqual(inventory["dims"]["themes"], ["light"])

    def test_gated_merge(self):
        runs = [dict(DRIVER_OUTPUT["runs"][0], flags="all_on", nodes=DRIVER_OUTPUT["runs"][0]["nodes"] + [
            {"idx": 2, "role": "button", "name": "Captions", "parent": "window", "order": 2, "visible": True, "focusable": True}])]
        bundle = inv.parse_runtime({"runs": DRIVER_OUTPUT["runs"] + runs}, {"role": "subject"})
        gated = {i["id"]: i["gated"] for i in bundle["inventory"]["items"]}
        self.assertTrue(gated["control:board:button:captions"])
        self.assertFalse(gated["control:board:button:批准"])


if __name__ == "__main__":
    unittest.main()
