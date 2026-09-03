"""act/digest 进化建议 (§16 self-evolution): the analytics-driven heuristics
behind ``build_suggestions`` and the feature→event relation table.

Pinned (P3 mutation net):
- ``_feature_related``: slack/gmail radars match on source OR an event-name
  prefix; obsidian only on ``radar_scan`` from source obsidian; digest and
  auto_resume on their exact event names; analytics and unknown features are
  ALWAYS related (never suggest closing them);
- the data-sufficiency guard: <14 days of history or <30 events -> no
  "关闭功能" suggestion even with zero related events; enough history and an
  enabled feature with zero related events -> one suggestion, disabled
  features skipped;
- resume storm: strictly more than 10 failed auto_resume/resume_launch events
  (``ok is False`` only);
- reject ratio: strictly above 50 % of approve+reject decisions; no decisions
  -> no suggestion;
- ``file_suggestion_cards`` swallows a filing failure and keeps going.
"""
import datetime as _dt
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import digest
from act.lib import analytics, config


def _ev(event, **kw):
    kw.setdefault("event", event)
    return kw


class FeatureRelatedTestCase(unittest.TestCase):
    def test_radar_features_match_source_or_event_prefix(self):
        self.assertTrue(digest._feature_related("slack_radar", {"source": "slack"}))
        self.assertTrue(digest._feature_related("slack_radar", {"event": "slack_mcp"}))
        self.assertFalse(digest._feature_related("slack_radar", {"event": "radar_scan", "source": "gmail"}))
        self.assertTrue(digest._feature_related("gmail_radar", {"source": "gmail"}))
        self.assertTrue(digest._feature_related("gmail_radar", {"event": "gmail_x"}))
        self.assertFalse(digest._feature_related("gmail_radar", {"event": "x", "source": "slack"}))

    def test_obsidian_needs_radar_scan_from_obsidian(self):
        self.assertTrue(digest._feature_related(
            "obsidian_radar", {"event": "radar_scan", "source": "obsidian"}))
        self.assertFalse(digest._feature_related(
            "obsidian_radar", {"event": "radar_skip", "source": "obsidian"}))
        self.assertFalse(digest._feature_related(
            "obsidian_radar", {"event": "radar_scan", "source": "gmail"}))

    def test_exact_event_features(self):
        self.assertTrue(digest._feature_related("digest", {"event": "digest_generated"}))
        self.assertTrue(digest._feature_related("digest", {"event": "oneonone_prep"}))
        self.assertFalse(digest._feature_related("digest", {"event": "digest_skip"}))
        for name in ("auto_resume", "resume_launch", "auto_resume_exhausted"):
            self.assertTrue(digest._feature_related("auto_resume", {"event": name}))
        self.assertFalse(digest._feature_related("auto_resume", {"event": "resume_ok"}))

    def test_analytics_and_unknown_features_are_always_related(self):
        self.assertTrue(digest._feature_related("analytics", {"event": "anything"}))
        self.assertTrue(digest._feature_related("analytics", {}))
        self.assertTrue(digest._feature_related("brand_new_flag", {}))


class SuggestionHeuristicsTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self.cfg = config.Config()

    def _history(self, days, count):
        """Fake the all-time analytics log: ``count`` events spanning ``days``."""
        base = _dt.datetime(2026, 6, 1, tzinfo=_dt.timezone.utc)
        step = (_dt.timedelta(days=days) / max(count - 1, 1)) if count > 1 else _dt.timedelta(0)
        events = [{"event": "noise", "ts": (base + step * i).strftime("%Y-%m-%dT%H:%M:%SZ")}
                  for i in range(count)]
        return mock.patch.object(analytics, "read_events", return_value=iter(events))

    def test_short_history_never_suggests_closing(self):
        with self._history(days=3, count=50):
            out = digest.build_suggestions(self.cfg, events=[])
        self.assertEqual([t for t, _ in out if t.startswith("建议关闭")], [])
        with self._history(days=30, count=10):
            out = digest.build_suggestions(self.cfg, events=[])
        self.assertEqual([t for t, _ in out if t.startswith("建议关闭")], [])

    def test_enough_history_flags_unused_enabled_features_only(self):
        self.cfg.features = dict(config.DEFAULT_FEATURES)
        self.cfg.features["gmail_radar"] = False          # off -> never suggested
        related = [_ev("radar_scan", source="obsidian"), _ev("slack_x"),
                   _ev("digest_generated"), _ev("auto_resume")]
        with self._history(days=20, count=40):
            out = digest.build_suggestions(self.cfg, events=related)
        titles = [t for t, _ in out]
        self.assertNotIn("建议关闭功能 gmail_radar（近 30 天零相关事件，白耗资源）", titles)
        self.assertNotIn("建议关闭功能 slack_radar（近 30 天零相关事件，白耗资源）", titles)
        self.assertNotIn("建议关闭功能 obsidian_radar（近 30 天零相关事件，白耗资源）", titles)
        # feedback_sync / auto_deploy have no related events in the table at
        # all -> always related -> never suggested; analytics likewise
        self.assertEqual([t for t in titles if t.startswith("建议关闭")], [])
        # drop the slack event -> slack_radar becomes the one unused feature
        with self._history(days=20, count=40):
            out = digest.build_suggestions(self.cfg, events=related[:1] + related[2:])
        self.assertEqual([t for t, _ in out if t.startswith("建议关闭")],
                         ["建议关闭功能 slack_radar（近 30 天零相关事件，白耗资源）"])

    def test_resume_storm_is_strictly_more_than_ten_failures(self):
        fails = [_ev("auto_resume", ok=False)] * 5 + [_ev("resume_launch", ok=False)] * 5
        with self._history(days=1, count=2):
            self.assertEqual(digest.build_suggestions(self.cfg, events=fails), [])
            out = digest.build_suggestions(
                self.cfg, events=fails + [_ev("auto_resume", ok=False)])
        self.assertEqual(out, [("建议修自动恢复（近 30 天失败频繁）",
                                "近 30 天失败 11 次（无效重复的头号来源）")])
        # ok=None / ok=True / other events never count
        with self._history(days=1, count=2):
            self.assertEqual(digest.build_suggestions(
                self.cfg, events=[_ev("auto_resume")] * 20 + [_ev("auto_resume", ok=True)] * 20
                + [_ev("other", ok=False)] * 20), [])

    def test_reject_ratio_is_strictly_above_half(self):
        with self._history(days=1, count=2):
            self.assertEqual(digest.build_suggestions(self.cfg, events=[]), [])
            half = [_ev("inbox_reject")] * 2 + [_ev("inbox_approve")] * 2
            self.assertEqual(digest.build_suggestions(self.cfg, events=half), [])
            out = digest.build_suggestions(self.cfg, events=half + [_ev("inbox_reject")])
        self.assertEqual(out, [("建议改进提案质量（拒绝率超过 50%）",
                                "拒绝率 3/5（先看卡片 summary 是否说人话）")])

    def test_default_window_reads_the_last_30_days(self):
        seen = {}

        def fake_read(since=None):
            seen.setdefault("since", []).append(since)
            return iter([])

        with mock.patch.object(analytics, "read_events", side_effect=fake_read):
            digest.build_suggestions(self.cfg)
        since = seen["since"][0]
        self.assertIsNotNone(since)
        age = _dt.datetime.now(_dt.timezone.utc) - since
        self.assertAlmostEqual(age.total_seconds(), 30 * 86400, delta=120)
        self.assertIsNone(seen["since"][1])   # the all-time read for the guard


class FileSuggestionCardsTestCase(unittest.TestCase):
    def test_filing_failure_is_swallowed_and_the_rest_still_files(self):
        calls = []

        def fake_merge(req, high_confidence=False):
            calls.append(req.title)
            if req.title == "bad":
                raise RuntimeError("registry hiccup")
            return req

        with mock.patch.object(digest, "merge_or_new", side_effect=fake_merge):
            filed = digest.file_suggestion_cards(
                [("bad", ""), ("good", "detail")], today=_dt.date(2026, 7, 13))
        self.assertEqual(calls, ["bad", "good"])
        self.assertEqual([r.title for r in filed], ["good"])
        (good,) = filed
        self.assertEqual(good.summary, "建议：good — detail")
        self.assertEqual(good.sources[0]["quote"], "detail")
        self.assertEqual(good.sources[0]["date"], "2026-07-13")


if __name__ == "__main__":
    unittest.main()
