"""§2 投影里夜报变异体活下来的那些格（外加 §5 / §7 / §37 / §38 / §40 / §44.6 /
§48.4 / §65 / §71 / §76.2 挂在同一张投影面上的条文）。

`act/lib/dashboard.py` 首轮 400 体只有 26% 杀伤——真正的原因是靶区映射
（qa/mutation_targets.toml）里一个 dashboard 判例都没有：列的全是顺带 import
它的邻居测试，§2 的 lane/字段语义只被 `test_dashboard_golden_projection.py`
的字节 golden 盖着。十个主干判例补进映射之后仍有一批体活着，全落在下面这
六类「golden 照不到」的格子里——它们都不是「多一行少一行」，而是**会改变卡
面含义**的判断：

* **上限与端点**：`_TINFO_CACHE_MAX`（512 条，防腐 #4：热路径缓存必须有帽）、
  `COMPLETED_CAP` / `ARCHIVED_CAP`（§2 / §5：出机列表 50 条封顶而 counts 仍报
  真实总数）、`_NOTES_TEXT_CAP`（§38：2000 字尾切）、`_transcript_sig` 的
  8 字符短 id 闸。golden 只有一张卡，一条都碰不到帽。
* **排序里「没有时间戳」与「时间戳是 0」的区别**：`merge_suggestions` 与
  completed 两处 `key=... or 0`。缺 `requested_at` / `accepted_at` 的行绝不许
  因为回落值而**插到**有时间戳的行前面（平局按原序）。
* **判断的端点**：`_decision_due`（§76.2 今天到期 = 已到点）、
  `_mention_escalated`（§76.2 阈值 0 = 关、`>=` 是「够了」不是「超了」）、
  `_cost_view` 的 `>=` 阈值（§40 正好等于门槛要显示）、`_epoch` 的
  「带时区的时间戳按它自己的时区读」。
* **缺省 = 安全侧**：`getattr` 的那几个回落——缺 `user_titled` 的对象绝不许
  被报成「owner 亲手命名」（§37 会钉住标题、拦住 LLM 改名）、缺
  `silent_merge_count` 绝不许凭空报一个并入数、缺 `fold_receipt_notices` 的
  老 cfg 照常发回执（§44.6 fail-open）、缺 `approval_mention_escalation` 绝不
  自己开始催人（§3.6 anti-nag）。
* **降级路径答的是形状，不是 None**：`_self_improve_view` / `_live_config` /
  `_radar_health_data` / `_radar_rounds_data` / `_secret_file_started` /
  `_source_signals` / `_dir_is_nonempty`——宪法第 11 条，坏文件不许崩 pass，
  更不许把「答不上来」冒充成一个能被 `or` 吞掉的假值。
* **出机的字节**：`write_dashboard` 的 `parents=True` / `ensure_ascii=False` /
  `indent=2`，以及 roster 子进程的 `capture_output` + 30 秒超时（§55：一个挂住
  的 claude 不许拖垮 ~10 s 的 pass，它的 stdout 也不许漏进 actd 的终端）。

注入缝只有本模块**本来就朝外的边界**：`subprocess.run`、`config.load_config`、
`radar_health` / `radar_rounds` / `fold_receipts` / `self_improve` / `secrets`
这几个协作模块的读函数、`_today()` 时钟、`HOME`。被测单元自己（`_transcript_
info_cached`、`_notes_text`、`_cap_completed`、lane 投影…）一律真跑。

**七个体判为等价（可达输入上无可观察差异，不强杀）**：

* `_slept` 的 `_int_or(..., 0)`（`0 → -1`）：外面还套着 `max(0, …)`，坏值走
  哪个缺省最后都是 0。
* `_repeated` 的 `_int_or(..., 1)`（`1 → 0`）：后面跟着 `or 1`，`0 or 1` 与
  `1 or 1` 同解。
* `_mention_escalated` 的两个缺省（`getattr(..., 0)` 与 `_int_or(..., 0)`，
  都是 `0 → -1`）：下一行 `threshold > 0`，非正的缺省一律等于「关」。
* `_notes_text` 的 `if nl >= 0`（`0 → -1`）：`nl == -1`（整段没换行）时
  `clipped[-1 + 1:]` 就是 `clipped[0:]`，与不切同解。
* 两处排序回落 `... or 0` 的 `0 → -1`（`_merge_suggestions` 的 `requested_at`、
  `_cap_completed` 的 `accepted_at`）：`or` 把「缺键」与「epoch 0」一并折成同一个
  回落值，因此回落值只在**负 epoch**（1970 年之前的时间戳）面前才看得出差别——
  registry 落的 ISO 时间戳不可能是负的，而且真要去钉那个差别，钉住的将是「1969 年
  的卡排在无时间戳的卡之后」，正好与函数自己写的「缺时间戳沉底」相反。回落值抬高
  的那一侧（`0 → 1`）是真会插队的，已由上面那条「最老的请求也排在没有请求之前」
  钉死。
"""
import datetime as _dt
import json
import os
import re
import types
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.scratch_testkit import scratch_dir

from act.lib import (config, dashboard, fold_receipts, radar_health, radar_rounds,
                     secrets, self_improve, transcripts)
from act.lib.registry import Requirement

_NOW = _dt.datetime(2026, 9, 15, 12, 0, tzinfo=_dt.timezone.utc)


def _req(**fields) -> Requirement:
    base = {"id": "R-700", "title": "一张卡", "status": "card_sent"}
    base.update(fields)
    return Requirement.from_dict(base)


def _tmpdir(case, prefix: str) -> Path:
    return Path(scratch_dir(case, prefix=prefix))


class SandboxHomeMixin:
    """`~/.claude/projects` 只准落在沙箱里（transcript 签名会 glob 它）。"""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"HOME": str(_tmpdir(self, "dash-mut-home-"))})
        patcher.start()
        self.addCleanup(patcher.stop)


# --------------------------------------------------------------------------- #
# 上限与端点
# --------------------------------------------------------------------------- #
class TranscriptCacheIsBoundedTestCase(SandboxHomeMixin, unittest.TestCase):
    """热路径 memo 必须有帽（防腐 #4）——帽子是 512 条，且是「到了就清」。"""

    def setUp(self):
        super().setUp()
        dashboard._TINFO_CACHE.clear()
        self.addCleanup(dashboard._TINFO_CACHE.clear)
        patcher = mock.patch.object(transcripts, "transcript_info",
                                    return_value=("head", "/tmp/cwd"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fill(self, n: int):
        for i in range(n):
            dashboard._TINFO_CACHE["filler-%04d" % i] = ((), None)

    def test_the_511th_neighbour_does_not_trigger_a_sweep(self):
        self._fill(511)
        dashboard._transcript_info_cached("cafef00d-1111-4000-8000-aaaabbbbcccc")
        self.assertEqual(len(dashboard._TINFO_CACHE), 512)

    def test_the_512th_entry_sweeps_the_whole_cache(self):
        self._fill(512)
        dashboard._transcript_info_cached("cafef00d-1111-4000-8000-aaaabbbbcccc")
        # 清空后只剩新写进去的那一条——帽子是硬的，不是「差不多」。
        self.assertEqual(len(dashboard._TINFO_CACHE), 1)

    def test_a_short_session_id_is_never_signed(self):
        """7 个字符的短 id 会 glob 到整个 projects 目录——签不出来就别签，
        调用方回落到**不缓存**的真查（宁可慢，不给一个错的答案）。"""
        self.assertIsNone(dashboard._transcript_sig("abcdefg"))
        self.assertIsNone(dashboard._transcript_sig("abcdefg-1111-4000-8000-x"))
        # 8 个字符起才签得出来（沙箱 HOME 里没有 transcript → 空签名，不是 None）
        self.assertEqual(dashboard._transcript_sig("abcdefgh"), ())


class CompletedAndArchivedCapsTestCase(unittest.TestCase):
    """§2 / §5：出机的两条长列表 50 条封顶，counts 仍然报真实总数。"""

    CAP = 50
    TOTAL = 51

    @classmethod
    def setUpClass(cls):
        cfg = config.Config()
        delivered = [_req(id="R-%03d" % i, status="delivered",
                          execution={"accepted_at": 1_700_000_000 + i})
                     for i in range(cls.TOTAL)]
        archived = [_req(id="A-%03d" % i, status="archived",
                         archived_at="2026-09-%02d" % (i % 28 + 1))
                    for i in range(cls.TOTAL)]
        cls.dash = dashboard.build_dashboard(reqs=delivered, agents=[], cfg=cfg,
                                             archived=archived)

    def test_completed_is_capped_but_the_count_is_honest(self):
        self.assertEqual(len(self.dash["completed"]), self.CAP)
        self.assertEqual(self.dash["counts"]["completed"], self.TOTAL)

    def test_archived_is_capped_but_the_count_is_honest(self):
        self.assertEqual(len(self.dash["archived"]), self.CAP)
        self.assertEqual(self.dash["counts"]["archived"], self.TOTAL)


class NotesClipIsTailAlignedTestCase(unittest.TestCase):
    """§38：备注按 2000 字**尾切**，切口对齐行首，并诚实地说「更早的省略了」。"""

    def test_exactly_at_the_cap_nothing_is_clipped(self):
        notes = "笔" * 2000
        req = _req(notes=notes)
        self.assertEqual(dashboard._notes_text(req), notes)
        self.assertNotIn(dashboard._NOTES_CLIP_MARKER, dashboard._notes_text(req))

    def test_one_char_over_the_cap_clips_and_says_so(self):
        out = dashboard._notes_text(_req(notes="头" + "笔" * 2000))
        self.assertTrue(out.startswith(dashboard._NOTES_CLIP_MARKER))
        self.assertTrue(out.endswith("笔" * 100))

    def test_the_clip_snaps_forward_past_a_leading_newline(self):
        """切口正好落在换行符上：那半行（这里是空的）要丢掉，不许留一个空行——
        Swift 的 FoldNote.parse 只能看见完整的行。"""
        out = dashboard._notes_text(_req(notes="早" * 100 + "\n" + "新" * 1999))
        self.assertEqual(out, dashboard._NOTES_CLIP_MARKER + "\n" + "新" * 1999)

    def test_a_giant_single_line_survives_whole(self):
        out = dashboard._notes_text(_req(notes="单" * 2500))
        self.assertEqual(out, dashboard._NOTES_CLIP_MARKER + "\n" + "单" * 2000)

    def test_no_notes_is_no_key(self):
        self.assertIsNone(dashboard._notes_text(_req(notes="   ")))


# --------------------------------------------------------------------------- #
# 排序：「没有时间戳」不许插队到「时间戳是 0」前面
# --------------------------------------------------------------------------- #
class MissingTimestampsSinkTestCase(unittest.TestCase):
    """两处 `key=... or 0` 的回落值必须把「没有时间戳」压到队尾——**连想象得到
    最老的那一个时间戳都排在它前面**。回落值一旦抬高，缺时间戳的行就会插队到
    真有请求时间的行前面（合并建议的「最新请求在最上」与已验收的「最近验收在
    最上」同时失真）。"""

    def _merge_dir(self, *stems_and_stamps) -> Path:
        d = _tmpdir(self, "dash-mut-merge-")
        for stem, stamp in stems_and_stamps:
            body = {"status": "done", "ids": ["R-1", "R-2"]}
            if stamp is not None:
                body["requested_at"] = stamp
            (d / ("%s.json" % stem)).write_text(json.dumps(body), encoding="utf-8")
        return d

    def test_the_oldest_conceivable_request_still_outranks_no_request(self):
        # a.json 在文件名序里排在前面，但它没有 requested_at——即便 b 的请求时间
        # 只有 epoch 1 秒，b 也得排在前面。
        rows = dashboard._merge_suggestions(
            self._merge_dir(("a", None), ("b", "1970-01-01T00:00:01Z")))
        self.assertEqual([r["id"] for r in rows], ["b", "a"])
        self.assertEqual([r["requested_at"] for r in rows], [1, None])

    def test_newest_request_first(self):
        rows = dashboard._merge_suggestions(
            self._merge_dir(("a", "2026-09-01T00:00:00Z"),
                            ("b", "2026-09-15T00:00:00Z")))
        self.assertEqual([r["id"] for r in rows], ["b", "a"])

    def test_completed_rows_without_accepted_at_do_not_jump_the_queue(self):
        rows = [{"id": "a"}, {"id": "b", "accepted_at": 1}]
        capped, total = dashboard._cap_completed(rows)
        self.assertEqual([r["id"] for r in capped], ["b", "a"])
        self.assertEqual(total, 2)

    def test_a_newer_acceptance_still_wins(self):
        rows = [{"id": "old", "accepted_at": 1}, {"id": "new", "accepted_at": 9}]
        capped, _ = dashboard._cap_completed(rows)
        self.assertEqual([r["id"] for r in capped], ["new", "old"])


# --------------------------------------------------------------------------- #
# 判断的端点
# --------------------------------------------------------------------------- #
class DecisionDueEndpointTestCase(unittest.TestCase):
    """§76.2：「今天截止」就是已到点——不是明天才算，也不是昨天才算。"""

    def setUp(self):
        patcher = mock.patch.object(dashboard, "_today",
                                    return_value=_dt.date(2026, 9, 15))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_today_is_already_due(self):
        self.assertIs(dashboard._decision_due(_req(deadline="2026-09-15")), True)

    def test_yesterday_is_due_and_tomorrow_is_not(self):
        self.assertIs(dashboard._decision_due(_req(deadline="2026-09-14")), True)
        self.assertIs(dashboard._decision_due(_req(deadline="2026-09-16")), False)

    def test_no_deadline_never_nags(self):
        self.assertIs(dashboard._decision_due(_req()), False)
        self.assertIs(dashboard._decision_due(_req(deadline="下周三")), False)


class MentionEscalationThresholdTestCase(unittest.TestCase):
    """§76.2：阈值是「够了」不是「超了」；0 / 缺键 / 坏值一律 = 关（§3.6 anti-nag）。"""

    def _escalated(self, threshold, repeated):
        cfg = config.Config()
        cfg.approval_mention_escalation = threshold
        req = _req()
        req.repeated_mentions = repeated
        return dashboard._mention_escalated(req, cfg)

    def test_reaching_the_threshold_counts(self):
        self.assertIs(self._escalated(3, 3), True)
        self.assertIs(self._escalated(3, 2), False)

    def test_a_threshold_of_one_fires_on_the_first_restatement(self):
        self.assertIs(self._escalated(1, 5), True)

    def test_zero_means_off_no_matter_how_loud_the_card_is(self):
        self.assertIs(self._escalated(0, 99), False)

    def test_a_cfg_without_the_key_never_starts_nagging(self):
        """老配置 / 鸭子类型注入：缺键 = 关，不是「1 次就催」。"""
        req = _req()
        req.repeated_mentions = 9
        self.assertIs(dashboard._mention_escalated(req, types.SimpleNamespace()), False)

    def test_a_corrupt_threshold_is_off_too(self):
        self.assertIs(self._escalated("abc", 9), False)


class CostThresholdTestCase(unittest.TestCase):
    """§40：正好等于门槛的估算要显示徽章（`>=`，不是 `>`）。"""

    def test_exactly_at_the_threshold_shows(self):
        cfg = config.Config()
        cfg.show_cost_above_usd = 5.0
        cost, show, state = dashboard._cost_view(_req(cost_estimate_usd=5.0), cfg)
        self.assertEqual((cost, show, state), (5.0, True, "estimated"))

    def test_below_the_threshold_still_tells_the_money_story(self):
        cfg = config.Config()
        cfg.show_cost_above_usd = 5.0
        self.assertEqual(dashboard._cost_view(_req(cost_estimate_usd=4.99), cfg),
                         (4.99, False, "estimated"))


class EpochReadsTheStampsOwnZoneTestCase(unittest.TestCase):
    """§2：ISO → epoch int。带时区的时间戳按**它自己的**时区读，不许被当成 UTC
    的同一面墙钟；不带时区的才补 UTC。"""

    def test_an_offset_is_honoured(self):
        aware = dashboard._epoch("2026-01-01T00:00:00+09:00")
        self.assertEqual(
            aware,
            int(_dt.datetime(2025, 12, 31, 15, 0, tzinfo=_dt.timezone.utc).timestamp()))
        self.assertNotEqual(aware, dashboard._epoch("2026-01-01T00:00:00Z"))

    def test_a_naive_stamp_is_utc(self):
        self.assertEqual(dashboard._epoch("2026-01-01T00:00:00"),
                         dashboard._epoch("2026-01-01T00:00:00Z"))

    def test_a_bool_is_not_a_timestamp(self):
        self.assertIsNone(dashboard._epoch(True))


class IsoNowShapeTestCase(unittest.TestCase):
    def test_generated_at_is_a_zulu_string(self):
        self.assertRegex(dashboard._iso_now(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# --------------------------------------------------------------------------- #
# 缺省 = 安全侧
# --------------------------------------------------------------------------- #
class AbsentFieldsFallBackSafeTestCase(unittest.TestCase):
    """`getattr` 的回落值就是「没有证据时说什么」——每一个都指向不多说一句。"""

    def test_an_object_that_never_claims_a_pinned_title_is_not_pinned(self):
        """§37：`user_titled` 是 owner 亲手命名的钉子（钉住后 LLM 不再改名）。
        对象没说过 = 整键不出，绝不替 owner 认领。"""
        stub = types.SimpleNamespace(id="P-20260915-x", title="标题", notes=None)
        out = dashboard._title_fields(stub)
        self.assertNotIn("user_titled", out)
        self.assertEqual(out["display_id"], "P-20260915-x")

    def test_a_pinned_title_does_say_so(self):
        out = dashboard._title_fields(_req(display_title="盯住这个名字", user_titled=True))
        self.assertIs(out["user_titled"], True)

    def test_no_silent_merge_count_means_zero_not_one(self):
        """§2：并入次数是投影出来的证据，缺了就是 0——绝不凭空报一次并入。"""
        self.assertEqual(dashboard._silent_merged(types.SimpleNamespace()), 0)
        self.assertEqual(dashboard._silent_merged(_req(silent_merge_count="abc")), 0)

    def test_repeated_mentions_never_drop_below_one(self):
        """一张卡至少被提过一次——0 / 坏值都归一到 1（lane 行上的「第 N 次」）。"""
        req = _req()
        req.repeated_mentions = 0
        self.assertEqual(dashboard._repeated(req), 1)
        req.repeated_mentions = "abc"
        self.assertEqual(dashboard._repeated(req), 1)
        req.repeated_mentions = 4
        self.assertEqual(dashboard._repeated(req), 4)

    def test_slept_seconds_floor_at_zero(self):
        """§71.2：睡掉的秒数只会是非负；坏值 / 负值 → 0 = 整键不出。"""
        self.assertEqual(dashboard._slept({"slept_seconds": 90}), 90)
        self.assertEqual(dashboard._slept({"slept_seconds": -5}), 0)
        self.assertEqual(dashboard._slept({"slept_seconds": "abc"}), 0)
        self.assertEqual(dashboard._slept({}), 0)


class FoldReceiptSwitchTestCase(unittest.TestCase):
    """§44.6：回执开关只有**显式关掉**才闭嘴；缺键的老 cfg 照发（fail-open）。"""

    def setUp(self):
        patcher = mock.patch.object(
            fold_receipts, "load_recent",
            side_effect=lambda *a, **k: [{"req": "R-1", "channel": "slack",
                                          "at": 1_700_000_000, "count": 2}])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_old_cfg_without_the_key_still_gets_receipts(self):
        rows = dashboard._fold_receipts(types.SimpleNamespace())
        self.assertEqual([r["req"] for r in rows], ["R-1"])
        self.assertEqual(rows[0]["title"], "")   # 目标卡已不在 registry → 留空

    def test_no_cfg_at_all_still_gets_receipts(self):
        self.assertEqual(len(dashboard._fold_receipts()), 1)
        self.assertEqual(len(dashboard._fold_receipts(None)), 1)

    def test_switching_it_off_empties_the_list_not_the_key(self):
        cfg = types.SimpleNamespace(fold_receipt_notices=False)
        self.assertEqual(dashboard._fold_receipts(cfg), [])


# --------------------------------------------------------------------------- #
# 降级路径答形状，不答 None
# --------------------------------------------------------------------------- #
class DegradedPathsAnswerAShapeTestCase(unittest.TestCase):
    """宪法第 11 条：读坏了的每一处都要答出**完整形状**——None 会被下游的
    `or` / `if` 静默吞掉，把「读不到」伪装成「没有」。"""

    def test_self_improve_view_keeps_the_full_shape_and_clips_the_error(self):
        with mock.patch.object(self_improve, "board_view",
                               side_effect=RuntimeError("x" * 300)):
            view = dashboard._self_improve_view(config.Config())
        self.assertEqual(view["enabled"], False)
        self.assertEqual(view["paused"], False)
        self.assertEqual(view["error"], "x" * 200)     # §65 错误串上限 200

    def test_a_broken_config_read_falls_back_to_the_callers_snapshot(self):
        snapshot = config.Config()
        with mock.patch.object(config, "load_config", side_effect=RuntimeError("boom")):
            self.assertIs(dashboard._live_config(snapshot), snapshot)

    def test_broken_radar_health_is_an_empty_dict(self):
        with mock.patch.object(radar_health, "load_radar_health",
                               side_effect=RuntimeError("boom")):
            self.assertEqual(dashboard._radar_health_data(), {})
        with mock.patch.object(radar_health, "load_radar_health", return_value=["nope"]):
            self.assertEqual(dashboard._radar_health_data(), {})

    def test_radar_rounds_pass_through_and_fall_back(self):
        ledger = {"gmail": {"requested_at": "2026-09-15T00:00:00Z"}}
        with mock.patch.object(radar_rounds, "load_rounds", return_value=ledger):
            self.assertEqual(dashboard._radar_rounds_data(), ledger)
        with mock.patch.object(radar_rounds, "load_rounds", side_effect=RuntimeError("x")):
            self.assertEqual(dashboard._radar_rounds_data(), {})

    def test_an_unstattable_secret_file_is_not_started(self):
        with mock.patch.object(Path, "exists", side_effect=OSError("permission")):
            self.assertIs(dashboard._secret_file_started(secrets.SLACK_TOKEN_FILE), False)

    def test_source_signals_that_cannot_be_probed_are_no_signals(self):
        with mock.patch.object(secrets, "read_secret", side_effect=OSError("disk")):
            self.assertEqual(dashboard._source_signals(config.Config(), "gmail", {}),
                             {"intent": False, "secret_present": False})

    def test_an_unreadable_directory_is_not_a_repo(self):
        """§7：`target_kind` 的判据——探不动的目录算「新」，不算「已有」。"""
        with mock.patch.object(Path, "exists", side_effect=OSError("permission")):
            self.assertIs(dashboard._dir_is_nonempty(Path("/nope")), False)


class DirIsNonemptyIsAllThreeTestCase(unittest.TestCase):
    """§7：「已有仓库」= 存在 ∧ 是目录 ∧ 非空——三个都要，不是任意一个。"""

    def test_a_plain_file_is_not_a_repo(self):
        d = _tmpdir(self, "dash-mut-dir-")
        f = d / "README.md"
        f.write_text("x", encoding="utf-8")
        self.assertIs(dashboard._dir_is_nonempty(f), False)

    def test_an_empty_directory_is_not_a_repo(self):
        self.assertIs(dashboard._dir_is_nonempty(_tmpdir(self, "dash-mut-empty-")), False)

    def test_a_missing_path_is_not_a_repo(self):
        self.assertIs(dashboard._dir_is_nonempty(_tmpdir(self, "dash-mut-gone-") / "nope"), False)

    def test_a_directory_with_content_is(self):
        d = _tmpdir(self, "dash-mut-full-")
        (d / "f").write_text("x", encoding="utf-8")
        self.assertIs(dashboard._dir_is_nonempty(d), True)


# --------------------------------------------------------------------------- #
# §48.4 / §71.1 的投影面
# --------------------------------------------------------------------------- #
class SourceHealthSignalsTestCase(unittest.TestCase):
    """§48.4：`intent` / `secret_present` 每源恒在，且**没说 = False**——
    setup 类诊断卡靠它们把「从没碰过 Gmail 的新装机」挡在门外（§3.6）。"""

    def test_missing_signal_keys_read_as_no_intent(self):
        row = dashboard._source_health("gmail", False, {}, _NOW, None, {})
        self.assertIs(row["intent"], False)
        self.assertIs(row["secret_present"], False)
        self.assertIs(row["enabled"], False)

    def test_signals_pass_through_as_real_bools(self):
        row = dashboard._source_health("gmail", False, {}, _NOW, None,
                                       {"intent": 1, "secret_present": "yes"})
        self.assertIs(row["intent"], True)
        self.assertIs(row["secret_present"], True)


class QueuedReasonAsleepTestCase(unittest.TestCase):
    """§71.1：派发闸按住整个 pass 时排队卡说「电脑睡着了」，不说「还没轮到」。"""

    def test_machine_asleep_is_its_own_kind(self):
        self.assertEqual(dashboard._queued_reason_view(_req(), {"machine_asleep": True}),
                         {"kind": "asleep"})

    def test_nothing_blocking_is_no_key(self):
        self.assertIsNone(dashboard._queued_reason_view(_req(), {}))


class CompletionHintShapeTestCase(unittest.TestCase):
    """§76.2：「疑似已完成」的证据——有时间**或**有说法就投；两样都没有才算空壳。"""

    def test_a_note_without_a_timestamp_still_speaks(self):
        view = dashboard._completion_hint_view(
            _req(completion_hint={"at": "坏时间", "note": "对方说已经发过了",
                                  "channel": "slack"}))
        self.assertEqual(view, {"at": None, "note": "对方说已经发过了",
                                "channel": "slack"})

    def test_a_timestamp_without_a_note_still_speaks(self):
        view = dashboard._completion_hint_view(
            _req(completion_hint={"at": "2026-09-15T00:00:00Z", "note": "",
                                  "channel": "gmail"}))
        self.assertEqual(view["at"], dashboard._epoch("2026-09-15T00:00:00Z"))
        self.assertEqual(view["note"], "")

    def test_an_empty_shell_is_omitted_entirely(self):
        self.assertIsNone(dashboard._completion_hint_view(_req(completion_hint={})))
        self.assertIsNone(dashboard._completion_hint_view(
            _req(completion_hint={"note": "", "channel": "slack"})))
        self.assertIsNone(dashboard._completion_hint_view(_req()))


# --------------------------------------------------------------------------- #
# 可见性与并发口径
# --------------------------------------------------------------------------- #
class InvisibleCardsEnterNoLaneTestCase(unittest.TestCase):
    """契约 四 / §5：`merged` 与 `archived` 各自独立地把卡赶出所有看板列
    ——两个判据是「或」，不是「且」。"""

    def test_merged_is_invisible(self):
        self.assertIs(dashboard._invisible(_req(status="merged")), True)

    def test_a_legacy_merged_into_status_is_invisible(self):
        self.assertIs(dashboard._invisible(_req(status="merged_into:R-9")), True)

    def test_an_archived_card_lingering_in_the_active_dir_is_invisible(self):
        self.assertIs(dashboard._invisible(_req(status="archived")), True)
        self.assertIs(dashboard._invisible(_req(status="rejected")), True)

    def test_a_live_card_is_visible(self):
        self.assertIs(dashboard._invisible(_req(status="card_sent")), False)

    def test_the_lane_router_agrees(self):
        cfg = config.Config()
        dash = dashboard.build_dashboard(
            reqs=[_req(id="R-1", status="archived"), _req(id="R-2", status="merged"),
                  _req(id="R-3", status="card_sent")],
            agents=[], cfg=cfg, archived=[])
        self.assertEqual([r["id"] for r in dash["needs_approval"]], ["R-3"])
        self.assertEqual(sum(dash["counts"][lane] for lane in dashboard._LANES), 1)


class LiveSessionCountTestCase(unittest.TestCase):
    """§51 并发口径：一张 EXECUTING 且带 session 的卡记**一个**。"""

    def test_each_running_card_counts_once(self):
        reqs = [_req(id="R-1", status="executing", execution={"session_id": "a1b2c3d4"}),
                _req(id="R-2", status="executing", execution={"session_id": "e5f6a7b8"}),
                _req(id="R-3", status="executing", execution={}),
                _req(id="R-4", status="card_sent")]
        self.assertEqual(dashboard._live_session_count(reqs), 2)
        self.assertEqual(dashboard._live_session_count([]), 0)


# --------------------------------------------------------------------------- #
# 出机的字节
# --------------------------------------------------------------------------- #
class RosterQueryIsBoundedAndQuietTestCase(unittest.TestCase):
    """§55：roster 子进程必须收走它的 stdout 并带超时——一个挂住的 claude 不许
    拖垮 ~10 s 的 pass，它的输出也不许漏进 actd 的终端。"""

    def _call(self, rc=0, stdout="[]"):
        proc = types.SimpleNamespace(returncode=rc, stdout=stdout, stderr="")
        with mock.patch.object(dashboard.subprocess, "run", return_value=proc) as run:
            out = dashboard._claude_agents_stdout()
        return out, run.call_args

    def test_the_call_captures_decoded_output_and_is_time_bounded(self):
        out, call = self._call()
        self.assertEqual(out, "[]")
        self.assertIs(call.kwargs["capture_output"], True)
        # text=True：这个函数的契约是「返回 stdout 的**字符串**」（注释里的
        # `proc.stdout.strip()` 与调用方的 json.loads 都按 str 写）——拿回
        # bytes 就是把解码问题推给下游。
        self.assertIs(call.kwargs["text"], True)
        self.assertEqual(call.kwargs["timeout"], 30)
        self.assertEqual(call.kwargs["env"]["DISABLE_AUTOUPDATER"], "1")

    def test_a_silent_success_is_not_a_roster(self):
        """rc=0 但 stdout 空，和 rc!=0 但有输出——两种都要判成「问不到」。"""
        self.assertIsNone(self._call(rc=0, stdout="   \n")[0])
        self.assertIsNone(self._call(rc=1, stdout='[{"id": "a"}]')[0])


class WriteDashboardBytesTestCase(unittest.TestCase):
    """§2：落盘的字节形状本身是契约——目录自建、中文不转义、缩进两格。"""

    def test_the_whole_parent_chain_is_created(self):
        target = _tmpdir(self, "dash-mut-write-") / "state" / "nested" / "dashboard.json"
        dashboard.write_dashboard({"counts": {}}, path=target)
        self.assertTrue(target.exists())

    def test_cjk_stays_readable_and_the_indent_is_two(self):
        target = _tmpdir(self, "dash-mut-write-") / "dashboard.json"
        dashboard.write_dashboard({"title": "中文标题"}, path=target)
        text = target.read_text(encoding="utf-8")
        self.assertIn("中文标题", text)              # ensure_ascii=False
        self.assertNotIn("\\u", text)
        self.assertIn('\n  "title":', text)          # indent=2，不多不少
        self.assertFalse(re.search(r'\n\s*\n', text))


if __name__ == "__main__":
    unittest.main()
