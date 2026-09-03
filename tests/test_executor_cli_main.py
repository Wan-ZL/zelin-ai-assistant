"""``python -m act.executor <req_id>`` — the standalone dispatch CLI.

Exit codes: 2 usage (no id), 1 unknown card, 1 launch failure (DispatchError
and its subclasses — the card stays where it was), 0 dispatched (prints the
session id and status). ``dispatch`` and ``load`` are patched: the CLI is the
thin shell around them, nothing here launches claude.
"""
import contextlib
import io
import unittest
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor
from act.lib.registry import Requirement, State


def _run(argv, **patches):
    out = io.StringIO()
    with contextlib.ExitStack() as stack:
        for name, value in patches.items():
            stack.enter_context(mock.patch.object(executor, name, value))
        stack.enter_context(contextlib.redirect_stdout(out))
        rc = executor._main(argv)
    return rc, out.getvalue()


class MainTestCase(unittest.TestCase):
    def test_usage_without_an_id(self):
        rc, out = _run([])
        self.assertEqual(rc, 2)
        self.assertIn("usage: python -m act.executor <req_id>", out)

    def test_unknown_card_is_an_error(self):
        rc, out = _run(["R-404"], load=mock.Mock(return_value=None))
        self.assertEqual(rc, 1)
        self.assertIn("R-404 not found", out)

    def test_dispatch_failure_reports_the_kept_status(self):
        req = Requirement(id="R-001", title="t", status=State.APPROVED.value)
        boom = mock.Mock(side_effect=executor.DispatchBackingOff("still backing off"))
        rc, out = _run(["R-001"], load=mock.Mock(return_value=req), dispatch=boom)
        self.assertEqual(rc, 1)
        self.assertIn("status stays approved", out)
        self.assertIn("still backing off", out)

    def test_success_prints_session_and_status(self):
        req = Requirement(id="R-001", title="t", status=State.APPROVED.value)

        def dispatch(r):
            r.execution = {"session_id": "abc12345"}
            r.set_status(State.EXECUTING)
            return r
        rc, out = _run(["R-001"], load=mock.Mock(return_value=req), dispatch=dispatch)
        self.assertEqual(rc, 0)
        self.assertIn("dispatched R-001 -> session abc12345 (status=executing)", out)

    def test_success_without_execution_dict_prints_none(self):
        req = Requirement(id="R-001", title="t", status=State.APPROVED.value)
        rc, out = _run(["R-001"], load=mock.Mock(return_value=req),
                       dispatch=mock.Mock(return_value=req))
        self.assertEqual(rc, 0)
        self.assertIn("session None", out)


if __name__ == "__main__":
    unittest.main()
