"""act/lib/self_improve.py 的变异残存补杀（CONTRACT §65.1–§65.5 / §0 第 3、11 条）。

夜间变异跑（§57）在自动草稿 PR 通道上留了一批活口，全都落在「这条通道会替 owner
按下按钮」的安全面上：谁算 owner（issue #310 的身份脏值）、gh 的非零退出码不等于
「没有红检查」、标签失败不许顶替暂停、没暂停就没有「恢复」、以及几处出生即带帽的
台账（拒绝记忆 256 KiB、foreign 200 条、owner 评论原文 1500 字）。另有两处形制：
`default_gh` 的子进程边界（永不无限等）与三份落盘文本的 UTF-8 / 键序。

判据一律写字面量而不是读模块常量——读常量的断言会跟着变异体一起挪，等于没钉。
与既有 §65 判例（test_self_improve_*.py）互不重叠。
"""
import datetime as _dt
import hashlib
import io
import json
import os
import shutil
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports
from tests.self_improve_testkit import FakeGh, lane_card, pr_doc, unavailable_gh

from act.lib import config, notify, registry, self_improve
from act.lib.registry import State

BRANCH = "ai/self-improve/R-900"
SLUG = "o/r"
NOW = _dt.datetime(2026, 9, 9, 15, 0, tzinfo=_dt.timezone.utc)


def _clean() -> None:
    config.ensure_state_dirs()
    for p in config.REGISTRY_DIR.glob("*.yaml"):
        p.unlink()
    for name in ("lane.json", "lane.lock", "rejected.jsonl"):
        p = self_improve.state_dir() / name
        if p.exists():
            p.unlink()


def _si_source(**over) -> dict:
    src = {"who": "loop", "channel": "self_improve", "date": "2026-09-09",
           "ref": "proposal:abc", "quote": "让 doctor 多一行"}
    src.update(over)
    return src


class LaneCase(unittest.TestCase):
    def setUp(self):
        _clean()
        self.addCleanup(_clean)
        self.notify = mock.patch.object(notify, "notify").start()
        self.addCleanup(mock.patch.stopall)
        self.cfg = config.Config(self_improve_enabled=True)      # §65.1 通道开着

    def review_card(self, req_id="P-7", pr=268, **over):
        delivery = {"verified": True, "reason": None, "pr_number": pr, "branch": BRANCH,
                    "pr_url": "https://github.com/%s/pull/%d" % (SLUG, pr)}
        card = lane_card(req_id, status=State.REVIEW.value,
                         execution={"session_id": "aaaa1111", "done": True, "delivery": delivery},
                         **over)
        registry.save(card)
        return card

    def tick(self, gh, **kw):
        return self_improve.tick(self.cfg, gh=gh, now=NOW, force=True, **kw)

    def bodies(self) -> list:
        return [c[0][1] for c in self.notify.call_args_list]


# --------------------------------------------------------------------------- #
# gh 边界与时间读法
# --------------------------------------------------------------------------- #
class GhBoundaryTestCase(LaneCase):
    def test_the_gh_subprocess_is_bounded_captured_and_textual(self):
        """§65：gh 是唯一外部工具——它挂住绝不许把一整个 actd pass 拖住。"""
        seen = {}

        class _Proc:
            returncode = 0
            stdout = '{"ok": true}'

        def _run(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            return _Proc()

        with mock.patch.dict(os.environ, {self_improve.GH_ENV: "1"}), \
                mock.patch.object(self_improve.shutil, "which", return_value="/usr/bin/gh"), \
                mock.patch.object(self_improve.subprocess, "run", _run):
            rc, out = self_improve.default_gh(["pr", "view", "9"], "/repo")
        self.assertEqual((rc, out), (0, '{"ok": true}'))
        self.assertEqual(seen["cmd"], ["gh", "pr", "view", "9"])
        self.assertEqual(seen["kw"]["cwd"], "/repo")
        self.assertTrue(seen["kw"]["capture_output"])
        self.assertTrue(seen["kw"]["text"])
        self.assertEqual(seen["kw"]["timeout"], 60)

    def test_a_timezone_less_last_tick_is_read_as_utc_and_nothing_is_forced(self):
        """巡检节流的时钟：无时区的时间戳按 UTC 读；``force`` 出厂是假。"""
        cfg = config.Config(raw={"self_improve": {"tick_minutes": 60}},
                            self_improve_enabled=True)
        self.assertFalse(self_improve.tick_due({"last_tick_at": "2026-09-09T14:30:00"}, cfg, NOW))
        self.assertTrue(self_improve.tick_due({"last_tick_at": "2026-09-09T13:30:00"}, cfg, NOW))
        self.assertTrue(self_improve.tick_due({}, cfg, NOW))          # 从没跑过 = 该跑

    def test_a_pr_row_without_an_integer_number_is_not_a_pr(self):
        """§0 第 11 条：外部 JSON 的字段类型逐个消毒——字符串编号不是编号。"""
        rows = [{"number": "12", "state": "OPEN"}, {"state": "OPEN"}]

        def gh(args, cwd):
            return 0, json.dumps(rows)

        self.assertIsNone(self_improve.find_pr_by_branch(gh, "/repo", BRANCH, SLUG))
        rows.append({"number": 31, "state": "OPEN"})
        self.assertEqual(self_improve.find_pr_by_branch(gh, "/repo", BRANCH, SLUG), 31)

    def test_a_non_zero_gh_exit_is_not_the_same_as_no_red_checks(self):
        """`gh pr checks` 有失败时自身退出 1（没有 required check 时 8）——
        把那当成「没有红检查」会让跟进卡永远不铸（§65.5）。"""
        rows = [{"name": "Gates", "bucket": "fail"}, {"name": "Tests", "bucket": "pass"}]
        for rc in (0, 1, 8):
            def gh(args, cwd, _rc=rc):
                return _rc, json.dumps(rows)
            self.assertEqual(self_improve.red_required_checks(gh, "/repo", 9), ["Gates"], rc)

        def broken(args, cwd):
            return 2, json.dumps(rows)
        self.assertEqual(self_improve.red_required_checks(broken, "/repo", 9), [])

    def test_an_unavailable_gh_still_returns_the_whole_delivery_shape(self):
        """§65.3 的 ``execution.delivery`` 是 add-only 形状——失败路径也得字段齐全。"""
        card = lane_card("P-7")
        self.assertEqual(
            self_improve.verify_delivery(card, self.cfg, gh=unavailable_gh, now=NOW),
            {"verified": False, "reason": "gh_unavailable", "branch": BRANCH, "repo": None,
             "pr_number": None, "pr_url": None, "pr_draft": None, "pr_state": None,
             "base": None, "head_sha": None, "changed_files": 0, "sensitive_paths": [],
             "checked_at": "2026-09-09T15:00:00Z"})

    def test_a_blank_or_non_string_pr_head_falls_back_to_the_card_branch(self):
        """分支名是核验唯一的锚（§65.3）——空串 / 非字符串不许当分支名用。"""
        for head in ("", 123, None):
            card = lane_card("P-7", sources=[_si_source(pr_number=5, head=head)])
            self.assertEqual(self_improve.expected_branch(card), BRANCH, repr(head))


# --------------------------------------------------------------------------- #
# lane.json / 拒绝记忆：落盘形制与两道帽
# --------------------------------------------------------------------------- #
class LedgerTestCase(LaneCase):
    def _wipe_state_dir(self):
        shutil.rmtree(config.STATE_DIR, ignore_errors=True)
        self.addCleanup(config.ensure_state_dirs)

    def test_lane_state_lands_as_sorted_utf8_text_even_with_no_state_dir(self):
        self._wipe_state_dir()
        st = {"paused": True, "paused_reason": "敏感路径", "paused_pr": 3}
        self_improve.save_state(st)
        text = self_improve.lane_state_path().read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps(st, ensure_ascii=False, indent=2, sort_keys=True))
        self.assertIn("敏感路径", text)                       # 原文落盘，不是 \uXXXX
        self.assertLess(text.index('"paused"'), text.index('"paused_pr"'))
        self.assertIn('\n  "paused": true,', text)            # 一键一行、缩进两格
        self_improve.save_state(st)                           # 目录已在 → 照样覆盖写
        self.assertTrue(self_improve.lane_paused())

    def test_pausing_with_no_state_dir_still_takes_the_lock_and_returns_the_callers_copy(self):
        self._wipe_state_dir()
        mine = {"keep": 1}
        out = self_improve.pause("sensitive_paths", pr_number=3, paths=["act/llm.py"],
                                 card="P-7", now=NOW, st=mine)
        self.assertIs(out, mine)                              # 调用方的内存副本同步更新
        self.assertTrue(mine["paused"])
        self.assertEqual(mine["keep"], 1)
        on_disk = self_improve.pause("sensitive_paths", pr_number=4, now=NOW)
        self.assertIsInstance(on_disk, dict)
        self.assertEqual(on_disk["paused_pr"], 4)

    def test_a_rejection_line_is_sorted_utf8_json_and_a_broken_line_is_skipped(self):
        """坏行不许吃掉它后面的记忆——去重靠这份台账（§65.5 / P5 每日循环）。"""
        self._wipe_state_dir()
        entry = {"fingerprint": "abc123", "title": "让 doctor 多一行", "card": "P-7"}
        self_improve.record_rejection(entry)
        path = self_improve.rejected_path()
        self.assertEqual(path.read_text(encoding="utf-8"),
                         json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        self.assertIn("让 doctor 多一行", path.read_text(encoding="utf-8"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{ not json\n")
            fh.write(json.dumps({"fingerprint": "def456", "card": "P-8"}) + "\n")
        self.assertEqual([e["card"] for e in self_improve.rejected_entries()], ["P-7", "P-8"])
        self.assertTrue(self_improve.is_rejected("def456"))

    def test_the_rejection_memory_is_capped_at_256_kib_and_not_a_byte_sooner(self):
        """防腐 #4：出生即带帽。正好 256 KiB 不动，超出一行就压到最近半数行。"""
        path = self_improve.rejected_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(_padded(i), ensure_ascii=False, sort_keys=True) + "\n"
                                for i in range(1023)), encoding="utf-8")
        self_improve.record_rejection(_padded(1023))
        self.assertEqual(path.stat().st_size, 256 * 1024)
        self.assertEqual(len(self_improve.rejected_entries()), 1024)
        self_improve.record_rejection(_padded(1024))
        self.assertEqual(len(self_improve.rejected_entries()), 513)

    def test_the_fingerprint_is_a_stable_sixteen_hex_digest_of_the_normalised_title(self):
        fp = self_improve.fingerprint("  Let   DOCTOR say  more ")
        self.assertEqual(fp, self_improve.fingerprint("let doctor say more"))
        self.assertEqual(fp, hashlib.sha1(b"let doctor say more").hexdigest()[:16])
        self.assertEqual(len(fp), 16)

    def test_the_cli_prints_the_board_view_as_indented_utf8_json(self):
        self_improve.save_state({"paused": True, "paused_reason": "敏感路径",
                                 "paused_pr": 3, "paused_paths": ["act/llm.py"]})
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(self_improve._main([]), 0)
        text = buf.getvalue()
        self.assertIn("敏感路径", text)                       # UTF-8 原文，不转义
        doc = json.loads(text)
        self.assertTrue(doc["paused"])
        self.assertEqual(text, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def _padded(i: int) -> dict:
    """序列化后正好 255 字符（+ 换行 = 256 B）的一条拒绝记忆——帽的边界要算得准。"""
    entry = {"card": "P-%04d" % i, "fingerprint": "%016x" % i, "pad": ""}
    entry["pad"] = "x" * (255 - len(json.dumps(entry, ensure_ascii=False, sort_keys=True)))
    return entry


# --------------------------------------------------------------------------- #
# §65.4 敏感路径护栏：标签是锦上添花，暂停才是真源
# --------------------------------------------------------------------------- #
class SensitiveGuardTestCase(LaneCase):
    def _harvest(self, gh, **over):
        card = lane_card("P-7", **over)
        ex: dict = {}
        logs: list = []
        self_improve.on_harvest(card, ex, cfg=self.cfg, gh=gh, log=logs.append)
        return ex["delivery"], logs

    def test_a_pr_without_a_number_cannot_be_labelled_but_still_pauses_the_lane(self):
        pr = pr_doc(7, branch=BRANCH, files=("act/lib/policy.py",))
        pr["number"] = None                                   # gh 回了一份缺编号的 JSON
        delivery, logs = self._harvest(FakeGh({7: pr}))
        self.assertEqual(delivery["sensitive_paths"], ["act/lib/policy.py"])
        self.assertIsNone(delivery["label"])
        self.assertIn("labelled=False", logs[0])
        self.assertTrue(self_improve.lane_paused())            # 标签失败不阻塞暂停

    def test_gh_dying_mid_label_still_pauses_the_lane(self):
        class _NoLabelGh(FakeGh):
            def __call__(self, args, cwd):
                if tuple(args[:2]) == ("label", "create"):
                    raise self_improve.GhUnavailable("gh went away")
                return super().__call__(args, cwd)

        gh = _NoLabelGh({7: pr_doc(7, branch=BRANCH, files=("act/llm.py",))})
        delivery, logs = self._harvest(gh)
        self.assertIsNone(delivery["label"])
        self.assertIn("labelled=False", logs[0])
        self.assertTrue(self_improve.lane_paused())

    def test_the_notices_name_the_card_even_with_no_title_and_never_say_none(self):
        """通知里的 "None" 是 owner 看得见的谎——标题缺失就用卡号，URL 缺失就留空。"""
        pr = pr_doc(7, branch=BRANCH, files=("act/lib/policy.py",))
        del pr["url"]                                          # gh 回了一份没有 url 的 JSON
        delivery, _logs = self._harvest(FakeGh({7: pr}), title="")
        self.assertFalse(delivery["verified"])
        bodies = self.bodies()
        self.assertEqual(len(bodies), 2)                       # 暂停 + 未核验各一条
        for body in bodies:
            self.assertIn("P-7", body)
            self.assertNotIn("None", body)

    def test_the_dispatch_record_pins_the_repo_identity(self):
        """§65.3 的仓库身份在派发那一刻就钉住——审计痕不许是 None。"""
        cfg = config.Config(raw={"self_improve": {"github_repo": SLUG}},
                            self_improve_enabled=True)
        record = self_improve.dispatch_record(lane_card("P-7"), cfg)
        self.assertEqual(record["self_improve"],
                         {"branch": BRANCH, "egress": "none", "lane": True, "repo": SLUG})


# --------------------------------------------------------------------------- #
# §65.5 巡检：谁算 owner、跟进卡、foreign 台账、暂停自动清
# --------------------------------------------------------------------------- #
class TickTestCase(LaneCase):
    def test_a_non_string_gh_login_is_not_an_owner_and_is_not_cached(self):
        """issue #310：身份是准入判据——`gh api user` 回的脏值不许长成一个 owner。"""
        self.review_card()
        gh = FakeGh({268: pr_doc(268, state="MERGED", merged_by="12345")}, login=12345)
        summary = self.tick(gh)
        self.assertEqual(summary["accepted"], [])
        self.assertEqual(registry.load("P-7").status, State.REVIEW.value)
        self.assertNotIn("owner_login", self_improve.load_state())

    def test_a_dirty_cached_login_is_re_read_from_gh(self):
        """lane.json 里的坏缓存不许顶替真身份，否则 owner 自己的合并永不结算。"""
        self.review_card()
        self_improve.save_state({"owner_login": 12345})
        gh = FakeGh({268: pr_doc(268, state="MERGED", merged_by="zelinPostman")},
                    login="zelinPostman")
        self.assertEqual(self.tick(gh)["accepted"], ["P-7"])
        self.assertEqual(self_improve.load_state()["owner_login"], "zelinPostman")

    def test_accepting_a_merge_appends_to_the_notes_instead_of_replacing_them(self):
        self.review_card(notes="原有备注")
        gh = FakeGh({268: pr_doc(268, state="MERGED", merged_by="o")})
        self.assertEqual(self.tick(gh)["accepted"], ["P-7"])
        notes = registry.load("P-7").notes
        self.assertIn("原有备注", notes)
        self.assertIn("PR merged", notes)

    def test_only_a_closed_event_actor_settles_a_closed_pr(self):
        """`gh pr view` 不给 closedBy——事件流里只有 ``closed`` 那一条算数。"""
        class _EventsGh(FakeGh):
            events: list = []

            def __call__(self, args, cwd):
                if args[0] == "api" and args[1].endswith("/events"):
                    self.calls.append(list(args))
                    return 0, json.dumps(self.events)
                return super().__call__(args, cwd)

        self.review_card()
        gh = _EventsGh({268: pr_doc(268, state="CLOSED")}, login="Wan-ZL", slug="Wan-ZL/r")
        # 关闭事件在前、打标签在后：只认 `closed` 那一条，否则 owner 的关闭会被
        # 一个机器人的后续事件顶掉，卡永不结算。
        gh.events = [{"event": "closed", "actor": {"login": "Wan-ZL"}},
                     {"event": "labeled", "actor": {"login": "github-actions[bot]"}}]
        summary = self.tick(gh)
        self.assertEqual(summary["rejected"], ["P-7"])
        self.assertTrue(self_improve.is_rejected(self_improve.fingerprint("lane 测试卡")))

    def test_the_foreign_ledger_stays_at_two_hundred_and_drops_the_undated_first(self):
        """防腐 #4：出生即带帽；没有时间戳的条目按最旧算（先丢）。"""
        ledger = {str(n): {"state": "CLOSED", "actor": "x", "at": "2026-01-02T00:00:00Z",
                           "card": "P-1"} for n in range(1, 200)}
        ledger["0"] = {"state": "CLOSED", "actor": "x", "card": "P-1"}      # 没有 at
        self.assertEqual(len(ledger), 200)
        self.review_card(pr=999)
        self_improve.save_state({"foreign": ledger})
        gh = FakeGh({999: pr_doc(999, state="CLOSED")}, login="owner-account",
                    closers={999: ["stranger"]})
        summary = self.tick(gh)
        self.assertEqual(summary["rejected"], [])
        self.assertEqual(registry.load("P-7").status, State.REVIEW.value)
        foreign = self_improve.load_state()["foreign"]
        self.assertEqual(len(foreign), 200)
        self.assertIn("999", foreign)
        self.assertNotIn("0", foreign)

    def test_a_lane_that_is_not_paused_is_never_resumed(self):
        """「恢复通道」只对真的暂停有意义——没暂停就不该去动那张 PR。"""
        self.review_card(pr=7)
        self_improve.save_state({"paused": False, "paused_pr": 42})
        gh = FakeGh({7: pr_doc(7, branch=BRANCH),
                     42: pr_doc(42, state="MERGED", merged_by="o")})
        summary = self.tick(gh)
        self.assertFalse(summary["resumed"])
        self.assertNotIn("resumed_at", self_improve.load_state())
        self.assertEqual(gh.argv_with("pr", "view", "42"), [])   # 根本不去查它

    def test_a_flagged_pr_that_cannot_be_found_leaves_the_lane_paused(self):
        """查不到被标记的 PR = 不知道 → 保持暂停，且绝不崩巡检（§0 第 11 条）。"""
        self_improve.save_state({"paused": True, "paused_pr": 99})
        summary = self.tick(FakeGh({}))
        self.assertEqual(summary, {"accepted": [], "rejected": [], "followups": [],
                                   "resumed": False})
        self.assertTrue(self_improve.lane_paused())


# --------------------------------------------------------------------------- #
# §65.5 跟进卡：owner 评论的归一与帽
# --------------------------------------------------------------------------- #
class FollowupTestCase(LaneCase):
    def test_a_comment_without_a_usable_timestamp_is_dropped_and_the_url_is_carried(self):
        gh = FakeGh({9: pr_doc(9)}, comments={9: [
            {"author": {"login": "Wan-ZL"}, "createdAt": 20260909, "body": "数字时间戳"},
            {"author": {"login": "Wan-ZL"}, "createdAt": "2026-09-09T10:00:00Z",
             "body": "真的一条", "url": "https://github.com/o/r/pull/9#issuecomment-1"},
        ]})
        rows = self_improve.owner_comments(gh, "/repo", 9, {"Wan-ZL"}, slug=SLUG)
        self.assertEqual([r["body"] for r in rows], ["真的一条"])
        self.assertEqual(rows[0]["url"], "https://github.com/o/r/pull/9#issuecomment-1")

    def test_the_follow_up_credits_the_earliest_commenter_and_caps_the_quote(self):
        comments = [{"login": "first", "at": "2026-09-09T10:00:00Z", "body": "x" * 3000,
                     "url": None},
                    {"login": "second", "at": "2026-09-09T11:00:00Z", "body": "later",
                     "url": None}]
        card = self_improve.mint_followup(pr_doc(9), comments, ["Gates"], self.cfg, NOW)
        src = self_improve.pr_source(card)
        self.assertEqual(src["who"], "first")
        self.assertEqual(len(src["quote"]), 1500)
