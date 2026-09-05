"""§17（issue #23）重点人物账本 — 一轮 pass 端到端（act/people_ledger.py，假 runner）。

默认关 = 零痕迹（不打日志、不打 analytics）；首跑只记游标不回填；此后只处理
游标之上的笔记且每轮 ≤ max_notes_per_pass（余量下一轮）；只对被提及的人
发一次密封模型调用（prompt 含 owner / 人名 / 围栏内的 open 条目与笔记）；输出
并入该人账本并重写渲染稿；坏 JSON / 调用失败只记日志不崩 pass；通知在 pass
末尾合并（≤3 人逐人，>3 人一条汇总）；锁被占直接让路。假 runner 站在 claude
的位置——本套件绝不 spawn 真 claude。
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import people_ledger as pl
from act.lib import analytics, config, notify
from act.lib import people_ledger_store as store


def reply(new=None, done=None):
    return json.dumps({"new": new or [], "done": done or []})


class FakeRunner:
    """llm.run's runner seam: records the prompt, answers from a queue."""

    def __init__(self, *replies):
        self.replies = list(replies) or [reply()]
        self.prompts = []

    def __call__(self, argv, **kwargs):
        self.prompts.append(kwargs.get("input") or argv[argv.index("-p") + 1])   # prompt_via="arg"：紧跟 -p
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return subprocess.CompletedProcess(argv, 0, stdout=r, stderr="")


class LedgerRunnerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-ledger-run-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "state").mkdir()
        self.vault = self.root / "raw"
        self.vault.mkdir()
        mock.patch.object(config, "STATE_DIR", self.root / "state").start()
        self.addCleanup(mock.patch.stopall)
        self.notified = []
        mock.patch.object(notify, "notify", side_effect=lambda *a, **k: self.notified.append((a, k)) or True).start()
        self.events = []
        mock.patch.object(analytics, "log_event", side_effect=lambda ev, **f: self.events.append((ev, f)) or True).start()
        self.cfg = config.Config(owner_name="Zelin", people_ledger_enabled=True,
                                 people_ledger_people=["arash.k", "sal.khan"], raw={"people_ledger": {}})
        self.t = time.time() + 60.0   # 未来 mtime：首跑（无笔记）把游标记成 now，之后写的笔记必须在游标之上

    def note(self, name, text, age=0):
        p = self.vault / name
        p.write_text(text, encoding="utf-8")
        self.t += 10
        os.utime(p, (self.t - age, self.t - age))
        return p

    def run_once(self, runner=None, cfg=None):
        return pl.run_once(cfg=cfg or self.cfg, runner=runner or FakeRunner(), root=self.vault)

    def first_run(self):
        s = self.run_once()
        self.assertTrue(s.get("first_run"))
        return s

    # ---- gates ----
    def test_disabled_is_a_silent_no_op(self):
        self.note("a.md", "Arash: I'll send it")
        s = self.run_once(cfg=config.Config(people_ledger_enabled=False, people_ledger_people=["arash.k"]))
        self.assertEqual(s["skipped"], "disabled")
        self.assertFalse(store.ledger_dir().exists())
        self.assertEqual(self.events, [])
        self.assertEqual(self.notified, [])

    def test_no_valid_people_skips_and_logs_the_guard(self):
        cfg = config.Config(people_ledger_enabled=True, watch_people=["your.manager"])
        s = self.run_once(cfg=cfg)
        self.assertEqual((s["skipped"], s["people"]), ("no_people", 0))
        log = store.log_path().read_text(encoding="utf-8")
        self.assertIn("'your.manager' disabled", log)
        self.assertIsNone(store.load_cursor())

    def test_vault_missing(self):
        s = pl.run_once(cfg=self.cfg, runner=FakeRunner(), root=self.root / "nope")
        self.assertEqual(s["skipped"], "vault_missing")

    # ---- first run: no backfill ----
    def test_first_run_records_cursor_and_backfills_nothing(self):
        self.note("old.md", "Arash promised to review the PR. I'll send the report to Arash.")
        runner = FakeRunner()
        s = pl.run_once(cfg=self.cfg, runner=runner, root=self.vault)
        self.assertTrue(s["first_run"])
        self.assertEqual(runner.prompts, [])
        self.assertEqual(store.load_cursor()["marker"], self.t)
        self.assertEqual([e for e, _ in self.events], ["people_ledger_first_run"])
        self.assertEqual(self.notified, [])
        # 第二轮：旧笔记仍不处理
        runner = FakeRunner()
        s = pl.run_once(cfg=self.cfg, runner=runner, root=self.vault)
        self.assertEqual((s["notes"], runner.prompts), (0, []))

    # ---- the happy path ----
    def test_new_note_updates_only_mentioned_people(self):
        self.first_run()
        self.note("2026-09-04-sync.md", "Sync with Arash. Zelin: I'll send the eval report by Friday.")
        runner = FakeRunner(reply(new=[{"direction": "owner_owes", "text": "Send the eval report by Friday",
                                        "quote": "I'll send the eval report by Friday", "speaker": "owner"},
                                       {"direction": "person_owes", "text": "ignored", "speaker": "assistant"}]))
        s = pl.run_once(cfg=self.cfg, runner=runner, root=self.vault)
        self.assertEqual((s["notes"], s["pairs"], s["new_items"], s["done_items"]), (1, 1, 1, 0))
        prompt = runner.prompts[0]
        self.assertIn("between Zelin and Arash", prompt)
        self.assertIn("I'll send the eval report", prompt)
        self.assertIn("CURRENT OPEN ITEMS", prompt)
        doc = store.load_ledger(store.Person("arash.k", store.tokens_for("arash.k")))
        self.assertEqual([it["text"] for it in store.open_items(doc)], ["Send the eval report by Friday"])
        self.assertEqual(doc["items"][0]["note"], "2026-09-04-sync.md")
        self.assertTrue(store.ledger_path("arash-k").exists())
        self.assertFalse(store.ledger_path("sal-khan").exists())       # 未提及 → 不调用、不建账
        rendered = store.rendered_path(self.cfg, "arash-k")
        self.assertIn("L-1 · Send the eval report by Friday", rendered.read_text(encoding="utf-8"))
        self.assertEqual(len(self.notified), 1)
        (title, body), kw = self.notified[0]
        self.assertIn("Arash", title)
        self.assertIn(str(rendered), body)
        self.assertEqual(kw["kind"], pl.NOTIFY_KIND)
        ev = dict(self.events)["people_ledger_pass"]
        self.assertEqual((ev["people"], ev["notes"], ev["new_items"]), (2, 1, 1))
        for v in ev.values():                      # 只有计数，没有人名 / 文件名
            self.assertIsInstance(v, (int, float))

    def test_later_note_marks_done_and_open_items_ride_in_the_prompt(self):
        self.first_run()
        self.note("n1.md", "Arash: I'll review your PR tomorrow.")
        pl.run_once(cfg=self.cfg, runner=FakeRunner(reply(new=[
            {"direction": "person_owes", "text": "Review Zelin's PR", "quote": "I'll review your PR tomorrow"}])),
            root=self.vault)
        self.note("n2.md", "Arash reviewed the PR this morning.")
        runner = FakeRunner(reply(done=["L-1"]))
        s = pl.run_once(cfg=self.cfg, runner=runner, root=self.vault)
        self.assertEqual((s["new_items"], s["done_items"]), (0, 1))
        self.assertIn('"id": "L-1"', runner.prompts[0])
        doc = store.load_ledger(store.Person("arash.k", store.tokens_for("arash.k")))
        self.assertEqual(doc["items"][0]["status"], "done")
        self.assertEqual(doc["items"][0]["done_note"], "n2.md")
        self.assertIn("- [x] L-1", store.rendered_path(self.cfg, "arash-k").read_text(encoding="utf-8"))

    def test_nothing_new_writes_nothing_and_notifies_nobody(self):
        self.first_run()
        self.note("n1.md", "Chatted with Arash about the weather.")
        s = self.run_once(runner=FakeRunner(reply()))
        self.assertEqual((s["pairs"], s["new_items"]), (1, 0))
        self.assertFalse(store.ledger_path("arash-k").exists())
        self.assertEqual(self.notified, [])

    # ---- guards on the model side ----
    def test_bad_output_and_failed_call_are_logged_not_fatal(self):
        self.first_run()
        self.note("n1.md", "Arash said hi")
        self.note("n2.md", "Sal said hi")
        runner = FakeRunner("no json here", RuntimeError("boom"))
        s = self.run_once(runner=runner)
        self.assertEqual((s["notes"], s["pairs"], s["parse_failed"], s["call_failed"]), (2, 2, 1, 1))
        log = store.log_path().read_text(encoding="utf-8")
        self.assertIn("unparseable output on n1.md for arash-k", log)
        self.assertIn("call failed on n2.md for sal-khan: RuntimeError", log)
        self.assertEqual(store.load_cursor()["marker"], self.t)      # 仍推进（不回填、不重烧）

    def test_new_and_done_of_the_wrong_type_are_dropped_not_fatal(self):
        """宪法第 11 条：``new`` 给了数字 / dict、``done`` 给了字串——丢，不崩 pass。"""
        self.first_run()
        self.note("n1.md", "Arash said hi")
        self.note("n2.md", "Arash said hi again")
        runner = FakeRunner(json.dumps({"new": 5, "done": "L-1"}),
                            json.dumps({"new": {"direction": "owner_owes", "text": "x"}, "done": None}))
        s = self.run_once(runner=runner)
        self.assertEqual((s["notes"], s["pairs"], s["new_items"], s["done_items"], s["parse_failed"]),
                         (2, 2, 0, 0, 0))
        self.assertFalse(store.ledger_path("arash-k").exists())

    def test_main_never_leaks_a_traceback(self):
        """cron 链的 stdout/stderr 进 screenpipe 日志：崩了只留 stderr 一行 + ledger.log 里的 traceback，exit 1。"""
        with mock.patch.object(pl, "run_once", side_effect=KeyError("boom")), \
                mock.patch("builtins.print") as out:
            self.assertEqual(pl.main(["--once"]), 1)
        self.assertEqual(out.call_count, 1)
        self.assertIn("people_ledger: failed", out.call_args[0][0])
        self.assertIs(out.call_args[1].get("file"), pl.sys.stderr)
        log = store.log_path().read_text(encoding="utf-8")
        self.assertIn("run crashed:", log)
        self.assertIn("KeyError", log)

    def test_pass_budget_defers_the_rest_but_keeps_same_mtime_siblings_together(self):
        """墙钟预算：超预算后不开新笔记、游标停在最后处理的那篇；同 mtime 的兄弟篇一起过。"""
        self.first_run()
        a = self.note("a.md", "Arash a")
        self.note("b.md", "Arash b")
        os.utime(a, (self.t, self.t))                    # a 与 b 同 mtime
        self.note("c.md", "Arash c")
        self.note("d.md", "Arash d")
        with mock.patch.object(pl, "PASS_BUDGET_S", -1):   # 每次边界检查都算超预算
            runner = FakeRunner(reply())
            s = self.run_once(runner=runner)
        self.assertEqual(s["notes"], 2)                     # a + b（同 mtime），c / d 留下轮
        seen = "".join(runner.prompts)
        self.assertIn("Arash a", seen)
        self.assertIn("Arash b", seen)
        self.assertNotIn("Arash c", seen)
        self.assertEqual(store.load_cursor()["marker"], self.t - 20)   # b 的 mtime（c、d 各 +10）
        self.assertIn("pass budget", store.log_path().read_text(encoding="utf-8"))
        s = self.run_once(runner=FakeRunner(reply()))       # 默认预算：余量一轮清完
        self.assertEqual(s["notes"], 2)

    def test_prompt_carries_only_the_most_recent_open_items(self):
        self.first_run()
        p = store.Person("arash.k", store.tokens_for("arash.k"))
        doc = store.load_ledger(p)
        store.merge(doc, [{"direction": "owner_owes", "text": "task %d" % i} for i in range(pl.PROMPT_OPEN_MAX + 5)],
                    [], "seed.md", "2026-09-01")
        store.save_ledger(doc)
        self.note("n.md", "Arash")
        runner = FakeRunner(reply())
        self.run_once(runner=runner)
        opened = json.loads(runner.prompts[0].split("CURRENT OPEN ITEMS:\n", 1)[1].split("\n")[1])
        self.assertEqual(len(opened), pl.PROMPT_OPEN_MAX)
        self.assertEqual(opened[-1]["id"], "L-%d" % (pl.PROMPT_OPEN_MAX + 5))
        self.assertNotIn("L-1", {it["id"] for it in opened})

    def test_output_tolerates_fences_and_prose(self):
        self.assertEqual(pl.parse_output('Sure!\n```json\n{"new": [], "done": ["L-2"]}\n```'), {"new": [], "done": ["L-2"]})
        self.assertIsNone(pl.parse_output("[1, 2]"))
        self.assertIsNone(pl.parse_output(""))
        self.assertIsNone(pl.parse_output(None))
        self.assertEqual(pl.parse_output('{"a": {"b": 1}} trailing'), {"a": {"b": 1}})
        # 第一个 "{" 不是合法 JSON → 跳过继续找
        self.assertEqual(pl.parse_output('{oops} then {"a": 1}'), {"a": 1})
        self.assertIsNone(pl.parse_output("{oops} {still bad"))

    def test_main_status_and_once(self):
        with mock.patch.object(pl, "status_lines", return_value=["Arash: 0 open / 0 total → x"]), \
                mock.patch("builtins.print") as out:
            self.assertEqual(pl.main(["--status"]), 0)
            out.assert_called_once_with("Arash: 0 open / 0 total → x")
        with mock.patch.object(pl, "status_lines", return_value=[]), mock.patch("builtins.print") as out:
            pl.main(["--status"])
            out.assert_called_once_with("(no people configured)")
        with mock.patch.object(pl, "run_once", return_value={"enabled": False, "skipped": "disabled"}), \
                mock.patch("builtins.print") as out:
            self.assertEqual(pl.main(["--once"]), 0)
            out.assert_not_called()                                   # 默认关：零输出
        with mock.patch.object(pl, "run_once", return_value={"enabled": True, "notes": 1}), \
                mock.patch("builtins.print") as out:
            self.assertEqual(pl.main(["--once"]), 0)
            self.assertEqual(json.loads(out.call_args[0][0]), {"enabled": True, "notes": 1})

    def test_unreadable_note_is_skipped(self):
        self.first_run()
        p = self.note("bad.md", "x")
        p.write_bytes(b"\xff\xfe\x00bad")
        s = self.run_once(runner=FakeRunner())
        self.assertEqual(s["notes"], 0)
        self.assertIn("unreadable note bad.md", store.log_path().read_text(encoding="utf-8"))

    # ---- caps + cursor ----
    def test_per_pass_cap_leaves_the_rest_for_the_next_round(self):
        self.first_run()
        self.cfg.raw["people_ledger"]["max_notes_per_pass"] = 2
        for i in range(5):
            self.note("n%d.md" % i, "Arash %d" % i)
        runner = FakeRunner(reply())
        s = self.run_once(runner=runner)
        self.assertEqual((s["notes"], s["pairs"]), (2, 2))
        self.assertTrue(all("Arash 0" in p or "Arash 1" in p for p in runner.prompts))   # 最早两篇先走
        self.assertEqual(store.load_cursor()["marker"], self.t - 30)      # 第 2 篇的 mtime
        s = self.run_once(runner=FakeRunner(reply()))
        self.assertEqual(s["notes"], 2)
        s = self.run_once(runner=FakeRunner(reply()))
        self.assertEqual(s["notes"], 1)
        self.assertEqual(pl.max_notes_per_pass(config.Config(raw={"people_ledger": {"max_notes_per_pass": "x"}})),
                         pl.DEFAULT_MAX_NOTES_PER_PASS)
        self.assertEqual(pl.max_notes_per_pass(config.Config(raw={"people_ledger": {"max_notes_per_pass": 0}})),
                         pl.DEFAULT_MAX_NOTES_PER_PASS)

    def test_due_notes_keeps_same_mtime_siblings_together(self):
        notes = [(1.0, Path("a")), (2.0, Path("b")), (2.0, Path("c")), (3.0, Path("d"))]
        self.assertEqual([p.name for _, p in pl.due_notes(notes, 0.5, 2)], ["a", "b", "c"])
        self.assertEqual([p.name for _, p in pl.due_notes(notes, 2.0, 2)], ["d"])
        self.assertEqual(pl.due_notes(notes, 9.0, 2), [])

    # ---- coalesced notifications ----
    def test_more_than_three_people_get_one_summary(self):
        cfg = config.Config(owner_name="Zelin", people_ledger_enabled=True, raw={"people_ledger": {}},
                            people_ledger_people=["alice.a", "bobby.b", "carol.c", "diana.d"])
        pl.run_once(cfg=cfg, runner=FakeRunner(), root=self.vault)
        self.note("all.md", "alice bobby carol diana all promised things")
        runner = FakeRunner(reply(new=[{"direction": "person_owes", "text": "do it"}]))
        s = pl.run_once(cfg=cfg, runner=runner, root=self.vault)
        self.assertEqual((s["pairs"], s["new_items"]), (4, 4))
        self.assertEqual(len(self.notified), 1)
        (title, body), _kw = self.notified[0]
        self.assertIn("4", body)

    # ---- lock ----
    @unittest.skipIf(pl.fcntl is None, "flock is POSIX-only; the Windows pass runs unlocked by design")
    def test_lock_held_yields(self):
        self.first_run()
        self.note("n.md", "Arash")
        held = pl._acquire_lock()
        self.assertIsNotNone(held)
        try:
            s = self.run_once(runner=FakeRunner())
        finally:
            held.close()
        self.assertEqual(s["skipped"], "lock_held")
        self.assertIn(("people_ledger_skip", {"reason": "lock_held"}), self.events)

    def test_status_lines(self):
        self.first_run()
        lines = pl.status_lines(self.cfg)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("Arash: 0 open / 0 total"))

    def test_time_source_is_note_mtime_not_now(self):
        self.first_run()
        self.note("n.md", "Arash", age=0)
        runner = FakeRunner(reply(new=[{"direction": "owner_owes", "text": "x"}]))
        pl.run_once(cfg=self.cfg, runner=runner, root=self.vault)
        doc = store.load_ledger(store.Person("arash.k", store.tokens_for("arash.k")))
        self.assertEqual(doc["items"][0]["date"], time.strftime("%Y-%m-%d", time.localtime(self.t)))


if __name__ == "__main__":
    unittest.main()
