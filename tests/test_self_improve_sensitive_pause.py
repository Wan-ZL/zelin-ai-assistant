"""§64.4 敏感路径护栏：PR diff 触及写死的受保护路径 → 标签 needs-owner-eyes +
通道暂停（state/self_improve/lane.json）→ policy 对后续 lane 卡报
self_improve:paused（上卡）→ owner 三条出口（处理 PR 后巡检自动清 / 看板
POST /api/self-improve/resume / CLI --resume）。

钉：路径集合本身（改这张表就在表里）；前缀 vs 精确匹配；gh argv 形状（label
create --force → pr edit --add-label）；标签失败不阻塞暂停；暂停可见于
dashboard 顶层 self_improve；server 端清暂停与 act 侧 clear_pause 键集逐字一致
（server 不 import act，路径由 paths.py 镜像）。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import FakeGh, lane_card, pr_doc
from tests.test_server_common import assert_envelope, post_json, start_server

from act import actd
from act.lib import config, notify, registry, self_improve
from act.lib.dashboard import build_dashboard
from act.lib.registry import State
from server import paths as server_paths
from server import self_improve_lane as server_lane

BRANCH = "ai/self-improve/R-900"


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    lane = self_improve.lane_state_path()
    if lane.exists():
        lane.unlink()


class SensitivePathsTestCase(unittest.TestCase):
    def test_wall_list_is_exactly_the_law(self):
        self.assertEqual(self_improve.SENSITIVE_PATHS, (
            "act/lib/policy.py", "act/lib/self_improve.py", "act/llm.py",
            ".github/workflows/", "install.sh", "scripts/auto-deploy.sh"))
        self.assertEqual(self_improve.PAUSE_LABEL, "needs-owner-eyes")

    def test_hits_prefix_for_dirs_exact_for_files(self):
        files = [{"path": ".github/workflows/ci.yml"}, {"path": "act/lib/policy.py"},
                 {"path": "act/lib/policy_extra.py"}, {"path": "install.sh"},
                 {"path": "docs/install.sh.md"}, {"path": "scripts/auto-deploy.sh"},
                 {"path": "act/llm.py"}, {"path": "act/actd.py"}, "act/lib/self_improve.py",
                 42, {"nopath": 1}]
        self.assertEqual(self_improve.sensitive_hits(files), [
            ".github/workflows/ci.yml", "act/lib/policy.py", "act/lib/self_improve.py",
            "act/llm.py", "install.sh", "scripts/auto-deploy.sh"])
        self.assertEqual(self_improve.sensitive_hits(None), [])
        self.assertEqual(self_improve.sensitive_hits([{"path": ".github/workflow"}]), [])


class PauseOnHarvestTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.notify = mock.patch.object(notify, "notify").start()
        self.addCleanup(mock.patch.stopall)

    def test_touching_policy_labels_and_pauses(self):
        gh = FakeGh({123: pr_doc(branch=BRANCH, files=("act/lib/policy.py", "README.md"))})
        ex = {"session_id": "x"}
        logs = []
        res = self_improve.on_harvest(lane_card(), ex, gh=gh, log=logs.append)
        # 核验本身通过（是 draft、diff 非空）——护栏是叠加的一刀，不是失败
        self.assertTrue(res["verified"])
        self.assertEqual(res["sensitive_paths"], ["act/lib/policy.py"])
        self.assertEqual(res["label"], "needs-owner-eyes")
        self.assertNotIn("interrupted_reason", ex)
        # gh argv：先确保标签存在（--force 幂等），再贴到 PR 上
        create = gh.argv_with("label", "create")[0]
        self.assertEqual(create[:4], ["label", "create", "needs-owner-eyes", "--force"])
        self.assertEqual(gh.argv_with("pr", "edit")[0],
                         ["pr", "edit", "123", "--add-label", "needs-owner-eyes", "-R", "o/r"])
        self.assertEqual(create[-2:], ["-R", "o/r"])
        # 暂停落盘 + 可读
        st = self_improve.load_state()
        self.assertTrue(st["paused"])
        self.assertEqual(st["paused_reason"], "sensitive_paths")
        self.assertEqual(st["paused_pr"], 123)
        self.assertEqual(st["paused_pr_url"], "https://github.com/o/r/pull/123")
        self.assertEqual(st["paused_paths"], ["act/lib/policy.py"])
        self.assertEqual(st["paused_card"], "P-7")
        self.assertTrue(self_improve.lane_paused())
        self.notify.assert_called_once()
        self.assertIn("act/lib/policy.py", self.notify.call_args.args[1])
        self.assertTrue(any("lane paused" in line for line in logs))

    def test_label_failure_does_not_block_the_pause(self):
        gh = FakeGh({123: pr_doc(branch=BRANCH, files=("install.sh",))}, edit_rc=1)
        ex = {}
        res = self_improve.on_harvest(lane_card(), ex, gh=gh)
        self.assertIsNone(res["label"])
        self.assertTrue(self_improve.lane_paused())

    def test_unverified_and_sensitive_does_both(self):
        # 不是 draft 且改了 workflow：既标 interrupted 又暂停
        gh = FakeGh({123: pr_doc(branch=BRANCH, draft=False, files=(".github/workflows/ci.yml",))})
        ex = {}
        res = self_improve.on_harvest(lane_card(), ex, gh=gh)
        self.assertEqual(res["reason"], "pr_not_draft")
        self.assertEqual(ex["interrupted_reason"], "delivery_unverified")
        self.assertTrue(self_improve.lane_paused())
        self.assertEqual(self.notify.call_count, 2)

    def test_pause_gates_the_next_lane_card_in_actd(self):
        self_improve.pause("sensitive_paths", pr_number=1, pr_url="u", paths=["install.sh"])
        card = lane_card("P-8", status=State.CARD_SENT.value, execution=None)
        registry.save(card)
        n = actd.auto_dispatch_pass(config.Config())
        self.assertEqual(n, 0)
        req = registry.load("P-8")
        self.assertEqual(req.status, State.CARD_SENT.value)
        self.assertEqual(req.execution["auto_dispatch_block"], "self_improve:paused")
        self.assertIn("self_improve:paused", req.notes)
        # 解除 → 下一 pass 放行，token 清掉
        self_improve.clear_pause()
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 1)
        req = registry.load("P-8")
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertNotIn("auto_dispatch_block", req.execution)

    def test_hand_cards_keep_flowing_while_lane_is_paused(self):
        self_improve.pause("sensitive_paths")
        hand = lane_card("P-9", status=State.CARD_SENT.value, execution=None,
                         sources=[{"channel": "quick_capture", "date": "d", "quote": "手打"}],
                         cost_estimate_usd=1.0)
        registry.save(hand)
        self.assertEqual(actd.auto_dispatch_pass(config.Config()), 1)


class PauseVisibilityAndClearTestCase(unittest.TestCase):
    def setUp(self):
        _clean()
        mock.patch.object(notify, "notify").start()
        self.addCleanup(mock.patch.stopall)

    def test_dashboard_top_level_key_reflects_pause(self):
        with mock.patch("act.lib.dashboard._run_claude_agents", return_value=[]):
            dash = build_dashboard(cfg=config.Config())
        self.assertEqual(dash["self_improve"], {
            "enabled": True, "paused": False, "paused_reason": None, "paused_pr": None,
            "paused_pr_url": None, "paused_paths": [], "paused_at": None})
        self_improve.pause("sensitive_paths", pr_number=5, pr_url="u5", paths=["act/llm.py"],
                           card="P-1")
        with mock.patch("act.lib.dashboard._run_claude_agents", return_value=[]):
            dash = build_dashboard(cfg=config.Config(raw={"self_improve": {"enabled": False}}))
        view = dash["self_improve"]
        self.assertFalse(view["enabled"])
        self.assertTrue(view["paused"])
        self.assertEqual(view["paused_pr"], 5)
        self.assertEqual(view["paused_paths"], ["act/llm.py"])
        self.assertTrue(view["paused_at"].endswith("Z"))

    def test_clear_pause_keeps_unrelated_state(self):
        self_improve.save_state({"owner_login": "Wan-ZL", "followups": {"3": {"date": "d"}}})
        self_improve.pause("sensitive_paths", pr_number=5, pr_url="u5", paths=["act/llm.py"])
        st = self_improve.clear_pause("owner")
        self.assertFalse(st["paused"])
        self.assertEqual(st["resumed_by"], "owner")
        self.assertEqual(st["owner_login"], "Wan-ZL")
        self.assertEqual(st["followups"], {"3": {"date": "d"}})
        for key in ("paused_reason", "paused_pr", "paused_pr_url", "paused_paths", "paused_card"):
            self.assertNotIn(key, st)

    def test_tick_auto_clears_when_flagged_pr_is_handled_by_owner(self):
        self_improve.pause("sensitive_paths", pr_number=5, pr_url="u5", paths=["act/llm.py"])
        gh = FakeGh({5: pr_doc(5, branch=BRANCH, state="OPEN")})
        self_improve.tick(config.Config(), gh=gh, force=True)
        self.assertTrue(self_improve.lane_paused())           # 还开着 → 继续暂停
        gh.prs[5] = pr_doc(5, branch=BRANCH, state="MERGED", merged_by="somebody-else")
        summary = self_improve.tick(config.Config(), gh=gh, force=True)
        self.assertFalse(summary["resumed"])                  # 不是 owner 合的 → 不清
        self.assertTrue(self_improve.lane_paused())
        gh.prs[5] = pr_doc(5, branch=BRANCH, state="MERGED")  # owner 合的
        summary = self_improve.tick(config.Config(), gh=gh, force=True)
        self.assertTrue(summary["resumed"])
        st = self_improve.load_state()
        self.assertFalse(st["paused"])
        self.assertEqual(st["resumed_by"], "pr_merged")

    def test_tick_does_not_clear_when_a_bot_closed_the_flagged_pr(self):
        self_improve.pause("sensitive_paths", pr_number=5, pr_url="u5", paths=["act/llm.py"])
        gh = FakeGh({5: pr_doc(5, branch=BRANCH, state="CLOSED")}, closers={5: ["dependabot[bot]"]})
        self.assertFalse(self_improve.tick(config.Config(), gh=gh, force=True)["resumed"])
        self.assertTrue(self_improve.lane_paused())
        gh.closers[5] = ["dependabot[bot]", "Wan-ZL"]        # 最后一次关闭是 owner
        self.assertTrue(self_improve.tick(config.Config(), gh=gh, force=True)["resumed"])

    def test_update_state_touches_only_named_keys(self):
        # 两个进程各写各的键：server 恢复端点不许覆盖 actd 同一时刻写的暂停
        self_improve.save_state({"owner_login": "Wan-ZL"})
        self_improve.pause("sensitive_paths", pr_number=5)
        disk = self_improve.load_state()
        disk["followups"] = {"5": {"date": "d"}}
        self_improve.save_state(disk)                          # 模拟另一写者插进来
        st = self_improve._update_state({"last_tick_at": "t"})
        self.assertEqual(st["followups"], {"5": {"date": "d"}})
        self.assertTrue(st["paused"])
        self.assertEqual(st["last_tick_at"], "t")
        self.assertEqual(st["owner_login"], "Wan-ZL")

    def test_cli_resume(self):
        self_improve.pause("sensitive_paths")
        with mock.patch("builtins.print") as printed:
            rc = self_improve._main(["--resume"])
        self.assertEqual(rc, 0)
        self.assertFalse(self_improve.lane_paused())
        self.assertIn('"paused": false', printed.call_args.args[0])

    def test_corrupt_state_file_reads_as_not_paused(self):
        p = self_improve.lane_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("[]", encoding="utf-8")
        self.assertFalse(self_improve.lane_paused())
        p.write_text("{not json", encoding="utf-8")
        self.assertEqual(self_improve.load_state(), {})


class ServerResumeRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="zai-lane-route-"))
        (self.home / "state").mkdir()
        _, self.port = start_server(self, self.home)

    def _lane(self):
        return server_paths.self_improve_lane_path(self.home)

    def test_path_mirrors_act_layout(self):
        rel = self_improve.lane_state_path().relative_to(config.HOME)
        self.assertEqual(self._lane(), self.home / rel)

    def test_resume_clears_pause_and_keeps_other_keys(self):
        p = self._lane()
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"paused": True, "paused_reason": "sensitive_paths",
                                 "paused_pr": 5, "paused_pr_url": "u", "paused_paths": ["x"],
                                 "paused_card": "P-1", "owner_login": "Wan-ZL",
                                 "last_tick_at": "t"}), encoding="utf-8")
        status, obj = post_json(self.port, "/api/self-improve/resume", {})
        self.assertEqual(status, 200)
        self.assertEqual(obj, {"ok": True, "paused": False, "was_paused": True})
        st = json.loads(p.read_text(encoding="utf-8"))
        self.assertFalse(st["paused"])
        self.assertEqual(st["resumed_by"], "owner")
        self.assertEqual(st["owner_login"], "Wan-ZL")
        self.assertEqual(st["last_tick_at"], "t")
        self.assertEqual(set(server_lane._PAUSE_KEYS) & set(st), set())

    def test_server_clears_exactly_the_keys_act_clears(self):
        # server 不 import act：这里就是两边键集的 drift-pin
        _clean()
        self_improve.pause("sensitive_paths", pr_number=5, pr_url="u", paths=["x"], card="P-1")
        before = set(self_improve.load_state())
        after = set(self_improve.clear_pause())
        self.assertEqual(before - after, set(server_lane._PAUSE_KEYS))
        self.assertEqual(tuple(server_lane._PAUSE_KEYS), self_improve.PAUSE_KEYS)

    def test_both_writers_share_one_lock_file(self):
        # act 与 server 的 flock 都落在 <lane.json>.lock——同名才互斥
        status, _ = post_json(self.port, "/api/self-improve/resume", {})
        self.assertEqual(status, 200)
        self.assertTrue(self._lane().with_suffix(".lock").exists())
        _clean()
        self_improve.pause("sensitive_paths")
        self.assertTrue(self_improve.lane_state_path().with_suffix(".lock").exists())
        self.assertEqual(self._lane().with_suffix(".lock").name,
                         self_improve.lane_state_path().with_suffix(".lock").name)

    def test_resume_without_a_file_is_idempotent_200(self):
        status, obj = post_json(self.port, "/api/self-improve/resume", {})
        self.assertEqual(status, 200)
        self.assertEqual(obj["was_paused"], False)
        self.assertFalse(json.loads(self._lane().read_text(encoding="utf-8"))["paused"])

    def test_unknown_field_is_400(self):
        status, obj = post_json(self.port, "/api/self-improve/resume", {"force": True})
        self.assertEqual(status, 400)
        assert_envelope(self, obj, "UNKNOWN_FIELD")


if __name__ == "__main__":
    unittest.main()
