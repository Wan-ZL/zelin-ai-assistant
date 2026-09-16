"""§65.5 追记（issue #310）：谁算 owner 本人，以及集合外的人处理 PR 时只记一次。

owner 集合 = 仓库 slug 的 owner ∪ gh 当前身份 ∪ 配置 `owner_logins`，比对大小写
不敏感——这台机器的 gh 登的是工作号、PR 是个人号合的时候，旧定义（只认 gh 身份）
把 owner 自己的合并判成「别人干的」，卡永不结算、每个 tick 刷同一行日志。
集合外的 actor：卡照旧不动，但 lane.json `foreign` 台账让日志与卡上的 note 各只出
一次；`--forget-owner` 清掉缓存的 gh login。
"""
import datetime as _dt
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import FakeGh, lane_card, pr_doc

from act import actd
from act.lib import config, notify, policy, registry, self_improve
from act.lib.registry import State
from server import settings_catalog

BRANCH = "ai/self-improve/R-900"
SLUG = "Wan-ZL/zelin-ai-assistant"
WORK_LOGIN = "zelinPostman"       # 这台机器的 gh auth 身份（工作号）
NOW = _dt.datetime(2026, 9, 9, 15, 0, tzinfo=_dt.timezone.utc)


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    for name in ("lane.json", "rejected.jsonl"):
        p = self_improve.state_dir() / name
        if p.exists():
            p.unlink()


def _review_card(req_id="P-7", pr=268):
    delivery = {"verified": True, "reason": None, "pr_number": pr,
                "pr_url": f"https://github.com/{SLUG}/pull/{pr}", "branch": BRANCH}
    card = lane_card(req_id, status=State.REVIEW.value,
                     execution={"session_id": "aaaa1111", "done": True, "delivery": delivery})
    registry.save(card)
    return card


def _pr(state="MERGED", merged_by="Wan-ZL"):
    return pr_doc(268, branch=BRANCH, draft=False, state=state, merged_by=merged_by,
                  url=f"https://github.com/{SLUG}/pull/268")


class OwnerIdentityCase(unittest.TestCase):
    def setUp(self):
        _clean()
        mock.patch.object(notify, "notify").start()
        self.addCleanup(mock.patch.stopall)
        self.cfg = config.Config(self_improve_enabled=True)   # §65.1 通道开着

    def _tick(self, gh, now=NOW, **kw):
        return self_improve.tick(self.cfg, gh=gh, now=now, force=True, **kw)

    def _gh(self, prs, **kw):
        return FakeGh(prs, login=WORK_LOGIN, slug=SLUG, **kw)


class RepoOwnerCountsTestCase(OwnerIdentityCase):
    def test_merge_by_the_repo_slug_owner_settles_the_card(self):
        _review_card()
        gh = self._gh({268: _pr(merged_by="Wan-ZL")})
        summary = self._tick(gh)
        self.assertEqual(summary["accepted"], ["P-7"])
        req = registry.load("P-7")
        self.assertEqual(req.status, State.DELIVERED.value)
        self.assertEqual(req.execution["accepted_via"], "pr_merged")

    def test_login_comparison_is_case_insensitive(self):
        _review_card()
        # gh 身份 `zelinPostman` vs GitHub 回的 `ZELINPOSTMAN`（login 大小写不敏感）
        gh = self._gh({268: _pr(merged_by="ZELINPOSTMAN")})
        self.assertEqual(self._tick(gh)["accepted"], ["P-7"])
        self.assertEqual(registry.load("P-7").status, State.DELIVERED.value)

    def test_configured_extra_login_still_counts(self):
        _review_card()
        self.cfg = config.Config(raw={"self_improve": {"owner_logins": ["Elenvo-AI"]}},
                                 self_improve_enabled=True)
        gh = self._gh({268: _pr(merged_by="elenvo-ai")})
        self.assertEqual(self._tick(gh)["accepted"], ["P-7"])

    def test_close_by_the_repo_owner_rejects(self):
        _review_card()
        gh = self._gh({268: _pr(state="CLOSED", merged_by=None)},
                      closers={268: ["Wan-ZL"]})
        self.assertEqual(self._tick(gh)["rejected"], ["P-7"])
        self.assertEqual(registry.load("P-7").status, State.TRASHED.value)

    def test_repo_owner_comment_mints_a_followup(self):
        _review_card()
        gh = self._gh({268: _pr(state="OPEN")},
                      comments={268: [{"author": {"login": "Wan-ZL"}, "body": "Where is the test?",
                                       "createdAt": "2026-09-09T14:00:00Z"}]})
        self.assertEqual(len(self._tick(gh)["followups"]), 1)

    def test_a_stranger_is_still_not_the_owner(self):
        _review_card()
        gh = self._gh({268: _pr(merged_by="collaborator")})
        self.assertEqual(self._tick(gh)["accepted"], [])
        self.assertEqual(registry.load("P-7").status, State.REVIEW.value)


class ForeignLedgerTestCase(OwnerIdentityCase):
    def _foreign(self):
        return json.loads(self_improve.lane_state_path().read_text(encoding="utf-8"))["foreign"]

    def test_foreign_handling_is_logged_and_noted_once_across_ticks(self):
        _review_card()
        gh = self._gh({268: _pr(merged_by="collaborator")})
        first, second = [], []
        self._tick(gh, log=first.append)
        self._tick(gh, now=NOW + _dt.timedelta(hours=1), log=second.append)
        self.assertEqual([line for line in first if "someone other than the owner" in line],
                         ["self_improve: P-7 PR #268 MERGED by someone other than the owner "
                          "(@collaborator) — card left as is"])
        self.assertEqual([line for line in second if "someone other than the owner" in line], [])
        notes = registry.load("P-7").notes
        self.assertEqual(notes.count("不在 owner 集合"), 1)
        self.assertIn("[2026-09-09 PR merged] @collaborator", notes)
        self.assertEqual(registry.load("P-7").status, State.REVIEW.value)

    def test_ledger_entry_shape_and_reset_on_a_new_actor(self):
        _review_card()
        closed = {268: _pr(state="CLOSED", merged_by=None)}
        self._tick(self._gh(closed, closers={268: ["collaborator"]}))
        self.assertEqual(self._foreign()["268"],
                         {"state": "CLOSED", "actor": "collaborator",
                          "at": "2026-09-09T15:00:00Z", "card": "P-7"})
        later = NOW + _dt.timedelta(hours=1)
        logs = []
        self._tick(self._gh(closed, closers={268: ["collaborator", "someone-else"]}),
                   now=later, log=logs.append)
        self.assertTrue(any("@someone-else" in line for line in logs))
        self.assertEqual(self._foreign()["268"]["actor"], "someone-else")
        self.assertEqual(registry.load("P-7").notes.count("不在 owner 集合"), 2)

    def test_ledger_is_capped(self):
        ledger = {str(n): {"state": "MERGED", "actor": "x", "at": f"2026-09-{n % 28 + 1:02d}",
                           "card": "P-1"} for n in range(self_improve.FOREIGN_CAP + 5)}
        self_improve._trim_foreign(ledger)
        self.assertEqual(len(ledger), self_improve.FOREIGN_CAP)


class ForgetOwnerTestCase(OwnerIdentityCase):
    def test_forget_owner_drops_the_cached_login(self):
        self_improve.save_state({"owner_login": WORK_LOGIN, "repo_slug": SLUG,
                                 "paused": False})
        with mock.patch("builtins.print"):
            self.assertEqual(self_improve._main(["--forget-owner"]), 0)
        st = self_improve.load_state()
        self.assertNotIn("owner_login", st)
        self.assertEqual(st["repo_slug"], SLUG)    # 其余键不动

    def test_cached_login_is_refilled_on_the_next_tick(self):
        """换了 gh 登录身份的完整路径：忘掉旧 login → 下一轮重问 `gh api user`。"""
        _review_card()
        self_improve.save_state({"owner_login": "stale-account"})
        with mock.patch("builtins.print"):
            self_improve._main(["--forget-owner"])
        gh = self._gh({268: _pr(merged_by=WORK_LOGIN)})
        self.assertEqual(self._tick(gh)["accepted"], ["P-7"])
        self.assertIn(["api", "user"], gh.calls)
        self.assertEqual(self_improve.load_state()["owner_login"], WORK_LOGIN)


class SettingsKnobTestCase(unittest.TestCase):
    """issue #310 第 2 条：`owner_logins` 在设置页「开发者」区可编辑，且**只有它**
    每 pass 现读（§65.8 追记：块里其余键仍随 actd 启动冻结）。"""

    def setUp(self):
        self.addCleanup(lambda: config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True))
        config.SETTINGS_OVERRIDES_PATH.unlink(missing_ok=True)

    def _write(self, doc):
        config.SETTINGS_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_OVERRIDES_PATH.write_text(json.dumps(doc), encoding="utf-8")

    def test_nested_spelling_reaches_the_lane_through_the_raw_block(self):
        self._write({"self_improve": {"owner_logins": [" Wan-ZL ", "", 7]}})
        cfg = config.load_config()
        self.assertEqual(policy.self_improve_config(cfg)["owner_logins"], ["Wan-ZL", "7"])

    def test_flat_spelling_lands_too(self):
        self._write({"self_improve.owner_logins": ["Wan-ZL"]})
        self.assertEqual(policy.self_improve_config(config.load_config())["owner_logins"],
                         ["Wan-ZL"])

    def test_bad_shapes_are_silent_noops(self):
        for doc in ({"self_improve": "junk"}, {"self_improve": {"owner_logins": "Wan-ZL"}},
                    {"self_improve.owner_logins": "Wan-ZL"}):
            self._write(doc)
            self.assertEqual(policy.self_improve_config(config.load_config())["owner_logins"], [])

    def test_the_switch_keeps_its_own_flat_spelling(self):
        """总开关没有第二套写入面（§65.1 追记）：嵌套块里的 enabled 不是键。"""
        self._write({"self_improve": {"enabled": True}})
        self.assertFalse(config.load_config().self_improve_enabled)

    def test_actd_refreshes_owner_logins_but_freezes_the_rest(self):
        frozen = config.Config(raw={"self_improve": {"tick_minutes": 5}})
        self._write({"self_improve": {"owner_logins": ["Wan-ZL"]}})
        actd._refresh_model_knobs(frozen)
        self.assertEqual(policy.self_improve_config(frozen)["owner_logins"], ["Wan-ZL"])
        self.assertEqual(policy.self_improve_config(frozen)["tick_minutes"], 5)
        self._write({})                       # 设置页清空列表 = diff-write 删键
        actd._refresh_model_knobs(frozen)
        self.assertEqual(policy.self_improve_config(frozen)["owner_logins"], [])
        self.assertEqual(policy.self_improve_config(frozen)["tick_minutes"], 5)

    def test_a_raw_block_that_is_not_a_table_stands_the_refresh_down(self):
        """`config.yaml` 手改成一个列表（或别的非表文档）时 `cfg.raw` 不是 dict——
        §65.8 的现读原地返回：不给它造一个 `self_improve:` 块、不崩 pass，而同一个
        刷新点上其余的旋钮照常落地（宪法第 11 条：坏配置只影响它自己那一半）。"""
        frozen = config.Config(raw=[])
        self._write({"self_improve": {"owner_logins": ["Wan-ZL"]},
                     "approval_mention_escalation": 0})
        actd._refresh_model_knobs(frozen)
        self.assertEqual(frozen.raw, [])                       # 一个键都没被塞进去
        self.assertEqual(frozen.approval_mention_escalation, 0)  # 其余旋钮照旧现读

    def test_catalog_row_is_the_second_developer_row(self):
        section = next(s for s in settings_catalog.SECTIONS if s["id"] == "maintainer")
        row = section["fields"][1]
        self.assertEqual(row["key"], "self_improve_owner_logins")
        self.assertEqual(row["kind"], "list")
        self.assertEqual(row["default"], [])
        self.assertEqual(row["config"], ("self_improve", "owner_logins"))
        self.assertEqual(row["override"], "self_improve.owner_logins")
        self.assertIn(row["override"], config._OVERRIDE_HANDLERS)
        self.assertIn(row["override"].split(".")[0], config._OVERRIDE_HANDLERS)


if __name__ == "__main__":
    unittest.main()
