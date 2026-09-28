"""参考驾驶员与驾驶协议的对表（CONTRACT §79.2）。

协议有两端：TS 侧 `web/e2e/ui_scout/core/protocol.ts` 做消毒，python 侧
`scripts/qa/ui_scout_pilot.py` 把同一套词表写进 prompt。两端各写一份是协议的性质决定的
（一端是代码、一端是给模型看的话），所以这里逐字对表——漂了就判红，别等到模型发出一个
TS 侧拒收的动词才发现（防腐 #9 命名单源在跨语言时的落法）。

另外钉：外部页面文本进 prompt 必过围栏（宪法第 5 条）、模型任何一种失态都降级成 give_up
而不是抛（宪法第 11 条）、绝不真起 claude（runner 注入缝）。
"""

import os
import sys
import unittest

from tests import TMP_HOME  # noqa: F401 - ensures the sandbox env is set first

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_QA_DIR = os.path.join(_ROOT, "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import ui_scout_pilot as pilot  # noqa: E402

PROTOCOL_TS = os.path.join(_ROOT, "web", "e2e", "ui_scout", "core", "protocol.ts")


def _ts_literals(block_name):
    """protocol.ts 里 `export const <NAME> = [ ... ] as const;` 的字符串字面量，按顺序。

    只按引号切——这三个块都是一行一行的纯字面量数组，没有注释也没有转义引号。
    """
    with open(PROTOCOL_TS, encoding="utf-8") as handle:
        source = handle.read()
    start = source.index("export const %s = [" % block_name)
    body = source[start:source.index("]", start)]
    parts = body.split('"')          # `"a", "b"` → ['', 'a', ', ', 'b', '']
    return [parts[i] for i in range(1, len(parts), 2)]


class VocabularyMirrorTestCase(unittest.TestCase):
    """prompt 里的词表 = protocol.ts 的词表，逐字、同序。"""

    def test_verbs_match_the_ts_source(self):
        self.assertEqual(list(pilot.VERBS), _ts_literals("VERBS"))

    def test_keys_match_the_ts_source(self):
        self.assertEqual(list(pilot.KEYS), _ts_literals("KEYS"))

    def test_pages_match_the_ts_source(self):
        self.assertEqual(list(pilot.PAGES), _ts_literals("PAGES"))

    def test_every_verb_is_spelled_in_the_instructions(self):
        for verb in pilot.VERBS:
            self.assertIn('"verb":"%s"' % verb, pilot.INSTRUCTIONS,
                          "%s 没有写进给模型的动作清单" % verb)

    def test_keys_and_pages_are_offered_as_alternatives(self):
        self.assertIn("|".join(pilot.KEYS), pilot.INSTRUCTIONS)
        self.assertIn("|".join(pilot.PAGES), pilot.INSTRUCTIONS)

    def test_the_extractor_only_accepts_known_verbs(self):
        self.assertIsNone(pilot.extract_action('{"verb":"drag","ref":"e1"}'))


class ObservationMirrorTestCase(unittest.TestCase):
    """prompt 读的每一个 Observation 字段，跑者那边都真的会发——否则就是一段死的好意。

    `notes` 出过这个事：prompt 里写着「沙箱须知：%s」，protocol.ts 的 Observation 没这个
    字段、run.ts 也没填，于是每一步都渲染成「沙箱须知：（无）」，§79.1 说的「防假红的第一道
    闸」一直是空的；本文件的 fixture 自己手写了一个 notes 键，还把这条测试照绿了。所以这里
    不看 fixture，只看**两端的真源**。
    """

    def _source(self, *parts):
        with open(os.path.join(_ROOT, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_notes_is_a_declared_observation_field(self):
        source = self._source("web", "e2e", "ui_scout", "core", "protocol.ts")
        body = source.split("export interface Observation {", 1)[1].split("}", 1)[0]
        self.assertIn("notes:", body)

    def test_the_runner_actually_fills_notes(self):
        source = self._source("web", "e2e", "ui_scout", "run.ts")
        self.assertIn("notes: journey.notes", source)

    def test_every_observation_key_the_prompt_reads_is_declared(self):
        body = self._source("web", "e2e", "ui_scout", "core", "protocol.ts") \
            .split("export interface Observation {", 1)[1].split("\n}", 1)[0]
        for key in ("goal", "notes", "step", "maxSteps", "url", "lang",
                    "lastAction", "lastError", "settling", "screenshot", "elements", "text"):
            self.assertIn("%s:" % key, body, "prompt 读 %s，但 Observation 没声明它" % key)


class TimeoutPairingTestCase(unittest.TestCase):
    """驾驶员的预算从跑者那一个真源派生，且**严格更短**（§79.2）。

    两边各留一个默认值、名字还不一样，本分支一度就是这样（跑者 120 s、驾驶员 180 s）：
    跑者先放弃，模型 180 s 才答上来，那一行漂回来就成了下一问的答案，整趟错位一步。
    """

    def test_the_default_mirrors_the_ts_source(self):
        with open(PROTOCOL_TS, encoding="utf-8") as handle:
            source = handle.read()
        line = source.split("export const DEFAULT_PILOT_TIMEOUT_MS = ", 1)[1].split(";", 1)[0]
        self.assertEqual(int(line.replace("_", "")), pilot.DEFAULT_RUNNER_TIMEOUT_MS)

    def test_it_is_derived_from_the_runners_budget(self):
        self.assertEqual(pilot.step_timeout({"ZAI_UI_SCOUT_PILOT_TIMEOUT_MS": "60000"}), 50)

    def test_it_is_always_strictly_shorter_than_the_runners_budget(self):
        for runner_ms in (20_000, 60_000, 120_000, 600_000):
            budget = pilot.step_timeout({"ZAI_UI_SCOUT_PILOT_TIMEOUT_MS": str(runner_ms)})
            self.assertLess(budget * 1000, runner_ms,
                            "%s ms 的读超时配了 %s s 的模型预算" % (runner_ms, budget))

    def test_a_missing_or_broken_knob_falls_back_to_the_mirrored_default(self):
        expected = pilot.DEFAULT_RUNNER_TIMEOUT_MS // 1000 - pilot.TIMEOUT_SLACK
        for env in ({}, {"ZAI_UI_SCOUT_PILOT_TIMEOUT_MS": ""},
                    {"ZAI_UI_SCOUT_PILOT_TIMEOUT_MS": "abc"},
                    {"ZAI_UI_SCOUT_PILOT_TIMEOUT_MS": "-1"}):
            self.assertEqual(pilot.step_timeout(env), expected)

    def test_a_tiny_budget_still_leaves_the_model_some_time(self):
        self.assertEqual(pilot.step_timeout({"ZAI_UI_SCOUT_PILOT_TIMEOUT_MS": "1000"}),
                         pilot.MIN_STEP_TIMEOUT)


class FailSeverityMirrorTestCase(unittest.TestCase):
    """判红门槛两端同一个值：TS 侧 `FAIL_SEVERITY` 是真源，python 的 `--fail-on` 默认值是镜像。

    一个没人读的「单一真源」比没有更坏——它看起来像个闸，实际上谁都没关。这条测试就是那把锁。
    """

    def _ts_fail_severity(self):
        path = os.path.join(_ROOT, "web", "e2e", "ui_scout", "core", "findings.ts")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        return source.split("export const FAIL_SEVERITY: Severity = ", 1)[1].split(";", 1)[0].strip('"')

    def test_the_python_default_mirrors_the_ts_constant(self):
        import ui_scout
        parsed = ui_scout.build_parser().parse_args([])
        self.assertEqual(parsed.fail_on, self._ts_fail_severity())

    def test_the_three_actions_are_mutually_exclusive(self):
        """`--check --summary` 从前静默按 summary 走；现在它是个明说的错。"""
        import ui_scout
        with self.assertRaises(SystemExit):
            ui_scout.build_parser().parse_args(["--check", "--summary"])

    def test_the_runner_actually_reads_the_constant(self):
        path = os.path.join(_ROOT, "web", "e2e", "ui_scout.spec.ts")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("severity === FAIL_SEVERITY", source)


class StepEchoTestCase(unittest.TestCase):
    """写回去的每一行都盖上问题的 step——配对靠传输层，不靠模型的记性（§79.2）。"""

    def _answer(self, obs, reply='{"verb":"wait","ms":100}'):
        return pilot.answer(obs, lambda _prompt: _Proc(reply))

    def test_the_step_is_stamped_from_the_question(self):
        self.assertEqual(self._answer({"step": 7})["step"], 7)

    def test_a_step_the_model_made_up_is_overwritten(self):
        answered = self._answer({"step": 7}, '{"verb":"wait","ms":100,"step":1}')
        self.assertEqual(answered["step"], 7)

    def test_give_up_is_stamped_too(self):
        """降级成 give_up 的那一步也要配对得上，否则跑者会连它一起丢掉。"""
        answered = self._answer({"step": 4}, "模型今天不想说 JSON")
        self.assertEqual(answered["verb"], "give_up")
        self.assertEqual(answered["step"], 4)

    def test_an_observation_without_a_step_is_not_faked(self):
        """问题里没有 step 就不许自己编一个——编了就等于把配对闸关掉。"""
        self.assertNotIn("step", self._answer({}))


class PromptTestCase(unittest.TestCase):
    def _obs(self, **over):
        obs = {
            "protocol": 1, "journey": "board_tour", "goal": "看一遍看板",
            "notes": "沙箱里没有 actd", "step": 2, "maxSteps": 10,
            "url": "http://127.0.0.1:1/", "page": "board", "lang": "zh-CN",
            "screenshot": "/tmp/run/board_tour/step-02.png",
            "elements": [
                {"ref": "e1", "role": "button", "name": "捕获", "tag": "button",
                 "enabled": True, "editable": False},
                {"ref": "e2", "role": "textbox", "name": "一句话…", "tag": "textarea",
                 "enabled": True, "editable": True},
                {"ref": "e3", "role": "button", "name": "停用的", "tag": "button",
                 "enabled": False, "editable": False},
            ],
            "text": "提案 4 运行中 4", "lastAction": None, "lastError": None,
        }
        obs.update(over)
        return obs

    def test_untrusted_page_content_goes_through_the_fence(self):
        from act.lib import sanitize
        prompt = pilot.build_prompt(self._obs())
        self.assertIn(sanitize.UNTRUSTED_OPEN, prompt)
        self.assertIn(sanitize.UNTRUSTED_CLOSE, prompt)
        # 页面文本必须落在围栏**里面**
        fenced = prompt.split(sanitize.UNTRUSTED_OPEN, 1)[1].split(sanitize.UNTRUSTED_CLOSE, 1)[0]
        self.assertIn("提案 4 运行中 4", fenced)
        self.assertIn("捕获", fenced)

    def test_goal_and_sandbox_notes_are_outside_the_fence(self):
        from act.lib import sanitize
        prompt = pilot.build_prompt(self._obs())
        head = prompt.split(sanitize.UNTRUSTED_OPEN, 1)[0]
        self.assertIn("看一遍看板", head)
        self.assertIn("沙箱里没有 actd", head)

    def test_screenshot_path_is_handed_over_with_a_read_instruction(self):
        prompt = pilot.build_prompt(self._obs())
        self.assertIn("/tmp/run/board_tour/step-02.png", prompt)
        self.assertIn("先用 Read 打开", prompt)

    def test_no_screenshot_does_not_ask_the_model_to_read_an_empty_path(self):
        """`ZAI_UI_SCOUT_NO_SHOTS=1` 时跑者发的是**空串**不是缺键，兜底不能靠 dict 默认值。"""
        for obs in (self._obs(screenshot=""), self._obs(screenshot=None), {}):
            prompt = pilot.build_prompt(obs)
            self.assertIn("这一步没有截图", prompt)
            self.assertNotIn("先用 Read 打开", prompt)

    def test_disabled_elements_are_not_offered(self):
        lines = pilot.element_lines(self._obs()["elements"])
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("[可输入]" in line for line in lines))
        self.assertFalse(any("停用的" in line for line in lines))

    def test_element_table_is_capped(self):
        many = [{"ref": "e%d" % i, "role": "button", "name": "x", "enabled": True}
                for i in range(pilot.MAX_ELEMENTS + 50)]
        self.assertEqual(len(pilot.element_lines(many)), pilot.MAX_ELEMENTS)

    def test_first_step_says_so_instead_of_printing_none(self):
        self.assertIn("（这是第一步）", pilot.build_prompt(self._obs()))

    def test_settling_is_told_to_the_driver_both_ways(self):
        self.assertIn("落定了", pilot.build_prompt(self._obs(settling=False)))
        self.assertIn("还没有", pilot.build_prompt(self._obs(settling=True)))

    def test_a_missing_element_table_is_not_fatal(self):
        self.assertEqual(pilot.element_lines(None), [])
        self.assertIn("目标：", pilot.build_prompt({}))


class ExtractActionTestCase(unittest.TestCase):
    def test_bare_json(self):
        self.assertEqual(pilot.extract_action('{"verb":"click","ref":"e7"}'),
                         {"verb": "click", "ref": "e7"})

    def test_model_chatter_around_the_json(self):
        reply = '我先看一下截图。\n看起来要点「捕获」。\n{"verb":"click","ref":"e7"}\n'
        self.assertEqual(pilot.extract_action(reply), {"verb": "click", "ref": "e7"})

    def test_the_last_object_wins(self):
        reply = '例如 {"verb":"wait","ms":100}，但我决定 {"verb":"done","summary":"到了"}'
        self.assertEqual(pilot.extract_action(reply), {"verb": "done", "summary": "到了"})

    def test_prose_only(self):
        self.assertIsNone(pilot.extract_action("我觉得应该点第一个按钮。"))

    def test_empty_and_none(self):
        self.assertIsNone(pilot.extract_action(""))
        self.assertIsNone(pilot.extract_action(None))


class _Proc(object):
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


class DegradationTestCase(unittest.TestCase):
    """模型失态的每一种形状都降级成一条 give_up——绝不抛、绝不让一步带崩一趟。"""

    OBS = {"goal": "g", "step": 1, "maxSteps": 5, "elements": [], "text": ""}

    def test_happy_path(self):
        action = pilot.decide(self.OBS, lambda _prompt: _Proc('{"verb":"wait","ms":200}'))
        self.assertEqual(action, {"verb": "wait", "ms": 200})

    def test_non_zero_returncode(self):
        action = pilot.decide(self.OBS, lambda _prompt: _Proc("", 1, "credit balance too low"))
        self.assertEqual(action["verb"], "give_up")
        self.assertIn("credit balance", action["summary"])

    def test_unparseable_reply(self):
        action = pilot.decide(self.OBS, lambda _prompt: _Proc("我不知道该点哪个"))
        self.assertEqual(action["verb"], "give_up")

    def test_runner_that_raises(self):
        def runner(_prompt):
            raise OSError("claude 不在 PATH 上")
        action = pilot.decide(self.OBS, runner)
        self.assertEqual(action["verb"], "give_up")
        self.assertIn("claude 不在 PATH 上", action["summary"])

    def test_timeout_is_an_exception_not_a_crash(self):
        import subprocess

        def runner(_prompt):
            raise subprocess.TimeoutExpired(["claude"], 180)
        self.assertEqual(pilot.decide(self.OBS, runner)["verb"], "give_up")


class PumpTestCase(unittest.TestCase):
    """一行进一行出；跑者发来的坏行也要回一行，否则跑者会一直等。"""

    class _Out(object):
        def __init__(self):
            self.lines = []

        def write(self, text):
            self.lines.append(text)

        def flush(self):
            pass

    def test_one_line_in_one_line_out(self):
        out = self._Out()
        pilot.pump(['{"goal":"g","elements":[],"text":""}\n', "\n"], out,
                   lambda _prompt: _Proc('{"verb":"done","summary":"到了"}'))
        self.assertEqual(len(out.lines), 1)
        self.assertIn('"done"', out.lines[0])
        self.assertTrue(out.lines[0].endswith("\n"))

    def test_a_bad_line_still_gets_an_answer(self):
        out = self._Out()
        pilot.pump(["{不是 JSON\n"], out, lambda _prompt: _Proc("unused"))
        self.assertEqual(len(out.lines), 1)
        self.assertIn("give_up", out.lines[0])

    def test_output_is_ascii_safe_json_with_chinese_kept_readable(self):
        out = self._Out()
        pilot.pump(["{不是 JSON\n"], out, lambda _prompt: _Proc("unused"))
        self.assertIn("合法 JSON", out.lines[0])


class BoundaryTestCase(unittest.TestCase):
    """防腐 #3：模型调用只经 act/llm.py，驾驶员里不许出现第二条边界。"""

    def test_only_tool_handed_to_the_model_is_read(self):
        self.assertEqual(pilot.ALLOWED_TOOLS, "Read")

    def test_module_calls_llm_run_and_nothing_else(self):
        with open(os.path.join(_QA_DIR, "ui_scout_pilot.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("llm.run(", source)
        self.assertNotIn("subprocess.run(", source)
        self.assertNotIn("subprocess.Popen(", source)


if __name__ == "__main__":
    unittest.main()
