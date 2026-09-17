"""scripts/qa/shell_ui_probe.py 的五个探针各自的判定（CONTRACT §58 覆盖证据 `axprobe:`）。

被探行为的法条：Dock 徽章 §15/§54.1、⌃⌥Space 快速捕获 §68.13、菜单 open_page
§54.4/§61.6、§28 通知中继、§68.7 终端接管。这里**只注入假 osascript + 假 HTTP**：
不碰真 AX、不联网、不起子进程——真机跑是 --summary 的事。
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts", "qa"))
import shell_ui_probe as probe  # noqa: E402


class FakeEnv:
    """LiveEnv 的注入替身：osascript 按「脚本里出现的关键词」回放，HTTP 查表。"""

    def __init__(self, osa=None, http=None, ages=None, entries=None, pgrep="58743", ls=None):
        self._ls = ls or probe.OsaResult(127, "", "lsappinfo absent")
        self._osa = osa or {}
        self._http = http or {}
        self._ages = ages or {}
        self._entries = entries or {}
        self._pgrep = pgrep
        self.osa_calls = []
        self.http_calls = []

    # --- 注入缝 ---------------------------------------------------------
    def osascript(self, script):
        self.osa_calls.append(script)
        for needle, result in self._osa.items():
            if needle in script:
                return result
        return probe.OsaResult(0, "")

    def http(self, method, path, payload=None):
        self.http_calls.append((method, path, payload))
        return self._http.get((method, path), probe.HttpResult(404, '{"error": {"message": "not found"}}'))

    def age_s(self, *parts):
        return self._ages.get("/".join(parts))

    def entries(self, *parts):
        return list(self._entries.get("/".join(parts), []))

    def entry_age_s(self, name, *parts):
        return self.age_s(*(list(parts) + [name]))

    def pgrep(self, name):
        return self._pgrep

    def lsappinfo(self, bundle_id=None):
        return self._ls


AX_OK = {probe.AX_ENABLED_SCRIPT: probe.OsaResult(0, "true")}


def board(counts):
    return probe.HttpResult(200, json.dumps({"counts": counts}))


def hotkey_out(prev="Google Chrome", front=probe.APP_PROCESS, role="AXTextArea",
               place=probe.COMPOSER_PLACEHOLDERS[0], desc="快速捕获（⌘L · ⌃⌥Space）",
               title="Zelin's AI Assistant — 任务台"):
    return probe.OsaResult(0, "\t".join([prev, front, role, place, desc, title]))


class AccessibilityGateTest(unittest.TestCase):
    """辅助功能没授权 = BLOCKED + owner_action，绝不绕行（goal 硬约束）。"""

    def test_ui_elements_disabled_blocks(self):
        env = FakeEnv(osa={probe.AX_ENABLED_SCRIPT: probe.OsaResult(0, "false")})
        out = probe.run_probe(env, "dock_badge")
        self.assertTrue(out["blocked"])
        self.assertIn("UI elements enabled = false", out["error"])
        self.assertEqual(out["owner_action"], probe.OWNER_ACTION)
        self.assertEqual(probe.classify(out), "BLOCKED")

    def test_ax_error_code_blocks_verbatim(self):
        err = "System Events got an error: osascript is not allowed assistive access. (-25211)"
        env = FakeEnv(osa={probe.AX_ENABLED_SCRIPT: probe.OsaResult(1, "", err)})
        out = probe.run_probe(env, "hotkey_focus")
        self.assertTrue(out["blocked"])
        self.assertEqual(out["error"], err)

    def test_ax_denied_mid_probe_blocks(self):
        env = FakeEnv(osa=dict(AX_OK, **{
            "AXStatusLabel": probe.OsaResult(1, "", "AX API disabled (-1719)")}))
        out = probe.run_probe(env, "dock_badge")
        self.assertTrue(out["blocked"])
        self.assertIn("-1719", out["error"])

    def test_shell_not_running_blocks_with_pgrep_output(self):
        env = FakeEnv(osa=AX_OK, pgrep="")
        out = probe.run_probe(env, "menu_open_page")
        self.assertTrue(out["blocked"])
        self.assertIn("pgrep -x %s -> <empty>" % probe.APP_PROCESS, out["error"])


class DockBadgeProbeTest(unittest.TestCase):
    """§15 v0.46 ②：徽章 = 提案 + 需输入 + 待验收（web pushBadge → 壳 DockBadge）。
    LaunchServices 拿不到时回落 AX（下面这些用例全部走回落路，行为与之前一致）。"""

    def setUp(self):
        probe.BADGE_SETTLE_DELAY = 0

    def _env(self, badge_out, board_res):
        return FakeEnv(osa=dict(AX_OK, **{"AXStatusLabel": probe.OsaResult(0, badge_out)}),
                       http={("GET", "/api/board"): board_res})

    def test_badge_matches_board_counts(self):
        out = probe.run_probe(self._env("badge:5", board(
            {"needs_approval": 2, "needs_input": 1, "review": 2})), "dock_badge")
        self.assertTrue(out["present"])
        self.assertEqual((out["badge"], out["expected"]), (5, 5))

    def test_lane_lengths_when_counts_absent(self):
        res = probe.HttpResult(200, json.dumps(
            {"needs_approval": [{"id": "R-1"}], "needs_input": [], "review": [{"id": "R-2"}]}))
        out = probe.run_probe(self._env("badge:2", res), "dock_badge")
        self.assertTrue(out["present"])
        self.assertEqual(out["expected"], 2)

    def test_no_badge_means_zero(self):
        out = probe.run_probe(self._env("badge:none", board({})), "dock_badge")
        self.assertTrue(out["present"])
        self.assertEqual(out["badge"], 0)

    def test_mismatch_is_missing_with_reason(self):
        out = probe.run_probe(self._env("badge:1", board({"needs_approval": 3})), "dock_badge")
        self.assertFalse(out["present"])
        self.assertIn("Dock badge 1 != board count 3", out["reason"])
        self.assertEqual(probe.classify(out), "MISSING")

    def test_board_unavailable_expects_no_badge(self):
        res = probe.HttpResult(404, json.dumps(
            {"error": {"message": "dashboard.json not found", "details": {"path": "x"}}}))
        out = probe.run_probe(self._env("badge:none", res), "dock_badge")
        self.assertTrue(out["present"])
        self.assertEqual(out["board_status"], 404)
        self.assertIn("board unavailable", out["note"])

    def test_dock_tile_absent_is_missing(self):
        out = probe.run_probe(self._env("error:Can’t get UI element", board({})), "dock_badge")
        self.assertFalse(out["present"])
        self.assertIn("Dock tile not readable", out["reason"])


class HotkeyFocusProbeTest(unittest.TestCase):
    """§68.13 ⌃⌥Space → 提案列 composer 拿到焦点，收尾还原原前台 app。"""

    def _env(self, osa_out, http=None):
        return FakeEnv(osa=dict(AX_OK, **{"key code": osa_out}), http=http or {})

    def test_composer_focused_is_present(self):
        env = self._env(hotkey_out())
        out = probe.run_probe(env, "hotkey_focus")
        self.assertTrue(out["present"])
        self.assertEqual(out["restored_frontmost"], "Google Chrome")
        self.assertEqual(out["placeholder"], probe.COMPOSER_PLACEHOLDERS[0])

    def test_english_placeholder_also_counts(self):
        out = probe.run_probe(self._env(hotkey_out(place=probe.COMPOSER_PLACEHOLDERS[1])),
                              "hotkey_focus")
        self.assertTrue(out["present"])

    def test_script_sends_control_option_space_and_restores(self):
        script = probe.hotkey_focus_script()
        self.assertIn("key code %d using {control down, option down}" % probe.HOTKEY_KEYCODE,
                      script)
        self.assertIn("tell application process prevApp to set frontmost to true", script)

    def test_shell_not_frontmost_is_missing(self):
        """壳没被前置、又没读到窗口标题 = 真 MISSING（不推给前置条件）。"""
        out = probe.run_probe(self._env(hotkey_out(front="Google Chrome", role="none", place="",
                                                   title="nowindow")),
                              "hotkey_focus")
        self.assertFalse(out.get("blocked"))
        self.assertFalse(out["present"])
        self.assertIn("frontmost='Google Chrome'", out["reason"])

    def test_wrong_field_is_missing_not_blocked(self):
        env = self._env(hotkey_out(role="AXTextArea", place="搜索…"),
                        http={("GET", "/api/setup"): probe.HttpResult(200, '{"needed": false}')})
        out = probe.run_probe(env, "hotkey_focus")
        self.assertFalse(out.get("blocked"))
        self.assertFalse(out["present"])
        self.assertIn("is not the proposal composer", out["reason"])

    def test_setup_wizard_precondition_is_blocked(self):
        """§68.5 首启向导顶掉看板页 → 前置条件不满足记 BLOCKED，不记 MISSING、也不绕开。"""
        env = self._env(hotkey_out(role="AXGroup", place="", desc="",
                                   title="Zelin's AI Assistant — 初始设置"),
                        http={("GET", "/api/setup"): probe.HttpResult(200, '{"needed": true}')})
        out = probe.run_probe(env, "hotkey_focus")
        self.assertTrue(out["blocked"])
        self.assertIn("setup wizard", out["error"])
        self.assertIn("先去看板", out["owner_action"])


class MenuOpenPageProbeTest(unittest.TestCase):
    """§54.4 D40：菜单 关于 / 设置… / 权限体检… → open_page；观测量 = 窗口标题。"""

    def _out(self, about="关于", settings="设置", permissions="权限体检",
             restored="任务台"):
        lines = ["about\tZelin's AI Assistant — " + about,
                 "settings\tZelin's AI Assistant — " + settings,
                 "permissions\tZelin's AI Assistant — " + permissions,
                 "restored\tZelin's AI Assistant — " + restored]
        return FakeEnv(osa=dict(AX_OK, **{"click menu item": probe.OsaResult(0, "\n".join(lines))}))

    def test_three_pages_open(self):
        out = probe.run_probe(self._out(), "menu_open_page")
        self.assertTrue(out["present"])
        self.assertTrue(all(p["opened"] for p in out["pages"].values()))
        self.assertTrue(out["restored_board"])

    def test_english_titles_count(self):
        out = probe.run_probe(self._out("About", "Settings", "Permissions Checkup", "Workbench"),
                              "menu_open_page")
        self.assertTrue(out["present"])

    def test_page_that_did_not_open_is_missing(self):
        out = probe.run_probe(self._out(permissions="关于"), "menu_open_page")
        self.assertFalse(out["present"])
        self.assertIn("permissions", out["reason"])
        self.assertFalse(out["pages"]["permissions"]["opened"])

    def test_script_clicks_all_three_items_by_title(self):
        script = probe.menu_open_page_script()
        for _slug, zh_item, en_item, _zh, _en in probe.MENU_PAGES:
            self.assertIn(zh_item, script)
            self.assertIn(en_item, script)
        self.assertIn("click menu item", script)


class NotifyRelayProbeTest(unittest.TestCase):
    """§28 中继活着的只读观测量：壳 5 s tick 的心跳（§68.7）+ 队列没有积压。"""

    def _env(self, age, entries=(), entry_ages=None):
        ages = {"state/shell.heartbeat": age}
        for name, value in (entry_ages or {}).items():
            ages["state/notify_queue/" + name] = value
        return FakeEnv(ages=ages, entries={"state/notify_queue": list(entries)},
                       http={("GET", "/api/health"): probe.HttpResult(200, "{}")})

    def test_fresh_heartbeat_and_empty_queue(self):
        out = probe.run_probe(self._env(2.1), "notify_relay")
        self.assertTrue(out["present"])
        self.assertEqual((out["queue_depth"], out["stale_entries"]), (0, 0))

    def test_no_ax_and_no_write_calls(self):
        env = self._env(2.1)
        probe.run_probe(env, "notify_relay")
        self.assertEqual(env.osa_calls, [])
        self.assertEqual([c[0] for c in env.http_calls], ["GET"])

    def test_missing_heartbeat_is_missing(self):
        out = probe.run_probe(self._env(None), "notify_relay")
        self.assertFalse(out["present"])
        self.assertIn("state/shell.heartbeat missing", out["reason"])

    def test_stale_heartbeat_is_missing(self):
        out = probe.run_probe(self._env(probe.HEARTBEAT_FRESH_S + 1), "notify_relay")
        self.assertFalse(out["present"])
        self.assertIn("is not running", out["reason"])

    def test_undrained_backlog_is_missing(self):
        env = self._env(1.0, entries=["a.json", "b.json"],
                        entry_ages={"a.json": probe.NOTIFY_STALE_AFTER_S + 5, "b.json": 3.0})
        out = probe.run_probe(env, "notify_relay")
        self.assertFalse(out["present"])
        self.assertEqual((out["queue_depth"], out["stale_entries"]), (2, 1))

    def test_non_json_queue_files_ignored(self):
        out = probe.run_probe(self._env(1.0, entries=["x.json.tmp"]), "notify_relay")
        self.assertTrue(out["present"])
        self.assertEqual(out["queue_depth"], 0)


class TerminalTakeoverProbeTest(unittest.TestCase):
    """§68.7：POST /api/terminal 只带 card_id（命令由 server 从投影行推导），壳开终端窗口。"""

    def _env(self, board_res, post_res=None, before="count:1", after="count:2"):
        counts = [probe.OsaResult(0, before)] + [probe.OsaResult(0, after)]
        state = {"n": 0}

        def osa(script):
            if "count of windows of process" in script:
                idx = min(state["n"], len(counts) - 1)
                state["n"] += 1
                return counts[idx]
            return probe.OsaResult(0, "true")

        env = FakeEnv(http={("GET", "/api/board"): board_res,
                            ("POST", "/api/terminal"): post_res or probe.HttpResult(
                                200, '{"ok": true, "queue_id": "q1"}')})
        env.osascript = osa  # type: ignore[assignment]
        return env

    @staticmethod
    def _board_with_session():
        return probe.HttpResult(200, json.dumps(
            {"running": [{"id": "R-1", "copy_cmd": "cd /x && claude --resume abc"}]}))

    def test_skipped_without_allow_enqueue(self):
        out = probe.run_probe(FakeEnv(), "terminal_takeover")
        self.assertTrue(out["skipped"])
        self.assertEqual(probe.classify(out), "SKIPPED")

    def test_new_terminal_window_is_present(self):
        env = self._env(self._board_with_session())
        out = probe.run_probe(env, "terminal_takeover", allow_enqueue=True,
                              sleep=lambda _s: None)
        self.assertTrue(out["present"])
        self.assertEqual(out["card_id"], "R-1")
        self.assertEqual(env.http_calls[-1], ("POST", "/api/terminal", {"card_id": "R-1"}))

    def test_no_new_window_is_missing(self):
        env = self._env(self._board_with_session(), after="count:1")
        out = probe.run_probe(env, "terminal_takeover", allow_enqueue=True, wait_s=1.0,
                              sleep=lambda _s: None)
        self.assertFalse(out["present"])
        self.assertIn("window count stayed at 1", out["reason"])

    def test_server_error_is_missing_with_message(self):
        env = self._env(self._board_with_session(), post_res=probe.HttpResult(
            503, '{"error": {"code": "SHELL_UNAVAILABLE", "message": "shell is not running"}}'))
        out = probe.run_probe(env, "terminal_takeover", allow_enqueue=True,
                              sleep=lambda _s: None)
        self.assertFalse(out["present"])
        self.assertIn("503", out["reason"])
        self.assertIn("shell is not running", out["reason"])

    def test_board_without_session_card_is_blocked(self):
        env = self._env(probe.HttpResult(200, json.dumps({"needs_approval": [{"id": "R-9"}]})))
        out = probe.run_probe(env, "terminal_takeover", allow_enqueue=True,
                             sleep=lambda _s: None)
        self.assertTrue(out["blocked"])
        self.assertIn("no board card has copy_cmd or session_id", out["error"])

    def test_session_id_only_card_counts(self):
        res = probe.HttpResult(200, json.dumps({"review": [{"id": "R-3", "session_id": "s1"}]}))
        self.assertEqual(probe.takeover_card_id(res)[0], "R-3")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class DockBadgeLaunchServicesTest(unittest.TestCase):
    """2026-09-17 实测：徽章是 42 时 Dock 的 AXStatusLabel 仍回 missing value；
    `lsappinfo … StatusLabel`（NSDockTile.badgeLabel 的发布面）才是真源，AX 只作回落。"""

    def setUp(self):
        probe.BADGE_SETTLE_DELAY = 0

    def _env(self, ls_out, board_res, ax="badge:none"):
        return FakeEnv(osa=dict(AX_OK, **{"AXStatusLabel": probe.OsaResult(0, ax)}),
                       http={("GET", "/api/board"): board_res},
                       ls=probe.OsaResult(0, ls_out))

    def test_launchservices_label_beats_a_blind_ax_read(self):
        out = probe.run_probe(self._env('"StatusLabel"={ "label"="42" }', board(
            {"needs_approval": 20, "needs_input": 2, "review": 20})), "dock_badge")
        self.assertTrue(out["present"])
        self.assertEqual((out["badge"], out["badge_raw"]), (42, "42"))
        self.assertTrue(out["source"].startswith("LaunchServices"))

    def test_kcfnull_means_no_badge(self):
        out = probe.run_probe(self._env('"StatusLabel"={ "label"=kCFNULL }', board({})), "dock_badge")
        self.assertTrue(out["present"])
        self.assertEqual(out["badge"], 0)

    def test_null_bracket_means_no_badge(self):
        out = probe.run_probe(self._env('"StatusLabel"=[ NULL ]', board({})), "dock_badge")
        self.assertEqual(out["badge"], 0)

    def test_no_statuslabel_falls_back_to_ax(self):
        env = FakeEnv(osa=dict(AX_OK, **{"AXStatusLabel": probe.OsaResult(0, "badge:3")}),
                      http={("GET", "/api/board"): board({"needs_approval": 3})},
                      ls=probe.OsaResult(0, "(no such app)"))
        out = probe.run_probe(env, "dock_badge")
        self.assertTrue(out["present"])
        self.assertIn("fallback", out["source"])

    def test_zero_with_pending_board_settles_by_rereading(self):
        env = self._env('"StatusLabel"={ "label"=kCFNULL }', board({"needs_approval": 4}))
        # 第一次 0，之后真源变成 4（网页 push 到了）
        seq = [probe.OsaResult(0, '"StatusLabel"={ "label"=kCFNULL }'),
               probe.OsaResult(0, '"StatusLabel"={ "label"="4" }')]
        env.lsappinfo = lambda bundle_id=None: seq.pop(0) if len(seq) > 1 else seq[0]
        out = probe.run_probe(env, "dock_badge")
        self.assertTrue(out["present"])
        self.assertEqual(out["badge"], 4)
