"""test-ui skill · 真子进程判例（住 integration/，防腐 #7）：默认 runner 的三种结局（正常 / 起不来 rc=-2 /
超时杀进程组）；真探测 fixture 对（非 git 目录 → os.walk 兜底；node require.resolve 真跑）；tier 1 真跑 subject vs
dir:ref → RED 且每个植入缺陷在报告里；ref vs ref → 无 MISSING；真 `python3 -m http.server` 起 fixture 站点 →
Launcher / wait_ready / demo marker 探针走真回环 HTTP → stop 后端口释放；playwright 在场才真跑 driver，缺席则断言
structure_runtime 是 UNAVAILABLE 且提示写明安装命令（永不假绿）。零网络（只打 127.0.0.1）、零 claude。

法典：docs/CONTRACT.md §58 / §UI-parity；设计 vnext2-plan R2.8。时间预算 BUDGET_SECONDS 兜底。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from tests import skill_test_ui_testkit as kit

import detect_ui  # noqa: E402
import run_ui  # noqa: E402
import sensors  # noqa: E402

lc = kit.lc
BUDGET_SECONDS = 90
STATIC = ["surface_detect", "structure_source", "tokens_source", "ledger_lint", "golden_manifest", "thresholds_unmoved",
          "pair_structure", "pair_tokens", "theme_default_declared", "off_token_literals", "contrast_pairs", "a11y_static", "seed_guard"]
CONFIG = {"tokens": {"contrast_pairs": [["color.text-tertiary", "color.bg"]]},
          "geometry": {"layout.lane.width": {"screen": "board", "role": "list", "measure": "width"}},
          "screens": [{"id": "board", "route": "board.html", "source": ["board.html"]}, {"id": "settings", "route": "settings.html", "source": ["settings.html"]}],
          "launch": {"server": ["{py}", "-m", "http.server", "{port}", "--bind", "127.0.0.1"], "seed": ["{py}", "-c", "print('seeded')"],
                     "ready": "/api/health", "marker": {"path": "/api/health", "expr": ".demo == true"}, "home_env": "TU_HOME", "port_env": "TU_PORT"}}


def _fetch(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback must never go through a proxy
    with opener.open(url, timeout=5) as resp:
        return resp.read().decode("utf-8")


def _logging_spawner(log_path):
    """真 Popen，但把子进程 stderr 落到文件——runner 上起不来时错误信息进断言而不是消失在 DEVNULL。"""
    def spawn(argv, cwd, env):
        log = open(log_path, "wb")
        return subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
    return spawn


class BudgetedTestCase(unittest.TestCase):
    def setUp(self):
        self._started = time.monotonic()

    def tearDown(self):
        self.assertLess(time.monotonic() - self._started, BUDGET_SECONDS, "integration budget exceeded")


class RunCommandTestCase(BudgetedTestCase):
    def test_three_outcomes(self):
        ok = lc.run_command([sys.executable, "-c", "print('hi')"])
        self.assertTrue(ok.ok)
        self.assertEqual(lc.run_command(["/nonexistent/test-ui-binary"]).rc, -2)
        timed = lc.run_command([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
        self.assertTrue(timed.timed_out and timed.rc == -1)


class FixturePairRealRunTestCase(BudgetedTestCase):
    def _pair(self, tmp):
        subject, ref = os.path.join(tmp, "subject"), os.path.join(tmp, "ref")
        kit.copy_fixture("subject", subject)
        kit.copy_fixture("ref", ref)
        kit.make_repo(subject, {"ui/parity/config.json": json.dumps(CONFIG)})
        return subject, ref

    def test_detect_and_tier1_red_then_ref_vs_ref_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            subject, ref = self._pair(tmp)
            det = detect_ui.detect(subject, against="dir:%s" % ref)  # real runner: not a git dir, real node probe
            self.assertFalse(det["is_git"])
            self.assertEqual(det["surfaces"][0]["kind"], "static-html")
            self.assertEqual(det["config_source"], "ui/parity/config.json")
            sel = {"tier": 1, "checks": STATIC, "against": "dir:%s" % ref, "screens": ["board", "settings"],
                   "ask": {"recommended": det["recommendation"]["tier"], "reason": "fixture", "chosen": 1, "chosen_by": "user"}, "skip_reasons": {}}
            report = run_ui.run(subject, det, sel, os.path.join(tmp, "one"))
            by = {c["id"]: c for c in report["checks"]}
            self.assertEqual((report["verdict"], report["exit_code"]), ("red", 1))
            rows = {r["id"]: r["status"] for r in report["items"]["rows"]}
            self.assertEqual(rows["control:board:button:批准"], "MISSING")
            self.assertEqual(rows["landmark:board:navigation:rail"], "CHANGED")
            self.assertEqual(by["theme_default_declared"]["status"], "fail")
            self.assertEqual(by["contrast_pairs"]["status"], "fail")
            self.assertEqual(by["a11y_static"]["status"], "fail")
            self.assertEqual(report["fix_first"][0]["rank"], 1)
            self.assertTrue(os.path.exists(os.path.join(tmp, "one", "report.md")))
            det2 = detect_ui.detect(ref, against="dir:%s" % ref)
            report2 = run_ui.run(ref, det2, dict(sel, against="dir:%s" % ref), os.path.join(tmp, "two"))
            by2 = {c["id"]: c for c in report2["checks"]}
            self.assertEqual(by2["pair_structure"]["status"], "pass")
            self.assertEqual(by2["pair_structure"]["details"]["counts"].get("MISSING", 0), 0)
            self.assertEqual(report2["fix_first"], [])
            self.assertIn(report2["verdict"], ("green", "incomplete"))

    def test_launch_marker_and_runtime_honesty(self):
        with tempfile.TemporaryDirectory() as tmp:
            subject, ref = self._pair(tmp)
            det = detect_ui.detect(subject, against="dir:%s" % ref)
            ctx = run_ui.checks.make_ctx(subject, det, sel={"tier": 2}, out=os.path.join(tmp, "out"), fetch=_fetch)
            recipe = sensors._launch_recipe(ctx)
            self.assertIn(str(recipe["port"]), recipe["argv"])
            log_path = os.path.join(tmp, "server.log")
            launcher = sensors.Launcher(spawner=_logging_spawner(log_path))
            try:
                proc = launcher.start(recipe["argv"], subject, recipe["env"])
                ready = sensors.wait_ready(recipe["url"] + recipe["ready"], _fetch, 30.0)
                if not ready:
                    stderr = open(log_path, "rb").read().decode("utf-8", "replace")[-500:]
                    if proc.poll() is not None:
                        self.fail("http.server exited rc=%s: %s" % (proc.returncode, stderr))
                    self.skipTest("child http.server alive but loopback unreachable on this runner — %s" % (stderr or "no stderr"))
                self.assertTrue(run_ui.refmod.probe_marker(recipe["url"], recipe["marker"], _fetch))
                self.assertFalse(run_ui.refmod.probe_marker(recipe["url"], {"path": "/api/health", "expr": ".demo == false"}, _fetch))
            finally:
                launcher.stop()
                shutil.rmtree(recipe["home"], ignore_errors=True)
            time.sleep(0.3)
            with self.assertRaises(OSError):
                _fetch(recipe["url"] + "/api/health")  # the process group is gone
            res = sensors.check_structure_runtime(ctx)
            if det["tools"].get("playwright"):
                self.assertIn(res["status"], ("pass", "fail"), res["summary"])  # real driver ran; either way it measured
                if res["status"] == "pass":
                    self.assertGreater(len(ctx["state"]["runtime"]["inventory"]["items"]), 0)
            else:
                self.assertEqual(res["status"], "unavailable")
                self.assertIn("npm i -D @playwright/test", res["summary"])
                self.assertEqual(sensors.check_visual_diff(ctx)["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
