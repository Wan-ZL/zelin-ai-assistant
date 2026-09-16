"""§63 / §63.5 追记 / §63.9–§63.12 的存储层里夜报变异体活下来的那几格（act/lib/recap_store.py）。

判例 tests/test_recap_store.py 钉住了这一层的主干（设置、inbox argv、投影、分栏、
已忽略短窗），但夜间变异（§57）在这几类格子上留了一串存活体，全是**边界与兜底**：

* **两条窗的端点**：晚到切片窗（48 h）、上一份纪要窗（14 天）、保留窗（90 天）与
  已忽略短窗——判例喂的时间都在窗中央，`<=` 改成 `<`、`86400` 改成 `86399` 照样通过。
  这里每一条窗都在**正好落在端点**与**端点外几十秒**两点上判，两个方向都钉住。
* **上限切多少、计数说多少**：`PROJECTION_CAP` / `FILED_PROJECTION_CAP` / `PRIOR_LIMIT`
  只有在真的超了一行的时候才看得见（§63.5 追记：切掉的必须由 `recap_counts` 如实报出）。
* **配置的下限与 fail-open 缺省**：`max(1, …)` 的地板（0 / 负数永不变成「一轮零份」或
  「立刻删」）、`getattr(cfg, "recap_enabled", True)`（缺键 = 开）与
  `getattr(cfg, "recap_slack_draft_enabled", False)`（缺键 = 关，唯一一条会出门的路默认关）。
* **手改坏的文件不许让看板崩、也不许混进判决**：键不合法的纪要文件、`open[]` 里的
  非 dict、改过名的文件（`unlink(missing_ok=True)` 就是为它写的）、没正文的 CLOSED 行。
* **出生状态与落盘形状**：skeleton 的 `version` / `partial` / `transcript_words` /
  `day.count`（出生即零，不预支配额）与 `_write_json` 的「中文照原样、键有序、两格缩进」
  ——state/ 下的文件是要用眼睛看、用 diff 读的。

注入缝只有 `config.STATE_DIR`（盘）与显式传进去的 `cfg` / `rows` / `now`；被测单元
本身一个都不 mock。

**四个体判为等价（可达输入上无可观察差异，不强杀）**：
* `_dismissed_expired` 的 `return False`（`→ return None`）：私名，唯一消费者是
  :func:`prune` 里的 `... or _dismissed_expired(...)`，None 与 False 同为假值。
* `prune` 里 `int(dismissed_days or 1)` 的那个 `1`（`→ 0` 与 `→ 2`）：外层 `max(1, …)`
  把两者压成同一个数——`dismissed_days` 为真时 `or` 根本不取它，为假时
  `max(1, 0) == max(1, 1) == max(1, 2) == 1`（而且那一轮 `marks` 已经是空表）。
* `_answers_argv` 的 `json.dumps(..., ensure_ascii=False, ...)`（`→ True`）：这一行只在
  `recap_intent.answers_ok` 放行之后才跑，而它要求每条答案匹配 `^[a-z]{1,8}\\d{0,2}=[a-z_]{1,12}$`
  ——全是 ASCII，两种转义产出同一串字节。
"""
import json
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config
from act.lib import recap_sessions as rs
from act.lib import recap_store as store
from act.lib import recap_text as text

DAY = 86400.0
# 窗的长度在测试里写死（不从被测模块读回来）——否则常量被改掉时
# 断言会跟着一起漂，边界就再也钉不住任何东西
LATE_SLICE_S = 48 * 3600.0
PRIOR_WINDOW_S = 14 * DAY
T = 1756670400.0          # 2026-08-31T20:00:00Z, minute-aligned
TZ = "UTC"

EN = ["Decided: the data mix moves Monday", "Split: Ann: the data mix",
      "Deadline: Monday", "Changed since last plan: none recorded", "Open: none"]
ZH = ["定了：数据配比周一改", "分工：Ann：数据配比", "截止：周一",
      "较上次变化：无记录", "待定：无"]


def _key(ts: float, app: str = "zoom") -> str:
    """`meeting:<UTC 分钟>-<app>`——KEY_RE 认得的形，且逐秒可算（与 tzdata 无关）。"""
    stamp = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H%M")
    return "meeting:%s-%s" % (stamp, app)


def _session(start: float, span: float = 1200.0, app: str = "zoom") -> rs.Session:
    return rs.Session(start=start, end=start + span, frames=40, audio_rows=30, app=app, events=[])


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-recap-store-mut-")
        self.addCleanup(self.tmp.cleanup)
        mock.patch.object(config, "STATE_DIR", Path(self.tmp.name) / "state").start()
        self.addCleanup(mock.patch.stopall)
        store.ensure_dirs()

    def save(self, start: float, *, app: str = "zoom", status: str = rs.CLOSED,
             body: bool = True, span: float = 1200.0) -> dict:
        """一份落盘的纪要；``body`` False = 出过会但没正文（`has_text` 说不）。"""
        key = _key(start, app)
        rec = store.new_record(_session(start, span, app), key, status)
        if body:
            rec.update({"version": 1, "generated_at": rs.iso_utc(start + span),
                        "quality": store.QUALITY_OK, "en": list(EN), "zh": list(ZH)})
        store.save_recap(rec)
        return rec

    def keys_on_disk(self) -> list:
        return sorted(rec["key"] for rec in store.list_recaps())


# --------------------------------------------------------------------------- #
# 两条上限：切掉多少，计数如实说（§63.5 追记，issue #301）
# --------------------------------------------------------------------------- #
class ProjectionBudgetTestCase(StoreCase):
    def _rows(self, n: int, *, filed: bool) -> list:
        rows = []
        for i in range(n):
            start = T - i * 3600
            row = {"key": _key(start, "zoom" if filed else "meet"), "start": rs.iso_utc(start),
                   "sent_at": rs.iso_utc(T - i * 60) if filed else None, "dismissed_at": None}
            rows.append(row)
        return rows

    def test_each_lane_gets_its_own_sixty_row_budget_and_the_counts_stay_true(self):
        """活跃与已归档各有 60 行的预算（归档一行永不挤掉活跃一行），被切掉的由
        `recap_counts` 如实报出——界面不许悄悄少东西（宪法第 3 条）。"""
        rows = self._rows(61, filed=False) + self._rows(61, filed=True)
        out = store.projection(rows=rows)
        self.assertEqual(len([r for r in out if not store.filed(r)]), 60)
        self.assertEqual(len([r for r in out if store.filed(r)]), 60)
        self.assertEqual(store.lane_counts(rows), {"active": 61, "archived": 61, "dismissed": 0})


# --------------------------------------------------------------------------- #
# 窗的端点
# --------------------------------------------------------------------------- #
class WindowBoundaryTestCase(StoreCase):
    def test_the_late_slice_window_ends_exactly_forty_eight_hours_after_the_close(self):
        """正好 48 h 前结束的那一场还收晚到切片，晚 30 秒的那一场不再收——窗是闭的
        且长度是 48 h（§63.2：晚到的音频只回到它真正属于的那一场）。"""
        inside = self.save(T - LATE_SLICE_S - 1200.0, app="zoom")
        outside = self.save(T - LATE_SLICE_S - 1230.0, app="meet")
        keys = [key for key, _lo, _hi in store.closed_intervals(T)]
        self.assertIn(inside["key"], keys)
        self.assertNotIn(outside["key"], keys)

    def test_the_prior_window_reaches_exactly_fourteen_days_back(self):
        """正好 14 天前那一份算「上一份」，早 7 秒的那一份不算——`priors_for` 的窗
        是 `[start - 14 天, start)`，两端都钉住。"""
        self.save(T - PRIOR_WINDOW_S, app="zoom")
        self.save(T - PRIOR_WINDOW_S - 7, app="meet")
        priors = store.priors_for(T, TZ)
        self.assertEqual(len(priors), 1)
        self.assertEqual(priors[0]["date"],
                         rs.local_dt(T - PRIOR_WINDOW_S, TZ).strftime("%Y-%m-%d"))

    def test_a_recap_is_never_its_own_prior_and_a_textless_one_never_counts(self):
        """自己不是自己的上一份（窗右端是开的），没出过正文的那一场也不是——
        喂回 prompt 的「较上次变化」锚点必须真有正文可比。"""
        self.save(T, app="zoom")                       # 这一场自己
        self.save(T - 3600, app="meet", body=False)    # 开过会，没出稿
        self.assertEqual(store.priors_for(T, TZ), [])

    def test_at_most_three_priors_ride_into_the_prompt_newest_first(self):
        for n in (1, 2, 3, 4):
            self.save(T - n * DAY, app="zoom")
        priors = store.priors_for(T, TZ)
        self.assertEqual(len(priors), 3)
        self.assertEqual([p["date"] for p in priors], sorted((p["date"] for p in priors), reverse=True))


# --------------------------------------------------------------------------- #
# 保留窗与已忽略短窗（§63.3 追记 2026-09-15，issue #301）
# --------------------------------------------------------------------------- #
class PruneTestCase(StoreCase):
    def _mark(self, key: str, dismissed_at: float) -> None:
        marks = store.load_marks()
        marks[key] = {"dismissed_at": rs.iso_utc(dismissed_at)}
        store.marks_path().write_text(json.dumps(marks), encoding="utf-8")

    def test_the_retention_backstop_keeps_the_meeting_that_sits_on_the_boundary(self):
        """保留窗按会议 start 算，正好 90 天那一场还在，早 50 秒的那一场删掉。"""
        kept = self.save(T - 90 * DAY, app="zoom")
        self.save(T - 90 * DAY - 50, app="meet")
        self.assertEqual(store.prune(T, 90), 1)
        self.assertEqual(self.keys_on_disk(), [kept["key"]])

    def test_the_dismissed_window_counts_from_the_dismissal_to_the_second(self):
        """已忽略的短窗从「按下忽略」那一刻算：正好 14 天前忽略的删掉，
        晚 10 秒忽略的还在（marks 由 server 写，这里只读）。"""
        gone = self.save(T - 3600, app="zoom")
        kept = self.save(T - 7200, app="meet")
        self._mark(gone["key"], T - 14 * DAY)
        self._mark(kept["key"], T - 14 * DAY + 10)
        self.assertEqual(store.prune(T, 90, 14), 1)
        self.assertEqual(self.keys_on_disk(), [kept["key"]])

    def test_a_one_day_window_deletes_exactly_one_day_after_the_dismissal(self):
        gone = self.save(T - 3600, app="zoom")
        self._mark(gone["key"], T - DAY)
        self.assertEqual(store.prune(T, 90, 1), 1)
        self.assertEqual(self.keys_on_disk(), [])

    def test_a_non_positive_dismissed_window_never_becomes_delete_now(self):
        """0 / 负数的短窗不许退化成「刚忽略就删」——地板是一天（多删一份纪要比
        留一份贵得多，§63.3 追记的 fail-safe 方向）。"""
        rec = self.save(T - 3600, app="zoom")
        self._mark(rec["key"], T - 100)
        self.assertEqual(store.prune(T, 90, -1), 0)
        self.assertEqual(self.keys_on_disk(), [rec["key"]])

    def test_a_hand_renamed_recap_file_never_crashes_the_round(self):
        """文件名与里面的 key 对不上（手改过 / 搬过）时，prune 照样把旁边那份
        到期的删掉，不抛 FileNotFoundError（宪法第 11 条）。"""
        stray = {"key": _key(T - 91 * DAY, "meet"), "start": rs.iso_utc(T - 91 * DAY),
                 "end": rs.iso_utc(T - 91 * DAY + 1200), "status": rs.CLOSED}
        (store.recaps_dir() / "meeting_renamed.json").write_text(json.dumps(stray), encoding="utf-8")
        self.save(T - 92 * DAY, app="zoom")
        self.assertEqual(store.prune(T, 90), 2)
        self.assertEqual(self.keys_on_disk(), [stray["key"]])   # 改过名的那份删不掉，但也没崩


# --------------------------------------------------------------------------- #
# 设置：地板与 fail-open / fail-closed 的缺省
# --------------------------------------------------------------------------- #
class SettingsTestCase(StoreCase):
    def test_a_config_object_without_the_recap_keys_is_on_with_the_draft_off(self):
        """老配置 / 鸭子类型的 cfg 缺这两个键时：纪要默认**开**（少一份纪要是损失），
        Slack 草稿默认**关**（那是唯一一条会出门的路，§63.4 owner 决策）。"""
        st = store.settings(types.SimpleNamespace(raw={}))
        self.assertIs(st["enabled"], True)
        self.assertIs(st["slack_draft_enabled"], False)
        self.assertEqual(st["default_shape"], text.DEFAULT_SHAPE)

    def test_every_cap_has_a_floor_of_one(self):
        """0 / 坏值配下来时每个上限的地板都是 1——「一轮零份」「保留零天」都是
        把功能静默关掉，配错一个字不该有这个后果。"""
        cfg = config.Config(raw={"recap": {"max_per_run": 0, "max_per_day": 0,
                                           "retention_days": 0, "dismissed_retention_days": 0}})
        st = store.settings(cfg)
        self.assertEqual((st["max_per_run"], st["max_per_day"]), (1, 1))
        self.assertEqual((st["retention_days"], st["dismissed_retention_days"]), (1, 1))


# --------------------------------------------------------------------------- #
# 出生状态与落盘形状
# --------------------------------------------------------------------------- #
class SkeletonTestCase(StoreCase):
    def test_a_fresh_state_starts_at_schema_one_with_an_unspent_day(self):
        st = store.new_state({"frames": 7, "audio": 9}, "2026-08-31T20:00:00Z")
        self.assertEqual(st["schema"], 1)
        self.assertEqual(st["day"], {"date": "", "count": 0})
        self.assertEqual((st["events"], st["open"], st["failures"]), ([], [], {}))

    def test_a_fresh_record_is_born_with_no_version_no_text_and_no_words(self):
        """出生的那一刻：还没出过稿（version 0）、不是阶段稿、零词——
        而且一个 recipient / channel / id / tier 都没有（它是笔记不是卡片，§0 第 4 条）。"""
        rec = store.new_record(_session(T), _key(T), rs.CLOSED)
        self.assertEqual(rec["version"], 0)
        self.assertIs(rec["partial"], False)
        self.assertEqual(rec["transcript_words"], 0)
        self.assertIs(store.has_text(rec), False)
        self.assertEqual(rec["tag_seq"], {})
        for absent in ("id", "status_machine", "recipient", "channel", "tier"):
            self.assertNotIn(absent, rec)

    def test_state_json_is_written_hand_readable_and_diff_stable(self):
        """state/ 下的文件是要用眼睛看、用 diff 读的：中文照原样落盘（不转 \\uXXXX）、
        键有序（同一份内容永远同一串字节）、两格缩进、末尾恰好一个换行。"""
        store.save_state({"zz": 1, "aa": {"note": "会议纪要"}})
        raw = store.sessions_path().read_text(encoding="utf-8")
        self.assertIn("会议纪要", raw)
        self.assertLess(raw.index('"aa"'), raw.index('"zz"'))
        self.assertIn('\n  "aa": {', raw)
        self.assertTrue(raw.endswith("}\n"))
        self.assertFalse(raw.endswith("}\n\n"))


# --------------------------------------------------------------------------- #
# 手改坏的输入：跳过，不崩、也不混进判决
# --------------------------------------------------------------------------- #
class MangledInputTestCase(StoreCase):
    def test_a_file_whose_key_is_not_a_recap_key_is_skipped(self):
        good = self.save(T)
        (store.recaps_dir() / "meeting_bogus.json").write_text(
            json.dumps({"key": "nope", "start": rs.iso_utc(T)}), encoding="utf-8")
        self.assertEqual(self.keys_on_disk(), [good["key"]])

    def test_open_rows_drops_every_entry_that_is_not_a_well_formed_row(self):
        """sessions.json 的 `open[]` 里非 dict / key 不合法的条目一律丢掉——
        手改过的文件不许让看板崩，也不许把一条假行送上 wire。"""
        rows = store.open_rows({"open": ["junk", {"key": "nope"}, 7, {"key": _key(T)}]})
        self.assertEqual([row["key"] for row in rows], [_key(T)])

    def test_has_lines_only_says_yes_to_a_non_empty_list(self):
        """「有五行正文吗」的唯一判据：非空 list。空表不算，一整段字符串也不算
        （§63.9：面板列出来的每一版都必须真能回退）。"""
        self.assertIs(store.has_lines([]), False)
        self.assertIs(store.has_lines("Decided: x"), False)
        self.assertIs(store.has_lines(None), False)
        self.assertIs(store.has_lines(list(EN)), True)


# --------------------------------------------------------------------------- #
# §63.11 的 `prior` 问题只在真有上一份时才问（`_has_prior` 与 priors_for 同源）
# --------------------------------------------------------------------------- #
class PriorQuestionTestCase(StoreCase):
    def _questions(self) -> list:
        row = next(row for row in store.all_rows() if row["key"] == _key(T))
        return [q["id"] for q in row["questions"]]

    def test_a_prior_exactly_fourteen_days_back_makes_the_question_appear(self):
        self.save(T)
        self.save(T - PRIOR_WINDOW_S, app="meet")
        self.assertIn("prior", self._questions())

    def test_a_prior_just_outside_the_window_does_not(self):
        self.save(T)
        self.save(T - PRIOR_WINDOW_S - 7, app="meet")
        self.assertNotIn("prior", self._questions())

    def test_a_textless_closed_recap_is_not_a_prior(self):
        """没出过正文的那一场不给「较上次变化」当锚点——判据与 :func:`priors_for`
        逐字同源，否则面板会问一个答了也没东西可比的问题。"""
        self.save(T)
        self.save(T - DAY, app="meet", body=False)
        self.assertNotIn("prior", self._questions())


# --------------------------------------------------------------------------- #
# inbox 特殊形 → argv（边界与闭表）
# --------------------------------------------------------------------------- #
class InboxArgvTestCase(StoreCase):
    def _argv(self, **decision):
        base = {"action": "recap_generate", "meeting_key": _key(T)}
        base.update(decision)
        return store.inbox_argv(base)

    def test_a_note_of_exactly_five_hundred_characters_still_goes_through(self):
        """帽是 500 字符**含**——正好 500 的备注照发，501 才是畸形（整条 noop）。"""
        self.assertEqual(self._argv(note="x" * 500), ["--generate", _key(T), "--note", "x" * 500])
        self.assertIsNone(self._argv(note="x" * 501))

    def test_the_first_version_is_a_revertible_target_and_zero_is_not(self):
        """`recap_revert` 的 version 必须是 ≥ 1 的真整数：1 是合法目标（第一版），
        0 / 负数 / bool 都是畸形（wire 上 `true` 真出现过）。"""
        self.assertEqual(store.inbox_argv({"action": "recap_revert", "meeting_key": _key(T),
                                           "version": 1}),
                         ["--revert", _key(T), "--to-version", "1"])
        for bad in (0, -1, True, "1"):
            self.assertIsNone(store.inbox_argv({"action": "recap_revert", "meeting_key": _key(T),
                                                "version": bad}))


if __name__ == "__main__":
    unittest.main()
