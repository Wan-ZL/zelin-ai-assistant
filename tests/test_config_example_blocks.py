"""config.example.yaml documents every config.yaml block the server reads.

The template's header promises it holds the keys the lite code reads. The
server reads three top-level blocks through ``settings.config_yaml_doc(home)
.get(<block>)`` (server/settings.py ``models``, server/recaps.py ``recap``,
server/display.py ``ui``); each must appear in the template, live or commented,
and the ``ui: display:`` layer must spell every key of display.VOCABULARY.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8")


def _blocks_read_by_server():
    found = set()
    for path in sorted((REPO_ROOT / "server").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        found.update(re.findall(r'config_yaml_doc\([^)]*\)\.get\("([a-z_]+)"\)', text))
    return found


class ConfigExampleBlocksTestCase(unittest.TestCase):
    def test_the_scan_sees_the_three_known_readers(self):
        self.assertTrue({"models", "recap", "ui"} <= _blocks_read_by_server())

    def test_every_block_the_server_reads_is_in_the_template(self):
        for block in sorted(_blocks_read_by_server()):
            self.assertRegex(TEMPLATE, r"(?m)^#? ?%s:" % re.escape(block), block)

    def test_the_ui_display_layer_spells_every_display_key(self):
        from server import display
        self.assertRegex(TEMPLATE, r"(?m)^#?\s+display:")
        for key in display.VOCABULARY:
            self.assertRegex(TEMPLATE, r"(?m)^#?\s+%s:" % re.escape(key), key)

    def test_the_header_names_the_server_readers(self):
        header = TEMPLATE.split("\n\n", 1)[0]
        for reader in ("server/display.py", "server/recaps.py", "server/settings.py"):
            self.assertIn(reader, header)


if __name__ == "__main__":
    unittest.main()
