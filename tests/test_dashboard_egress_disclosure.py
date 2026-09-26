"""§7 ``egress[]`` on the approval card — GitHub repo creation is disclosed BEFORE approval (issue #11).

The approval card is the product's security boundary (PRIVACY.md egress row 8):
with ``execution.create_github_repo: true`` the executor runs ``gh repo create
--private`` when the target is a NEW directory and pushes content derived from
screen / meeting / mail sources. The card used to look identical either way.

**§78（issue #447 / owner 决策 D80）之后那张卡住在潜在任务列**（``debt[]``）：
提案车道退役，「促成运行」那颗批准键长在潜在任务行上，所以本节的披露义务逐条
跟着按钮搬家（§7 §78 追记）——``needs_approval[]`` 恒空，继续钉它等于这条安全
边界再也没有判例看着。

Pinned (mirrors executor.ensure_repo's gate byte-for-byte):
  - flag on + target_kind "new" + repo delivery → one row
    ``{"kind": "github_repo_create", "target": <name>, "visibility": "private"}``;
  - flag off (the default) → ``egress == []`` on every card, nothing else changes;
  - flag on but target existing / chat delivery → ``[]`` (no repo is created);
  - the key is present on **every** 潜在任务 row — ``detected``, the ``raising``
    placeholder and a retired ``card_sent`` straggler alike (§78: one lane, one
    card face, and the promotion button must never sit on a row that hides what
    leaves the machine), and the vocabulary constant matches the wire string.
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
    """§78：机器卡的唯一车道是潜在任务（``debt[]``）。"""
    return next(r for r in dash["debt"] if r["id"] == rid)


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

    def _card(self, rid, target, status=State.DETECTED.value, **kw):
        req = Requirement(id=rid, title="t", status=status,
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

    def test_no_target_repo_uses_the_default_dir_like_the_executor(self):
        # Codex review of #158 (P0): _target_view reports the default target as
        # "existing" without looking at the disk, but executor.dispatch hands
        # cfg.target_repo_path to ensure_repo whenever it is missing/empty →
        # gh repo create would run with egress=[] on the card. Mirror the executor.
        cfg = config.Config()
        cfg.create_github_repo = True
        cfg.default_target_repo = str(self.new_dir)          # missing → bootstrap
        req = Requirement(id="P-4", title="t", status=State.DETECTED.value)
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=cfg, archived=[])
        row = _row(dash, "P-4")
        self.assertIsNone(req.target_repo)
        self.assertEqual(row["egress"], [{"kind": "github_repo_create",
                                          "target": "brand-new-repo", "visibility": "private"}])
        cfg.default_target_repo = str(self.existing)         # non-empty → no bootstrap
        dash = dashboard.build_dashboard(reqs=[req], agents=[], cfg=cfg, archived=[])
        self.assertEqual(_row(dash, "P-4")["egress"], [])

    def test_stored_target_kind_new_discloses_even_if_dir_now_exists(self):
        # executor: `req.target_kind == "new" or compute_target_kind(target) == "new"`
        dash = _build([self._card("P-5", self.existing, target_kind="new")], create_github_repo=True)
        self.assertEqual(_row(dash, "P-5")["egress"][0]["target"], "existing-repo")

    def test_key_present_on_every_backlog_row_including_the_raising_placeholder(self):
        """§78 反转了上一版的后半句。它原来钉的是「``raising`` 占位行不带
        ``egress``」——那条区分在提案列还在时说得通：占位行在另一条车道上，
        上面没有批准键，也就没有可披露的后果。退役之后三种状态同住一列、同一张
        卡面，而「促成运行」那颗键就在这一列上；只要有一行**敢在同一个列表里不带
        ``egress``**，客户端就得为「缺键 = 不出机」还是「缺键 = 不知道」二选一猜，
        猜错的那一半正是 issue #11 要消灭的东西。新不变量：潜在任务列的**每一行**
        恒带 ``egress``（``detected`` / ``raising`` 灰占位 / 退役残留的
        ``card_sent`` 落单卡一视同仁），空 list = 批了也什么都不出机。"""
        raising = Requirement(id="P-9", title="t", status=State.RAISING.value)
        dash = _build([self._card("P-1", self.existing), raising,
                       self._card("P-8", self.new_dir,
                                  status=State.CARD_SENT.value)],
                      create_github_repo=True)
        self.assertEqual({r["id"] for r in dash["debt"]}, {"P-1", "P-9", "P-8"})
        for rid in ("P-1", "P-9", "P-8"):
            with self.subTest(row=rid):
                self.assertIn("egress", _row(dash, rid))
        # 占位行没有 target，披露的是「什么也不出机」而不是缺键
        self.assertEqual(_row(dash, "P-9")["egress"], [])
        # 落单卡与 detected 卡一样会出机建 repo，所以照样如实披露
        self.assertEqual(_row(dash, "P-8")["egress"][0]["kind"],
                         dashboard.EGRESS_GITHUB_REPO_CREATE)


if __name__ == "__main__":
    unittest.main()
