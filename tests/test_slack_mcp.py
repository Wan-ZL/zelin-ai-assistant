"""act/radar_slack.py v0.11 — token-less MCP fallback scan.

All passes run with an injected fake runner (no real ``claude`` subprocess)
inside the sandbox AIASSISTANT_HOME (tests/__init__.py). Contract under test:
(a) marker throttle — a fresh marker means the pass silently no-ops (launchd
    fires every 3 minutes; only a due pass may spend a claude call);
(b) a due pass runs, files cards through the registry pipeline, and advances
    the marker to the pass's start time;
(c) failure (non-zero exit / unparseable output / runner exception) leaves the
    marker UNTOUCHED (the next due pass re-covers the same window — that is
    the lid-closed gap guarantee) and records mcp_failed in radar_health.json;
(d) ```json fenced output is tolerated;
(e) a bare [] is a SUCCESS with zero cards (marker advances, health ok).
"""
import datetime as _dt
import json
import shutil
import subprocess
import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import radar_slack
from act.lib import config, health, registry


def _proc(stdout: str = "", returncode: int = 0,
          stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


class _FakeRunner:
    """Injectable runner: records prompts, returns a canned result or raises."""

    def __init__(self, result=None, exc=None):
        self.calls: list = []
        self.result = result if result is not None else _proc("[]")
        self.exc = exc

    def __call__(self, prompt: str):
        self.calls.append(prompt)
        if self.exc is not None:
            raise self.exc
        return self.result


_ITEM = {
    "title": "回复 manager 的 eval 数字确认",
    "summary": "manager 在 DM 里要今天的 eval 数字确认，需要 Zelin 回一条",
    "who": "your.manager",
    "channel": "DM",
    "date": "2026-07-08",
    "quote": "can you confirm the eval numbers today?",
}


def _write_marker(delta: _dt.timedelta) -> _dt.datetime:
    """Set the MCP marker to now-delta; returns the written instant.
    Second precision, matching what the marker file round-trips."""
    ts = (_dt.datetime.now(_dt.timezone.utc) - delta).replace(microsecond=0)
    radar_slack._write_mcp_marker(ts)
    return ts


class SlackMcpFallbackTestCase(unittest.TestCase):
    def setUp(self):
        for p in (radar_slack._mcp_marker_path(), health.HEALTH_PATH):
            if p.exists():
                p.unlink()
        if config.REGISTRY_DIR.exists():
            shutil.rmtree(config.REGISTRY_DIR)
        self.cfg = config.Config()  # defaults: fallback on, interval 30 min

    # -- (a) throttle ------------------------------------------------------- #
    def test_not_due_yet_is_silent_and_never_calls_claude(self):
        _write_marker(_dt.timedelta(minutes=5))   # 5 < 30 -> not due
        runner = _FakeRunner(_proc(json.dumps([_ITEM])))
        self.assertEqual(radar_slack.mcp_scan(self.cfg, runner=runner), 0)
        self.assertEqual(runner.calls, [])         # claude never spawned
        self.assertEqual(registry.load_all(), [])  # and no cards
        self.assertFalse(health.HEALTH_PATH.exists())  # silent: no beacon

    def test_due_when_marker_older_than_interval(self):
        _write_marker(_dt.timedelta(minutes=31))
        runner = _FakeRunner(_proc("[]"))
        radar_slack.mcp_scan(self.cfg, runner=runner)
        self.assertEqual(len(runner.calls), 1)

    # -- (b) success: cards + marker ---------------------------------------- #
    def test_success_creates_cards_and_advances_marker(self):
        old = _write_marker(_dt.timedelta(minutes=45))
        runner = _FakeRunner(_proc(json.dumps([_ITEM], ensure_ascii=False)))
        created = radar_slack.mcp_scan(self.cfg, runner=runner)
        self.assertEqual(created, 1)

        reqs = registry.load_all()
        self.assertEqual(len(reqs), 1)
        r = reqs[0]
        self.assertEqual(r.status, "card_sent")        # 进"待审批"
        self.assertEqual(r.title, _ITEM["title"])
        self.assertEqual(r.sources[0]["who"], "your.manager")
        self.assertEqual(r.sources[0]["quote"], _ITEM["quote"])

        marker = radar_slack._read_mcp_marker()
        self.assertIsNotNone(marker)
        self.assertGreater(marker, old)                # advanced past the old one
        # health: successful pass recorded
        data = json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertIsNone(data["slack"]["skip_reason"])
        self.assertIsNotNone(data["slack"]["last_ok"])

    def test_first_run_without_marker_scans_and_writes_marker(self):
        self.assertIsNone(radar_slack._read_mcp_marker())
        runner = _FakeRunner(_proc("[]"))
        radar_slack.mcp_scan(self.cfg, runner=runner)
        self.assertEqual(len(runner.calls), 1)
        self.assertIsNotNone(radar_slack._read_mcp_marker())

    # -- (c) failure: marker untouched + mcp_failed beacon ------------------- #
    def _assert_failed(self, runner: _FakeRunner, old_marker: _dt.datetime):
        self.assertEqual(radar_slack.mcp_scan(self.cfg, runner=runner), 0)
        self.assertEqual(radar_slack._read_mcp_marker(), old_marker)  # untouched
        data = json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertTrue(str(data["slack"]["skip_reason"]).startswith("mcp_failed:"))
        self.assertEqual(registry.load_all(), [])

    def test_nonzero_exit_keeps_marker(self):
        old = _write_marker(_dt.timedelta(hours=2))
        self._assert_failed(
            _FakeRunner(_proc("", returncode=1, stderr="boom")), old)

    def test_unparseable_output_keeps_marker(self):
        old = _write_marker(_dt.timedelta(hours=2))
        self._assert_failed(
            _FakeRunner(_proc("I could not reach Slack, sorry.")), old)

    def test_timeout_keeps_marker(self):
        old = _write_marker(_dt.timedelta(hours=2))
        self._assert_failed(
            _FakeRunner(exc=subprocess.TimeoutExpired(cmd="claude", timeout=300)),
            old)

    # -- (d) fenced JSON ------------------------------------------------------ #
    def test_json_fence_is_tolerated(self):
        _write_marker(_dt.timedelta(hours=1))
        fenced = "```json\n" + json.dumps([_ITEM], ensure_ascii=False) + "\n```"
        created = radar_slack.mcp_scan(self.cfg, runner=_FakeRunner(_proc(fenced)))
        self.assertEqual(created, 1)
        self.assertEqual(len(registry.load_all()), 1)

    # -- (e) empty array = success, zero cards -------------------------------- #
    def test_empty_array_is_success_with_zero_cards(self):
        old = _write_marker(_dt.timedelta(hours=1))
        created = radar_slack.mcp_scan(self.cfg, runner=_FakeRunner(_proc("[]")))
        self.assertEqual(created, 0)
        self.assertEqual(registry.load_all(), [])
        self.assertGreater(radar_slack._read_mcp_marker(), old)  # still advances
        data = json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertIsNone(data["slack"]["skip_reason"])

    # -- scan() routing -------------------------------------------------------- #
    def test_scan_routes_to_mcp_when_no_token(self):
        """scan() with no token + fallback on -> mcp_scan (due -> runner runs)."""
        orig = radar_slack.get_token
        radar_slack.get_token = lambda cfg=None: None
        try:
            runner = _FakeRunner(_proc("[]"))
            radar_slack.scan(self.cfg, mcp_runner=runner)
            self.assertEqual(len(runner.calls), 1)
        finally:
            radar_slack.get_token = orig

    def test_scan_skips_no_credentials_when_fallback_off(self):
        self.cfg.slack_mcp_fallback = False
        orig = radar_slack.get_token
        radar_slack.get_token = lambda cfg=None: None
        try:
            runner = _FakeRunner(_proc("[]"))
            self.assertEqual(radar_slack.scan(self.cfg, mcp_runner=runner), 0)
            self.assertEqual(runner.calls, [])
            data = json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))
            self.assertEqual(data["slack"]["skip_reason"], "no_credentials")
        finally:
            radar_slack.get_token = orig

    # -- prompt window ---------------------------------------------------------- #
    def test_prompt_carries_since_and_channels(self):
        marker = _write_marker(_dt.timedelta(hours=2))
        self.cfg.slack_channels = [{"id": "C01234ABCDE", "name": "team-channel"}]
        runner = _FakeRunner(_proc("[]"))
        radar_slack.mcp_scan(self.cfg, runner=runner)
        prompt = runner.calls[0]
        self.assertIn(marker.strftime("%Y-%m-%dT%H:%M:%SZ"), prompt)
        self.assertIn("team-channel", prompt)

    def test_stale_marker_lookback_capped_at_48h(self):
        _write_marker(_dt.timedelta(days=7))   # laptop closed for a week
        runner = _FakeRunner(_proc("[]"))
        radar_slack.mcp_scan(self.cfg, runner=runner)
        prompt = runner.calls[0]
        # the since in the prompt is ~48h ago, not 7 days ago
        since_str = prompt.split("自 ", 1)[1].split("（UTC）", 1)[0]
        since = _dt.datetime.strptime(since_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
        age_h = (_dt.datetime.now(_dt.timezone.utc) - since).total_seconds() / 3600
        self.assertLessEqual(age_h, 48.1)


if __name__ == "__main__":
    unittest.main()
