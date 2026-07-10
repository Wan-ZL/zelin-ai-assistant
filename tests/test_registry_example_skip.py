"""The shipped R-000-example.yaml is documentation, never a real card (it used
to surface in the backlog lane on every fresh install)."""
import os
import tempfile
import unittest


class ExampleCardNeverLoadsTestCase(unittest.TestCase):
    def test_example_file_excluded_from_load_all(self):
        with tempfile.TemporaryDirectory() as home:
            os.environ["AIASSISTANT_HOME"] = home
            import importlib
            from act.lib import config as config_mod
            importlib.reload(config_mod)
            from act.lib import registry as registry_mod
            importlib.reload(registry_mod)
            reg = config_mod.REGISTRY_DIR
            reg.mkdir(parents=True, exist_ok=True)
            (reg / "R-000-example.yaml").write_text(
                "id: R-000\ntitle: example\nstatus: detected\n", encoding="utf-8")
            (reg / "R-001.yaml").write_text(
                "id: R-001\ntitle: real\nstatus: detected\n", encoding="utf-8")
            ids = [r.id for r in registry_mod.load_all()]
            self.assertIn("R-001", ids)
            self.assertNotIn("R-000", ids)


if __name__ == "__main__":
    unittest.main()
