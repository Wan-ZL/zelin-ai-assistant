"""Meeting action-items path — 2026-07-08 backfill-storm regressions.

A fresh install ran the half-hourly cron over a vault with months of
historical meeting notes: 81 drafts landed in the invented placeholder
workbench, 81 notifications fired, and the >30-min pass overlapped its own
next cron invocation. Pinned here:

- placeholder guard: without an EXPLICIT execution.default_target_repo the
  drafts land in state/meetings (never the example placeholder path), with a
  one-time notice pointing at the Settings folder picker;
- notification coalescing: <=3 drafts per pass notify individually, >3 send
  ONE summary;
- state/radar.lock: a pass that finds the lock held exits as a clean no-op;
- explicit enablement: the pack no longer runs on the §16 default-on
  fallback — features.manager_pack must be configured (production ran it
  despite never being set up);
- keyword guard: placeholder/stopword watch_people ("your.manager" derives
  the token "your", matching nearly every English note) disables the pack.

All fixtures are synthetic. Runs entirely inside the sandbox
AIASSISTANT_HOME (tests/__init__.py).
"""
import contextlib
import fcntl
import io
import json
import shutil
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports
from tests.test_radar import BASE, RadarScanBase

from act import radar
from act.lib import config, notify

PACK_MD = "## Zelin 的 action items\n- Draft the synthetic follow-up note\n"


class MeetingsBase(RadarScanBase):
    """RadarScanBase + a watched manager keyword + captured notifications."""

    def setUp(self):
        super().setUp()
        self._write_cfg()
        self.addCleanup(self._meetings_cleanup)
        self._meetings_cleanup()
        self.sent: list = []  # (title, body) per notify.notify call
        patcher = mock.patch.object(
            notify, "notify",
            side_effect=lambda title, body, **kw: self.sent.append((title, body)) or True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_cfg(self, extra: str = "", watch: str = "boss.person",
                   features: str = "features:\n  manager_pack: true\n"):
        # manager_pack is explicit-enable only (post-2026-07-08); the base
        # config turns it on so these tests exercise the pack writer.
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw}"\n'
            f'  watch_people: ["{watch}"]\n' + features + extra,
            encoding="utf-8")

    @staticmethod
    def _meetings_cleanup():
        for name in (radar.NOTICE_PATH_NAME, radar.LOCK_PATH_NAME):
            p = config.STATE_DIR / name
            if p.exists():
                p.unlink()
        if config.SETTINGS_OVERRIDES_PATH.exists():
            config.SETTINGS_OVERRIDES_PATH.unlink()
        shutil.rmtree(config.STATE_DIR / "meetings", ignore_errors=True)

    def _meeting_notes(self, n: int, base=BASE):
        for i in range(n):
            self._note(f"2026-07-0{i + 1} sync-{i + 1}.md",
                       f"boss asked for synthetic follow-up {i + 1}", base + i)

    def _scan(self):
        return radar.scan(runner=lambda t: "[]", pack_runner=lambda t: PACK_MD)

    def _drafts(self, title_zh="会后 action-item 清单已生成"):
        return [(t, b) for t, b in self.sent if t == title_zh]

    def _notices(self):
        return [(t, b) for t, b in self.sent if "未设置工作台目录" in t
                or "Workbench folder not set" in t]


# --------------------------------------------------------------------------- #
# placeholder guard — no invented workbench, state/meetings fallback + notice
# --------------------------------------------------------------------------- #
class PlaceholderGuardTestCase(MeetingsBase):
    def test_unconfigured_workbench_falls_back_to_state_meetings(self):
        self._meeting_notes(1)
        summary = self._scan()
        self.assertEqual(summary["action_items"], 1)

        fallback = config.STATE_DIR / "meetings"
        drafts = sorted(fallback.glob("*-action-items.md"))
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].read_text(encoding="utf-8"), PACK_MD)
        # the guard is config-driven: unconfigured NEVER routes to the example
        # placeholder (even one left over from the bug era stays untouched)
        cfg = config.load_config()
        self.assertFalse(cfg.default_target_repo_configured)
        self.assertEqual(radar._meetings_dir(cfg), fallback)

        # one-time notice fired alongside the draft notification
        notices = self._notices()
        self.assertEqual(len(notices), 1)
        self.assertIn(str(fallback), notices[0][1])
        self.assertTrue((config.STATE_DIR / radar.NOTICE_PATH_NAME).exists())

    def test_fallback_notice_fires_only_once_across_passes(self):
        self._meeting_notes(1)
        self._scan()
        self._note("2026-07-05 later-sync.md", "boss wants another synthetic item",
                   BASE + 100)
        self._scan()
        self.assertEqual(len(self._drafts()), 2)   # both passes wrote + notified
        self.assertEqual(len(self._notices()), 1)  # notice stayed one-time

    def test_configured_workbench_routes_drafts_there(self):
        wb = self.tmp.name + "/workbench"
        self._write_cfg(f'execution:\n  default_target_repo: "{wb}"\n')
        self._meeting_notes(1)
        self._scan()
        cfg = config.load_config()
        self.assertTrue(cfg.default_target_repo_configured)
        drafts = sorted(radar._meetings_dir(cfg).glob("*-action-items.md"))
        self.assertEqual(len(drafts), 1)
        self.assertTrue(str(drafts[0]).startswith(wb))
        self.assertEqual(self._notices(), [])      # configured -> no notice
        self.assertFalse((config.STATE_DIR / radar.NOTICE_PATH_NAME).exists())

    def test_settings_override_counts_as_configured(self):
        wb = self.tmp.name + "/override-workbench"
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"default_target_repo": wb}), encoding="utf-8")
        cfg = config.load_config()
        self.assertTrue(cfg.default_target_repo_configured)
        self.assertEqual(str(radar._meetings_dir(cfg)), wb + "/meetings")


# --------------------------------------------------------------------------- #
# notification coalescing — individual <=3, one summary >3
# --------------------------------------------------------------------------- #
class CoalescingTestCase(MeetingsBase):
    def test_three_drafts_notify_individually(self):
        self._meeting_notes(3)
        summary = self._scan()
        self.assertEqual(summary["action_items"], 3)
        drafts = self._drafts()
        self.assertEqual(len(drafts), 3)
        for _, body in drafts:
            self.assertTrue(body.endswith("-action-items.md"))

    def test_four_drafts_coalesce_to_one_summary(self):
        self._meeting_notes(4)
        summary = self._scan()
        self.assertEqual(summary["action_items"], 4)
        drafts = self._drafts()
        self.assertEqual(len(drafts), 1)           # ONE summary, not four
        self.assertIn("4", drafts[0][1])
        self.assertIn(str(config.STATE_DIR / "meetings"), drafts[0][1])


# --------------------------------------------------------------------------- #
# explicit enablement — the §16 default-on fallback no longer runs the pack
# --------------------------------------------------------------------------- #
class ExplicitEnableTestCase(MeetingsBase):
    def test_default_on_fallback_no_longer_runs_the_pack(self):
        self._write_cfg(features="")  # no features block at all
        self._meeting_notes(1)
        summary = self._scan()
        self.assertEqual(summary["action_items"], 0)
        self.assertEqual(self._drafts(), [])
        cfg = config.load_config()
        # §16 global semantics unchanged: an absent flag still reads as on...
        self.assertTrue(cfg.feature("manager_pack"))
        # ...but the pack's own gate requires explicit configuration
        self.assertFalse(cfg.feature_explicit("manager_pack"))

    def test_explicit_true_in_config_yaml_enables(self):
        self._meeting_notes(1)  # base cfg already sets manager_pack: true
        self.assertEqual(self._scan()["action_items"], 1)

    def test_explicit_false_stays_off(self):
        self._write_cfg(features="features:\n  manager_pack: false\n")
        self._meeting_notes(1)
        self.assertEqual(self._scan()["action_items"], 0)

    def test_settings_override_counts_as_explicit(self):
        self._write_cfg(features="")
        config.SETTINGS_OVERRIDES_PATH.write_text(
            json.dumps({"features": {"manager_pack": True}}), encoding="utf-8")
        self._meeting_notes(1)
        self.assertEqual(self._scan()["action_items"], 1)


# --------------------------------------------------------------------------- #
# keyword guard — placeholder / stopword watch_people never scans
# --------------------------------------------------------------------------- #
class KeywordGuardTestCase(MeetingsBase):
    # contains "your", "my", and "jo" (in "major") — a degenerate keyword
    # WOULD match this text if the guard failed
    TRAP = "your major synthetic review is due, please email my notes"

    def _scan_with(self, watch: str):
        self._write_cfg(watch=watch)
        self._note("2026-07-01 all-hands.md", self.TRAP, BASE)
        return radar.scan(runner=lambda t: "[]", pack_runner=lambda t: PACK_MD)

    def test_placeholder_watch_people_disables_pack(self):
        self.assertEqual(self._scan_with("your.manager")["action_items"], 0)

    def test_stopword_keyword_disables_pack(self):
        self.assertEqual(self._scan_with("my.boss")["action_items"], 0)

    def test_short_token_disables_pack(self):
        self.assertEqual(self._scan_with("jo.smith")["action_items"], 0)

    def test_real_keyword_still_matches(self):
        self._write_cfg(watch="alice.wong")
        self._note("2026-07-01 sync.md", "alice asked for a synthetic recap", BASE)
        summary = radar.scan(runner=lambda t: "[]", pack_runner=lambda t: PACK_MD)
        self.assertEqual(summary["action_items"], 1)

    def test_manager_keyword_unit(self):
        cfg = config.Config()
        for people, expect in (([], ""), (["your.manager"], ""),
                               (["My.Boss"], ""), (["jo.smith"], ""),
                               (["alice.wong"], "alice")):
            cfg.watch_people = people
            self.assertEqual(radar._manager_keyword(cfg), expect, people)


# --------------------------------------------------------------------------- #
# state/radar.lock — an overlapped pass exits as a clean no-op
# --------------------------------------------------------------------------- #
class PassLockTestCase(MeetingsBase):
    @contextlib.contextmanager
    def _held_lock(self):
        config.ensure_state_dirs()
        fh = open(config.STATE_DIR / radar.LOCK_PATH_NAME, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            fh.close()

    def test_lock_held_skips_the_pass(self):
        self._meeting_notes(1)
        with self._held_lock():
            summary = radar.scan(
                runner=lambda t: self.fail("scanned while the lock was held"))
        self.assertEqual(summary["files_scanned"], 0)
        self.assertTrue(any("radar.lock" in s for s in summary["skipped"]))
        # the running pass covers it: marker untouched, next pass rescans
        second = self._scan()
        self.assertEqual(second["files_scanned"], 1)

    def test_once_exits_zero_when_locked(self):
        with self._held_lock():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = radar.main(["--once"])
        self.assertEqual(rc, 0)
        printed = json.loads(out.getvalue())
        self.assertTrue(any("radar.lock" in s for s in printed["skipped"]))

    def test_lock_released_after_a_pass(self):
        self._scan()
        lock = radar._acquire_pass_lock()
        self.assertIsNotNone(lock)
        lock.close()


if __name__ == "__main__":
    unittest.main()
