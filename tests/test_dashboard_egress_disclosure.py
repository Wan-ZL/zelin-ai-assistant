"""§7 ``egress[]`` on approval cards — GitHub repo creation is disclosed BEFORE approval (issue #11).

The approval card is the product's security boundary (PRIVACY.md egress row 8):
with ``execution.create_github_repo: true`` the executor runs ``gh repo create
--private`` when the target is a NEW directory and pushes content derived from
screen / meeting / mail sources. The card used to look identical either way.

Pinned (mirrors executor.ensure_repo's gate byte-for-byte):
  - flag on + target_kind "new" + repo delivery → one row
    ``{"kind": "github_repo_create", "target": <name>, "visibility": "private"}``;
  - flag off (the default) → ``egress == []`` on every card, nothing else changes;
  - flag on but target existing / chat delivery → ``[]`` (no repo is created);
  - the key is always present on needs_approval rows (clients rely on the list),
    absent on raising placeholders (no target known yet), and the vocabulary
    constant matches the wire string.
Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import config, dashboard
from act.lib.registry import Requirement, State


def _build(reqs, create_github_repo):
    cfg = config.Config()
    cfg.create_github_repo = create_github_repo
    return dashboard.build_dashboard(reqs=reqs, agents=[], cfg=cfg, archived=[])


def _row(dash, rid):
    return next(r for r in dash["needs_approval"] if r["id"] == rid)


class EgressDisclosureTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="egress-")
        base = Path(self.tmp.name)
        self.new_dir = base / "brand-new-repo"          # does not exist → "new"
        self.existing = base / "existing-repo"
        self.existing.mkdir()
        (self.existing / "README.md").write_text("x", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _card(self, rid, target, **kw):
        req = Requirement(id=rid, title="t", status=State.CARD_SENT.value,
                          target_repo=str(target))
        for k, v in kw.items():
            setattr(req, k, v)
        return req

    def test_flag_on_new_target_discloses_private_repo_creation(self):
        dash = _build([self._card("P-1", self.new_dir)], create_github_repo=True)
        row = _row(dash, "P-1")
        self.assertEqual(row["target_kind"], "new")
        self.assertEqual(row["egress"], [{
            "kind": "github_repo_create",
            "target": "brand-new-repo",
            "visibility": "private",
        }])
        self.assertEqual(dashboard.EGRESS_GITHUB_REPO_CREATE, "github_repo_create")

    def test_flag_off_default_changes_nothing(self):
        self.assertFalse(config.Config().create_github_repo)
        dash = _build([self._card("P-1", self.new_dir),
                       self._card("P-2", self.existing)], create_github_repo=False)
        self.assertEqual(_row(dash, "P-1")["egress"], [])
        self.assertEqual(_row(dash, "P-2")["egress"], [])

    def test_flag_on_existing_target_has_no_egress(self):
        dash = _build([self._card("P-2", self.existing)], create_github_repo=True)
        row = _row(dash, "P-2")
        self.assertEqual(row["target_kind"], "existing")
        self.assertEqual(row["egress"], [])

    def test_flag_on_chat_delivery_has_no_egress(self):
        # chat delivery never touches a repo (executor skips ensure_repo, §20)
        dash = _build([self._card("P-3", self.new_dir, delivery_mode="chat")],
                      create_github_repo=True)
        self.assertEqual(_row(dash, "P-3")["delivery_mode"], "chat")
        self.assertEqual(_row(dash, "P-3")["egress"], [])

    def test_key_always_present_on_proposals_absent_on_raising_placeholder(self):
        raising = Requirement(id="P-9", title="t", status=State.RAISING.value)
        dash = _build([self._card("P-1", self.existing), raising], create_github_repo=True)
        self.assertIn("egress", _row(dash, "P-1"))
        self.assertNotIn("egress", _row(dash, "P-9"))


if __name__ == "__main__":
    unittest.main()
