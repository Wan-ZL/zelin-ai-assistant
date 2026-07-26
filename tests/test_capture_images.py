"""输入框贴图（用户建议 #5）— the capture inbox action's ``images`` field.

Covers the whole local chain: capture JSON carries absolute PNG paths →
actd folds them into the card's ``execution.attachments`` (add-only, deduped,
junk-tolerant, works for both the proposal path and mode="run") →
executor.build_prompt lists them in a「用户附图」Read block, one path per
line — and stays byte-silent when a card has no attachments.

Hermetic: everything under the sandbox AIASSISTANT_HOME (tests/__init__.py);
no LLM runs (process_raising is never called; build_prompt is a pure
function with memory_inject off and a tempdir target).
"""
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import config, registry
from act.lib.registry import Requirement, State


class CaptureImagesTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        for p in config.INBOX_DIR.glob("*.json"):
            p.unlink()

    def _write_capture(self, text, images=None, mode=None):
        payload = {"action": "capture", "text": text,
                   "ts": "2026-07-25T00:00:00Z"}
        if images is not None:
            payload["images"] = images
        if mode is not None:
            payload["mode"] = mode
        path = config.INBOX_DIR / f"capture-{uuid.uuid4()}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False),
                        encoding="utf-8")

    def _the_card(self, title):
        entries = [r for r in registry.load_all() if r.title == title]
        self.assertEqual(len(entries), 1)
        return entries[0]

    def test_capture_images_land_in_execution_attachments(self):
        text = "看看这两张截图里的报错"
        imgs = ["/tmp/att/a-1.png", "/tmp/att/a-2.png"]
        self._write_capture(text, images=imgs)
        self.assertEqual(actd.process_inbox(), 1)
        req = self._the_card(text)
        self.assertEqual(req.status, State.RAISING.value)
        self.assertEqual((req.execution or {}).get("attachments"), imgs)

    def test_capture_without_images_leaves_execution_untouched(self):
        text = "不带图的普通捕获"
        self._write_capture(text)
        actd.process_inbox()
        req = self._the_card(text)
        self.assertNotIn("attachments", req.execution or {})

    def test_repeat_capture_appends_new_paths_only(self):
        # merge_or_new folds the second capture into the SAME card; its images
        # append add-only + deduped, never clobbering the earlier list.
        text = "把 bench 报告页面改成深色"
        self._write_capture(text, images=["/tmp/att/b-1.png", "/tmp/att/b-2.png"])
        actd.process_inbox()
        self._write_capture(text, images=["/tmp/att/b-2.png", "/tmp/att/b-3.png"])
        actd.process_inbox()
        req = self._the_card(text)
        self.assertEqual((req.execution or {}).get("attachments"),
                         ["/tmp/att/b-1.png", "/tmp/att/b-2.png",
                          "/tmp/att/b-3.png"])

    def test_junk_image_entries_are_dropped(self):
        text = "垃圾条目不许污染卡片"
        self._write_capture(text, images=["", 42, None, {"p": "x"},
                                          "  ", "/tmp/att/ok.png"])
        actd.process_inbox()
        req = self._the_card(text)
        self.assertEqual((req.execution or {}).get("attachments"),
                         ["/tmp/att/ok.png"])

    def test_non_list_images_is_ignored(self):
        text = "images 不是 list 时整个忽略"
        self._write_capture(text, images="/tmp/att/not-a-list.png")
        actd.process_inbox()
        req = self._the_card(text)
        self.assertNotIn("attachments", req.execution or {})

    def test_direct_run_capture_keeps_attachments(self):
        # mode="run" promotes straight to APPROVED — the attachments must ride
        # along so the dispatch prompt can list them.
        text = "直接开跑并看这张图"
        self._write_capture(text, images=["/tmp/att/run-1.png"], mode="run")
        actd.process_inbox()
        req = self._the_card(text)
        self.assertEqual(req.status, State.APPROVED.value)
        self.assertEqual((req.execution or {}).get("attachments"),
                         ["/tmp/att/run-1.png"])
        # the run promotion's own bookkeeping survives the attach
        self.assertIn("approved_at", req.execution)


class BuildPromptAttachmentsTestCase(unittest.TestCase):
    def _prompt(self, execution):
        req = Requirement(id="R-901", title="附图任务",
                          status=State.APPROVED.value,
                          delivery_mode="chat", execution=execution)
        cfg = config.Config()
        cfg.memory_inject = False  # keep the test off the real ~/.claude memory
        with tempfile.TemporaryDirectory(prefix="att-target-") as td:
            return executor.build_prompt(req, cfg=cfg, target=Path(td))

    def test_attachments_listed_one_per_line(self):
        prompt = self._prompt(
            {"attachments": ["/tmp/att/x-1.png", "/tmp/att/x-2.png"]})
        self.assertIn("## 用户附图（用 Read 工具打开查看）", prompt)
        self.assertIn("\n/tmp/att/x-1.png\n/tmp/att/x-2.png", prompt)

    def test_no_attachments_no_block(self):
        for execution in (None, {}, {"attachments": []},
                          {"attachments": "junk"},
                          {"attachments": [None, 3, "   "]}):
            with self.subTest(execution=execution):
                self.assertNotIn("用户附图", self._prompt(execution))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
