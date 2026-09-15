"""§63.3 / §63.10 / §63.11 / §63.12 的模板与闸门里夜报变异体活下来的那几格
（`act/lib/recap_text.py`）。

判例 tests/test_recap_shapes.py / test_recap_validate.py / test_recap_length_repair.py /
test_recap_item_tags.py 钉住了这四节的主干，但夜间变异（§57）在这个模块上留了一串
存活体，全落在五类格子里——它们都是「边界那一格」与「手改坏的记录进来时这一步答
什么」，而主干判例喂的一直是形状正确、离帽很远的输入：

* **出厂数字本身就是契约**：可修预算 28 / 12（量的是**删掉**多少字符，不是超出
  多少）、能出稿的最短转写 300 词、owner 更正的 500 字符、一个字母的序号帽 99。
  把它们 ±1 没有一条既有判例会红，但每一条都有下游后果——所以这里从**消费方**问
  「这个数在装机上意味着什么行为」，答案写成字面量。
* **区间的两端是闭的**：一条 240 字符的 item、6 节、24 条、恰好 0.72 的相似度——
  「刚好到帽」是合法的那一侧，判例从来只测过「远远之内」和「远远之外」。
* **闸门一趟报全**：分节校验里每一个坏键都要报出来（重试把问题原样引回模型），
  一条 finding 的 `line` 是它正文里引的那个行号（§63.3 的 add-only 行）。
* **公开谓词答真 bool、公开函数永不抛**：`is_filler_line` / `has_body` 答
  `False` 不答 `None`；`drop_prior` / `assign_tags` 拿到手改坏的记录原样退回。
* **标签是存储侧发的**（§63.12）：模型报的标签 en 为先、zh 补位；计数器上的坏值
  当 0（`true` / `"3"` / 负数都在手改过的文件里出现过）；一条的回挂不受前一条
  影响；相似度不许随条目长度漂移（difflib 的 200 元素 autojunk 启发式）。

注入缝只有模型（FakeRunner）与 fixture 的 screenpipe 库——被测单元自己一处没 mock。

**这些体判为等价（可达输入上无可观察差异，不强杀）**：

* `MIN_BODY_CHARS`（±1）、`body_left = len(out) - len(label)`（`-` → `+`）、
  `body_left < MIN_BODY_CHARS`（`<` → `<=`）：`_trim_line` 只会拿到**超帽**的行
  （`_length_only` 保证其余 finding 一条都没有，`label_mismatch` 会挡下整轮修剪），
  而删掉的字符又封在预算内，于是 `len(out) ≥ max_chars − max_trim` 恒成立——英文
  剩余正文 ≥ 105 字符、中文 ≥ 46 字符，永远够不到 8 这一格。3 万条随机行验证无差异。
* `_trimmed_body` 的 `while len(tokens) > 1`（`>` → `>=`、`1` → `0`、`1` → `2`）：
  三个变体只在「回退到只剩标签那一个 token」的状态上与原式不同，而那个状态下
  `_trim_line` 两边都返回 None（剩余正文吃到标签 / 出去的行仍超帽）。
* `_key_findings` 的 `cursor = -1`（`1` → `0` / `2`）与 `if index < cursor`
  （`<` → `<=`）：`cursor` 只装**已接受**键的下标（非负），键唯一（重复在上一闸
  就 `continue` 了），所以 `index < 0` 与 `index == cursor` 都不可达。
* `_ratio` 的 `if not a or not b`（`or` → `and`）：唯一会让 difflib 给出非 0 分的
  空串情形是「两边都空」（`ratio()` 对两个空串答 1.0），而那一种 `and` 照样拦下——
  其余情形 difflib 本来就答 0.0。
* `_row_tag` 的 `return ""`（`→ return None`）与 `_kept_items` 的 `return []`
  （`→ return None`）：前者四个消费者全部只取真假（`tag or n` / `if tag and …` /
  `_claimed` 的 `or`），后者的唯一调用点 `_kept_pairs` 已经用 `_sections_of` 把
  非 dict 滤掉了，那一行不可达。
* `_best_match` 的 `scored[0][0] - scored[1][0] < TAG_MATCH_MARGIN`（`<` → `<=`）：
  相似度是 `2M/T` 的有理数，条目帽之内 `T ≤ 480`；穷举 `T ≤ 2000` 的全部可达比值
  （608,295 个）没有一对的浮点差**恰好**等于 0.05，两个算子在可达输入上同解。
* `_mint` 的 `floor.get(letter, 0)`（`0` → ±1）：`floor` 由 `_seq_floor` 铺满
  `TAG_LETTERS` 的每一个字母，而 `_mint` 只会被 `tag_letter()` 给出的字母调用——
  缺省值取不到。
* `_zh_tagged` 的 `i < len(out)`（`<` → `<=`）：`out` 与 `_paired` 查的 zh 节表
  都是同一次 `_sections_of(payload["zh"])` 的结果，长度相同；`zh is not None`
  已经蕴含 `i < len(out)`。
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests import recap_fixture as fx

from act import recap
from act.lib import config, notify
from act.lib import recap_store as store
from act.lib import recap_text as rt

MIN = 60.0
META = {"when": "2026-08-31 12:56–13:16", "app": "zoom", "duration_min": 20}
# §63.12：一条长到会撞上 difflib 200 元素启发式的承诺（归一后 205 字符，仍在 240 帽内）
LONG_PREV = ("ann coordinates the evaluation harness rollout alongside quarterly planning reviews, "
             "keeps the migration checklist engineering maintains, and reconciles the onboarding "
             "materials inherited last season with the consolidated release notes")
LONG_CUR = ("ann coordinates the evaluation pipeline handoff alongside monthly planning sessions, "
            "keeps the migration checklist engineering maintains, and merges the onboarding "
            "instructions inherited last season with the consolidated handover notes")


def clean() -> dict:
    """校验干净的五行稿（fixture 的那一份）。"""
    return rt.parse_output(fx.good_output())


def sec(key: str, items, tags=None, modality: str = "decided") -> dict:
    out = {"key": key, "modality": modality, "items": list(items)}
    if tags is not None:
        out["tags"] = list(tags)
    return out


def payload(en: list, zh=None) -> dict:
    return {"en": list(en), "zh": list(zh if zh is not None else en)}


def previous(items, tags) -> dict:
    return {"en": [sec("decided", items, tags=tags)],
            "zh": [sec("decided", ["上一版的那一条"] * len(items), tags=tags)]}


def en_line(label: str, head_chars: int, tail_chars: int) -> str:
    """``label`` 开头、正好 ``head_chars`` 长的一段，后面跟一个 ``tail_chars`` 长的词。

    回退一次词边界正好落在 ``head_chars`` 上——于是「删掉多少字符」是精确可控的。"""
    body = ""
    while len(label) + len(body) < head_chars:
        body += " word"
    head = (label + body)[:head_chars]
    if head.endswith(" "):
        head = head[:-1] + "x"
    return head + " " + "z" * tail_chars


class TranscriptFloorTestCase(unittest.TestCase):
    """能出稿的最短转写：300 词进模型，299 词是 thin（一次模型调用都不发）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-floor-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "state").mkdir()
        mock.patch.object(config, "STATE_DIR", root / "state").start()
        mock.patch.object(notify, "notify", return_value=True).start()
        self.addCleanup(mock.patch.stopall)
        self.conn = fx.make_db(root / "db.sqlite")
        self.addCleanup(self.conn.close)
        self.cfg = config.Config(raw={"recap": {}})
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=fx.good_output(), stderr="")

    def meeting_of(self, words: int) -> dict:
        recap.run_once(now=fx.T0 - 3600, conn=self.conn, runner=self.runner, cfg=self.cfg)
        fx.add_frames(self.conn, fx.T0, 20)
        fx.add_audio(self.conn, fx.T0, 1, text=" ".join(["word"] * words), rows_per_minute=1)
        recap.run_once(now=fx.T0 + 34 * MIN, conn=self.conn, runner=self.runner, cfg=self.cfg)
        return store.load_recap(fx.KEY)

    def test_three_hundred_words_is_enough_to_summarize(self):
        rec = self.meeting_of(300)
        self.assertEqual(rec["transcript_words"], 300)
        self.assertEqual(len(self.calls), 1)                  # 模型被调用了一次
        self.assertNotEqual(rec["quality"], store.QUALITY_THIN)
        self.assertTrue(rt.has_body(rec))

    def test_one_word_short_of_the_floor_never_reaches_the_model(self):
        rec = self.meeting_of(299)
        self.assertEqual((rec["transcript_words"], rec["quality"]), (299, store.QUALITY_THIN))
        self.assertEqual(self.calls, [])                      # 太薄 = 不花那次调用
        self.assertIsNone(rec["en"])


class PromptBlocksTestCase(unittest.TestCase):
    """prompt 里那几块第三方正文（宪法第 5 条：全部进围栏，一块都不许丢）。"""

    def test_a_prior_recap_rides_into_the_prompt(self):
        prior = {"date": "2026-08-27",
                 "en": ["Decided: the mix stays", "Split: not assigned", "Deadline: none set",
                        "Changed since last plan: none recorded", "Open: none"]}
        prompt = rt.build_prompt("the transcript", META, [prior])
        self.assertIn("Prior recap dated 2026-08-27:", prompt)
        for line in prior["en"]:
            self.assertIn(line, prompt)                       # 上一份纪要的正文真的在块里

    def test_the_owner_correction_is_cut_at_five_hundred_characters(self):
        note = "a" * 499 + "Z" + "b" * 80
        prompt = rt.build_prompt("the transcript", META, [], note=note)
        self.assertIn("a" * 499 + "Z", prompt)                # 第 500 个字符还在
        self.assertNotIn("Z" + "b", prompt)                   # 第 501 个字符不在


class SectionsParsingTestCase(unittest.TestCase):
    """两语言全有或全无：一边解析不出 = 整份输出当没解析出来（走重试那条路）。"""

    def good(self) -> dict:
        return {"key": "decided", "modality": "decided", "items": ["the run moves"]}

    def test_an_empty_section_list_is_not_a_parsed_recap(self):
        self.assertIs(rt.sections_wellformed([]), False)
        self.assertIs(rt.sections_wellformed("not a list"), False)
        self.assertIs(rt.sections_wellformed([self.good()]), True)
        self.assertIsNone(rt.parse_sections(json.dumps({"en": [], "zh": [self.good()]})))

    def test_one_broken_language_unparses_the_whole_output(self):
        good = self.good()
        self.assertIsNone(rt.parse_sections(json.dumps({"en": [good], "zh": "nope"})))
        self.assertIsNone(rt.parse_sections(json.dumps({"en": "nope", "zh": [good]})))
        self.assertIsNotNone(rt.parse_sections(json.dumps({"en": [good], "zh": [good]})))


class FindingRowTestCase(unittest.TestCase):
    """一条 finding 的结构行（§63.3 add-only）：`line` 就是它正文里引的那个号。"""

    def test_the_structured_row_points_at_the_line_its_text_quotes(self):
        rec = clean()
        rec["en"][2] = "Deadline: Friday as Ann said"
        found = rt.validate_detail(rec)
        self.assertEqual([f["code"] for f in found], ["reported_speech"])
        self.assertEqual((found[0]["lang"], found[0]["line"]), ("en", 3))
        self.assertIn("line 3", found[0]["text"])             # 结构行与文案引的是同一个号

    def test_every_per_line_code_agrees_with_its_own_text(self):
        rec = clean()
        rec["en"][3] = "Changed: " + "alpha " * 30 + "said"   # 标签错 + 超长 + 转述，同一行
        codes = {f["code"]: f for f in rt.validate_detail(rec) if f["lang"] == "en"}
        self.assertEqual(sorted(codes), ["label_mismatch", "line_too_long", "reported_speech"])
        for code, finding in codes.items():
            with self.subTest(code=code):
                self.assertEqual(finding["line"], 4)
                self.assertIn("line 4", finding["text"])


class SectionGateTestCase(unittest.TestCase):
    """可发送长版的确定性闸（§63.10）：帽的两端 + 一趟把问题报全。"""

    def rows(self, en, zh=None) -> list:
        return rt.validate_sections_detail(payload(en, zh))

    def codes(self, en, zh=None) -> list:
        return [f["code"] for f in self.rows(en, zh) if f["lang"] == "en"]

    def test_every_bad_key_is_reported_in_one_pass(self):
        # 重试把 findings 原样引回模型：漏报一条 = 模型下一轮照犯
        secs = [sec("nonsense", ["a"]), sec("decided", ["b"]), sec("decided", ["c"]),
                sec("also-nonsense", ["d"])]
        texts = [f["text"] for f in self.rows(secs) if f["code"] == "section_key" and f["lang"] == "en"]
        self.assertEqual(len(texts), 3)
        self.assertTrue(any("nonsense" in t and "not one of" in t for t in texts))
        self.assertTrue(any("appears twice" in t for t in texts))
        self.assertTrue(any("also-nonsense" in t for t in texts))

    def test_an_item_exactly_at_the_cap_is_legal(self):
        at_cap = ("commitment " * 25)[:240]
        self.assertEqual(len(at_cap), 240)
        short = [sec("decided", ["短的一条"])]
        self.assertNotIn("item_too_long", self.codes([sec("decided", [at_cap])], short))
        over = self.rows([sec("decided", [at_cap + "x"])], short)
        long_rows = [f for f in over if f["code"] == "item_too_long"]
        self.assertEqual([(f["over"], f["line"], f["limit"]) for f in long_rows], [(1, 1, 240)])

    def test_the_six_keys_all_fit_and_a_seventh_section_does_not(self):
        six = [sec(key, ["one item"]) for key in rt.SECTION_KEYS]
        self.assertNotIn("section_count", self.codes(six))
        seventh = self.rows(six + [sec("open", ["one more"])])
        counts = [f for f in seventh if f["code"] == "section_count" and f["lang"] == "en"]
        self.assertEqual([(f["over"], f["limit"]) for f in counts], [(1, 6)])

    def test_twenty_four_items_all_fit_and_a_twenty_fifth_does_not(self):
        items = ["commitment number %d" % i for i in range(24)]
        self.assertNotIn("item_count", self.codes([sec("decided", items)]))
        over = self.rows([sec("decided", items + ["one too many"])])
        counts = [f for f in over if f["code"] == "item_count" and f["lang"] == "en"]
        self.assertEqual([(f["over"], f["limit"]) for f in counts], [(1, 24)])


class LengthRepairBudgetTestCase(unittest.TestCase):
    """§63.3 追记 2026-09-15：预算量的是**删掉**多少字符，不是超出多少。"""

    def test_the_budget_counts_the_characters_deleted_not_the_overrun(self):
        # 两行超出量一模一样（28）：可修的是删掉 28 个字符那一行；另一行的词边界在
        # 第 139 个字符上，回退一次要删 29 个——那是内容问题，整轮不修，交给人
        repairable, stubborn = en_line("Decided:", 140, 27), en_line("Decided:", 139, 28)
        self.assertEqual((len(repairable), len(stubborn)), (168, 168))
        rec = clean()
        rec["en"][0] = repairable
        fixed, repairs = rt.repair_lengths(rec)
        self.assertEqual(repairs, [{"lang": "en", "line": 1, "over": 28, "removed": 28}])
        self.assertEqual(len(fixed["en"][0]), 140)
        self.assertEqual(rt.validate(fixed), [])
        rec = clean()
        rec["en"][0] = stubborn
        fixed, repairs = rt.repair_lengths(rec)
        self.assertEqual(repairs, [])
        self.assertEqual(fixed["en"], rec["en"])              # 原样退回 → 需复核
        self.assertTrue(rt.validate(fixed))

    def test_the_chinese_budget_is_twelve_characters(self):
        body = "下周五交第一版评测报告，" * 8
        for extra, expected in ((12, [{"lang": "zh", "line": 3, "over": 12, "removed": 12}]), (13, [])):
            with self.subTest(extra=extra):
                rec = clean()
                rec["zh"][2] = ("截止：" + body)[:rt.MAX_CHARS_ZH + extra]
                self.assertEqual(len(rec["zh"][2]), 60 + extra)
                fixed, repairs = rt.repair_lengths(rec)
                self.assertEqual(repairs, expected)
                self.assertEqual(len(fixed["zh"][2]), 60 if expected else 60 + extra)

    def test_a_line_with_no_word_boundary_is_never_repaired_over_its_cap(self):
        # 修剪的出口只有一个：回到帽之内。剪不动就是剪不动——把一行超帽的正文报成
        # 「已修」等于对粘出去的那份撒谎
        rec = clean()
        rec["en"][0] = "Decided:" + "c" * 148
        fixed, repairs = rt.repair_lengths(rec)
        self.assertEqual(repairs, [])
        self.assertEqual(fixed, rec)
        self.assertTrue(rt.validate(fixed))
        self.assertGreater(len(fixed["en"][0]), rt.MAX_CHARS_EN)


class FillerLookupTestCase(unittest.TestCase):
    """「这一部分是空的」是一次查表：行号必须是真行号，标签对不上就不是填充。"""

    def test_the_lookup_only_answers_about_a_real_line_number(self):
        self.assertIs(rt.is_filler_line("Open: none", 4), True)
        for index in (-1, 5, 99, -99):
            with self.subTest(index=index):
                self.assertIs(rt.is_filler_line("Open: none", index), False)

    def test_a_line_that_is_not_the_template_filler_is_not_filler(self):
        self.assertIs(rt.is_filler_line("this line lost its label", 0), False)
        self.assertIs(rt.is_filler_line("Deadline: none set but confirmed next week", 2), False)
        self.assertIs(rt.is_filler_line("Split: not assigned", 1), False)   # 未分配是一条真信息

    def test_has_body_answers_a_real_bool(self):
        self.assertIs(rt.has_body("a recap"), False)
        self.assertIs(rt.has_body(None), False)
        self.assertIs(rt.has_body({}), False)
        self.assertIs(rt.has_body({"en": ["Decided: the run moves"]}), True)


class DropPriorTestCase(unittest.TestCase):
    """§63.11 `prior=drop` 的确定性落地：钉成模板自己规定的空写法，然后整行/整节消失。"""

    def test_each_language_is_pinned_to_its_own_filler(self):
        rec = clean()
        rec["en"][3] = "Changed since last plan: the mix moved to Monday"
        rec["zh"][3] = "较上次变化：配比改到周一"
        out = rt.drop_prior("lines", rec)
        self.assertTrue(rt.is_filler_line(out["en"][3], 3))
        self.assertTrue(rt.is_filler_line(out["zh"][3], 3))
        self.assertNotIn(out["en"][3], rt.render(out["en"]))   # 渲染时整行略掉
        self.assertNotIn(out["zh"][3], rt.render(out["zh"]))
        # 英文那份粘出去的是英文：钉下来的串不许串语言（模板逐字要求的那两个）
        self.assertEqual(out["en"][3], "Changed since last plan: none recorded")
        self.assertEqual(out["zh"][3], "较上次变化：无记录")
        self.assertEqual(rt.validate(out), [])

    def test_a_record_the_gate_would_reject_is_handed_back_untouched(self):
        for lang, value in (("en", None), ("zh", "a string"), ("en", ["only", "three", "lines"])):
            with self.subTest(lang=lang, value=value):
                rec = dict(clean(), **{lang: value})
                self.assertEqual(rt.drop_prior("lines", rec)[lang], value)
        self.assertEqual(rt.drop_prior("lines", "not a record"), "not a record")
        self.assertIsNone(rt.drop_prior("lines", None))

    def test_the_sendable_shape_keeps_a_mangled_record_whole(self):
        mangled = {"en": "not a list", "zh": [sec("decided", ["x"])]}
        self.assertEqual(rt.drop_prior("sections", mangled), mangled)
        # 削到零节 = 一份空正文：宁可不删那一节（§63.10 的最后一款）
        only_changed = {"en": [sec("changed", ["the mix moved"])],
                        "zh": [sec("changed", ["配比改了"])]}
        self.assertEqual(rt.drop_prior("sections", only_changed), only_changed)
        both = {"en": [sec("decided", ["x"]), sec("changed", ["y"])],
                "zh": [sec("decided", ["甲"]), sec("changed", ["乙"])]}
        dropped = rt.drop_prior("sections", both)
        self.assertEqual([s["key"] for s in dropped["en"]], ["decided"])
        self.assertEqual([s["key"] for s in dropped["zh"]], ["decided"])


class TagCounterTestCase(unittest.TestCase):
    """§63.12 计数器：单调、永不复用、用尽就不发标签；坏值当 0。"""

    def test_the_last_number_a_letter_can_issue_is_ninety_nine(self):
        out, seq = rt.assign_tags(payload([sec("decided", ["first one", "second one"])]),
                                  seq={"D": 98})
        self.assertEqual(out["en"][0]["tags"], ["D99", ""])   # 号用尽 = 不发标签
        self.assertEqual(seq, {"D": 99})
        body = rt.render_sections(out["en"])
        self.assertIn("D99. first one", body)
        self.assertIn("2. second one", body)                  # 回落到 §63.10 的连续编号

    def test_a_hand_mangled_counter_never_skips_a_number_that_was_never_issued(self):
        for bad in ({"D": True}, {"D": "3"}, {"D": -5}, {"D": None}, {"D": 2.5}, {"Q": 9}, "not a dict"):
            with self.subTest(bad=bad):
                out, seq = rt.assign_tags(payload([sec("decided", ["a fresh commitment"])]), seq=bad)
                self.assertEqual(out["en"][0]["tags"], ["D1"])
                self.assertEqual(seq, {"D": 1})

    def test_a_counter_that_was_really_issued_is_never_walked_back(self):
        out, seq = rt.assign_tags(payload([sec("decided", ["a fresh commitment"])]), seq={"D": 7})
        self.assertEqual((out["en"][0]["tags"], seq), (["D8"], {"D": 8}))


class TagClaimTestCase(unittest.TestCase):
    """§63.12 模型报上来的标签：en 为先、zh 补位，收不收由存储侧判。"""

    def setUp(self):
        self.previous = previous(["the gpu budget is approved"], ["D1"])
        self.fresh = "the vendor contract is signed"        # 与上一版正文毫不相像

    def test_the_english_side_names_the_tag(self):
        claimed = payload([sec("decided", [self.fresh], tags=["D1"])],
                          [sec("decided", ["合同签了"], tags=[])])
        out, _seq = rt.assign_tags(claimed, previous=self.previous, seq={"D": 1})
        self.assertEqual(out["en"][0]["tags"], ["D1"])
        self.assertEqual(out["zh"][0]["tags"], ["D1"])        # 两语言按位置共享同一份

    def test_the_chinese_side_stands_in_when_the_english_one_says_nothing(self):
        claimed = payload([sec("decided", [self.fresh], tags=[])],
                          [sec("decided", ["合同签了"], tags=["D1"])])
        out, _seq = rt.assign_tags(claimed, previous=self.previous, seq={"D": 1})
        self.assertEqual(out["en"][0]["tags"], ["D1"])

    def test_a_tag_the_previous_version_never_issued_is_dropped_and_re_minted(self):
        claimed = payload([sec("decided", [self.fresh], tags=["S4"])],
                          [sec("decided", ["合同签了"], tags=["S4"])])
        out, seq = rt.assign_tags(claimed, previous=self.previous, seq={"D": 1})
        self.assertEqual((out["en"][0]["tags"], seq), (["D2"], {"D": 2}))


class TagReattachTestCase(unittest.TestCase):
    """§63.12 确定性回挂：模型漏报时按归一正文的相似度挂回去（两道门）。"""

    def test_a_kept_claim_does_not_stop_the_next_item_from_being_re_attached(self):
        prev = previous(["the gpu budget is approved", "the vendor contract is signed"], ["D1", "D2"])
        current = payload([sec("decided", ["the gpu budget is approved",
                                           "the vendor contract was signed"], tags=["D1", ""])],
                          [sec("decided", ["预算通过了", "合同签了"], tags=["D1", ""])])
        out, seq = rt.assign_tags(current, previous=prev, seq={"D": 2})
        self.assertEqual(out["en"][0]["tags"], ["D1", "D2"])  # 第二条的回挂不受第一条影响
        self.assertEqual(seq, {"D": 2})                       # 一个新号都不用发

    def test_a_long_item_is_still_the_same_commitment_after_a_rewrite(self):
        # 相似度不许随条目长度漂移：240 字符的条目会撞上 difflib 的 200 元素启发式
        prev = previous([LONG_PREV], ["D1"])
        current = payload([sec("decided", [LONG_CUR], tags=[])],
                          [sec("decided", ["改写过的那一条"], tags=[])])
        self.assertLessEqual(max(len(LONG_PREV), len(LONG_CUR)), rt.MAX_ITEM_CHARS_EN)
        out, seq = rt.assign_tags(current, previous=prev, seq={"D": 1})
        self.assertEqual((out["en"][0]["tags"], seq), (["D1"], {"D": 1}))

    def test_an_item_with_no_letters_left_does_not_crash_the_matcher(self):
        prev = previous(["the gpu budget is approved"], ["D1"])
        current = payload([sec("decided", ["———", "the gpu budget is approved"], tags=["", ""])],
                          [sec("decided", ["———", "预算通过了"], tags=["", ""])])
        out, seq = rt.assign_tags(current, previous=prev, seq={"D": 1})
        self.assertEqual((out["en"][0]["tags"], seq), (["D2", "D1"], {"D": 2}))

    def test_a_match_exactly_at_the_gate_counts_and_a_tie_never_does(self):
        shared = "x" * 18
        needle = shared + "hijklmn"
        # 分数恰好 0.72 = 门上那一格，收（门是闭的）
        self.assertEqual(rt._best_match(needle, [("D1", shared + "abcdefg")]), "D1")
        # 分不出是哪一条（并列）= 认不出：错挂一条引用比多发一个新标签贵得多
        self.assertEqual(rt._best_match(needle, [("D1", shared + "abcdefg"),
                                                 ("D2", shared + "opqrstu")]), "")


class TagMangledPayloadTestCase(unittest.TestCase):
    """§63.12 派发永不抛：形状不像一份分节稿就原样退回，计数器只归一。"""

    def test_a_recap_whose_languages_disagree_on_the_sections_still_gets_its_tags(self):
        current = payload([sec("decided", ["first"]), sec("split", ["second"])],
                          [sec("decided", ["甲"])])
        out, seq = rt.assign_tags(current)
        self.assertEqual([s["tags"] for s in out["en"]], [["D1"], ["S1"]])
        self.assertEqual(seq, {"D": 1, "S": 1})

    def test_one_mangled_language_leaves_the_whole_payload_alone(self):
        good = [sec("decided", ["the run moves"])]
        for broken in ({"en": good, "zh": "not a list"}, {"en": [], "zh": good},
                       {"en": good, "zh": [{"key": 1, "modality": "decided", "items": []}]}):
            with self.subTest(broken=broken):
                out, seq = rt.assign_tags(broken, seq={"D": 3, "Q": 9, "S": "bad"})
                self.assertEqual(out, broken)                 # 原样退回，一个标签都不派
                self.assertEqual(seq, {"D": 3, "S": 0})       # 发到第几号一个也不少


if __name__ == "__main__":
    unittest.main()
