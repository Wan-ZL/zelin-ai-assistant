"""actd 'capture' inbox action (CONTRACT §10) — one-liner -> RAISING card.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py); no LLM
is invoked (process_raising is NOT called here).
"""
import json
import unittest
import uuid

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import actd
from act.lib import config, registry


class CaptureActionTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        if config.REGISTRY_DIR.exists():
            for p in config.REGISTRY_DIR.glob("*.yaml"):
                p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()

    def _write_capture(self, text: str):
        payload = {"action": "capture", "text": text,
                   "ts": "2026-07-07T00:00:00Z"}
        path = config.INBOX_DIR / f"capture-{uuid.uuid4()}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def test_capture_creates_raising_entry_with_title_eq_text(self):
        text = "给 my-bench 加一个一键导出报告按钮"
        self._write_capture(text)
        processed = actd.process_inbox()
        self.assertEqual(processed, 1)

        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        req = entries[0]
        self.assertEqual(req.status, registry.State.RAISING.value)
        # 原话保留在 sources，channel=quick_capture（契约 §10）
        self.assertEqual(req.sources[0]["channel"], "quick_capture")
        self.assertEqual(req.sources[0]["quote"], text)
        # inbox 文件读后即删
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])

    def test_capture_same_text_twice_is_idempotent(self):
        text = "把 phase I 的任务生成脚本整理进 repo"
        self._write_capture(text)
        actd.process_inbox()
        self._write_capture(text)
        actd.process_inbox()

        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)          # merge_or_new 按 title 合并
        self.assertEqual(entries[0].status, registry.State.RAISING.value)

    def test_capture_does_not_downgrade_already_expanded_card(self):
        text = "整理 secrets 契约文档"
        self._write_capture(text)
        actd.process_inbox()
        req = [r for r in registry.load_all() if r.title == text][0]
        req.set_status(registry.State.CARD_SENT)   # 模拟 process_raising 已扩写完
        registry.save(req)

        self._write_capture(text)
        actd.process_inbox()
        entries = [r for r in registry.load_all() if r.title == text]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, registry.State.CARD_SENT.value)

    def test_capture_with_empty_text_creates_nothing(self):
        self._write_capture("   ")
        actd.process_inbox()
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(list(config.INBOX_DIR.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
