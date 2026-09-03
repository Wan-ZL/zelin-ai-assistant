"""``python -m act.analyze <req_id>`` — the standalone CLI (§8).

Exit codes pinned: 2 = usage (no id), 1 = unknown card, 0 = expanded (the
expansion itself is stubbed — the LLM boundary has its own judgments).
"""
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import analyze
from act.lib import config, registry
from act.lib.registry import Requirement, State


class AnalyzeCliTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = analyze._main(argv)
        return rc, buf.getvalue()

    def test_no_argument_is_usage(self):
        rc, out = self._run([])
        self.assertEqual(rc, 2)
        self.assertIn("usage:", out)

    def test_unknown_card_is_exit_1(self):
        rc, out = self._run(["R-404"])
        self.assertEqual(rc, 1)
        self.assertIn("not found", out)

    def test_known_card_is_expanded(self):
        registry.save(Requirement(id="R-905", title="调研", status=State.RAISING.value))

        def fake_expand(req, cfg=None, runner=None):
            req.summary = "expanded"
            req.set_status(State.CARD_SENT)
            return req

        with mock.patch.object(analyze, "expand_debt", side_effect=fake_expand):
            rc, out = self._run(["R-905"])
        self.assertEqual(rc, 0)
        self.assertIn("expanded R-905 -> card_sent", out)


if __name__ == "__main__":
    unittest.main()
