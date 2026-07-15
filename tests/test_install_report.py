"""act/lib/install_report.py — the CONTRACT §23 writer install.sh calls.

Must (a) write valid JSON atomically at state/install_report.json with
version/timestamp/mode/user/steps/agents, (b) parse the shell-side
``name=status[:detail]`` line format including colons inside detail,
(c) keep the CLI non-fatal and stdin-driven exactly the way install.sh
pipes it. Everything runs under the sandbox AIASSISTANT_HOME.
"""
import datetime as _dt
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import __version__
from act.lib import install_report

REPO_ROOT = Path(__file__).resolve().parents[1]


class ParseStepsTestCase(unittest.TestCase):
    def test_parses_name_status_detail(self):
        steps = install_report.parse_steps(
            "config=ok:created from example\nlaunchd=fail:2 agent(s) failed to load\n")
        self.assertEqual(steps[0], {"name": "config", "status": "ok",
                                    "detail": "created from example"})
        self.assertEqual(steps[1]["status"], "fail")

    def test_detail_may_contain_colons_and_is_optional(self):
        steps = install_report.parse_steps(
            "runtime_python=ok:/usr/bin/python3\nstate_dirs=ok\n\n")
        self.assertEqual(steps[0]["detail"], "/usr/bin/python3")
        self.assertIsNone(steps[1]["detail"])
        self.assertEqual(len(steps), 2)

    def test_malformed_line_is_visible_not_dropped(self):
        steps = install_report.parse_steps("garbage without equals\n")
        self.assertEqual(steps[0]["status"], "fail")
        self.assertEqual(steps[0]["detail"], "unparsable step line")


class WriteReportTestCase(unittest.TestCase):
    def test_writes_report_with_version_timestamp_and_agents(self):
        path = install_report.write_report(
            mode="pkg-postinstall",
            steps=[{"name": "config", "status": "ok", "detail": None}],
            agents_loaded=["com.zelin.aiassistant.actd"],
            user="tester",
        )
        self.assertEqual(path, install_report.REPORT_PATH)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], __version__)
        self.assertEqual(data["mode"], "pkg-postinstall")
        self.assertEqual(data["user"], "tester")
        self.assertEqual(data["agents_loaded"], ["com.zelin.aiassistant.actd"])
        self.assertEqual(data["steps"][0]["name"], "config")
        # second-precision "…Z" timestamp, parseable by the Swift reader
        _dt.datetime.strptime(data["generated_at"], "%Y-%m-%dT%H:%M:%SZ")
        # atomic write leaves no .tmp behind
        self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_overwrites_previous_report(self):
        install_report.write_report("interactive", [], [], user="a")
        install_report.write_report("pkg-postinstall", [], [], user="b")
        data = json.loads(install_report.REPORT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(data["mode"], "pkg-postinstall")
        self.assertEqual(data["user"], "b")


class ConcurrentWriteTestCase(unittest.TestCase):
    """§23 原子写在多 writer 下的保证（夜间审计批次）：固定共享 tmp 名会让
    并发 writer 互抢——一方 os.replace 拿走对方的 tmp（FileNotFoundError 崩
    writer），另一方残余 fd 写已 rename 的 inode（读者见撕裂 JSON）。mkstemp
    唯一名后两个症状都必须消失（终端重跑 install.sh 与 in-app repair 可并发）。"""

    def test_concurrent_writers_no_crash_no_torn_reads(self):
        errors = []

        def spin(tag):
            try:
                for _ in range(25):
                    install_report.write_report(
                        "interactive",
                        [{"name": tag, "status": "ok", "detail": "x" * 20000}],
                        [], user=tag)
            except Exception as exc:  # noqa: BLE001 - the regression itself
                errors.append(exc)

        threads = [threading.Thread(target=spin, args=(f"w{i}",))
                   for i in range(3)]
        for t in threads:
            t.start()
        torn = 0
        while any(t.is_alive() for t in threads):
            if install_report.REPORT_PATH.exists():
                try:
                    json.loads(install_report.REPORT_PATH.read_text(
                        encoding="utf-8"))
                except ValueError:
                    torn += 1
        for t in threads:
            t.join()
        self.assertEqual(errors, [])   # 旧实现：必现 FileNotFoundError
        self.assertEqual(torn, 0)      # 读者任何时刻都读到完整 JSON
        # 最终文件完整、且没留下唯一名 tmp 残骸
        json.loads(install_report.REPORT_PATH.read_text(encoding="utf-8"))
        leftovers = [p for p in install_report.REPORT_PATH.parent.iterdir()
                     if p.name.startswith("install_report.json.")
                     and p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class CliTestCase(unittest.TestCase):
    """The exact shape install.sh calls: steps piped on stdin, labels in --agents."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="install-report-home-")
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *args, stdin=""):
        env = dict(os.environ, AIASSISTANT_HOME=str(self.home))
        return subprocess.run(
            [sys.executable, "-m", "act.lib.install_report", *args],
            cwd=REPO_ROOT, env=env, input=stdin,
            capture_output=True, text=True,
        )

    def test_cli_steps_stdin_writes_report(self):
        proc = self._run("--mode", "pkg-postinstall", "--steps-stdin",
                         "--agents", "com.zelin.aiassistant.actd com.zelin.aiassistant.radar",
                         stdin="config=ok:kept\nlaunchd=ok:2 agents loaded\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = self.home / "state" / "install_report.json"
        self.assertTrue(report.exists())
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(len(data["steps"]), 2)
        self.assertEqual(data["agents_loaded"],
                         ["com.zelin.aiassistant.actd", "com.zelin.aiassistant.radar"])

    def test_cli_requires_mode(self):
        proc = self._run("--steps-stdin", stdin="")
        self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
