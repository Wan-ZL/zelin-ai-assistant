"""purge_trash retention pass — the pin (permanent) guard (audit 2026-07-15).

pin's whole purpose is "the retention pass never hard-deletes this"; the only
enforcement is one line in actd.purge_trash and the permanent field surviving
the skip-unset-optionals serializer. Neither had a test — a regression would
hard-delete every user-pinned card 60 days after trashing, silently.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import json
import unittest
import uuid

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd
from act.lib import config, registry
from act.lib.registry import Requirement, State


def _iso_days_ago(days: int) -> str:
    dt = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _mk_trashed(req_id, days_ago=100):
    req = Requirement(id=req_id, title=f"trashed {req_id}",
                      status=State.TRASHED.value,
                      trashed_at=_iso_days_ago(days_ago),
                      trash_reason="deleted", prev_status="detected")
    registry.save(req)
    return req


def _drop(action, req_id):
    config.ensure_state_dirs()
    path = config.INBOX_DIR / f"{uuid.uuid4()}.json"
    path.write_text(json.dumps({"id": req_id, "action": action}),
                    encoding="utf-8")
    return path


class TrashPurgeTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()

    def test_pinned_trashed_card_survives_retention_purge(self):
        _mk_trashed("R-501")
        _mk_trashed("R-502")
        # pin through the REAL inbox action, not by poking the field — the
        # test must cover the pin->save->reload round-trip the user relies on
        _drop("pin", "R-501")
        actd.process_inbox()

        purged = actd.purge_trash(config.Config())  # retention default: 60d

        self.assertEqual(purged, 1)
        pinned = registry.load("R-501")
        self.assertIsNotNone(pinned)                 # survived on disk
        self.assertTrue(pinned.permanent)            # field round-tripped
        self.assertEqual(pinned.status, State.TRASHED.value)
        self.assertIsNone(registry.load("R-502"))    # unpinned twin is gone

    def test_zero_retention_days_purges_nothing(self):
        _mk_trashed("R-503")
        cfg = config.Config()
        cfg.trash_retention_days = 0
        self.assertEqual(actd.purge_trash(cfg), 0)
        self.assertIsNotNone(registry.load("R-503"))

    def test_fresh_trash_is_never_purged(self):
        _mk_trashed("R-504", days_ago=1)
        self.assertEqual(actd.purge_trash(config.Config()), 0)
        self.assertIsNotNone(registry.load("R-504"))


if __name__ == "__main__":
    unittest.main()
