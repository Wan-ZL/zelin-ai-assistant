"""§37 session-content search index — state/search_index.json (Mac-local).

Covers (v0.37 build brief):
- executor.transcript_plain_text: main-thread user+assistant text only
  (sidechain / isMeta / tool-result lines excluded — the v0.33.1 discipline),
  tail-capped;
- search_index.update_card: builds/refreshes entries atomically, no-ops on
  missing session/transcript, tolerates a corrupt existing file;
- search_index.prune: terminal (trashed/merged) and absent cards drop, live
  cards stay, missing file = instant no-op.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor
from act.lib import config, registry, search_index
from act.lib.registry import Requirement, State

SID = "cccc3333-0000-4000-8000-000000000003"  # short id = cccc3333


def _line(kind: str, text: str, **extra) -> str:
    d = {"type": kind, "message": {"content": [{"type": "text", "text": text}]}}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False)


class TranscriptPlainTextTestCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="searchidx-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.proj = Path(self.home) / ".claude" / "projects" / "p"
        self.proj.mkdir(parents=True)

    def _write(self, lines: list) -> None:
        (self.proj / f"{SID}.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    def test_main_thread_user_and_assistant_only(self):
        self._write([
            _line("user", "请研究 EB-1A 的推荐信"),
            _line("assistant", "好的，先列出三个方向"),
            _line("assistant", "sidechain 的话", isSidechain=True),
            _line("user", "meta 行", isMeta=True),
            json.dumps({"type": "user", "toolUseResult": {"x": 1},
                        "message": {"content": [
                            {"type": "tool_result", "content": "tool"}]}}),
            _line("assistant", "最终结论：绿卡材料齐了"),
        ])
        text = executor.transcript_plain_text(SID)
        self.assertIn("请研究 EB-1A 的推荐信", text)
        self.assertIn("最终结论：绿卡材料齐了", text)
        self.assertNotIn("sidechain 的话", text)
        self.assertNotIn("meta 行", text)
        self.assertNotIn("tool", text)

    def test_tail_cap(self):
        self._write([_line("assistant", "x" * 1000) for _ in range(10)])
        text = executor.transcript_plain_text(SID, cap=500)
        self.assertEqual(len(text), 500)

    def test_missing_or_short_sid(self):
        self.assertIsNone(executor.transcript_plain_text("abc"))
        self.assertIsNone(executor.transcript_plain_text(SID))  # no transcript


class UpdateAndPruneTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        if search_index.INDEX_PATH.exists():
            search_index.INDEX_PATH.unlink()

    def test_update_card_writes_entry(self):
        with mock.patch.object(executor, "transcript_plain_text",
                               return_value="会话里聊到了 EB-1A"):
            self.assertTrue(search_index.update_card("R-960", SID))
        data = json.loads(search_index.INDEX_PATH.read_text(encoding="utf-8"))
        self.assertEqual(data["R-960"]["text"], "会话里聊到了 EB-1A")
        self.assertIn("updated_at", data["R-960"])
        # unchanged text -> no rewrite
        with mock.patch.object(executor, "transcript_plain_text",
                               return_value="会话里聊到了 EB-1A"):
            self.assertFalse(search_index.update_card("R-960", SID))

    def test_update_card_noops_without_session_or_text(self):
        self.assertFalse(search_index.update_card("R-961", None))
        with mock.patch.object(executor, "transcript_plain_text",
                               return_value=None):
            self.assertFalse(search_index.update_card("R-961", SID))
        self.assertFalse(search_index.INDEX_PATH.exists())

    def test_corrupt_index_is_tolerated(self):
        search_index.INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        search_index.INDEX_PATH.write_text("{not json", encoding="utf-8")
        self.assertEqual(search_index.load_index(), {})
        with mock.patch.object(executor, "transcript_plain_text",
                               return_value="重建后的文本"):
            self.assertTrue(search_index.update_card("R-962", SID))
        data = json.loads(search_index.INDEX_PATH.read_text(encoding="utf-8"))
        self.assertEqual(list(data), ["R-962"])

    def test_prune_drops_terminal_and_absent(self):
        live = Requirement(id="R-963", title="live",
                           status=State.EXECUTING.value)
        registry.save(live)
        binned = Requirement(id="R-964", title="binned",
                             status=State.TRASHED.value)
        registry.save(binned)
        search_index.INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        search_index.INDEX_PATH.write_text(json.dumps({
            "R-963": {"updated_at": "x", "text": "keep"},
            "R-964": {"updated_at": "x", "text": "trashed"},
            "R-999": {"updated_at": "x", "text": "gone"},
        }), encoding="utf-8")
        self.assertEqual(search_index.prune(), 2)
        data = json.loads(search_index.INDEX_PATH.read_text(encoding="utf-8"))
        self.assertEqual(list(data), ["R-963"])

    def test_prune_missing_file_is_free(self):
        self.assertEqual(search_index.prune(), 0)


if __name__ == "__main__":
    unittest.main()
