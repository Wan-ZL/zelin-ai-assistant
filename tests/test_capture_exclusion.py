"""P1-9 sensitive-app capture exclusion — config parsing, export-side SQL
filter behaviour, and drift guards for the three copies of the default list
(act/lib/config.py, mac/Sources/Recording.swift, ingest/screenpipe-export.sh
python-less fallback, config.example.yaml).

Config files live under the sandbox AIASSISTANT_HOME set in tests/__init__.py;
the repo source files read by the drift guards are located via __file__.
"""
import re
import sqlite3
import unittest
from pathlib import Path

from act.lib import config

REPO_ROOT = Path(__file__).resolve().parent.parent


class IgnoredAppsConfigTestCase(unittest.TestCase):
    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    @staticmethod
    def _cleanup():
        if config.CONFIG_PATH.exists():
            config.CONFIG_PATH.unlink()

    def test_defaults_when_key_absent(self):
        cfg = config.load_config()
        self.assertEqual(cfg.recording_ignored_apps, config.DEFAULT_IGNORED_APPS)
        self.assertIn("1Password", cfg.recording_ignored_apps)
        self.assertIn("Keychain Access", cfg.recording_ignored_apps)

    def test_custom_list_replaces_defaults(self):
        config.CONFIG_PATH.write_text(
            "recording:\n  ignored_apps:\n    - Chase\n    - '  1Password  '\n",
            encoding="utf-8",
        )
        cfg = config.load_config()
        self.assertEqual(cfg.recording_ignored_apps, ["Chase", "1Password"])

    def test_explicit_empty_list_is_opt_out(self):
        config.CONFIG_PATH.write_text(
            "recording:\n  ignored_apps: []\n", encoding="utf-8"
        )
        cfg = config.load_config()
        self.assertEqual(cfg.recording_ignored_apps, [])
        self.assertEqual(config.recording_exclusion_sql(cfg), "")


class ExclusionSqlTestCase(unittest.TestCase):
    @staticmethod
    def _frames_query(fragment, last_frame=0):
        # same shape as ingest/screenpipe-export.sh
        return (
            "SELECT f.id FROM frames f "
            f"WHERE f.id > {last_frame} "
            "AND f.full_text IS NOT NULL AND length(f.full_text) > 0 "
            f"{fragment} ORDER BY f.id ASC"
        )

    def _make_db(self):
        db = sqlite3.connect(":memory:")
        db.execute(
            "CREATE TABLE frames (id INTEGER PRIMARY KEY, app_name TEXT,"
            " window_name TEXT, full_text TEXT)"
        )
        db.executemany(
            "INSERT INTO frames VALUES (?, ?, ?, ?)",
            [
                (1, "1Password", "1Password — Login", "vault item text"),
                (2, "Google Chrome", "GitHub - Google Chrome (Incognito)", "page"),
                (3, None, None, "frame with NULL app/window"),
                (4, "Safari", "Anthropic Docs", "normal browsing"),
                (5, "Keychain Access", "Keychain Access", "password rows"),
                (6, "Slack", "login — o'brien bank support", "chat"),
            ],
        )
        return db

    def test_case_insensitive_substring_both_columns(self):
        cfg = config.Config()  # defaults
        rows = self._make_db().execute(
            self._frames_query(config.recording_exclusion_sql(cfg))
        ).fetchall()
        # 1 (app match), 2 (window-title match), 5 (app match) excluded;
        # NULL row 3 must survive (coalesce guards NULL NOT LIKE → NULL)
        self.assertEqual([r[0] for r in rows], [3, 4, 6])

    def test_single_quote_in_term_is_escaped(self):
        cfg = config.Config(recording_ignored_apps=["O'Brien Bank"])
        fragment = config.recording_exclusion_sql(cfg)
        self.assertIn("o''brien bank", fragment)
        rows = self._make_db().execute(self._frames_query(fragment)).fetchall()
        self.assertEqual([r[0] for r in rows], [1, 2, 3, 4, 5])

    def test_scoped_syntax_mirrors_engine(self):
        # engine's App::Title scoping (screenpipe 0.3.349) — export filter
        # must not silently degrade these to match-nothing substrings
        cases = [
            (["Google Chrome::Incognito"], [1, 3, 4, 5, 6]),  # app AND title
            (["::keychain"], [1, 2, 3, 4, 6]),                # title-only
            (["Safari::"], [1, 2, 3, 5, 6]),                  # app-only
        ]
        for terms, expected in cases:
            cfg = config.Config(recording_ignored_apps=terms)
            rows = self._make_db().execute(
                self._frames_query(config.recording_exclusion_sql(cfg))
            ).fetchall()
            self.assertEqual([r[0] for r in rows], expected, terms)

    def test_fragment_uses_config_yaml_list(self):
        try:
            config.CONFIG_PATH.write_text(
                "recording:\n  ignored_apps:\n    - Slack\n", encoding="utf-8"
            )
            rows = self._make_db().execute(
                self._frames_query(config.recording_exclusion_sql())
            ).fetchall()
            self.assertEqual([r[0] for r in rows], [1, 2, 3, 4, 5])
        finally:
            config.CONFIG_PATH.unlink()


class DefaultListDriftTestCase(unittest.TestCase):
    """The default list exists in four places by necessity (python config,
    Swift launch recipe, shell fallback, config.example.yaml docs) — these
    guards fail loudly when someone edits one copy only."""

    def test_swift_default_matches_python(self):
        swift = (REPO_ROOT / "mac" / "Sources" / "Recording.swift").read_text(
            encoding="utf-8"
        )
        block = re.search(
            r"defaultIgnoredApps\s*=\s*\[(.*?)\]", swift, re.DOTALL
        )
        self.assertIsNotNone(block, "defaultIgnoredApps literal not found")
        apps = re.findall(r'"([^"]+)"', block.group(1))
        self.assertEqual(apps, config.DEFAULT_IGNORED_APPS)

    def test_shell_fallback_matches_python(self):
        script = (REPO_ROOT / "ingest" / "screenpipe-export.sh").read_text(
            encoding="utf-8"
        )
        line = re.search(r"for term in (.+); do", script)
        self.assertIsNotNone(line, "fallback term loop not found")
        terms = re.findall(r"'([^']+)'", line.group(1))
        self.assertEqual(
            terms, [a.lower() for a in config.DEFAULT_IGNORED_APPS]
        )

    def test_example_yaml_matches_python(self):
        if config.yaml is None:  # pragma: no cover - PyYAML always in CI
            self.skipTest("PyYAML not installed")
        data = config.yaml.safe_load(
            (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            data["recording"]["ignored_apps"], config.DEFAULT_IGNORED_APPS
        )


if __name__ == "__main__":
    unittest.main()
