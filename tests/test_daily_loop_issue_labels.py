"""§70.3 ⑩ 追记：owner 的 tracker 分诊标签先于一切——带 `loop_inputs.EXCLUDED_ISSUE_LABELS`
任一标签的开放 issue 永不成提案（PR #213 判例：#23 带 `素材库-idea` 仍被铸卡）。

钉住的行为：有标签 → 零 Signal、一行 `issue_parked` 摘要、不花「do it」额度；同一张
去掉标签 → 照旧成 Signal；标签名逐字、区分大小写；八枚标签逐个参数化；标题仍进
`titles`（`gh_title` 同题去重不变）；整轮运行的审计行 `skipped.label_parked` 计数。
零网络：gh 是注入的假 runner，整轮跑在沙箱 AIASSISTANT_HOME（tests/__init__.py）。
"""
import datetime as _dt
import json
import unittest

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, daily_loop, loop_inputs, registry

TZ = _dt.timezone(_dt.timedelta(hours=-7))
NOW = _dt.datetime(2026, 9, 5, 3, 31, tzinfo=TZ)

PARKED = {"number": 23, "title": "Generalize the manager pack into a per-person commitments ledger",
          "author": {"login": "Wan-ZL"}, "body": "product idea", "url": "u23",
          "labels": [{"id": "L1", "name": "enhancement", "color": "a2eeef"},
                     {"id": "L2", "name": "素材库-idea", "color": "ededed"}]}
PLAIN = dict(PARKED, labels=[{"id": "L1", "name": "enhancement"}])


def _fake_gh(issues, comments=None):
    """`gh issue list` → issues，但只回 `--json` 点名的字段（真 gh 亦然——`labels` 不在
    字段表里就没有 `labels`，钉住 wire 契约）；`gh issue view` → comments（None = gh 失败）；
    记下每次调用。"""
    calls = []

    def gh(args):
        calls.append(list(args))
        if args[:2] == ["issue", "list"] and "--search" not in args:
            fields = args[args.index("--json") + 1].split(",")
            return json.dumps([{k: v for k, v in row.items() if k in fields} for row in issues])
        if args[:2] == ["issue", "view"] and comments is not None:
            return json.dumps({"comments": comments})
        return None
    gh.calls = calls
    return gh


def _list_fields(gh) -> list:
    """记录里那次 `gh issue list` 请求的 `--json` 字段表。"""
    call = next(c for c in gh.calls if c[:2] == ["issue", "list"] and "--search" not in c)
    return call[call.index("--json") + 1].split(",")


class ParkedLabelTestCase(unittest.TestCase):
    def test_labelled_issue_never_becomes_a_signal(self):
        gh = _fake_gh([PARKED])
        sigs, summaries, titles = loop_inputs.issue_signals(gh)
        self.assertEqual(sigs, [])
        self.assertEqual([s.kind for s in summaries], [loop_inputs.PARKED_SUMMARY_KIND])
        self.assertIn("#23", summaries[0].text)
        self.assertIn("素材库-idea", summaries[0].text)
        self.assertEqual(summaries[0].ref, "u23")
        self.assertEqual(titles, [PARKED["title"]])            # still open → still dedups gh_title
        self.assertIn("labels", _list_fields(gh))              # the wire field the whole gate hangs on

    def test_same_issue_without_the_label_is_proposed(self):
        sigs, summaries, _ = loop_inputs.issue_signals(_fake_gh([PLAIN]))
        self.assertEqual([s.fingerprint for s in sigs], ["issue:23"])
        self.assertEqual(summaries, [])

    def test_label_match_is_exact_and_case_sensitive(self):
        for name, parked in (("wontfix", True), ("Wontfix", False), ("WONTFIX", False),
                             ("wontfix ", False), ("素材库", False), ("素材库-idea", True),
                             ("素材库-Idea", False), ("needs-owner-eyes", False)):
            with self.subTest(label=name):
                issue = dict(PLAIN, labels=[{"name": name}])
                self.assertEqual(loop_inputs.parked_label(issue), name if parked else None)
                sigs, _, _ = loop_inputs.issue_signals(_fake_gh([issue]))
                self.assertEqual(len(sigs), 0 if parked else 1)

    def test_every_excluded_label_parks(self):
        self.assertEqual(loop_inputs.EXCLUDED_ISSUE_LABELS,
                         ("素材库-idea", "needs-owner", "wontfix", "invalid", "duplicate",
                          "decision-needed", "proposal", "mac-retire"))
        for name in loop_inputs.EXCLUDED_ISSUE_LABELS:
            with self.subTest(label=name):
                sigs, summaries, _ = loop_inputs.issue_signals(
                    _fake_gh([dict(PLAIN, labels=[{"name": "bug"}, {"name": name}])]))
                self.assertEqual(sigs, [])
                self.assertEqual(len(summaries), 1)

    def test_label_beats_owner_authorship_and_do_it(self):
        # 非 owner 作者 + owner 评论「do it」，但带 needs-owner → 仍不铸，且不花一次 gh issue view
        issue = dict(PARKED, number=90, author={"login": "Carol929"}, labels=[{"name": "needs-owner"}])
        gh = _fake_gh([issue], comments=[{"author": {"login": "Wan-ZL"}, "body": "do it"}])
        sigs, summaries, _ = loop_inputs.issue_signals(gh)
        self.assertEqual(sigs, [])
        self.assertEqual([s.kind for s in summaries], [loop_inputs.PARKED_SUMMARY_KIND])
        self.assertFalse(any(c[:2] == ["issue", "view"] for c in gh.calls))
        # 去掉标签 → D18 的「do it」路径照旧工作
        gh = _fake_gh([dict(issue, labels=[])], comments=[{"author": {"login": "Wan-ZL"}, "body": "do it"}])
        self.assertEqual([s.fingerprint for s in loop_inputs.issue_signals(gh)[0]], ["issue:90"])

    def test_label_shapes_gh_might_return(self):
        self.assertEqual(loop_inputs.parked_label(dict(PLAIN, labels="garbage")), None)
        self.assertEqual(loop_inputs.parked_label(dict(PLAIN, labels=[None, 3, {"id": "x"}])), None)
        self.assertEqual(loop_inputs.parked_label(dict(PLAIN, labels=["wontfix"])), "wontfix")   # bare strings
        self.assertEqual(loop_inputs.parked_label({"number": 1}), None)                          # field missing

    def test_parked_count_only_counts_parked_summaries(self):
        # D18 `issue_nonowner` 与 D33 advisory 摘要同住一个 summaries 列表，不能混进 label_parked
        mixed = [loop_inputs.Summary(kind="issue_nonowner", text="#90 by Carol929"),
                 loop_inputs.Summary(kind=loop_inputs.PARKED_SUMMARY_KIND, text="#23 素材库-idea"),
                 loop_inputs.Summary(kind="advisory", text="doctor FAIL", fingerprint="doctor:x"),
                 loop_inputs.Summary(kind=loop_inputs.PARKED_SUMMARY_KIND, text="#27 mac-retire")]
        self.assertEqual(loop_inputs.parked_count([]), 0)
        self.assertEqual(loop_inputs.parked_count(mixed), 2)
        self.assertEqual(loop_inputs.parked_count(mixed[:1]), 0)


class AuditRowTestCase(unittest.TestCase):
    """整轮 run：parked issue 的计数进审计行 `skipped.label_parked`，摘要行进 `summaries`。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        daily_loop.state_path().unlink(missing_ok=True)
        daily_loop.log_path().unlink(missing_ok=True)
        loop_inputs.materials_path().unlink(missing_ok=True)

    def test_run_counts_parked_issues_and_mints_no_card(self):
        other = {"number": 5, "title": "make the loop quieter please", "body": "b",
                 "author": {"login": "Wan-ZL"}, "url": "u5", "labels": [{"name": "loop-seed"}]}
        # 非 owner、无标签、没人说「do it」→ D18 `issue_nonowner` 摘要，与 parked 同住 summaries
        nonowner = {"number": 90, "title": "please add a dark mode toggle", "body": "b",
                    "author": {"login": "Carol929"}, "url": "u90", "labels": []}
        result = daily_loop.run(config.Config(), now=NOW, gh=_fake_gh([PARKED, other, nonowner]),
                                doctor=lambda: "[]")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["proposals"], 1)
        self.assertEqual(result["summaries"], 2)
        refs = sorted(r.sources[0]["ref"] for r in registry.load_all() if r.title.startswith("🤖 "))
        self.assertEqual(refs, ["self_improve:issue:5"])                # #23 / #90 never minted
        entry = json.loads(daily_loop.log_path().read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(entry["skipped"]["label_parked"], 1)            # counts parked rows only, not #90
        self.assertEqual(entry["skipped"]["advisory"], 0)               # the other skip keys are untouched
        self.assertEqual([s["kind"] for s in entry["summaries"]],
                         [loop_inputs.PARKED_SUMMARY_KIND, "issue_nonowner"])
        self.assertEqual(entry["inputs"]["issues"], 1)


if __name__ == "__main__":
    unittest.main()
