"""store2 字段词表 drift-pin（BUILD-CONTRACT §3 + CONTRACT header 字段纪律）。

背景：store2 的 migrate/export 曾手抄整套 registry 字段词表（"基线 registry
不可 import"，merge 当天理由失效）。手抄的症状不是报错而是**静默丢字段**——
registry 加一个 add-only 字段、migrate 不认识它，from_dict/normalize_card 的
discard 语义会让它在一次性迁移里无声消失，且不可逆。

现在词表已 import 单源（``export_yaml`` 拿 registry 的 CORE_ORDER/
OPTIONAL_ORDER，``migrate_yaml`` 拿 store 的 CARD_COLUMNS）。仍是逐字复刻的
只剩 ``FIELD_DEFAULTS`` 与 from_dict/to_dict 归一语义（dataclass 默认值不是
可 import 的表）——本文件把这两处钉死：新字段只要漏补默认值或归一语义走偏，
这里立刻红，而不是等到某次迁移之后才发现卡片少了一章。
"""
import unittest
from dataclasses import MISSING, fields

from tests import TMP_HOME  # noqa: F401 - sandbox env 先于任何 act.* import

from act.lib import registry
from act.lib.store2 import export_yaml, migrate_yaml, store

# registry dataclass 默认值与 export 侧 FIELD_DEFAULTS 的**已登记**差异
# （形如 name: (registry 侧, export 侧)）：
# - sources 在 registry 是 default_factory=list，export 侧存 None 并在
#   normalize_card 里现做空 list（防跨卡共享同一个 list 引用）；
# - id 在 registry 是必填位置参数（无默认），export 侧的字典形状要求有个占位。
_NO_DEFAULT = object()
_DEFAULT_EXCEPTIONS = {"sources": ([], None), "id": (_NO_DEFAULT, "")}


def _registry_defaults() -> dict:
    """Requirement dataclass 的可序列化字段 → 默认值（工厂默认现场取值；
    无默认的必填字段落 _NO_DEFAULT 哨兵，键仍在——覆盖面比对要用）。"""
    out = {}
    for f in fields(registry.Requirement):
        if f.name.startswith("_"):
            continue          # _file/_in_list 是内部记账，永不序列化
        if f.default is not MISSING:
            out[f.name] = f.default
        elif f.default_factory is not MISSING:   # type: ignore[misc]
            out[f.name] = f.default_factory()    # type: ignore[misc]
        else:
            out[f.name] = _NO_DEFAULT
    return out


class FieldVocabularySingleSourceTestCase(unittest.TestCase):
    """词表只有一份——store2 侧拿到的必须**就是** registry 那个对象。"""

    def test_export_reuses_registry_order_lists_rather_than_copying(self):
        self.assertIs(export_yaml.CORE_ORDER, registry.CORE_ORDER)
        self.assertIs(export_yaml.OPTIONAL_ORDER, registry.OPTIONAL_ORDER)

    def test_migrate_reuses_store_hot_column_tuple(self):
        self.assertIs(migrate_yaml.CARD_COLUMNS, store.CARD_COLUMNS)

    def test_order_lists_are_disjoint_and_cover_the_dataclass(self):
        """core/optional 不重叠，且合起来 == 可序列化的 dataclass 字段全集。

        registry 加了字段却忘了进词表 = 该字段永不落盘（to_dict 只遍历这两个
        表）——这条是那个 bug 的唯一守卫。
        """
        core, optional = set(registry.CORE_ORDER), set(registry.OPTIONAL_ORDER)
        self.assertEqual(core & optional, set())
        self.assertEqual(core | optional, set(_registry_defaults()))


class FieldDefaultsCoverageTestCase(unittest.TestCase):
    """FIELD_DEFAULTS 是手抄的最后一块——覆盖面与取值逐字段钉死。"""

    def test_defaults_cover_exactly_the_registry_vocabulary(self):
        vocabulary = set(registry.CORE_ORDER) | set(registry.OPTIONAL_ORDER)
        self.assertEqual(
            set(export_yaml.FIELD_DEFAULTS), vocabulary,
            "export_yaml.FIELD_DEFAULTS 与 registry 字段词表不一致——"
            "registry 新增字段时必须同步补默认值，否则迁移会静默丢该字段")

    def test_default_values_match_the_dataclass(self):
        expected = _registry_defaults()
        for name, want in expected.items():
            got = export_yaml.FIELD_DEFAULTS[name]
            if name in _DEFAULT_EXCEPTIONS:
                self.assertEqual((want, got), _DEFAULT_EXCEPTIONS[name],
                                 f"{name} 的已登记差异变了，先改注释再改测试")
                continue
            self.assertEqual(got, want, f"{name} 默认值与 registry 不一致")


class NormalizeCardMirrorsFromDictTestCase(unittest.TestCase):
    """normalize_card == from_dict∘to_dict（键序在内），逐条复刻不是口号。"""

    def _assert_same(self, raw: dict):
        want = registry.Requirement.from_dict(raw).to_dict()
        got = export_yaml.normalize_card(raw)
        self.assertEqual(list(got.items()), list(want.items()))

    def test_every_field_set_round_trips_identically(self):
        raw = {"id": "R-900", "title": "全字段卡", "type": "engineering",
               "tier": "T2", "status": "review", "hardness": "hard",
               "deadline": "2026-09-01", "repeated_mentions": 3,
               "green_sign_required": True, "disagreement": "有分歧",
               "cost_estimate_usd": 1.5,
               "sources": [{"channel": "slack", "date": "2026-08-30"}],
               "plan": ["step 1", "step 2"], "summary": "一句话",
               "definition_of_done": ["dod"], "outputs": ["out.md"],
               "card": {"sent_at": 123}, "execution": {"session_id": "s1"},
               "improvement_of": "R-800", "merged_into": "R-700",
               "target_repo": "repo-x", "target_kind": "existing",
               "delivery_mode": "chat", "notes": "备注",
               "trashed_at": "2026-08-01", "prev_status": "detected",
               "trash_reason": "deleted", "permanent": True,
               "origin_trust": "hand", "thread_id": "R-900",
               "thread_key": "slack:1", "archived_at": "2026-08-02",
               "archive_reason": "done", "display_title": "显示名",
               "user_titled": True, "former_titles": ["旧名"],
               "split_from": "R-600", "silent_merge_count": 2,
               "preset": "proposals_triage", "work_id": "R-901",
               "assessment": {"summary": "修好了", "verdict": "建议验收",
                              "verdict_reason": "清单全满足", "at": "2026-09-02T00:00:00Z",
                              "source_hash": "abcd"},
               "merged_from": ["P-10", "P-11"]}
        self.assertEqual(set(raw), set(export_yaml.FIELD_DEFAULTS))  # fixture 自检
        self._assert_same(raw)

    def test_empty_card_falls_back_to_the_same_core_block(self):
        self._assert_same({})

    def test_omission_quirks_agree(self):
        """省略语义的坑位：0 == False 的 silent_merge_count、默认 repo 的
        delivery_mode、空串 notes —— 三处都必须整键消失。"""
        raw = {"id": "R-901", "silent_merge_count": 0, "delivery_mode": "repo",
               "notes": "", "permanent": False, "former_titles": []}
        self._assert_same(raw)
        got = export_yaml.normalize_card(raw)
        for absent in ("silent_merge_count", "delivery_mode", "notes",
                       "permanent", "former_titles"):
            self.assertNotIn(absent, got)

    def test_tolerance_paths_agree(self):
        """LLM/手写 YAML 的脏值：数字 id/title/tier、repo 别名、词表外
        delivery_mode、未知顶层键 —— 归一结果两侧必须逐字相同。"""
        self._assert_same({"id": 4, "title": 456, "tier": 7,
                           "repo": "alias-repo", "delivery_mode": "carrier",
                           "unknown_future_key": "x"})


if __name__ == "__main__":
    unittest.main()
