"""Audit 2026-07 — prompt patches for HTML-file delivery + absolute paths.

User-reported bug: with default_output_format=html the chat-delivery contract
instructed the agent to paste raw HTML into the transcript, and no prompt rule
required absolute paths (bg sessions isolate into <target>/.claude/worktrees/,
so relative paths point nowhere the owner can find). Pins:
A. html block: write HTML deliverables to a FILE at {target}/deliverables/,
   never paste raw source;
B. chat clause: file-type artifact carve-out — FINAL DRAFT: line then the
   file's absolute path + short summary (keeps _promote_if_delivered's
   non-empty-draft signal alive);
C. universal FILE PATH REPORTING block (all delivery modes, both formats);
D. rework gate_lines repeat the same rules, else one 打回 undoes A-C.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

from act import executor
from act.lib import config, registry
from act.lib.registry import Requirement, State

FULL_SID = "feedc0de-0000-4000-8000-000000000001"


class BuildPromptPathRulesTestCase(unittest.TestCase):
    def _prompt(self, fmt: str = "markdown", delivery_mode: str = "repo"):
        cfg = config.Config()
        cfg.memory_inject = False
        cfg.voice_enabled = False
        cfg.default_output_format = fmt
        req = Requirement.from_dict({"id": "R-980", "title": "写一份报告",
                                     "delivery_mode": delivery_mode})
        td = Path(tempfile.mkdtemp(prefix="audit-prompt-"))
        return executor.build_prompt(req, cfg, target=td), td

    def test_html_block_demands_file_at_target_deliverables(self):
        prompt, td = self._prompt(fmt="html", delivery_mode="chat")
        self.assertIn(f"{td}/deliverables/", prompt)
        self.assertIn("NEVER paste raw HTML", prompt)
        self.assertIn("ABSOLUTE path", prompt)

    def test_chat_clause_has_file_artifact_carveout(self):
        prompt, _ = self._prompt(delivery_mode="chat")
        self.assertIn("file-type artifacts", prompt)
        # the carve-out keeps the FINAL DRAFT: marker non-empty (path+summary)
        self.assertIn("absolute path plus a", prompt)
        self.assertIn("never the raw source", prompt)

    def test_file_path_reporting_block_is_universal(self):
        # applies to every mode/format — including the markdown default
        for fmt in ("markdown", "html"):
            for mode in ("chat", "repo"):
                prompt, td = self._prompt(fmt=fmt, delivery_mode=mode)
                self.assertIn("FILE PATH REPORTING", prompt,
                              msg=f"{fmt}/{mode}")
                self.assertIn(f"{td}/.claude/worktrees/", prompt,
                              msg=f"{fmt}/{mode}")

    def test_markdown_repo_still_has_no_html_block(self):
        prompt, _ = self._prompt(fmt="markdown", delivery_mode="repo")
        self.assertNotIn("OUTPUT FORMAT", prompt)


class ReworkGateLinePathRulesTestCase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        self.wt = Path(tempfile.mkdtemp(prefix="audit-rework-wt-")) / "worktree"
        for name, ret in (("_agent_info", {}),):
            patcher = mock.patch.object(executor, name, return_value=ret)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            executor, "_transcript_info",
            side_effect=lambda sid: (FULL_SID, self.wt)
            if str(sid).startswith("feedc0de") else None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _rework_prompt(self, delivery_mode: str) -> str:
        req = Requirement.from_dict({
            "id": "R-981", "title": "打回措辞", "status": State.REVIEW.value,
            "delivery_mode": delivery_mode,
            "execution": {"session_id": "feedc0de", "done": True}})
        registry.save(req)
        seen: dict = {}

        def runner(p: str) -> subprocess.CompletedProcess:
            seen["prompt"] = p
            return subprocess.CompletedProcess(
                ["claude"], 0, stdout="backgrounded · feedc0de", stderr="")

        self.assertTrue(executor.rework(req, "再改一版", self.cfg, runner=runner))
        return seen["prompt"]

    def test_chat_gate_line_has_file_artifact_exception(self):
        prompt = self._rework_prompt("chat")
        self.assertIn("文件型交付物", prompt)
        self.assertIn("绝对路径", prompt)
        self.assertIn("不贴源码", prompt)

    def test_repo_gate_line_requires_absolute_paths_too(self):
        self.assertIn("绝对路径", self._rework_prompt("repo"))


if __name__ == "__main__":
    unittest.main()
