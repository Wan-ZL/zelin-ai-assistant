"""The shipped R-000-example.yaml is documentation, never a real card (it used
to surface in the backlog lane on every fresh install).

registry reads config.REGISTRY_DIR at call time, so a patch.object is enough —
no env mutation, no module reload (an earlier version reloaded act.lib.config
and leaked a dead HOME into every later test module in the suite).
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act.lib import config, registry


class ExampleCardNeverLoadsTestCase(unittest.TestCase):
    def test_example_file_excluded_from_load_all(self):
        with tempfile.TemporaryDirectory(dir=TMP_HOME) as tmp:
            reg = Path(tmp) / "registry"
            reg.mkdir()
            (reg / "R-000-example.yaml").write_text(
                "id: R-000\ntitle: example\nstatus: detected\n", encoding="utf-8")
            (reg / "R-001.yaml").write_text(
                "id: R-001\ntitle: real\nstatus: detected\n", encoding="utf-8")
            with mock.patch.object(config, "REGISTRY_DIR", reg):
                ids = [r.id for r in registry.load_all()]
            self.assertIn("R-001", ids)
            self.assertNotIn("R-000", ids)


if __name__ == "__main__":
    unittest.main()
