"""§17（issue #23）重点人物账本 — 人物解析、关键词护栏与配置三层。

护栏是这个功能存在的前提（2026-07-08 占位配置退化成 "your" 回填 92 篇笔记）：
占位 ``your.manager`` / 停用词 / <3 字母 token 一律不产生匹配器；``people_ledger.people``
非空优先，空则回落 ``sources.watch_people``；开关默认 false，config.yaml 块与
overrides 扁平键（``people_ledger_enabled`` / ``people_ledger_people``）都被读到。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config
from act.lib import people_ledger_store as store


class TokenGuardTestCase(unittest.TestCase):
    def test_placeholder_and_stopwords_yield_no_tokens(self):
        for bad in ("your.manager", "Your.Manager", "your", "the", "my", "", "  ", "ab", "@my"):
            with self.subTest(handle=bad):
                self.assertEqual(store.tokens_for(bad), [])

    def test_handle_derives_whole_and_parts(self):
        self.assertEqual(store.tokens_for("arash.khoshbakht"), ["arash.khoshbakht", "arash", "khoshbakht"])
        self.assertEqual(store.tokens_for("@sal_khan"), ["sal_khan", "sal", "khan"])
        # 短段被砍，整串保留
        self.assertEqual(store.tokens_for("li.xu"), ["li.xu"])

    def test_cjk_name_allows_two_chars(self):
        self.assertEqual(store.tokens_for("小明"), ["小明"])
        self.assertEqual(store.slugify("小明")[:2], "p-")
        self.assertEqual(store.display_name("小明"), "小明")

    def test_mention_is_word_bounded_and_case_insensitive(self):
        p = store.Person("arash.k", store.tokens_for("arash.k"))
        self.assertTrue(p.mentioned_in("Met ARASH today"))
        self.assertTrue(p.mentioned_in("ping @arash.k about it"))
        self.assertFalse(p.mentioned_in("the arashi festival"))
        self.assertFalse(p.mentioned_in(""))

    def test_slug_and_display(self):
        self.assertEqual(store.slugify("Arash.Khoshbakht"), "arash-khoshbakht")
        self.assertEqual(store.display_name("arash.khoshbakht"), "Arash")
        self.assertEqual(store.display_name("@sal.khan"), "Sal")


class ResolvePeopleTestCase(unittest.TestCase):
    def test_people_list_wins_over_watch_people(self):
        cfg = config.Config(people_ledger_people=["arash.k"], watch_people=["sal.khan"])
        people, dropped = store.resolve_people(cfg)
        self.assertEqual([p.handle for p in people], ["arash.k"])
        self.assertEqual(dropped, [])

    def test_empty_people_falls_back_to_watch_people_with_guard(self):
        cfg = config.Config(watch_people=["your.manager", "sal.khan", "my", "SAL.KHAN"])
        people, dropped = store.resolve_people(cfg)
        self.assertEqual([p.handle for p in people], ["sal.khan"])       # 去重（大小写）
        self.assertEqual(dropped, ["your.manager", "my"])

    def test_no_people_at_all(self):
        self.assertEqual(store.resolve_people(config.Config()), ([], []))


class ConfigLayersTestCase(unittest.TestCase):
    def _load(self, body: str, overrides=None) -> config.Config:
        root = Path(tempfile.mkdtemp(prefix="cfg-ledger-"))
        (root / "config.yaml").write_text(body, encoding="utf-8")
        ov = root / "settings_overrides.json"
        if overrides is not None:
            ov.write_text(json.dumps(overrides), encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", root / "config.yaml"), \
                mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", ov):
            return config.load_config()

    def test_default_off_and_empty(self):
        cfg = self._load("owner:\n  name: X\n")
        self.assertFalse(cfg.people_ledger_enabled)
        self.assertEqual(cfg.people_ledger_people, [])
        self.assertFalse(config.Config().people_ledger_enabled)

    def test_yaml_block_read_and_bad_values_tolerated(self):
        cfg = self._load("people_ledger:\n  enabled: 'yes'\n  people: [arash.k, 7, '', null, ' sal ']\n"
                         "  max_notes_per_pass: 3\n")
        self.assertTrue(cfg.people_ledger_enabled)
        self.assertEqual(cfg.people_ledger_people, ["arash.k", "7", "sal"])
        self.assertEqual(cfg.raw["people_ledger"]["max_notes_per_pass"], 3)
        cfg = self._load("people_ledger:\n  enabled: maybe\n  people: not-a-list\n")
        self.assertFalse(cfg.people_ledger_enabled)
        self.assertEqual(cfg.people_ledger_people, [])

    def test_override_flat_keys(self):
        cfg = self._load("people_ledger:\n  enabled: false\n  people: [a.b.c]\n",
                         {"people_ledger_enabled": True, "people_ledger_people": ["sal.khan", 3]})
        self.assertTrue(cfg.people_ledger_enabled)
        self.assertEqual(cfg.people_ledger_people, ["sal.khan", "3"])
        self.assertIn("people_ledger_people", config._OVERRIDE_LIST_FIELDS)
        self.assertIn("people_ledger_enabled", config._OVERRIDE_FIELDS)

    def test_override_empty_list_means_fallback_to_watch_people(self):
        cfg = self._load("people_ledger:\n  people: [a.b.c]\nsources:\n  watch_people: [sal.khan]\n",
                         {"people_ledger_people": []})
        self.assertEqual(cfg.people_ledger_people, [])
        people, _ = store.resolve_people(cfg)
        self.assertEqual([p.handle for p in people], ["sal.khan"])


if __name__ == "__main__":
    unittest.main()
