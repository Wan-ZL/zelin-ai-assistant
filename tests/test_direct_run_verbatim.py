"""§34 追记 D81（issue #448）——直跑卡的那句话逐字进会话，看板需求走 CLI 旁路。

判例（钉死的行为）：
- `dispatch_prompt.verbatim_direct_run` 的真值表：光杆 direct-run 卡为真；带
  plan / preset（§34bis 清理卡）/ DoD / summary 的一律为假（模板兜底，宁可多
  包装不可漏指令）；非 direct-run 卡恒假——含 §37.1 已有的三条假阳性形态
  （提升追加的 tag 行、fold 嵌入的字面标签、非 str notes），它们此刻不仅是
  「少问一次 CARD TITLE」，而是「整份需求文档在不在」。
- `typed_sentence` = capture 出生引文（换行原样），引文缺/异形回落 title。
- `render()` 对逐字卡返回**恰好**那句话：没有 `# Requirement` 头、没有
  `## Plan` / `## Sources`、没有围栏、没有 memory 头。
- `direct_run_system_prompt` 载着看板要的那点东西：工作编号、工作目录、
  chat 交付与安全边界、附图清单、§37.1 分档 CARD TITLE；**不含** memory 头。
- argv 旁路：dispatch 与 resume 两个启动点都挂 `--append-system-prompt`，
  prompt 位仍是最后一个参数且逐字等于那句话；非逐字卡的 argv 一个字节不变。
- 打回：逐字卡的 feedback 原样送达（会话契约在 system prompt 里常驻）。
- 收割：`harvest_delivery(whole_message=True)` 在没有 FINAL DRAFT marker 时
  整条收最后一条消息；有 marker 照旧按 marker 切。默认口径逐字节不变。
- `session.harvest_kwargs` 的让路：实测睡眠打断过的会话不吃整条口径
  （§71.3 要的是一次原地重试，不是把 "went to sleep" 当成果）。
- 端到端：blocked 的逐字直跑会话没有 marker 也能提升进待验收、带全文、
  **不**被标成 interrupted。
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import actd, executor
from act.lib import config, registry, sanitize
from act.lib import dispatch_prompt as dp
from act.lib.actd import session as actd_session
from act.lib.registry import Requirement, State

SENTENCE = "帮我把上周那份 onboarding 文档理一下\n顺便合并重复段落"
SID = "aaaa1111-0000-4000-8000-000000000001"


def _direct_run(**over) -> Requirement:
    """A card shaped exactly like `inbox._capture_direct_run` mints one."""
    base = dict(
        id="R-900", title="帮我把上周那份 onboarding 文档理一下 顺便合并重复段落",
        type="other", tier="T1", status=State.APPROVED.value, hardness="soft",
        delivery_mode="chat", notes="[direct-run] 用户直接开跑",
        sources=[registry.capture_source("zelin", "quick_capture", SENTENCE,
                                         capture_id="capture-abc")],
        execution={"approved_at": "2026-09-27T00:00:00Z"},
    )
    base.update(over)
    return Requirement(**base)


def _cfg(**over):
    cfg = config.Config()
    cfg.memory_inject = True      # 真开着也不许出现在逐字卡的 prompt 里
    cfg.voice_enabled = False
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


class VerbatimPredicateTestCase(unittest.TestCase):
    def test_plain_direct_run_card_is_verbatim(self):
        self.assertTrue(dp.verbatim_direct_run(_direct_run()))

    def test_preset_cleanup_card_keeps_the_template(self):
        # §34bis：固定 plan 只有模板的可信 ## Plan 区送得到（sources 围栏是
        # untrusted DATA，指令写进去按律会被忽略）——这类卡绝不走逐字。
        card = _direct_run(plan=["只读 registry", "产出三组清单"],
                           preset="proposals_triage")
        self.assertFalse(dp.verbatim_direct_run(card))

    def test_any_reviewed_instruction_content_falls_back_to_template(self):
        for field, value in (("plan", ["一步"]), ("preset", "proposals_triage"),
                             ("definition_of_done", ["CI 全绿"]),
                             ("summary", "一段人审过的摘要")):
            with self.subTest(field=field):
                self.assertFalse(dp.verbatim_direct_run(_direct_run(**{field: value})))

    def test_non_direct_run_cards_are_never_verbatim(self):
        # §37.1 已有的三条假阳性形态：此刻它们决定「整份需求文档在不在」
        for notes in (None,
                      "from radar: slack #ai-team\n[direct-run] 交付改为 chat",
                      "fold: 用户说「帮我查下 [direct-run] 标签为什么没生效」",
                      123):
            with self.subTest(notes=notes):
                self.assertFalse(dp.verbatim_direct_run(_direct_run(notes=notes)))

    def test_false_positive_shapes_still_get_the_full_template(self):
        cfg, target = _cfg(), Path("/golden/target")
        for notes in ("from radar: slack #ai-team\n[direct-run] 交付改为 chat",
                      "fold: 用户说「帮我查下 [direct-run] 标签为什么没生效」", 123):
            with self.subTest(notes=notes):
                prompt = dp.render(_direct_run(notes=notes), cfg, target, False)
                self.assertIn("QUALITY GATE", prompt)
                self.assertIn("## Sources", prompt)


class TypedSentenceTestCase(unittest.TestCase):
    def test_capture_quote_wins_and_keeps_newlines(self):
        self.assertEqual(dp.typed_sentence(_direct_run()), SENTENCE)

    def test_only_the_birth_source_counts_never_a_later_one(self):
        # 往后扫 = 把一段第三方文字（radar 引文、并入进来的别人的话）整条当成
        # prompt 送进会话——逐字派发没有围栏，那就是一条现成的注入路
        for sources in (["not-a-dict", {"quote": "别人说的话"}],
                        [{"quote": "  "}, {"quote": "别人说的话"}]):
            with self.subTest(sources=sources):
                card = _direct_run(sources=sources)
                self.assertEqual(dp.typed_sentence(card), card.title)

    def test_falls_back_to_title_without_a_usable_quote(self):
        self.assertEqual(dp.typed_sentence(_direct_run(sources=None)),
                         _direct_run().title)


class VerbatimRenderTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg, self.target = _cfg(), Path("/golden/target")
        self.prompt = dp.render(_direct_run(), self.cfg, self.target, False)

    def test_prompt_is_exactly_the_sentence(self):
        self.assertEqual(self.prompt, SENTENCE)

    def test_no_template_scaffolding_survives(self):
        for needle in ("# Requirement", "## Plan", "## Sources", "QUALITY GATE",
                       "FILE PATH REPORTING", "CARD TITLE",
                       sanitize.UNTRUSTED_OPEN):
            self.assertNotIn(needle, self.prompt, needle)

    def test_memory_head_never_reaches_a_verbatim_card(self):
        # issue #448 的原始症状：那份 auto-memory 索引属于另一个项目
        with tempfile.TemporaryDirectory(prefix="d81-mem-") as td:
            mem = Path(td) / "MEMORY.md"
            mem.write_text("# MEMORY\n- 地雷一\n- 地雷二\n", encoding="utf-8")
            with mock.patch.object(config, "MEMORY_PATH", mem):
                prompt = dp.render(_direct_run(), self.cfg, self.target, False)
                system = dp.direct_run_system_prompt(_direct_run(), self.cfg,
                                                     self.target)
        self.assertEqual(prompt, SENTENCE)
        self.assertNotIn("地雷一", system)
        self.assertNotIn("auto-memory", system)


class SystemPromptTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg, self.target = _cfg(), Path("/golden/target")

    def _system(self, **over) -> str:
        return dp.direct_run_system_prompt(_direct_run(**over), self.cfg, self.target)

    def test_carries_the_work_id_and_cwd(self):
        system = self._system()
        self.assertIn("R-900", system)
        self.assertIn(str(self.target), system)

    def test_says_the_sentence_is_verbatim(self):
        self.assertIn("原话", self._system())

    def test_keeps_the_delivery_and_safety_boundaries(self):
        system = self._system()
        self.assertIn("待验收", system)
        self.assertIn("push 到 main", system)
        self.assertIn("对外发消息", system)
        self.assertIn(f"{self.target}/deliverables/", system)

    def test_keeps_the_resident_upgrade_escape_hatch(self):
        # 常驻升级条款（聊天交付那一节）：「不进任何 repo」不许把 owner 自己的「定稿/落盘」
        # 指令反压住——system prompt 的位阶比他后来那条消息高
        self.assertIn("定稿", self._system())

    def test_final_draft_is_offered_not_mandated(self):
        system = self._system()
        self.assertIn("FINAL DRAFT:", system)
        self.assertIn("可选", system)

    def test_carries_the_card_title_tier(self):
        # §37.1 强制档（direct-run 无 display_title）——换了载体，判决不变
        self.assertIn("required this round", self._system())
        self.assertIn("re-check required",
                      self._system(display_title="整理 onboarding 文档"))

    def test_carries_the_attachment_list(self):
        system = self._system(execution={"attachments": ["/tmp/att/a.png"]})
        self.assertIn("用户附图", system)
        self.assertIn("/tmp/att/a.png", system)


class LaunchArgvTestCase(unittest.TestCase):
    """启动点的 argv：旁路挂在哪、prompt 位还是不是最后一个参数。"""

    def _argv(self, req, runner_call) -> list:
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="backgrounded · aaaa1111")
        with mock.patch.object(executor.subprocess, "run", fake_run):
            runner_call(req)
        return seen["cmd"]

    def test_dispatch_launch_appends_the_system_prompt(self):
        req, cfg = _direct_run(), _cfg()
        cmd = self._argv(req, lambda r: executor._default_runner(
            dp.render(r, cfg, Path("/golden/target"), False),
            Path("/golden/target"), name="R-900 · x", cfg=cfg, req=r))
        self.assertEqual(cmd.count("--append-system-prompt"), 1)
        i = cmd.index("--append-system-prompt")
        self.assertIn("待验收", cmd[i + 1])
        self.assertEqual(cmd[-2:], ["--", SENTENCE])   # prompt 位仍在末尾

    def test_non_verbatim_card_argv_is_unchanged(self):
        req, cfg = _direct_run(plan=["一步"]), _cfg()
        cmd = self._argv(req, lambda r: executor._default_runner(
            "PROMPT", Path("/golden/target"), name="n", cfg=cfg, req=r))
        self.assertNotIn("--append-system-prompt", cmd)
        self.assertNotIn("--", cmd)
        self.assertEqual(cmd[-1], "PROMPT")

    def test_a_dash_leading_sentence_still_launches(self):
        # 那一位自此是用户原话，而 commander 把以 `-` 开头的 operand 当选项
        # （实测 `claude -p "--version …"` → error: unknown option）——owner 打一句
        # 「--dangerously-skip-permissions 是干嘛的」就会每 pass 派发失败、5 次
        # 后撞上 §4 的派发刹车
        dashed = "--dangerously-skip-permissions 是干嘛的"
        req, cfg = _direct_run(sources=[registry.capture_source(
            "zelin", "quick_capture", dashed)]), _cfg()
        cmd = self._argv(req, lambda r: executor._default_runner(
            dp.render(r, cfg, Path("/golden/target"), False),
            Path("/golden/target"), name="n", cfg=cfg, req=r))
        self.assertEqual(cmd[-2:], ["--", dashed])

    def test_resume_launch_appends_it_too(self):
        # system prompt 是每次调用给的——resume 不重新挂上，打回那一轮的会话
        # 就没了交付与安全边界
        req, cfg = _direct_run(), _cfg()
        cmd = self._argv(req, lambda r: executor._run_resume(
            cfg, r, SID, Path("/golden/target"), "再补一句"))
        self.assertIn("--append-system-prompt", cmd)
        self.assertIn("--resume", cmd)
        self.assertEqual(cmd[-2:], ["--", "再补一句"])

    def test_the_contract_target_is_the_workbench_not_the_resume_cwd(self):
        # resume 的 cwd 是 transcript 上一次的目录——bg 会话中途会钻进
        # <workbench>/.claude/worktrees/<name>，拿它当交付目录 = 把成果指进一个
        # 随时会被 worktree 回收扫走的隐藏目录
        req, cfg = _direct_run(), _cfg()
        worktree = cfg.target_repo_path / ".claude" / "worktrees" / "R-900"
        cmd = self._argv(req, lambda r: executor._run_resume(
            cfg, r, SID, worktree, "再补一句"))
        system = cmd[cmd.index("--append-system-prompt") + 1]
        self.assertNotIn(".claude/worktrees", system)
        self.assertIn(f"{cfg.target_repo_path}/deliverables/", system)


class ReworkVerbatimTestCase(unittest.TestCase):
    def test_feedback_reaches_a_verbatim_card_untouched(self):
        self.assertEqual(
            dp.rework_prompt(_direct_run(), _cfg(), "  再把第三节压短一点  "),
            "再把第三节压短一点")

    def test_other_cards_keep_the_rework_wrapper(self):
        prompt = dp.rework_prompt(_direct_run(plan=["一步"]), _cfg(), "改一下")
        self.assertIn("Zelin 验收后打回了这次交付", prompt)
        self.assertIn("改一下", prompt)


class WholeMessageHarvestTestCase(unittest.TestCase):
    """executor.harvest_delivery(whole_message=...) —— 没有 marker 怎么收。"""

    LONG = "第一段答案。\n\n第二段答案，很长，" + "细节" * 300

    def setUp(self):
        # fake $HOME so the ~/.claude/projects glob lands in a throwaway dir
        home = tempfile.mkdtemp(prefix="d81-tx-")
        p = mock.patch.dict(os.environ, {"HOME": home})
        p.start()
        self.addCleanup(p.stop)
        self.proj = Path(home) / ".claude" / "projects" / "p"
        self.proj.mkdir(parents=True)

    def _write(self, text: str) -> None:
        rows = [
            {"type": "user", "message": {"content": SENTENCE}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
        ]
        (self.proj / f"{SID}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")

    def test_no_marker_whole_message_becomes_the_draft(self):
        self._write(self.LONG)
        out = executor.harvest_delivery(SID, whole_message=True)
        self.assertEqual(out["final_draft"], self.LONG)
        self.assertEqual(out["delivered_summary"], self.LONG[:500])

    def test_default_is_byte_identical_to_before(self):
        self._write(self.LONG)
        out = executor.harvest_delivery(SID)
        self.assertIsNone(out["final_draft"])
        self.assertEqual(out["delivered_summary"], self.LONG[:500])

    def test_a_real_marker_still_splits_on_it(self):
        self._write("交付说明\nFINAL DRAFT:\n成稿正文")
        out = executor.harvest_delivery(SID, whole_message=True)
        self.assertEqual(out["final_draft"], "成稿正文")
        self.assertEqual(out["delivered_summary"], "交付说明")

    def test_card_title_line_is_still_stripped(self):
        self._write("CARD TITLE: 整理 onboarding 文档\n答案正文")
        out = executor.harvest_delivery(SID, whole_message=True)
        self.assertEqual(out["card_title"], "整理 onboarding 文档")
        self.assertEqual(out["final_draft"], "答案正文")

    def test_a_lone_html_path_is_still_hydrated(self):
        # §15：交付物是一个 .html 文件时，成稿该是文件正文而不是「我写到了这个
        # 路径」那句话——marker 那条路一直如此，整条口径不许把它丢了
        page = Path(tempfile.mkdtemp(prefix="d81-html-")) / "report.html"
        page.write_text("<h1>整理结果</h1>", encoding="utf-8")
        self._write(f"写好了，见\n{page}")
        out = executor.harvest_delivery(SID, whole_message=True)
        self.assertEqual(out["final_draft"], "<h1>整理结果</h1>")
        self.assertIn(str(page), out["delivered_summary"])   # 路径仍在摘要里


class HarvestKwargsTestCase(unittest.TestCase):
    def test_verbatim_card_asks_for_the_whole_message(self):
        self.assertEqual(actd_session.harvest_kwargs(_direct_run(), {}),
                         {"whole_message": True})

    def test_sleep_interrupted_session_yields_to_the_retry(self):
        # §71.3：末尾那句 "API Error: … went to sleep" 不许冒充成果，否则
        # `_harvested_nothing` 变假、那次唯一的原地重试被顶掉
        self.assertEqual(
            actd_session.harvest_kwargs(_direct_run(), {"sleep_interrupted": True}),
            {})

    def test_the_sleep_yield_is_spent_after_the_one_retry(self):
        # 只让一次：`sleep_interrupted` 只在会话再次被看见活着时才被 _note_alive
        # 清掉，重试完没活过来的卡带着这面旗，宽口径不该就此永远回不来
        self.assertEqual(
            actd_session.harvest_kwargs(
                _direct_run(), {"sleep_interrupted": True, "sleep_retry_used": True}),
            {"whole_message": True})

    def test_other_cards_call_it_exactly_as_before(self):
        self.assertEqual(actd_session.harvest_kwargs(_direct_run(plan=["一步"]), {}), {})


class BlockedVerbatimPromotionTestCase(unittest.TestCase):
    """端到端：blocked 的逐字直跑会话没有 marker 也算交付（不是「会话受阻」）。"""

    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()
        self.cfg = config.Config()
        p = mock.patch.object(actd.notify, "notify", mock.Mock(return_value=True))
        self.notify = p.start()
        self.addCleanup(p.stop)
        probe = mock.patch.dict(actd._HARVEST_PROBE_AT, clear=True)
        probe.start()
        self.addCleanup(probe.stop)

    @staticmethod
    def _roster():
        return [{"id": "aaaa1111", "sessionId": SID, "state": "blocked",
                 "cwd": "/tmp/wt", "name": "bg agent",
                 "startedAt": "2026-09-27T00:00:00Z"}]

    def _harvest(self):
        """A harvest stub that honours the flag exactly like the real one."""
        def fake(sid, whole_message=False):
            if whole_message:
                return {"delivered_summary": "答案正文"[:500], "final_draft": "答案正文",
                        "card_title": None}
            return {"delivered_summary": "答案正文", "final_draft": None,
                    "card_title": None}
        return mock.Mock(side_effect=fake)

    def _pass(self, req, harvest):
        registry.save(req)
        with mock.patch.object(actd, "_run_claude_agents", return_value=self._roster()), \
             mock.patch.object(actd.executor, "harvest_delivery", harvest), \
             mock.patch.object(actd.executor, "resume", mock.Mock(return_value=True)):
            actd.reconcile_executing(self.cfg, set())
        return registry.load(req.id)

    def test_verbatim_card_promotes_with_the_whole_answer(self):
        req = _direct_run(status=State.EXECUTING.value,
                          execution={"session_id": "aaaa1111"})
        saved = self._pass(req, self._harvest())
        ex = saved.execution or {}
        self.assertEqual(saved.status, State.REVIEW.value)
        self.assertEqual(ex.get("final_draft"), "答案正文")
        self.assertNotIn("interrupted_reason", ex)   # 这是交付，不是中断

    def test_template_card_without_a_marker_is_still_an_interruption(self):
        req = _direct_run(plan=["一步"], status=State.EXECUTING.value,
                          execution={"session_id": "aaaa1111"})
        saved = self._pass(req, self._harvest())
        self.assertEqual(saved.status, State.REVIEW.value)
        self.assertEqual((saved.execution or {}).get("interrupted_reason"), "blocked")

    def test_a_vanished_session_still_resumes_on_a_half_sentence(self):
        # 宽判据**只**属于 blocked（会话确实收工了）。死掉 / 从 roster 消失的
        # 会话最后那句话可能只是「好的，我先看一下相关文件」——把半句在途进度
        # 当成果收下就等于把 §16/§46 的自动救活对这一类卡整条关掉。
        req = _direct_run(status=State.EXECUTING.value,
                          execution={"session_id": "aaaa1111"})
        registry.save(req)
        resume = mock.Mock(return_value=True)
        with mock.patch.object(actd, "_run_claude_agents", return_value=[]), \
             mock.patch.object(actd.executor, "harvest_delivery", self._harvest()), \
             mock.patch.object(actd.executor, "resume", resume):
            actd.reconcile_executing(self.cfg, set())
        saved = registry.load(req.id)
        resume.assert_called_once()
        self.assertEqual(saved.status, State.EXECUTING.value)
        self.assertIsNone((saved.execution or {}).get("final_draft"))

    def test_queued_briefing_is_injected_before_the_wider_delivery_test(self):
        # 宽判据会抢在注入窗口前面把卡提升掉，排队的 owner 指令就再也没人投递
        # 也没人留痕（`_drop_undelivered_steers` 只挂在 done 那条路上）
        req = _direct_run(status=State.EXECUTING.value,
                          execution={"session_id": "aaaa1111",
                                     "pending_briefings": ["fyi"]})
        registry.save(req)
        brief = mock.Mock(return_value=True)
        with mock.patch.object(actd, "_run_claude_agents", return_value=self._roster()), \
             mock.patch.object(actd.executor, "harvest_delivery", self._harvest()), \
             mock.patch.object(actd.executor, "brief", brief), \
             mock.patch.object(actd.executor, "resume", mock.Mock(return_value=True)):
            actd.reconcile_executing(self.cfg, set())
        brief.assert_called_once()
        # 本 pass 不提升：会话吃下那句话后，下一 pass 再按新的最后一条消息判交付
        self.assertEqual(registry.load(req.id).status, State.EXECUTING.value)


if __name__ == "__main__":
    unittest.main()
