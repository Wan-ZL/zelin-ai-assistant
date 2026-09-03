"""§65.5 巡检（self_improve.tick）：owner 合并 = 验收、owner 关闭 = 拒绝（回收站 +
拒绝记忆）、owner 评论 / 红 required check → 跟进卡（一 PR 一天一张、只认 owner
login、已有在途跟进卡不重铸、评论按 covered_until 去重）、节流（tick_minutes）、
gh 不可用整轮跳过但推进时间戳、零 lane 卡零 gh 调用。

跟进卡的形状是 producer 硬编码的：sources 唯一一条 self_improve + PR 坐标；
标题/plan 不含评论原文（原文只进 quote，build_prompt 围栏它）。
"""
import datetime as _dt
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import FakeGh, lane_card, pr_doc, unavailable_gh

from act.lib import config, notify, policy, registry, self_improve
from act.lib.registry import State

BRANCH = "ai/self-improve/R-900"
NOW = _dt.datetime(2026, 9, 2, 10, 0, tzinfo=_dt.timezone.utc)


def _clean():
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    for name in ("lane.json", "rejected.jsonl"):
        p = self_improve.state_dir() / name
        if p.exists():
            p.unlink()


def _review_card(req_id="P-7", pr=123, title="lane 测试卡"):
    delivery = {"verified": True, "reason": None, "pr_number": pr,
                "pr_url": f"https://github.com/o/r/pull/{pr}", "branch": BRANCH}
    card = lane_card(req_id, status=State.REVIEW.value, title=title,
                     execution={"session_id": "aaaa1111", "done": True, "delivery": delivery})
    registry.save(card)
    return card


def _comment(body, login="Wan-ZL", at="2026-09-02T09:00:00Z"):
    return {"author": {"login": login}, "body": body, "createdAt": at,
            "url": "https://github.com/o/r/pull/123#c1"}


class TickBase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.notify = mock.patch.object(notify, "notify").start()
        self.addCleanup(mock.patch.stopall)
        self.cfg = config.Config()

    def _tick(self, gh, now=NOW, **kw):
        return self_improve.tick(self.cfg, gh=gh, now=now, force=True, **kw)


class MergeAndCloseTestCase(TickBase):
    def test_merged_pr_accepts_the_card(self):
        _review_card()
        gh = FakeGh({123: pr_doc(branch=BRANCH, state="MERGED", draft=False)})
        summary = self._tick(gh)
        self.assertEqual(summary["accepted"], ["P-7"])
        req = registry.load("P-7")
        self.assertEqual(req.status, State.DELIVERED.value)
        self.assertEqual(req.execution["accepted_via"], "pr_merged")
        self.assertEqual(req.execution["accepted_at"], "2026-09-02T10:00:00Z")
        self.assertIn("PR merged", req.notes)
        self.assertIn("https://github.com/o/r/pull/123", req.notes)

    def test_merged_by_someone_else_leaves_the_card_in_review(self):
        _review_card()
        gh = FakeGh({123: pr_doc(branch=BRANCH, state="MERGED", draft=False,
                                 merged_by="collaborator")})
        logs = []
        summary = self._tick(gh, log=logs.append)
        self.assertEqual(summary["accepted"], [])
        self.assertEqual(registry.load("P-7").status, State.REVIEW.value)
        self.assertTrue(any("someone other than the owner" in line for line in logs))

    def test_closed_by_a_bot_leaves_the_card_and_memory_alone(self):
        _review_card()
        gh = FakeGh({123: pr_doc(branch=BRANCH, state="CLOSED")}, closers={123: ["github-actions[bot]"]})
        summary = self._tick(gh)
        self.assertEqual(summary["rejected"], [])
        self.assertEqual(registry.load("P-7").status, State.REVIEW.value)
        self.assertEqual(self_improve.rejected_entries(), [])
        self.assertIn(["api", "repos/o/r/issues/123/events"], gh.calls)

    def test_closed_pr_trashes_and_remembers_the_rejection(self):
        _review_card(title="让 doctor 多一行")
        gh = FakeGh({123: pr_doc(branch=BRANCH, state="CLOSED")})
        summary = self._tick(gh)
        self.assertEqual(summary["rejected"], ["P-7"])
        req = registry.load("P-7")
        self.assertEqual(req.status, State.TRASHED.value)
        self.assertEqual(req.trash_reason, "pr_closed")
        self.assertEqual(req.prev_status, State.REVIEW.value)   # 可恢复
        fp = self_improve.fingerprint("让 doctor 多一行")
        self.assertTrue(self_improve.is_rejected(fp))
        self.assertTrue(self_improve.is_rejected(self_improve.fingerprint("  让 DOCTOR 多一行 ")))
        self.assertFalse(self_improve.is_rejected(self_improve.fingerprint("别的")))
        entry = self_improve.rejected_entries()[0]
        self.assertEqual(entry["pr"], 123)
        self.assertEqual(entry["card"], "P-7")
        self.assertEqual(entry["closed_at"], "2026-09-02T12:00:00Z")

    def test_rejection_memory_is_capped(self):
        big = "x" * 1000
        for i in range(400):
            self_improve.record_rejection({"fingerprint": str(i), "title": big})
        size = self_improve.rejected_path().stat().st_size
        self.assertLessEqual(size, self_improve.REJECTED_CAP_BYTES + 2000)
        self.assertTrue(self_improve.is_rejected("399"))   # 最新的留着

    def test_garbage_lines_in_memory_are_skipped(self):
        p = self_improve.rejected_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"fingerprint": "a"}\nnot json\n[1]\n', encoding="utf-8")
        self.assertEqual(self_improve.rejected_entries(), [{"fingerprint": "a"}])

    def test_repo_unknown_skips_the_round_without_touching_cards(self):
        _review_card()
        gh = FakeGh({123: pr_doc(branch=BRANCH, state="MERGED", draft=False)}, slug=None)
        summary = self._tick(gh)
        self.assertEqual(summary["skipped"], "repo_unknown")
        self.assertEqual(registry.load("P-7").status, State.REVIEW.value)
        self.assertEqual(gh.argv_with("pr"), [])

    def test_only_review_lane_cards_with_a_pr_are_tracked(self):
        # executing 卡 / hand 卡 / 无 delivery 的 review 卡都不查 gh（连 repo view 都不发）
        registry.save(lane_card("P-1", status=State.EXECUTING.value))
        registry.save(lane_card("P-2", status=State.REVIEW.value,
                                execution={"done": True}))
        registry.save(lane_card("P-3", status=State.REVIEW.value,
                                sources=[{"channel": "quick_capture", "date": "d"}],
                                execution={"delivery": {"pr_number": 9}}))
        gh = FakeGh({})
        self._tick(gh)
        self.assertEqual(gh.calls, [])


class FollowupTestCase(TickBase):
    def test_owner_comment_mints_one_followup_card(self):
        _review_card()
        gh = FakeGh({123: pr_doc(branch=BRANCH)},
                    comments={123: [_comment("Where is the test?"),
                                    _comment("bot noise", login="github-actions[bot]")]})
        summary = self._tick(gh)
        self.assertEqual(len(summary["followups"]), 1)
        card = registry.load(summary["followups"][0])
        self.assertEqual(card.status, State.CARD_SENT.value)
        self.assertTrue(card.id.startswith("P-"))
        self.assertEqual(card.title, "跟进 PR #123：1 条 owner 评论 / 0 项红检查")
        self.assertNotIn("Where is the test", card.title)
        src = card.sources[0]
        self.assertEqual(src["channel"], "self_improve")
        self.assertEqual(src["pr_number"], 123)
        self.assertEqual(src["head"], BRANCH)
        self.assertEqual(src["head_sha"], "deadbeef")
        self.assertEqual(src["who"], "Wan-ZL")
        self.assertIn("Where is the test?", src["quote"])
        self.assertNotIn("bot noise", src["quote"])
        self.assertEqual(card.origin_trust, policy.PROPOSED)
        self.assertEqual(card.target_repo, str(config.HOME))
        self.assertEqual(card.delivery_mode, "repo")
        self.assertTrue(any("checkout ai/self-improve/R-900" in step for step in card.plan))
        self.assertTrue(any("同分支" in d for d in card.definition_of_done))
        # 它就是 lane 卡：下一 pass 免批
        self.assertEqual(policy.may_auto_dispatch(card, self.cfg), (True, "ok:self_improve"))
        self.assertEqual(self_improve.expected_branch(card), BRANCH)
        # owner login 缓存进 lane.json；ledger 记 covered_until
        st = self_improve.load_state()
        self.assertEqual(st["owner_login"], "Wan-ZL")
        self.assertEqual(st["followups"]["123"]["covered_until"], "2026-09-02T09:00:00Z")
        self.assertEqual(st["followups"]["123"]["parent"], "P-7")
        self.notify.assert_called_once()

    def test_one_per_pr_per_day(self):
        _review_card()
        gh = FakeGh({123: pr_doc(branch=BRANCH)}, comments={123: [_comment("a")]})
        first = self._tick(gh)["followups"]
        gh.comments[123].append(_comment("b", at="2026-09-02T09:30:00Z"))
        # 同一天：即便有新评论也不铸第二张
        self.assertEqual(self._tick(gh, now=NOW + _dt.timedelta(hours=2))["followups"], [])
        # 第二天：跟进卡若仍在途（card_sent）也不铸；验收掉它后新评论才铸
        tomorrow = NOW + _dt.timedelta(days=1)
        self.assertEqual(self._tick(gh, now=tomorrow)["followups"], [])
        f = registry.load(first[0])
        registry.trash(f, "deleted")
        second = self._tick(gh, now=tomorrow)["followups"]
        self.assertEqual(len(second), 1)
        self.assertIn("[2026-09-02T09:30:00Z @Wan-ZL] b", registry.load(second[0]).sources[0]["quote"])
        self.assertNotIn("@Wan-ZL] a", registry.load(second[0]).sources[0]["quote"])

    def test_red_required_checks_alone_mint_a_followup(self):
        _review_card()
        gh = FakeGh({123: pr_doc(branch=BRANCH)},
                    checks={123: [{"name": "Lint (shellcheck + ruff)", "bucket": "fail"},
                                  {"name": "ci", "bucket": "pass"},
                                  {"name": "Web tests", "bucket": "pending"}]})
        card = registry.load(self._tick(gh)["followups"][0])
        self.assertEqual(card.title, "跟进 PR #123：0 条 owner 评论 / 1 项红检查")
        self.assertEqual(card.sources[0]["who"], "ci")
        self.assertIn("Lint (shellcheck + ruff)", card.sources[0]["quote"])
        self.assertTrue(any("Lint (shellcheck + ruff)" in s for s in card.plan))
        self.assertTrue(any("Lint (shellcheck + ruff)" in d for d in card.definition_of_done))
        checks_call = gh.argv_with("pr", "checks")[0]
        self.assertEqual(checks_call[:4], ["pr", "checks", "123", "--required"])

    def test_no_owner_activity_no_card(self):
        _review_card()
        gh = FakeGh({123: pr_doc(branch=BRANCH)},
                    comments={123: [_comment("someone", login="stranger")]},
                    reviews={123: [{"author": {"login": "stranger"}, "body": "meh",
                                    "submittedAt": "2026-09-02T09:00:00Z"}]},
                    checks={123: [{"name": "ci", "bucket": "pass"}]})
        self.assertEqual(self._tick(gh)["followups"], [])
        self.assertEqual(registry.load_all().__len__(), 1)

    def test_reviews_and_inline_comments_count_too(self):
        _review_card()
        gh = FakeGh({123: pr_doc(branch=BRANCH)},
                    reviews={123: [{"author": {"login": "Wan-ZL"}, "body": "LGTM but rename",
                                    "state": "COMMENTED", "submittedAt": "2026-09-02T09:05:00Z"},
                                   {"author": {"login": "Wan-ZL"}, "body": "",
                                    "state": "APPROVED", "submittedAt": "2026-09-02T09:06:00Z"}]},
                    inline={123: [{"user": {"login": "Wan-ZL"}, "body": "typo here",
                                   "created_at": "2026-09-02T09:01:00Z",
                                   "html_url": "https://github.com/o/r/pull/123#r1"}]})
        card = registry.load(self._tick(gh)["followups"][0])
        quote = card.sources[0]["quote"]
        self.assertLess(quote.index("typo here"), quote.index("LGTM but rename"))  # 时间升序
        self.assertEqual(card.title, "跟进 PR #123：2 条 owner 评论 / 0 项红检查")
        self.assertIn(["api", "repos/o/r/pulls/123/comments"], gh.calls)   # 显式 slug
        self.assertIn(["api", "user"], gh.calls)          # owner login = gh 当前身份（D8）
        self.assertTrue(gh.pr_calls_all_pinned())

    def test_extra_owner_logins_from_config(self):
        _review_card()
        self.cfg = config.Config(raw={"self_improve": {"owner_logins": ["elenvo-ai"]}})
        gh = FakeGh({123: pr_doc(branch=BRANCH)},
                    comments={123: [_comment("from the other account", login="elenvo-ai")]})
        self.assertEqual(len(self._tick(gh)["followups"]), 1)

    def test_quote_is_capped(self):
        _review_card()
        gh = FakeGh({123: pr_doc(branch=BRANCH)}, comments={123: [_comment("y" * 5000)]})
        card = registry.load(self._tick(gh)["followups"][0])
        self.assertLessEqual(len(card.sources[0]["quote"]), self_improve.FOLLOWUP_QUOTE_CAP)


class ThrottleAndAvailabilityTestCase(TickBase):
    def test_not_due_until_tick_minutes_elapse(self):
        gh = FakeGh({})
        self.assertEqual(self_improve.tick(self.cfg, gh=gh, now=NOW), {"accepted": [],
                         "rejected": [], "followups": [], "resumed": False})
        self.assertEqual(self_improve.tick(self.cfg, gh=gh, now=NOW + _dt.timedelta(minutes=59)),
                         {"skipped": "not_due"})
        self.assertNotIn("skipped", self_improve.tick(
            self.cfg, gh=gh, now=NOW + _dt.timedelta(minutes=60)))
        cfg = config.Config(raw={"self_improve": {"tick_minutes": 5}})
        self.assertNotIn("skipped", self_improve.tick(
            cfg, gh=gh, now=NOW + _dt.timedelta(minutes=66)))

    def test_gh_unavailable_skips_but_advances_the_clock(self):
        _review_card()
        summary = self._tick(unavailable_gh)
        self.assertEqual(summary["skipped"], "gh_unavailable")
        self.assertEqual(self_improve.load_state()["last_tick_at"], "2026-09-02T10:00:00Z")
        self.assertEqual(registry.load("P-7").status, State.REVIEW.value)

    def test_state_file_shape(self):
        self._tick(FakeGh({}))
        st = json.loads(self_improve.lane_state_path().read_text(encoding="utf-8"))
        self.assertEqual(st, {"last_tick_at": "2026-09-02T10:00:00Z"})


if __name__ == "__main__":
    unittest.main()
