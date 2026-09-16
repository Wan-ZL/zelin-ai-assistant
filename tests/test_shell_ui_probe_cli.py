"""shell_ui_probe.py 的 CLI 契约：--list / --probe / --summary 的那一行与退出码（§58；§77.4 壳 UI 探针）。

goal 逐字规定 summary 末行 `SHELL probes=<k> present=<k>`（k = 真跑了的探针，
--summary 不带 --allow-enqueue 时是 4）；退出码 0 = 全 present、1 = 有 MISSING、
2 = 用法错、3 = BLOCKED。只读纪律（探针除 terminal_takeover 外只 GET）也钉在这里。
"""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts", "qa"))
import shell_ui_probe as probe  # noqa: E402

from tests.test_shell_ui_probe import AX_OK, FakeEnv, board, hotkey_out  # noqa: E402


def healthy_env(**overrides):
    """四个只读探针全 present 的假环境。"""
    menu = "\n".join([
        "about\tZelin's AI Assistant — 关于",
        "settings\tZelin's AI Assistant — 设置",
        "permissions\tZelin's AI Assistant — 权限体检",
        "restored\tZelin's AI Assistant — 任务台",
    ])
    osa = dict(AX_OK)
    osa["AXStatusLabel"] = probe.OsaResult(0, "badge:3")
    # 顺序有意义：菜单脚本收尾也按 ⌃⌥Space 还原看板页，"click menu item" 必须先匹配
    osa["click menu item"] = probe.OsaResult(0, menu)
    osa["key code"] = hotkey_out()
    osa.update(overrides.pop("osa", {}))
    http = {("GET", "/api/board"): board({"needs_approval": 3}),
            ("GET", "/api/health"): probe.HttpResult(200, "{}")}
    http.update(overrides.pop("http", {}))
    return FakeEnv(osa=osa, http=http,
                   ages={"state/shell.heartbeat": 1.2},
                   entries={"state/notify_queue": []}, **overrides)


def run(argv, env=None):
    buf = io.StringIO()
    code = probe.main(argv, env=env, out=buf)
    return code, buf.getvalue()


class ListTest(unittest.TestCase):
    def test_list_prints_every_probe_id(self):
        code, text = run(["--list"])
        self.assertEqual(code, probe.EXIT_OK)
        self.assertEqual(text.split(), list(probe.PROBE_IDS))

    def test_five_probe_ids_are_the_axprobe_vocabulary(self):
        self.assertEqual(probe.PROBE_IDS,
                         ("dock_badge", "hotkey_focus", "menu_open_page", "notify_relay",
                          "terminal_takeover"))


class SummaryTest(unittest.TestCase):
    def test_summary_runs_four_probes_and_prints_the_line(self):
        code, text = run(["--summary"], healthy_env())
        lines = text.strip().splitlines()
        self.assertEqual(lines[-1], "SHELL probes=4 present=4")
        self.assertEqual(code, probe.EXIT_OK)
        self.assertEqual([json.loads(ln)["probe"] for ln in lines[:-1]],
                         list(probe.SUMMARY_PROBES))

    def test_terminal_takeover_needs_allow_enqueue(self):
        env = healthy_env()
        run(["--summary"], env)
        self.assertNotIn(("POST", "/api/terminal", None),
                         [(m, p, None) for m, p, _b in env.http_calls])
        self.assertEqual([m for m, _p, _b in env.http_calls], ["GET"] * len(env.http_calls))

    def test_allow_enqueue_adds_the_fifth_probe(self):
        env = healthy_env(http={("POST", "/api/terminal"): probe.HttpResult(0, "", "refused")})
        _code, text = run(["--summary", "--allow-enqueue"], env)
        self.assertIn("terminal_takeover", text)
        self.assertRegex(text.strip().splitlines()[-1], r"^SHELL probes=5 present=\d$")

    def test_missing_probe_exits_one(self):
        env = healthy_env(osa={"AXStatusLabel": probe.OsaResult(0, "badge:9")})
        code, text = run(["--summary"], env)
        self.assertEqual(code, probe.EXIT_MISSING)
        self.assertEqual(text.strip().splitlines()[-1], "SHELL probes=4 present=3")

    def test_blocked_probe_exits_three(self):
        env = healthy_env(osa={probe.AX_ENABLED_SCRIPT: probe.OsaResult(0, "false")})
        code, text = run(["--summary"], env)
        self.assertEqual(code, probe.EXIT_BLOCKED)
        self.assertIn('"blocked": true', text)

    def test_summary_line_counts_executed_only(self):
        results = [{"probe": "a", "present": True}, {"probe": "b", "present": False},
                   {"probe": "c", "skipped": True}]
        self.assertEqual(probe.summary_line(results), "SHELL probes=2 present=1")


class SingleProbeTest(unittest.TestCase):
    def test_probe_prints_one_json_object_and_no_summary_line(self):
        code, text = run(["--probe", "dock_badge", "--json"], healthy_env())
        self.assertEqual(code, probe.EXIT_OK)
        self.assertEqual(len(text.strip().splitlines()), 1)
        self.assertEqual(json.loads(text)["probe"], "dock_badge")

    def test_unknown_probe_is_a_usage_error(self):
        code, text = run(["--probe", "nope"], healthy_env())
        self.assertEqual(code, probe.EXIT_USAGE)
        self.assertIn("unknown probe", text)

    def test_no_mode_prints_usage(self):
        code, text = run([], healthy_env())
        self.assertEqual(code, probe.EXIT_USAGE)
        self.assertIn("usage", text.lower())

    def test_single_probe_still_needs_allow_enqueue_for_the_write_probe(self):
        env = healthy_env()
        code, text = run(["--probe", "terminal_takeover"], env)
        self.assertEqual(code, probe.EXIT_OK)
        self.assertTrue(json.loads(text)["skipped"])
        self.assertEqual(env.http_calls, [])


class HomeResolutionTest(unittest.TestCase):
    """AIASSISTANT_HOME：显式 > env > 带 state/server.token 的候选（worktree 里跑
    时 live checkout 才是壳的 home）。"""

    def test_explicit_wins(self):
        self.assertEqual(probe.resolve_home("/tmp/zaa-home", env={"AIASSISTANT_HOME": "/other"}),
                         "/tmp/zaa-home")

    def test_env_wins_over_candidates(self):
        self.assertEqual(probe.resolve_home(None, env={"AIASSISTANT_HOME": "/tmp/zaa-env"}),
                         "/tmp/zaa-env")

    def test_worktree_parent_is_preferred_candidate(self):
        script = "/Volumes/X/repo/.claude/worktrees/qa-cov-shellprobe/scripts/qa/p.py"
        cands = probe.home_candidates(script)
        self.assertEqual(cands[0], "/Volumes/X/repo")
        self.assertIn("/Volumes/X/repo/.claude/worktrees/qa-cov-shellprobe", cands)

    def test_candidate_with_token_wins(self):
        script = "/Volumes/X/repo/.claude/worktrees/wt/scripts/qa/p.py"
        seen = []

        def exists(path):
            seen.append(path)
            return path == "/Volumes/X/repo/.claude/worktrees/wt/state/server.token"

        home = probe.resolve_home(None, env={}, script_path=script, exists=exists)
        self.assertEqual(home, "/Volumes/X/repo/.claude/worktrees/wt")
        self.assertTrue(seen[0].startswith("/Volumes/X/repo/state"))

    def test_plain_checkout_has_no_worktree_parent(self):
        self.assertIsNone(probe.worktree_parent("/Volumes/X/repo/scripts/qa/"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
