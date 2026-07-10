"""actd merge-apply side (merge-review 契约 四/五) — deterministic apply.

Covered:
- _apply_merge_verdict happy path for verdict=merge: primary absorbs sources /
  repeated_mentions / notes, secondary lands terminal ``merged`` with
  ``merged_into``;
- _merge_into_primary crash-ordering regression: the primary's absorption is
  persisted BEFORE each secondary is marked merged — a crash right after the
  first secondary's terminal save must NOT lose what the primary absorbed
  (retries skip already-merged secondaries, so a stale primary would lose the
  data permanently);
- cleanup_merge_jobs TTL sweep: expired done jobs removed, fresh ones kept.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import json
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, merge_review
from act.lib import config, registry
from act.lib.registry import Requirement, State


def _src(quote, channel="meeting", date="2026-07-01"):
    return {"who": "manager", "channel": channel, "date": date, "quote": quote}


def _iso_in(hours: int) -> str:
    dt = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class MergeApplyBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        merge_review.MERGE_DIR.mkdir(parents=True, exist_ok=True)
        for p in merge_review.MERGE_DIR.glob("*.json"):
            p.unlink()

    def _save(self, rid, quote, status=State.CARD_SENT.value, **kw):
        kw.setdefault("sources", [_src(quote)])
        req = Requirement(id=rid, title=f"Task {rid}", status=status, **kw)
        registry.save(req)
        return req


# --------------------------------------------------------------------------- #
# _apply_merge_verdict — verdict=merge happy path（契约 四）
# --------------------------------------------------------------------------- #
class ApplyMergeVerdictTestCase(MergeApplyBase):
    def test_merge_verdict_absorbs_secondary_into_primary(self):
        self._save("R-1", "prepare the OKR report")
        self._save("R-2", "don't forget the OKR report", repeated_mentions=2)
        job = {"id": "s-1", "verdict": "merge",
               "ids": ["R-1", "R-2"], "primary": "R-1"}

        actd._apply_merge_verdict(job)

        sec = registry.load("R-2")
        self.assertEqual(str(sec.status), State.MERGED.value)
        self.assertEqual(sec.merged_into, "R-1")

        primary = registry.load("R-1")
        self.assertEqual(str(primary.status), State.CARD_SENT.value)  # untouched
        self.assertEqual(int(primary.repeated_mentions), 3)  # 1 + 2
        quotes = [s.get("quote") for s in primary.sources]
        self.assertIn("prepare the OKR report", quotes)
        self.assertIn("don't forget the OKR report", quotes)
        self.assertIn("[merged] R-2", primary.notes)

    def test_keep_separate_is_noop(self):
        self._save("R-1", "a")
        self._save("R-2", "b")
        job = {"id": "s-2", "verdict": "keep_separate",
               "ids": ["R-1", "R-2"], "primary": "R-1"}
        actd._apply_merge_verdict(job)
        self.assertEqual(str(registry.load("R-2").status), State.CARD_SENT.value)


# --------------------------------------------------------------------------- #
# _merge_into_primary — crash-ordering regression（主卡先落盘，再置副卡终态）
# --------------------------------------------------------------------------- #
class MergeCrashOrderingTestCase(MergeApplyBase):
    def test_primary_persisted_before_secondary_marked_merged(self):
        self._save("R-1", "prepare the OKR report")
        self._save("R-2", "don't forget the OKR report")
        self._save("R-3", "OKR report again")

        real_save = registry.save
        crashed = []

        def crashing_save(req):
            real_save(req)
            if str(req.status) == State.MERGED.value and not crashed:
                crashed.append(req.id)
                raise RuntimeError("simulated crash after first merged save")

        with mock.patch.object(actd, "save", new=crashing_save):
            with self.assertRaises(RuntimeError):
                actd._merge_into_primary("R-1", ["R-2", "R-3"])
        self.assertEqual(crashed, ["R-2"])

        # the first secondary reached its terminal state before the crash ...
        sec = registry.load("R-2")
        self.assertEqual(str(sec.status), State.MERGED.value)
        self.assertEqual(sec.merged_into, "R-1")
        # ... so the primary on disk MUST already contain the absorbed data
        primary = registry.load("R-1")
        self.assertEqual(int(primary.repeated_mentions), 2)
        quotes = [s.get("quote") for s in primary.sources]
        self.assertIn("don't forget the OKR report", quotes)
        self.assertIn("[merged] R-2", primary.notes)
        # the second secondary was never touched — a retry will pick it up
        self.assertEqual(str(registry.load("R-3").status), State.CARD_SENT.value)


# --------------------------------------------------------------------------- #
# cleanup_merge_jobs — TTL sweep（契约 五）
# --------------------------------------------------------------------------- #
class CleanupMergeJobsTestCase(MergeApplyBase):
    def _write_job(self, sid, **fields):
        job = {"id": sid, **fields}
        merge_review.job_path(sid).write_text(
            json.dumps(job), encoding="utf-8")
        return job

    def test_expired_done_removed_fresh_kept(self):
        self._write_job("s-old", status="done", expires_at=_iso_in(-1))
        self._write_job("s-new", status="done", expires_at=_iso_in(23))

        removed = actd.cleanup_merge_jobs()

        self.assertEqual(removed, 1)
        self.assertFalse(merge_review.job_path("s-old").exists())
        self.assertTrue(merge_review.job_path("s-new").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
