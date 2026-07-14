"""radar_health.json writes for the obsidian source (v0.19.0 A) + the slack
``mcp_not_configured`` skip code (B4).

Two contracts under test:

1. act/radar.py writes an ``obsidian`` health entry at each scan outcome, but
   ONLY under the cron ingest chain (``AIASSISTANT_CRON=1`` — _owns_health).
   A manual / launchd run must never overwrite the cron's good health with a
   false vault_empty. Codes: disabled / vault_missing / vault_empty /
   no_api_key / extract_failed; a healthy pass is ok + last_cards (even when it
   found nothing newer than the marker → last_cards=0, NOT a skip).

2. act/radar_slack.mcp_scan files ``mcp_not_configured`` (distinct from the
   transient ``mcp_failed:``) when the fallback is on but no Slack MCP is
   registered in the claude CLI — without spending a claude -p call.

Everything runs inside the sandbox AIASSISTANT_HOME (tests/__init__.py) with
injected runners/probes — no real subprocess ever fires.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar, radar_slack
from act.lib import config, health, registry

BASE = 1_760_000_000.0  # fixed epoch — deterministic mtimes


def _read_obsidian() -> dict:
    return json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))["obsidian"]


def _item(title, hardness="soft", deadline=None, quote="do the thing"):
    return {"title": title, "type": "report", "tier": "T1",
            "hardness": hardness, "deadline": deadline,
            "cost_estimate_usd": None, "quote": quote}


# --------------------------------------------------------------------------- #
# A — obsidian radar_health writes (cron-owned)
# --------------------------------------------------------------------------- #
class ObsidianHealthBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        self._cleanup()
        # the cron ingest chain owns the obsidian health marker (install.sh:455)
        os.environ["AIASSISTANT_CRON"] = "1"
        self.addCleanup(lambda: os.environ.pop("AIASSISTANT_CRON", None))
        self.tmp = tempfile.TemporaryDirectory(prefix="radar-health-vault-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._cleanup)
        self.raw = Path(self.tmp.name) / "2 - raw"
        self.raw.mkdir(parents=True)
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw.as_posix()}"\n', encoding="utf-8")

    @staticmethod
    def _cleanup():
        for p in (config.CONFIG_PATH, health.HEALTH_PATH,
                  config.STATE_DIR / radar.MARKER_PATH_NAME,
                  config.STATE_DIR / radar.FAILED_QUEUE_NAME):
            if p.exists():
                p.unlink()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def _note(self, name, text, mtime):
        p = self.raw / name
        p.write_text(text, encoding="utf-8")
        os.utime(p, (mtime, mtime))
        return p


class ObsidianHealthTestCase(ObsidianHealthBase):
    def test_feature_off_is_disabled(self):
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{self.raw.as_posix()}"\n'
            "features:\n  obsidian_radar: false\n", encoding="utf-8")
        radar.scan(runner=lambda t: self.fail("scanned while off"))
        self.assertEqual(_read_obsidian()["skip_reason"], "disabled")

    def test_unconfigured_vault_is_vault_missing(self):
        config.CONFIG_PATH.write_text("sources: {}\n", encoding="utf-8")
        radar.scan(runner=lambda t: self.fail("scanned with no vault"))
        self.assertEqual(_read_obsidian()["skip_reason"], "vault_missing")

    def test_missing_dir_is_vault_missing(self):
        gone = Path(self.tmp.name) / "no-such-dir"
        config.CONFIG_PATH.write_text(
            f'sources:\n  obsidian_raw: "{gone.as_posix()}"\n', encoding="utf-8")
        radar.scan(runner=lambda t: self.fail("scanned a missing vault"))
        self.assertEqual(_read_obsidian()["skip_reason"], "vault_missing")

    def test_empty_vault_is_vault_empty(self):
        # dir exists but holds zero .md (recording off / TCC-blocked cron)
        radar.scan(runner=lambda t: self.fail("scanned an empty vault"))
        entry = _read_obsidian()
        self.assertEqual(entry["skip_reason"], "vault_empty")
        self.assertIsNone(entry["last_ok"])

    def test_extraction_failure_without_key_is_no_api_key(self):
        self._note("2026-07-08 note.md", "some content", BASE)
        self.addCleanup(setattr, radar, "_has_anthropic_key",
                        radar._has_anthropic_key)
        radar._has_anthropic_key = lambda: False
        radar.scan(runner=lambda t: "not json at all")  # -> extract failure
        self.assertEqual(_read_obsidian()["skip_reason"], "no_api_key")

    def test_extraction_failure_with_key_is_extract_failed(self):
        self._note("2026-07-08 note.md", "some content", BASE)
        self.addCleanup(setattr, radar, "_has_anthropic_key",
                        radar._has_anthropic_key)
        radar._has_anthropic_key = lambda: True
        radar.scan(runner=lambda t: "not json at all")  # -> extract failure
        self.assertEqual(_read_obsidian()["skip_reason"], "extract_failed")

    def test_healthy_scan_records_ok_and_card_count(self):
        self._note("2026-07-08 weekly.md", "Boss: ship the Q3 report by Jul 20",
                   BASE)
        runner = lambda t: json.dumps(  # noqa: E731
            [_item("Ship the Q3 report", hardness="hard", deadline="2026-07-20",
                   quote="ship the Q3 report by July 20")])
        radar.scan(runner=runner)
        entry = _read_obsidian()
        self.assertIsNone(entry["skip_reason"])
        self.assertIsNotNone(entry["last_ok"])
        self.assertEqual(entry["last_cards"], 1)  # hard + deadline = a card

    def test_scanned_but_nothing_newer_is_ok_with_zero_cards(self):
        # a pass that finds no note newer than the marker is HEALTHY, not a skip
        self._note("2026-07-08 old.md", "already seen", BASE)
        radar._write_marker(BASE)
        radar.scan(runner=lambda t: self.fail("re-read a note behind the marker"))
        entry = _read_obsidian()
        self.assertIsNone(entry["skip_reason"])
        self.assertIsNotNone(entry["last_ok"])
        self.assertEqual(entry["last_cards"], 0)

    def test_non_cron_context_never_writes_health(self):
        # _owns_health gate: a manual / launchd run (no AIASSISTANT_CRON) must
        # NOT touch the marker — else it would overwrite the cron's good health
        # with a false vault_empty (it can't see ~/Documents without FDA).
        os.environ.pop("AIASSISTANT_CRON", None)
        radar.scan(runner=lambda t: self.fail("scanned an empty vault"))
        self.assertFalse(health.HEALTH_PATH.exists())


# --------------------------------------------------------------------------- #
# B4 — slack mcp_not_configured
# --------------------------------------------------------------------------- #
def _proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


class _FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, prompt):
        self.calls.append(prompt)
        return _proc("[]")


class SlackMcpNotConfiguredTestCase(unittest.TestCase):
    def setUp(self):
        for p in (radar_slack._mcp_marker_path(),
                  radar_slack._mcp_present_marker_path(), health.HEALTH_PATH):
            if p.exists():
                p.unlink()
        if config.REGISTRY_DIR.exists():
            shutil.rmtree(config.REGISTRY_DIR)
        self.cfg = config.Config()  # defaults: fallback on, interval 30 min

    def test_absent_mcp_files_mcp_not_configured_without_claude(self):
        runner = _FakeRunner()
        created = radar_slack.mcp_scan(self.cfg, runner=runner,
                                       mcp_present=lambda: False)
        self.assertEqual(created, 0)
        self.assertEqual(runner.calls, [])              # never spent a claude -p
        self.assertEqual(registry.load_all(), [])
        data = json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertEqual(data["slack"]["skip_reason"], "mcp_not_configured")
        # success marker untouched — this is a config skip, not a scan
        self.assertIsNone(radar_slack._read_mcp_marker())

    def test_present_mcp_proceeds_to_scan(self):
        runner = _FakeRunner()
        radar_slack.mcp_scan(self.cfg, runner=runner, mcp_present=lambda: True)
        self.assertEqual(len(runner.calls), 1)          # preflight let it run
        data = json.loads(health.HEALTH_PATH.read_text(encoding="utf-8"))
        self.assertIsNone(data["slack"]["skip_reason"])  # healthy empty pass

    def test_mcp_not_configured_is_distinct_from_mcp_failed(self):
        radar_slack.mcp_scan(self.cfg, mcp_present=lambda: False)
        reason = json.loads(
            health.HEALTH_PATH.read_text(encoding="utf-8"))["slack"]["skip_reason"]
        self.assertEqual(reason, "mcp_not_configured")
        self.assertFalse(reason.startswith("mcp_failed:"))

    def test_present_cache_hit_is_silent(self):
        # a cached "absent" verdict (fresh marker) must NOT beacon every tick —
        # only a fresh probe records the skip.
        radar_slack._mcp_present_marker_path().write_text("0", encoding="utf-8")
        created = radar_slack.mcp_scan(self.cfg)   # real _slack_mcp_present, cache hit
        self.assertEqual(created, 0)
        self.assertFalse(health.HEALTH_PATH.exists())  # silent on a cache hit


if __name__ == "__main__":
    unittest.main()
