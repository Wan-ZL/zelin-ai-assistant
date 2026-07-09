"""merge-review 契约（冻结契约 merge-review 二/四/六）— registry + dashboard side.

Covered:
- registry: the new ``merged`` terminal state (State.MERGED) round-trips with
  ``merged_into`` through save/load, loads tolerantly (unknown keys, missing
  field), and coexists with the legacy ``merged_into:<id>`` status strings;
- merge_or_new: a merged parent absorbs restatements exactly like delivered
  (匹配压重述) — the OPPOSITE of trashed (决策 6) and of the legacy prefix;
- purge: actd.purge_trash never hard-deletes merged cards (purge 不清 merged);
- dashboard: merged cards enter NO column; the merge_suggestions partition
  projects state/merge/*.json — analyzing/done/failed emitted, dismissed and
  corrupt files skipped, requested_at ISO -> epoch int, expires_at (the TTL
  field, actd's cleanup bookkeeping) tolerated but never forwarded.

Runs entirely inside the sandbox AIASSISTANT_HOME (tests/__init__.py).
"""
import datetime as _dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act.lib import config, dashboard, registry
from act.lib.registry import Requirement, State

TITLE = "Prepare quarterly OKR report"


def _utc_epoch(*args) -> int:
    return int(_dt.datetime(*args, tzinfo=_dt.timezone.utc).timestamp())


def _iso(dt: _dt.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _src(channel="meeting", date="2026-07-01", quote="prepare the OKR report"):
    return {"who": "manager", "channel": channel, "date": date, "quote": quote}


def _incoming(title=TITLE, **kw):
    kw.setdefault("sources", [_src(channel="slack", date="2026-07-02",
                                   quote="don't forget the OKR report")])
    return Requirement(id="", title=title, **kw)


class RegistryBase(unittest.TestCase):
    def setUp(self):
        config.ensure_state_dirs()
        for p in config.REGISTRY_DIR.glob("*.yaml"):
            p.unlink()

    def _save(self, rid="R-100", title=TITLE, status=State.CARD_SENT.value, **kw):
        kw.setdefault("sources", [_src()])
        req = Requirement(id=rid, title=title, status=status, **kw)
        registry.save(req)
        return req

    def _all_ids(self):
        return sorted(r.id for r in registry.load_all())


# --------------------------------------------------------------------------- #
# merged 终态：枚举 + 往返 + 容错加载（契约 四）
# --------------------------------------------------------------------------- #
class MergedStateRoundTripTestCase(RegistryBase):
    def test_state_enum_has_merged_terminal(self):
        self.assertEqual(State.MERGED.value, "merged")
        self.assertEqual(str(State.MERGED), "merged")  # f-string 用裸值

    def test_merged_round_trips_with_merged_into(self):
        self._save(rid="R-200", status=State.MERGED.value, merged_into="R-100")
        got = registry.load("R-200")
        self.assertEqual(got.status, "merged")
        self.assertEqual(got.merged_into, "R-100")
        # 新终态不是 legacy 前缀态；merged_parent 走 merged_into 字段
        self.assertFalse(got.is_merged)
        self.assertEqual(got.merged_parent, "R-100")

    def test_merged_into_omitted_from_yaml_when_unset(self):
        req = self._save(rid="R-201")  # 普通卡：不写 merged_into
        text = Path(req._file).read_text(encoding="utf-8")
        self.assertNotIn("merged_into", text)

    def test_tolerant_loading_unknown_keys_and_missing_field(self):
        # from_dict 容错：未知键忽略、缺 merged_into -> None
        got = Requirement.from_dict({
            "id": "R-202", "status": "merged", "merged_into": "R-100",
            "some_future_field": {"x": 1},
        })
        self.assertEqual(got.status, "merged")
        self.assertEqual(got.merged_into, "R-100")
        legacy = Requirement.from_dict({"id": "R-203", "status": "detected"})
        self.assertIsNone(legacy.merged_into)
        self.assertIsNone(legacy.merged_parent)

    def test_legacy_merged_prefix_still_recognized(self):
        got = Requirement.from_dict(
            {"id": "R-204", "status": "merged_into:R-050"})
        self.assertTrue(got.is_merged)
        self.assertEqual(got.merged_parent, "R-050")


# --------------------------------------------------------------------------- #
# merge_or_new：merged 视同 delivered 参与匹配压重述（与 trashed 相反）
# --------------------------------------------------------------------------- #
class MergedMatchingTestCase(RegistryBase):
    def test_merged_parent_absorbs_restatement_like_delivered(self):
        self._save(status=State.MERGED.value, merged_into="R-050")
        got = registry.merge_or_new(_incoming())
        self.assertEqual(got.id, "R-100")                 # 匹配上了
        self.assertEqual(self._all_ids(), ["R-100"])      # 不新建卡
        self.assertEqual(got.status, State.MERGED.value)  # 状态不动、不复活
        self.assertEqual(got.merged_into, "R-050")
        self.assertEqual(got.repeated_mentions, 2)
        self.assertEqual(len(got.sources), 2)             # 新来源并入

    def test_merged_parent_with_increment_gets_improvement_card(self):
        # 视同 delivered：带增量的重提照旧走改进卡路径
        self._save(status=State.MERGED.value, merged_into="R-050",
                   deadline="2026-08-01")
        got = registry.merge_or_new(_incoming(deadline="2026-07-15"))
        self.assertNotEqual(got.id, "R-100")
        self.assertEqual(got.improvement_of, "R-100")
        parent = registry.load("R-100")
        self.assertEqual(parent.status, State.MERGED.value)  # 主体不动

    def test_trashed_parent_still_never_matches_the_contrast(self):
        # 对照组（决策 6）：回收站的卡重提必须重新出卡
        parent = self._save()
        registry.trash(parent, "merged-review: 不再需要")
        got = registry.merge_or_new(_incoming())
        self.assertNotEqual(got.id, "R-100")
        self.assertEqual(len(self._all_ids()), 2)

    def test_legacy_merged_prefix_still_never_matches(self):
        self._save(status="merged_into:R-050")
        got = registry.merge_or_new(_incoming())
        self.assertNotEqual(got.id, "R-100")


# --------------------------------------------------------------------------- #
# purge 不清 merged（契约 四：语义同回收站可见性，但 purge 不删）
# --------------------------------------------------------------------------- #
class PurgeSkipsMergedTestCase(RegistryBase):
    def test_purge_trash_deletes_old_trashed_but_never_merged(self):
        from act import actd

        old = _iso(_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=100))
        # merged 卡即使带着一个远古 trashed_at（曾进过回收站又被并入）也不能被清
        self._save(rid="R-300", status=State.MERGED.value, merged_into="R-050",
                   trashed_at=old)
        self._save(rid="R-301", status=State.TRASHED.value, trashed_at=old)

        purged = actd.purge_trash(config.Config())  # retention 默认 60 天

        self.assertEqual(purged, 1)
        self.assertIsNone(registry.load("R-301"))            # trashed 被清
        survivor = registry.load("R-300")                    # merged 还在
        self.assertIsNotNone(survivor)
        self.assertEqual(survivor.status, State.MERGED.value)
        self.assertEqual(survivor.merged_into, "R-050")


# --------------------------------------------------------------------------- #
# dashboard：merged 卡不进任何列
# --------------------------------------------------------------------------- #
_PARTITIONS = ("needs_approval", "running", "needs_input", "review",
               "completed", "debt", "trash")


class DashboardHidesMergedTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config()
        home = tempfile.mkdtemp(prefix="dash-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.merge_dir = Path(tempfile.mkdtemp(prefix="merge-dir-"))

    def test_merged_card_enters_no_column(self):
        merged = Requirement.from_dict({
            "id": "R-400", "title": "已并入主卡的副卡", "status": "merged",
            "merged_into": "R-050",
            "execution": {"session_id": "feedc0de"},  # 有旧 session 也不进列
        })
        visible = Requirement.from_dict(
            {"id": "R-401", "title": "普通欠账", "status": "detected"})
        dash = dashboard.build_dashboard(
            reqs=[merged, visible], agents=[], cfg=self.cfg,
            merge_dir=self.merge_dir)

        for part in _PARTITIONS:
            ids = [item["id"] for item in dash[part]]
            self.assertNotIn("R-400", ids, f"merged card leaked into {part}")
        # 普通卡照常投影，计数不含 merged
        self.assertEqual([i["id"] for i in dash["debt"]], ["R-401"])
        self.assertEqual(dash["counts"]["debt"], 1)
        for part in _PARTITIONS:
            if part != "debt":
                self.assertEqual(dash["counts"][part], 0)


# --------------------------------------------------------------------------- #
# merge_suggestions 分区（契约 六）：五情形 + TTL 字段
# --------------------------------------------------------------------------- #
class MergeSuggestionsPartitionTestCase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config()
        home = tempfile.mkdtemp(prefix="dash-home-")
        patcher = mock.patch.dict(os.environ, {"HOME": home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.merge_dir = Path(tempfile.mkdtemp(prefix="merge-dir-"))

    def _write(self, name: str, payload) -> Path:
        path = self.merge_dir / name
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, ensure_ascii=False),
                            encoding="utf-8")
        return path

    def _build(self, reqs=()):
        return dashboard.build_dashboard(
            reqs=list(reqs), agents=[], cfg=self.cfg, merge_dir=self.merge_dir)

    # -- analyzing ---------------------------------------------------------- #
    def test_analyzing_emitted_with_epoch_and_empty_optionals(self):
        self._write("MS-aaaa0001.json", {
            "id": "MS-aaaa0001", "ids": ["R-001", "R-002"],
            "requested_at": "2026-07-08T10:00:00Z", "status": "analyzing",
        })
        sugg = self._build()["merge_suggestions"]
        self.assertEqual(len(sugg), 1)
        item = sugg[0]
        self.assertEqual(item["id"], "MS-aaaa0001")
        self.assertEqual(item["ids"], ["R-001", "R-002"])
        self.assertEqual(item["status"], "analyzing")
        # ISO -> epoch int（契约 六）
        self.assertIsInstance(item["requested_at"], int)
        self.assertEqual(item["requested_at"], _utc_epoch(2026, 7, 8, 10))
        # 未出结论：可选字段为 null / 空清单，键都在（Swift decodeIfPresent）
        self.assertIsNone(item["verdict"])
        self.assertIsNone(item["primary"])
        self.assertIsNone(item["rationale"])
        self.assertIsNone(item["confidence"])
        self.assertIsNone(item["error"])
        self.assertEqual(item["action_plan"], [])

    # -- done ---------------------------------------------------------------- #
    def test_done_passes_verdict_fields_through(self):
        self._write("MS-bbbb0002.json", {
            "id": "MS-bbbb0002", "ids": ["R-001", "R-002", "R-003"],
            "requested_at": "2026-07-08T11:30:00Z", "status": "done",
            "verdict": "merge", "primary": "R-001",
            "rationale": "三张卡是同一件事的三次表述",
            "action_plan": ["R-002/R-003 sources 并入 R-001",
                            "副卡置 merged 并指向 R-001"],
            "confidence": "high",
            "expires_at": "2026-07-09T11:30:00Z",
        })
        item = self._build()["merge_suggestions"][0]
        self.assertEqual(item["status"], "done")
        self.assertEqual(item["verdict"], "merge")
        self.assertEqual(item["primary"], "R-001")
        self.assertEqual(item["rationale"], "三张卡是同一件事的三次表述")
        self.assertEqual(item["action_plan"],
                         ["R-002/R-003 sources 并入 R-001",
                          "副卡置 merged 并指向 R-001"])
        self.assertEqual(item["confidence"], "high")
        self.assertIsNone(item["error"])
        self.assertEqual(item["requested_at"], _utc_epoch(2026, 7, 8, 11, 30))

    # -- failed --------------------------------------------------------------- #
    def test_failed_emits_error_only(self):
        self._write("MS-cccc0003.json", {
            "id": "MS-cccc0003", "ids": ["R-001", "R-002"],
            "requested_at": "2026-07-08T12:00:00Z", "status": "failed",
            "error": "claude -p timed out after 300s",
        })
        item = self._build()["merge_suggestions"][0]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["error"], "claude -p timed out after 300s")
        self.assertIsNone(item["verdict"])

    # -- dismissed ------------------------------------------------------------ #
    def test_dismissed_not_emitted(self):
        self._write("MS-dddd0004.json", {
            "id": "MS-dddd0004", "ids": ["R-001", "R-002"],
            "requested_at": "2026-07-08T13:00:00Z", "status": "dismissed",
            "verdict": "keep_separate",
        })
        self.assertEqual(self._build()["merge_suggestions"], [])

    # -- corrupt --------------------------------------------------------------- #
    def test_corrupt_files_skipped_without_breaking_the_rest(self):
        self._write("MS-broken.json", "{{{ not json at all")
        self._write("MS-list.json", "[1, 2, 3]")  # 合法 JSON 但不是 dict
        self._write("MS-eeee0005.json", {
            "id": "MS-eeee0005", "ids": ["R-001", "R-002"],
            "requested_at": "2026-07-08T14:00:00Z", "status": "analyzing",
        })
        sugg = self._build()["merge_suggestions"]
        self.assertEqual([s["id"] for s in sugg], ["MS-eeee0005"])

    # -- TTL 字段 --------------------------------------------------------------- #
    def test_ttl_field_tolerated_but_never_forwarded(self):
        # expires_at 是 actd TTL 清理的记账字段：过期文件的删除是 actd 每 pass
        # 的事，dashboard 是纯投影 —— 文件还在就照发，且不外发 expires_at 键。
        past = _iso(_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1))
        self._write("MS-ffff0006.json", {
            "id": "MS-ffff0006", "ids": ["R-001", "R-002"],
            "requested_at": "2026-07-08T15:00:00Z", "status": "done",
            "verdict": "close_secondary", "primary": "R-001",
            "expires_at": past,
        })
        sugg = self._build()["merge_suggestions"]
        self.assertEqual(len(sugg), 1)
        self.assertNotIn("expires_at", sugg[0])
        self.assertEqual(
            set(sugg[0]),
            {"id", "ids", "status", "verdict", "primary", "rationale",
             "action_plan", "confidence", "error", "requested_at"})

    # -- misc ------------------------------------------------------------------ #
    def test_newest_request_first_and_counts_untouched(self):
        self._write("MS-old.json", {
            "id": "MS-old", "ids": ["R-001", "R-002"],
            "requested_at": "2026-07-08T09:00:00Z", "status": "analyzing"})
        self._write("MS-new.json", {
            "id": "MS-new", "ids": ["R-003", "R-004"],
            "requested_at": "2026-07-08T16:00:00Z", "status": "analyzing"})
        dash = self._build()
        self.assertEqual([s["id"] for s in dash["merge_suggestions"]],
                         ["MS-new", "MS-old"])
        # counts 分区不新增键（契约 六 只加分区）
        self.assertNotIn("merge_suggestions", dash["counts"])

    def test_missing_merge_dir_yields_empty_partition(self):
        dash = dashboard.build_dashboard(
            reqs=[], agents=[], cfg=self.cfg,
            merge_dir=self.merge_dir / "does-not-exist")
        self.assertEqual(dash["merge_suggestions"], [])


if __name__ == "__main__":
    unittest.main()
