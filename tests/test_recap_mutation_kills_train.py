"""§63 / §63.9–§63.12 的 recap 入口里夜报变异体活下来的那几格（act/recap.py）。

判例 tests/test_recap_runner.py / test_recap_revert.py / test_recap_shapes.py 钉住了
这条管线的主干（一场会一份纪要、OPEN/CLOSED、晚到切片、校验重试、两种形状、回退），
但夜间变异（§57）在下面这几类格子上留了一串存活体，全是主干踩不到的**边界、兜底与
预算**：

* **flock 的等待窗**：判例从不制造争用，于是「等多久」「等不等」一个字也没被判。
  这里用注入的时钟真占一次锁：按钮入口必须等满 `LOCK_WAIT_S`（等不到也退 0——
  cron 链上一次「有人在跑」不是错误），cron 轮次必须一秒都不等。
* **进 prompt 的那几段的帽与形**：voice profile 的 4000 字符帽、模型失败时异常里
  只带 stderr 的最后 160 字符、会议窗与通知标题里的真实起止时刻（`or 0.0` 被改成
  `and 0.0` 时它们会悄悄变成 1969 年）。
* **两次调用之间的取舍**：重试解析不出来时**第一次那份正文仍要留下**交人复核
  （`retry or parsed`），放弃的那一版不是阶段稿，可发送长版两次都没解析出来时
  要诚实落地成 generation_failed 而不是崩掉。
* **预算的算术**：每轮 / 每日上限各扣一格、新的一天从 0 起（不预支）、日计数器
  一次加一——判例只判「超了会不会停」，没判「扣多少」。
* **缺省与地板**：`cursor` 缺键 = 0 高水位、`--to-version` 缺省 = 0（= 不回退任何一版）、
  `generate(partial=False)`、`generate_lines(drop_prior=False)`（删一行是 owner 明说的
  答案，不是默认）、`record_shape` 认不出就回出厂形（永不 None）。
* **手改坏的记录不许崩、也不许被伪造**：重复版本号取**最新**那一条、没有版本号的
  记录回退出来是第 1 版、日志里畸形 `--answers` 只留前 120 字符。

注入缝只有本模块已经暴露的那几个：`conn` / `runner` / `cfg` / `now`、`config.STATE_DIR`
（盘）、`notify.notify`（系统通知边界）与 `recap.time`（时钟）。被测单元自己一个都不
mock，真 `claude` 一次都不起。

**五个体判为等价（可达输入上无可观察差异，不强杀）**：
* `_generate_closed` 与 `_closed_outcome` 的 `return False`（`→ return None`）：两个都是
  私名，唯一的消费者是 `_process_sessions` 里的 `elif not _closed_outcome(...)`，
  None 与 False 同为假值。
* `_previous_sections` 里 **zh 那一半**的 `or []`（`→ and []`）：这个 dict 只喂给
  `recap_text.assign_tags`，而它只读 `previous["en"]`（标签按位置从 en 侧派、zh 侧照位
  贴上），`previous["zh"]` 今天没有任何消费者——en 那一半照常被上面那条判例钉住。
* `_attempt` 的 `drop_prior: bool = False` 默认值（`→ True`）：两处调用点
  （:func:`generate_lines` 的首发与重试）都显式传参，这个默认值到不了。
* `_after_retry` 的 `if repairs and not validate_detail(repaired)`（`and → or`）：
  `repairs` 非空 ⟹ 所有 finding 都是 `line_too_long` 且每一行都被剪回帽内并保住了
  标签（`recap_text._trim_line` 的两道后置条件），于是 `validate_detail(repaired)` 必空；
  `repairs` 为空 ⟹ `repaired is best` 且它的 findings 非空（走到这里的前提）。两种情形
  下 `and` 与 `or` 同解。
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
from act.lib import recap_sessions as rs
from act.lib import recap_slack_draft as slack_draft
from act.lib import recap_store as store
from act.lib import recap_text as text

KEY = fx.KEY
MIN = 60.0
CHANNEL = "C0123456789"
# 10 个英文词 × 30 行 = 恰好 MIN_TRANSCRIPT_WORDS 个词
TEN_WORDS = "we agreed the training run moves to new data Monday"

# 第 4 行带**真内容**的一份干净回复——`prior=drop` 的落地才看得见
# （出厂模板那一行本来就是填充值，删与不删长得一模一样）
EN_WITH_PRIOR = ["Decided: the training run moves to the new data mix from Monday",
                 "Split: Ann: the data mix", "Deadline: Monday",
                 "Changed since last plan: the eval baseline moved up", "Open: none"]
ZH_WITH_PRIOR = ["定了：训练从周一起改用新数据配比", "分工：Ann：数据配比", "截止：周一",
                 "较上次变化：评测基线提高了", "待定：无"]


def _fence(doc: dict) -> str:
    return "```json\n" + json.dumps(doc, ensure_ascii=False) + "\n```"


def _lines_reply(en=None, zh=None) -> str:
    return _fence({"en": list(en or EN_WITH_PRIOR), "zh": list(zh or ZH_WITH_PRIOR)})


def _sections_reply(tag: str = "") -> str:
    """一节一条的可发送长版；``tag`` = 模型在条目前缀里报上来的标签（`[D1] …`）。"""
    return _fence({"en": [{"key": "decided", "modality": "decided",
                           "items": [tag + "Ann owns the data mix from Monday"]}],
                   "zh": [{"key": "decided", "modality": "decided",
                           "items": [tag + "数据配比自周一起归 Ann"]}]})


class FakeRunner:
    """llm.run 的 runner 缝：记下 argv 与 kwargs，按队列回话（绝不起真 claude）。"""

    def __init__(self, *replies):
        self.replies = list(replies) or [_lines_reply()]
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, subprocess.CompletedProcess):
            return reply
        return subprocess.CompletedProcess(argv, 0, stdout=reply, stderr="")

    @property
    def prompts(self) -> list:
        return [argv[2] for argv, _kw in self.calls]


class FakeClock:
    """`recap.time` 的替身：`sleep` 不真睡，只推进这只钟并记账。"""

    def __init__(self, start: float = 1000.0):
        self.now = start
        self.sleeps = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class RecapCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-mut-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "state").mkdir()
        mock.patch.object(config, "STATE_DIR", root / "state").start()
        self.addCleanup(mock.patch.stopall)
        self.notified = []
        mock.patch.object(notify, "notify",
                          side_effect=lambda *a, **k: self.notified.append((a, k)) or True).start()
        self.db_path = root / "db.sqlite"
        self.conn = fx.make_db(self.db_path)
        self.addCleanup(self.conn.close)
        self.cfg = config.Config(raw={"recap": {}})
        self.runner = FakeRunner()

    # -- helpers ----------------------------------------------------------- #
    def run_once(self, now, **kw):
        return recap.run_once(now=now, conn=self.conn, runner=self.runner, cfg=self.cfg, **kw)

    def first_run(self, now=fx.T0 - 3600):
        self.assertTrue(self.run_once(now)["first_run"])

    def meeting(self, minutes=20, audio=True, start=fx.T0, text_=fx.SENTENCE):
        fx.add_frames(self.conn, start, minutes)
        if audio:
            fx.add_audio(self.conn, start, minutes, text=text_)

    def settings(self) -> dict:
        return store.settings(self.cfg)

    def log_text(self) -> str:
        path = store.log_path()
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def closed_recap(self, **kw) -> dict:
        """走一轮真的 cron，落一份 CLOSED 的第一版。"""
        self.first_run()
        self.meeting(**kw)
        self.run_once(fx.T0 + 34 * MIN)
        return store.load_recap(KEY)


# --------------------------------------------------------------------------- #
# flock：等多久、退什么码
# --------------------------------------------------------------------------- #
@unittest.skipIf(recap.fcntl is None, "no flock on this platform")
class LockTestCase(RecapCase):
    def test_a_contended_lock_waits_the_whole_window_and_not_one_round_longer(self):
        """争用时等满 `wait_s` 才放弃：截止点是「现在 + wait_s」（不是减），
        而且到点那一刻就停（`>=`），不多睡一轮。"""
        clock = FakeClock()
        holder = recap.Lock(0.0)
        self.assertTrue(holder.__enter__())
        self.addCleanup(holder.__exit__)
        with mock.patch.object(recap, "time", clock):
            with recap.Lock(1.0) as ok:
                self.assertIs(ok, False)
        self.assertEqual(clock.sleeps, [0.5, 0.5])

    def test_leaving_the_lock_hands_it_to_the_next_run(self):
        with recap.Lock(0.0) as first:
            self.assertIs(first, True)
        with recap.Lock(0.0) as second:
            self.assertIs(second, True)

    def test_a_platform_without_flock_still_runs_and_still_exits_cleanly(self):
        """Windows 上没有 flock：锁是个 no-op，但它必须**放行**（永不把整条
        cron 链锁死），退出时也不许因为没有句柄而炸。"""
        with mock.patch.object(recap, "fcntl", None):
            with recap.Lock(5.0) as ok:
                self.assertIs(ok, True)


@unittest.skipIf(recap.fcntl is None, "no flock on this platform")
class LockedEntryPointTestCase(RecapCase):
    def test_a_button_run_waits_for_the_lock_while_a_cron_round_never_does(self):
        """按钮入口（`--generate` / `--slack-draft` / `--revert`）愿意等——用户刚按下的
        那一下不该被一轮定时任务吃掉；cron 轮次一秒都不等，下一轮再来。两种情形都退 0：
        「有人在跑」不是失败（非零会让 cron 链报警）。"""
        clock = FakeClock()
        holder = recap.Lock(0.0)
        self.assertTrue(holder.__enter__())
        self.addCleanup(holder.__exit__)
        with mock.patch.object(recap, "time", clock):
            self.assertEqual(recap.main([]), 0)
            self.assertEqual(clock.sleeps, [])
            self.assertEqual(recap.main(["--generate", KEY]), 0)
        self.assertGreater(len(clock.sleeps), 0)
        self.assertIn("holds the lock", self.log_text())


# --------------------------------------------------------------------------- #
# 进 prompt / 进异常的那几段
# --------------------------------------------------------------------------- #
class PromptMaterialTestCase(RecapCase):
    def test_the_voice_profile_that_reaches_the_prompt_is_capped(self):
        """档案是拼进 prompt 的，所以它必须有帽（防腐 #4）——4000 字符，多一个不进。"""
        (config.STATE_DIR / "voice-profile.md").write_text("a" * 5000, encoding="utf-8")
        self.assertEqual(len(recap.voice_profile_text()), 4000)

    def test_a_failed_model_call_quotes_only_the_tail_of_stderr(self):
        """非零退出抛出来的异常带着**最后 160 个字符**的 stderr（诊断够用、不把一整篇
        转写拖进日志），stdout 空时也照样拿得到 stderr——那正是唯一有话说的那一股。"""
        noise = "".join(str(i % 10) for i in range(400))
        self.runner = FakeRunner(subprocess.CompletedProcess([], 2, stdout="", stderr=noise))
        self.first_run()
        self.meeting()
        rec = store.new_record(rs.Session(start=fx.T0, end=fx.T0 + 1200, frames=40,
                                          audio_rows=30, app="zoom", events=[]), KEY, rs.CLOSED)
        with self.assertRaises(RuntimeError) as caught:
            recap.fill_record(rec, self.conn, self.settings(), self.runner, self.cfg, fx.T0 + 2000)
        message = str(caught.exception)
        self.assertIn("claude exit 2", message)
        self.assertIn(noise[-160:], message)
        self.assertNotIn(noise[-161:], message)

    @unittest.skipUnless(fx.HAS_TZDATA, "no tzdata on this runner")
    def test_the_prompt_states_the_meetings_real_local_window(self):
        """喂给模型的 `when` 是这场会**真正的**本地起止时刻——起点解析不出就回落 0.0
        （1969 年）、终点回落到起点（零长会议），两种都会让模型在错的时间轴上写稿。"""
        self.closed_recap()
        rec = store.load_recap(KEY)
        end_hm = rs.local_dt(rs.parse_ts(rec["end"]), fx.TZ).strftime("%H:%M")
        self.assertNotEqual(end_hm, "12:56")
        self.assertIn("2026-08-31 12:56–%s (%s)" % (end_hm, fx.TZ), self.runner.prompts[0])

    @unittest.skipUnless(fx.HAS_TZDATA, "no tzdata on this runner")
    def test_the_notification_names_the_meetings_real_window(self):
        """通知正文里那一行是 owner 在看板上找这份纪要的唯一线索：真实起止 + app + 时长。"""
        self.closed_recap()
        rec = store.load_recap(KEY)
        end_hm = rs.local_dt(rs.parse_ts(rec["end"]), fx.TZ).strftime("%H:%M")
        body = self.notified[0][0][1]
        self.assertTrue(body.startswith("12:56–%s · zoom · 20 min" % end_hm), body)

    def test_the_model_call_carries_the_recap_timeout(self):
        """一次挂起的 claude 不许把 30 分钟一轮的 cron 链拖住：四分钟硬超时。"""
        self.closed_recap()
        self.assertEqual(self.runner.calls[0][1]["timeout"], 240)


# --------------------------------------------------------------------------- #
# 两次调用之间的取舍
# --------------------------------------------------------------------------- #
class GenerationFallbackTestCase(RecapCase):
    def test_a_retry_that_does_not_parse_keeps_the_first_attempts_text(self):
        """重试连 JSON 都不是的时候，第一次那份**解析得出的**正文仍然落地（需复核）——
        两次里只要有一份正文就绝不丢，人可以照着改（§63.3）。"""
        self.runner = FakeRunner(_lines_reply(en=[EN_WITH_PRIOR[0] + " as Arash said"]
                                              + EN_WITH_PRIOR[1:]), "not json at all")
        rec = self.closed_recap()
        self.assertEqual(rec["quality"], store.QUALITY_NEEDS_REVIEW)
        self.assertIs(store.has_text(rec), True)
        self.assertTrue(rec["en"][0].endswith("as Arash said"))
        self.assertTrue(rec["problems"])

    def test_generate_lines_keeps_the_prior_line_unless_the_owner_asked_to_drop_it(self):
        """`prior=drop` 是 owner 明说的一条答案（§63.11），不是默认：没人说过的时候
        「较上次变化」那一行一个字都不许被改写。"""
        args = {"transcript": "we agreed the data mix moves on Monday " * 40,
                "priors": [], "voice_profile": None, "note": None, "partial": False,
                "shape": text.SHAPE_LINES,
                "meta": {"when": "2026-08-31 12:56–13:16 (UTC)", "app": "zoom", "duration_min": 20}}
        lines, quality, problems, _repairs = recap.generate_lines(args, self.runner, self.cfg)
        self.assertEqual((quality, problems), (store.QUALITY_OK, []))
        self.assertEqual(lines["en"][3], "Changed since last plan: the eval baseline moved up")

    def test_a_meeting_is_given_up_after_exactly_three_failed_rounds(self):
        """崩掉的模型调用跨轮重试，**第三轮**才放弃——放弃的那一版是诚实的
        generation_failed，而且它不是「阶段稿」（没人按过「现在生成」）。"""
        self.runner = FakeRunner(RuntimeError("boom"))
        self.first_run()
        self.meeting()
        for i in range(2):
            self.assertEqual(self.run_once(fx.T0 + (34 + 30 * i) * MIN)["generated"], 0)
            self.assertIsNone(store.load_recap(KEY))
        self.assertEqual(self.run_once(fx.T0 + 200 * MIN)["generated"], 1)
        rec = store.load_recap(KEY)
        self.assertEqual(rec["quality"], store.QUALITY_FAILED)
        self.assertIs(rec["partial"], False)

    def test_history_keeps_the_last_five_versions_and_evicts_the_oldest(self):
        """帽是 5 版：第六次重新生成把第一版挤出去（面板的文案照这条判例说话，§63.9）。"""
        self.closed_recap()
        for n in range(6):
            recap.generate(KEY, now=fx.T0 + (40 + n) * MIN, conn=self.conn,
                           runner=self.runner, cfg=self.cfg)
        rec = store.load_recap(KEY)
        self.assertEqual(rec["version"], 7)
        self.assertEqual([h["version"] for h in rec["history"]], [2, 3, 4, 5, 6])

    def test_a_transcript_of_exactly_the_floor_still_reaches_the_model(self):
        """词数下限是**严格小于**才算太薄：正好到线的那一场照出稿，
        否则一场刚够写的会被静默判成 thin_transcript。"""
        self.first_run()
        fx.add_frames(self.conn, fx.T0, 15)
        fx.add_audio(self.conn, fx.T0, 15, text=TEN_WORDS)
        self.run_once(fx.T0 + 34 * MIN)
        rec = store.load_recap(KEY)
        self.assertEqual(rec["transcript_words"], 300)
        self.assertEqual(rec["quality"], store.QUALITY_OK)
        self.assertEqual(len(self.runner.calls), 1)

    def test_fill_record_fills_in_place_and_hands_back_the_same_record(self):
        self.first_run()
        self.meeting()
        rec = store.new_record(rs.Session(start=fx.T0, end=fx.T0 + 1200, frames=40,
                                          audio_rows=30, app="zoom", events=[]), KEY, rs.CLOSED)
        out = recap.fill_record(rec, self.conn, self.settings(), self.runner, self.cfg, fx.T0 + 2000)
        self.assertIs(out, rec)
        self.assertEqual(rec["version"], 1)


# --------------------------------------------------------------------------- #
# §63.10 / §63.12 形状与标签
# --------------------------------------------------------------------------- #
class ShapeAndTagTestCase(RecapCase):
    def _sections_record(self) -> dict:
        rec = store.new_record(rs.Session(start=fx.T0, end=fx.T0 + 1200, frames=40,
                                          audio_rows=30, app="zoom", events=[]), KEY, rs.CLOSED)
        section = {"key": "decided", "modality": "decided",
                   "items": ["Ann owns the data mix from Monday"], "tags": ["D1"]}
        zh = {"key": "decided", "modality": "decided",
              "items": ["数据配比自周一起归 Ann"], "tags": ["D1"]}
        rec.update({"version": 1, "generated_at": rs.iso_utc(fx.T0 + 1200), "shape": "sections",
                    "quality": store.QUALITY_OK, "sections_en": [section], "sections_zh": [zh],
                    "tag_seq": {"D": 1}})
        store.save_recap(rec)
        return rec

    def test_copy_body_renders_a_sections_recap_that_has_no_stored_copy(self):
        """老记录 / 手改过的记录缺 `copy_*` 时现算一遍**本形状**的正文——永不抛，
        也永不拿另一形的键去渲染（那会让面板显示一份空正文配一颗可用的复制键）。"""
        rec = {"shape": text.SHAPE_SECTIONS, "en": None, "copy_en": None,
               "sections_en": [{"key": "decided", "modality": "decided",
                                "items": ["Ann owns the data mix from Monday"]}]}
        body = recap.copy_body(rec, "en")
        self.assertIsInstance(body, str)
        self.assertIn("Ann owns the data mix from Monday", body)

    def test_an_unknown_default_shape_falls_back_to_the_factory_shape(self):
        """形状的优先级链末端必须是一个**认得的**形（永不 None）：按钮 > 记录上一版 > 配置。"""
        self.assertEqual(recap.record_shape({}, {"default_shape": "junk"}), text.DEFAULT_SHAPE)
        self.assertEqual(recap.record_shape({"shape": text.SHAPE_SECTIONS}, {}), text.SHAPE_SECTIONS)

    def test_a_regenerated_item_keeps_the_tag_it_was_born_with(self):
        """§63.12 的整条命题：同一条承诺跨版还是同一个标签。上一版的带标签条目进
        prompt 的围栏、也进 `assign_tags` 的闸——少了它，模型报回来的 `D1` 会被当成
        外来声明丢掉，这一条于是拿到一个新号，引用就此断掉。"""
        self._sections_record()
        self.runner = FakeRunner(_sections_reply(tag="[D1] "))
        self.first_run()
        self.meeting()
        rec = recap.generate(KEY, now=fx.T0 + 40 * MIN, conn=self.conn,
                             runner=self.runner, cfg=self.cfg)
        self.assertEqual(rec["shape"], text.SHAPE_SECTIONS)
        self.assertEqual(rec["sections_en"][0]["tags"], ["D1"])
        self.assertEqual(rec["tag_seq"], {"D": 1})
        self.assertIn("[D1] Ann owns the data mix from Monday", self.runner.prompts[0])

    def test_a_sendable_recap_that_never_parsed_lands_as_failed_not_a_crash(self):
        """可发送长版两次都没解析出来 = 没有正文可派标签：诚实落地成 generation_failed，
        绝不在这条路上抛（宪法第 11 条；解析失败不许崩 pass）。"""
        self._sections_record()
        self.runner = FakeRunner("nonsense", "still nonsense")
        self.first_run()
        self.meeting()
        rec = recap.generate(KEY, now=fx.T0 + 40 * MIN, conn=self.conn,
                             runner=self.runner, cfg=self.cfg)
        self.assertEqual(rec["quality"], store.QUALITY_FAILED)
        self.assertIsNone(rec["sections_en"])
        self.assertEqual(rec["tag_seq"], {"D": 1})      # 台账不回头


# --------------------------------------------------------------------------- #
# §63.4 Slack 草稿（唯一一条会出门的路）
# --------------------------------------------------------------------------- #
class SlackDraftTestCase(RecapCase):
    def setUp(self):
        super().setUp()
        self.cfg.recap_slack_draft_enabled = True
        self.runner = FakeRunner(json.dumps({"status": slack_draft.STATUS_POSTED,
                                             "channel_link": ""}))

    def _record(self, **kw) -> dict:
        rec = {"key": KEY, "app": "zoom", "en": list(EN_WITH_PRIOR), "zh": None,
               "shape": text.SHAPE_LINES, "sections_en": None, "sections_zh": None,
               "copy_en": text.render(EN_WITH_PRIOR), "copy_zh": None}
        rec.update(kw)
        return rec

    def test_a_chinese_default_falls_back_to_the_english_body(self):
        """那一版缺这门语言时退到另一门——空草稿比一份英文草稿差得多（§63.10）。"""
        self.cfg.recap_default_language = "zh"
        receipt = recap.post_slack_draft(self._record(), CHANNEL, self.settings(),
                                         self.runner, self.cfg, fx.T0)
        self.assertEqual(receipt["status"], slack_draft.STATUS_POSTED)
        self.assertIn(EN_WITH_PRIOR[0], self.runner.prompts[0])

    def test_a_recap_without_text_is_never_drafted_even_with_a_stale_copy(self):
        """「有正文吗」的两道判据同源：`has_text` 说没有的那一份，哪怕记录上还留着
        一段旧的 `copy_en`，也宁可诚实地 failed——绝不把一份过期正文放进人的草稿箱。"""
        rec = self._record(en=None, copy_en="stale text from another version")
        receipt = recap.post_slack_draft(rec, CHANNEL, self.settings(),
                                         self.runner, self.cfg, fx.T0)
        self.assertEqual(receipt["status"], slack_draft.STATUS_FAILED)
        self.assertEqual(self.runner.calls, [])

    def test_the_draft_call_carries_the_draft_timeout(self):
        recap.post_slack_draft(self._record(), CHANNEL, self.settings(),
                               self.runner, self.cfg, fx.T0)
        self.assertEqual(self.runner.calls[0][1]["timeout"], 180)


# --------------------------------------------------------------------------- #
# 一轮的预算与游标
# --------------------------------------------------------------------------- #
class RoundBudgetTestCase(RecapCase):
    def two_meetings(self):
        self.meeting(minutes=12, start=fx.T0)
        self.meeting(minutes=12, start=fx.T0 + 20 * MIN)

    def test_a_cursor_without_its_keys_starts_from_the_zero_high_water_mark(self):
        """手改过 / 老版本的 sessions.json 缺 `cursor` 键时从 0 起读（= 什么都没读过），
        读完空库之后游标必须落回 0——负数会重读，1 会漏掉第一行。"""
        state = {"cursor": {}}
        self.assertEqual(recap._read_new(self.conn, state, rs.Options()), [])
        self.assertEqual(state["cursor"], {"frames": 0, "audio": 0})

    def test_a_fresh_day_gets_exactly_the_daily_budget_and_not_one_more(self):
        """新的一天从 0 计数（不预支）：`max_per_day` 是 1 就只出一份，
        另一场留在缓冲里等下一轮。"""
        self.cfg.raw["recap"].update({"max_per_day": 1, "max_per_run": 2})
        self.first_run()
        self.two_meetings()
        self.assertEqual(self.run_once(fx.T0 + 60 * MIN)["generated"], 1)

    def test_the_daily_budget_is_spent_one_per_recap_inside_one_round(self):
        """一轮之内每出一份扣一格：3 场会、每日 2 份 → 正好 2 份，第 3 场留到明天。"""
        self.cfg.raw["recap"].update({"max_per_day": 2, "max_per_run": 3})
        self.first_run()
        self.two_meetings()
        self.meeting(minutes=12, start=fx.T0 + 40 * MIN)
        self.assertEqual(self.run_once(fx.T0 + 80 * MIN)["generated"], 2)

    def test_the_daily_counter_advances_one_per_generated_recap_across_rounds(self):
        """跨轮的日计数器：一份加一格（不是两格、也不是永远停在 1），
        用满之后当天不再出稿。"""
        self.cfg.raw["recap"].update({"max_per_day": 2, "max_per_run": 1})
        self.first_run()
        self.two_meetings()
        self.meeting(minutes=12, start=fx.T0 + 40 * MIN)
        self.assertEqual(self.run_once(fx.T0 + 80 * MIN)["generated"], 1)
        self.assertEqual(self.run_once(fx.T0 + 110 * MIN)["generated"], 1)
        self.assertEqual(self.run_once(fx.T0 + 140 * MIN)["generated"], 0)
        self.assertEqual(store.load_state()["day"]["count"], 2)

    def test_audio_still_transcribing_from_just_before_the_start_holds_the_close(self):
        """待转写窗从会议开始**往前**一个 gap 算：开场前一分钟那块还没转写完的音频
        也算 pending，会议不许在转写追上之前就被判 CLOSED（§63 的晚到切片之所以少）。"""
        self.first_run()
        self.meeting(minutes=20)
        fx.add_pending_chunk(self.conn, fx.T0 - 60)
        summary = self.run_once(fx.T0 + 34 * MIN)
        self.assertEqual((summary["open"], summary["generated"]), (1, 0))

    def test_one_missing_recap_file_does_not_stop_the_late_slice_round(self):
        """晚到切片轮里一份读不出来的纪要只跳过它自己，后面的照常重新生成
        （`continue`，不是 `break`——一份坏文件不许吃掉整轮）。"""
        self.closed_recap()
        summary = {"regenerated": 0}
        recap._regenerate_late({"meeting:2026-08-31T1200-gone": 1, KEY: 2}, self.conn,
                               self.settings(), self.runner, self.cfg, fx.T0 + 40 * MIN, summary)
        self.assertEqual(summary["regenerated"], 1)
        self.assertEqual(store.load_recap(KEY)["version"], 2)

    def test_a_disabled_round_reports_an_honest_all_zero_summary(self):
        self.cfg.recap_enabled = False
        self.assertEqual(self.run_once(fx.T0),
                         {"first_run": False, "open": 0, "generated": 0, "regenerated": 0,
                          "pruned": 0, "skipped": "disabled"})

    def test_the_round_opens_the_engine_db_itself_when_no_connection_is_injected(self):
        """没有注入连接时这一轮自己去开 `recap.db_path` 那个库——打不开才是 no_db。"""
        self.cfg.raw["recap"]["db_path"] = str(self.db_path)
        summary = recap.run_once(now=fx.T0, runner=self.runner, cfg=self.cfg)
        self.assertIs(summary["first_run"], True)
        self.assertIsNone(summary["skipped"])


# --------------------------------------------------------------------------- #
# 按钮入口：生成 / 回退 / CLI
# --------------------------------------------------------------------------- #
class EntryPointTestCase(RecapCase):
    def _record_with_history(self, entries: list, version: int = 2) -> dict:
        rec = store.new_record(rs.Session(start=fx.T0, end=fx.T0 + 1200, frames=40,
                                          audio_rows=30, app="zoom", events=[]), KEY, rs.CLOSED)
        rec.update({"version": version, "generated_at": rs.iso_utc(fx.T0 + 1200),
                    "quality": store.QUALITY_OK, "en": list(EN_WITH_PRIOR),
                    "zh": list(ZH_WITH_PRIOR), "history": entries})
        store.save_recap(rec)
        return rec

    def _entry(self, version: int, first_line: str) -> dict:
        return {"version": version, "generated_at": rs.iso_utc(fx.T0), "partial": False,
                "quality": store.QUALITY_OK, "zh": list(ZH_WITH_PRIOR),
                "en": [first_line] + EN_WITH_PRIOR[1:]}

    def test_regenerating_a_closed_recap_is_not_a_partial(self):
        """「重新生成」一场已经结束的会出的是完整稿——`partial` 只属于
        「现在生成」按在一场进行中的会上的那一次。"""
        self.closed_recap()
        rec = recap.generate(KEY, now=fx.T0 + 40 * MIN, conn=self.conn,
                             runner=self.runner, cfg=self.cfg)
        self.assertIs(rec["partial"], False)

    def test_a_successful_generate_never_logs_an_unknown_key(self):
        """日志是这条路唯一的回执：认得的 key 不许留下「unknown key」，
        认不出的 key 必须留下（而且返回 None）。"""
        self.closed_recap()
        recap.generate(KEY, now=fx.T0 + 40 * MIN, conn=self.conn, runner=self.runner, cfg=self.cfg)
        self.assertNotIn("unknown key", self.log_text())
        self.assertIsNone(recap.generate("meeting:2026-01-01T0000-none", now=fx.T0,
                                         conn=self.conn, runner=self.runner, cfg=self.cfg))
        self.assertIn("generate: unknown key", self.log_text())

    def test_a_duplicate_stored_version_reverts_to_the_newest_entry(self):
        """同一个版本号在 history 里出现两次（手改过的文件）时回退取**最新**那一条——
        面板说「第 1 版」指的是它最后一次长的样子。"""
        self._record_with_history([self._entry(1, "Decided: the older first version"),
                                   self._entry(1, "Decided: the newer first version")])
        rec = recap.revert(KEY, 1, now=fx.T0 + 3600)
        self.assertEqual(rec["en"][0], "Decided: the newer first version")
        self.assertEqual(rec["reverted_from"], 1)

    def test_a_revert_on_a_record_without_a_version_lands_as_version_one(self):
        """版本号从 1 起（0 = 还没出过稿）：手改坏成 0 的记录回退出来是第 1 版，
        绝不是第 0 版或第 2 版。"""
        self._record_with_history([self._entry(1, "Decided: the stored first version")], version=0)
        rec = recap.revert(KEY, 1, now=fx.T0 + 3600)
        self.assertEqual(rec["version"], 1)
        self.assertEqual(rec["en"][0], "Decided: the stored first version")

    def test_a_revert_without_a_target_version_changes_nothing(self):
        """`--revert KEY` 不带 `--to-version` 的缺省是 0 = **不回退任何一版**：
        退 1、记录一字不动，理由进日志（缺省若是 1，一次手滑就会把正文换掉）。"""
        self._record_with_history([self._entry(1, "Decided: the stored first version")])
        self.assertEqual(recap.main(["--revert", KEY]), 1)
        self.assertEqual(store.load_recap(KEY)["version"], 2)
        self.assertIn("no stored version 0", self.log_text())

    def test_a_malformed_answers_blob_is_ignored_and_logged_truncated(self):
        """畸形的 `--answers` 照没有答案出稿（绝不静默改写这份纪要），
        理由进日志——但只留前 120 个字符（日志有帽，防腐 #4）。"""
        junk = "x" * 400
        self.assertEqual(recap._cli_answers(junk), [])
        log = self.log_text()
        self.assertIn(repr(junk[:120]), log)
        self.assertNotIn(repr(junk[:121]), log)


if __name__ == "__main__":
    unittest.main()
