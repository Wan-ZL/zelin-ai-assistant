"""Golden prompts — executor.build_prompt / rework / brief 的字节级判例。

P3 重构（CRAP ≤ 6）把三个 prompt 装配函数拆成小块，法典要求零行为变化
（vnext2-plan R2.3.2）。「零」在这里的定义就是这批 golden：每个夹具卡在
固定输入下产出的 prompt 全文，与 ``tests/fixtures/prompt/<case>.golden.txt``
逐字节相等（`.gitattributes -text`，任何平台零换行转换）。覆盖的分支：
repo 有/无 remote、chat 交付、html 输出格式、training、green sign、附图、
memory 注入、voice 档案、§37.1 三档 CARD TITLE（user / forced（direct-run
与不可读标题两种理由）/ recheck（含存量 display_title 的围栏现值））、
sources 的 who/ref 形态与非 dict 条目；rework 的 chat/repo 两种 gate 行与
三档 title 行；brief 的围栏与前缀。

重铸（只在有意改 prompt 文案的 PR 里）：
    python3 tests/test_executor_prompt_golden.py --write
路径类输入全部固定为 /golden/…（has_remote / resolve_voice_profile /
MEMORY_PATH 打桩），所以 golden 不含任何机器相关字节。
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import executor
from act.lib import config
from act.lib.registry import Requirement, State

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "prompt"
TARGET = Path("/golden/target")
VOICE = Path("/golden/voice-profile.md")
MEMORY = "# MEMORY\n- landmine one\n- landmine two\n"
FULL_SID = "feedc0de-0000-4000-8000-000000000001"

_SOURCES = [
    {"channel": "slack", "date": "2026-07-08", "who": "Quinton",
     "quote": "can you send the recap by Friday?"},
    {"channel": "meeting", "date": "2026-07-09", "ref": "standup notes §3"},
    "not-a-dict-source",
]


def _cfg(**over):
    cfg = config.Config()
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def _req(**over):
    base = dict(id="R-042", title="Follow up on the review thread",
                status=State.APPROVED.value, type="dev", tier=2, hardness="M",
                deadline="2026-07-12", summary="One-paragraph summary.",
                definition_of_done=["tests green", "PR opened"],
                plan=["read the thread", "draft the reply"], sources=_SOURCES,
                execution={"attachments": ["/golden/a.png", " ", 7, "/golden/b.png"]})
    base.update(over)
    return Requirement(**base)


# (case name, remote?, cfg overrides, requirement)
_BUILD_CASES = [
    ("build_repo_remote_recheck", True, {}, _req()),
    ("build_repo_no_remote_plain", False,
     {"memory_inject": False, "voice_enabled": False},
     _req(summary=None, definition_of_done=None, plan="just do it",
          sources=None, execution=None, deadline=None, type=None)),
    ("build_chat_html_green_sign", True, {"default_output_format": "HTML"},
     _req(delivery_mode="chat", green_sign_required=True, plan=None)),
    ("build_training_user_titled", True,
     {"self_check": False, "fresh_context_review": False},
     _req(type="Training", user_titled=True, display_title="钦定名")),
    ("build_direct_run_forced", True, {},
     _req(notes="[direct-run] 用户直接开跑\n后续 fold 行", title="修一下登录页")),
    ("build_unreadable_title_forced", False, {},
     _req(title="https://example.com/some/very/long/path?q=1")),
    ("build_display_title_recheck", True, {},
     _req(display_title="  整理  推荐信 " + "长" * 80, work_id="R-900", id="P-042")),
]

_REWORK_CASES = [
    ("rework_repo_recheck", _req(status=State.REVIEW.value,
                                 execution={"session_id": "feedc0de"})),
    ("rework_chat_forced", _req(status=State.REVIEW.value, delivery_mode="chat",
                                title="/Users/z/Documents/report.pdf",
                                target_repo="/golden/workbench",
                                execution={"session_id": "feedc0de"})),
    ("rework_user_titled", _req(status=State.REVIEW.value, user_titled=True,
                                execution={"session_id": "feedc0de"})),
]


def _render_build(remote, over, req):
    cfg = _cfg(**over)
    with tempfile.TemporaryDirectory(prefix="golden-mem-") as td:
        mem = Path(td) / "MEMORY.md"
        mem.write_text(MEMORY, encoding="utf-8")
        with mock.patch.object(executor, "has_remote", return_value=remote), \
                mock.patch.object(executor, "resolve_voice_profile", return_value=VOICE), \
                mock.patch.object(config, "MEMORY_PATH", mem):
            return executor.build_prompt(req, cfg, target=TARGET)


def _render_rework(req):
    seen = []

    def runner(p):
        seen.append(p)
        return subprocess.CompletedProcess(["claude"], 0, stdout="backgrounded · feedc0de")
    with mock.patch.object(executor, "_agent_info", return_value={}), \
            mock.patch.object(executor, "_transcript_info", return_value=(FULL_SID, TARGET)), \
            mock.patch.object(executor.Path, "mkdir"), \
            mock.patch.object(executor, "save"):
        ok = executor.rework(req, "  再补一个测试\n并更新 README  ", _cfg(), runner=runner)
    assert ok, "rework golden runner must launch"
    return seen[0]


def _render_brief():
    req = _req(status=State.EXECUTING.value,
               execution={"session_id": "feedc0de",
                          "pending_briefings": ["Quinton 说周五前要 recap", " ", "第二条"]})
    seen = []

    def runner(p):
        seen.append(p)
        return subprocess.CompletedProcess(["claude"], 0, stdout="backgrounded · feedc0de")
    with mock.patch.object(executor, "_agent_info", return_value={}), \
            mock.patch.object(executor, "_briefing_window_open", return_value=True), \
            mock.patch.object(executor, "_transcript_info", return_value=(FULL_SID, TARGET)), \
            mock.patch.object(executor.Path, "mkdir"), \
            mock.patch.object(executor, "load", return_value=req), \
            mock.patch.object(executor, "save"):
        ok = executor.brief(req, _cfg(), runner=runner)
    assert ok, "brief golden runner must launch"
    return seen[0]


def _all_cases():
    for name, remote, over, req in _BUILD_CASES:
        yield name, (lambda r=remote, o=over, q=req: _render_build(r, o, q))
    for name, req in _REWORK_CASES:
        yield name, (lambda q=req: _render_rework(q))
    yield "brief_fenced_lines", _render_brief


def _write_goldens():
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, render in _all_cases():
        (GOLDEN_DIR / f"{name}.golden.txt").write_bytes(render().encode("utf-8"))
        print("wrote", name)


class PromptGoldenTestCase(unittest.TestCase):
    def test_every_prompt_matches_its_golden(self):
        for name, render in _all_cases():
            with self.subTest(case=name):
                path = GOLDEN_DIR / f"{name}.golden.txt"
                self.assertTrue(path.exists(), f"{path.name} missing — see module docstring")
                self.assertEqual(render().encode("utf-8"), path.read_bytes(), name)

    def test_goldens_are_pinned_lf_on_disk(self):
        attrs = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("tests/fixtures/prompt/*.golden.txt -text", attrs)
        goldens = sorted(GOLDEN_DIR.glob("*.golden.txt"))
        self.assertEqual(len(goldens), len(list(_all_cases())))
        for p in goldens:
            self.assertNotIn(b"\r", p.read_bytes(), p.name)


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write_goldens()
    else:
        unittest.main()
