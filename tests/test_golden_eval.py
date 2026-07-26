"""golden_eval 回测工具 — build 真值标签 / classify LLM 合并 / score 混淆矩阵.

全部用注入的 fake extractor（绝不 spawn 真 claude），跑在 sandbox
AIASSISTANT_HOME（tests/__init__.py）里。钉住的契约：

- build: status/prev_status -> REAL/NOISE/PENDING 映射（含 archive/ 里
  prev_status 路径），R-000-example 永不入集，来源字段取 sources[0]，
  数据集只落 state/golden/（gitignored）；
- classify: meeting 卡走 LLM 并把 provenance/speaker 合并回 cards.jsonl；
  非 meeting 渠道恒 channel_api（不进 prompt）；批量 ≤12 张/次；
  LLM 输出是垃圾 -> 全部 unknown 而不是崩；
- score: REAL+screen 被拦 = 误杀（列 id+title），NOISE+screen 被拦 = 正确
  拦截，channel_api 恒出生，PENDING 单列不计指标。
"""
import json
import shutil
import subprocess
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import golden_eval
from act.lib import config, registry


def _proc(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr="")


class _FakeLLM:
    """注入的分类器：固定 JSON 回答（raw 优先，模拟坏输出），记录 prompts."""

    def __init__(self, items=None, raw=None):
        self.items = items if items is not None else []
        self.raw = raw
        self.calls: list = []

    def __call__(self, prompt: str):
        self.calls.append(prompt)
        if self.raw is not None:
            return _proc(self.raw)
        return _proc(json.dumps(self.items, ensure_ascii=False))


def _seed(req_id, title, status, channel="meeting", **kw) -> registry.Requirement:
    src = {"who": "manager", "channel": channel, "date": "2026-07-01",
           "quote": f"quote for {title}"}
    r = registry.Requirement(id=req_id, title=title, status=status,
                             sources=[src], **kw)
    registry.save(r)
    return r


def _clean_state():
    config.ensure_state_dirs()
    if config.REGISTRY_DIR.exists():          # archive/ 是子目录，一并清掉
        shutil.rmtree(config.REGISTRY_DIR)
    golden = config.STATE_DIR / "golden"
    if golden.exists():
        shutil.rmtree(golden)


class GoldenBuildTestCase(unittest.TestCase):
    def setUp(self):
        _clean_state()
        self.addCleanup(_clean_state)

    def test_label_mapping_including_archive_prev_status(self):
        _seed("R-101", "已交付的真需求", "delivered")
        _seed("R-102", "执行中的真需求", "executing")
        _seed("R-103", "回收站的噪音", "trashed")
        _seed("R-104", "现役备选未定论", "detected")
        _seed("R-105", "现役提案未定论", "card_sent")
        # archive/ 的 prev_status 路径：备选里过期封存 = NOISE；
        # 交付后封存 = REAL（prev_status 记着正经生命周期）。
        registry.archive(_seed("R-106", "备选过期封存", "detected"), "auto")
        registry.archive(_seed("R-107", "交付后封存", "delivered"), "user")
        # trash 走 registry.trash 带上 prev_status —— REAL 优先于 trashed 判定
        registry.trash(_seed("R-108", "验收后又扔掉", "review"), "deleted")

        cards = {c["id"]: c for c in golden_eval.build_cards()}
        labels = {i: c["label"] for i, c in cards.items()}
        self.assertEqual(labels["R-101"], golden_eval.LABEL_REAL)
        self.assertEqual(labels["R-102"], golden_eval.LABEL_REAL)
        self.assertEqual(labels["R-103"], golden_eval.LABEL_NOISE)
        self.assertEqual(labels["R-104"], golden_eval.LABEL_PENDING)
        self.assertEqual(labels["R-105"], golden_eval.LABEL_PENDING)
        self.assertEqual(labels["R-106"], golden_eval.LABEL_NOISE)
        self.assertEqual(labels["R-107"], golden_eval.LABEL_REAL)
        self.assertEqual(labels["R-108"], golden_eval.LABEL_REAL)
        self.assertEqual(cards["R-106"]["prev_status"], "detected")
        # 来源字段取 sources[0]
        self.assertEqual(cards["R-101"]["channel"], "meeting")
        self.assertEqual(cards["R-101"]["who"], "manager")
        self.assertEqual(cards["R-101"]["quote"], "quote for 已交付的真需求")
        self.assertEqual(cards["R-101"]["date"], "2026-07-01")

    def test_example_card_never_enters(self):
        config.REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        (config.REGISTRY_DIR / "R-000-example.yaml").write_text(
            "id: R-000\ntitle: 示例\nstatus: detected\n", encoding="utf-8")
        _seed("R-101", "真卡", "delivered")
        self.assertEqual([c["id"] for c in golden_eval.build_cards()], ["R-101"])

    def test_cmd_build_writes_jsonl_under_state_only(self):
        _seed("R-101", "真卡", "delivered")
        self.assertEqual(golden_eval.cmd_build(), 0)
        p = config.STATE_DIR / "golden" / "cards.jsonl"
        self.assertTrue(p.exists())
        (row,) = [json.loads(x) for x in
                  p.read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertEqual(row["id"], "R-101")
        # 隐私铁律：数据集只落 state/（sandbox 里即 TMP_HOME 下），绝不进 repo
        self.assertTrue(str(p).startswith(TMP_HOME))


class GoldenClassifyTestCase(unittest.TestCase):
    def setUp(self):
        _clean_state()
        self.addCleanup(_clean_state)

    def test_meeting_cards_classified_and_merged(self):
        _seed("R-201", "屏幕回声卡", "trashed", channel="meeting")
        _seed("R-202", "音频真人卡", "delivered", channel="meeting")
        _seed("R-203", "邮件渠道卡", "delivered", channel="gmail")
        golden_eval.cmd_build()
        llm = _FakeLLM(items=[
            {"id": "R-201", "provenance": "screen", "speaker": "assistant"},
            {"id": "R-202", "provenance": "audio", "speaker": "human"},
        ])
        self.assertEqual(golden_eval.cmd_classify(extractor=llm), 0)
        cards = {c["id"]: c for c in golden_eval._load_cards()}
        self.assertEqual(cards["R-201"]["provenance"], "screen")
        self.assertEqual(cards["R-201"]["speaker"], "assistant")
        self.assertEqual(cards["R-202"]["provenance"], "audio")
        self.assertEqual(cards["R-202"]["speaker"], "human")
        # 非 meeting 渠道不进 LLM：恒 channel_api，也不进 prompt
        self.assertEqual(cards["R-203"]["provenance"], golden_eval.CHANNEL_API)
        self.assertEqual(len(llm.calls), 1)
        self.assertIn("屏幕回声卡", llm.calls[0])
        self.assertNotIn("邮件渠道卡", llm.calls[0])

    def test_batches_cap_at_twelve_and_uncovered_fall_to_unknown(self):
        for i in range(13):
            _seed(f"R-3{i:02d}", f"会议卡 {i}", "detected", channel="meeting")
        golden_eval.cmd_build()
        llm = _FakeLLM(items=[])            # LLM 一张都没认出来
        self.assertEqual(golden_eval.cmd_classify(extractor=llm), 0)
        self.assertEqual(len(llm.calls), 2)  # 13 张 -> 12 + 1 两批
        for c in golden_eval._load_cards():
            self.assertEqual(c["provenance"], "unknown")
            self.assertEqual(c["speaker"], "unknown")

    def test_garbage_llm_output_degrades_to_unknown(self):
        _seed("R-201", "会议卡", "delivered", channel="meeting")
        golden_eval.cmd_build()
        llm = _FakeLLM(raw="definitely not json {{{")
        self.assertEqual(golden_eval.cmd_classify(extractor=llm), 0)
        (card,) = golden_eval._load_cards()
        self.assertEqual(card["provenance"], "unknown")
        self.assertEqual(card["speaker"], "unknown")

    def test_garbage_field_values_normalize_to_unknown(self):
        _seed("R-201", "会议卡", "delivered", channel="meeting")
        golden_eval.cmd_build()
        llm = _FakeLLM(items=[
            {"id": "R-201", "provenance": "SCREENSHOT!!", "speaker": None}])
        golden_eval.cmd_classify(extractor=llm)
        (card,) = golden_eval._load_cards()
        self.assertEqual(card["provenance"], "unknown")
        self.assertEqual(card["speaker"], "unknown")

    def test_classify_without_build_fails_cleanly(self):
        llm = _FakeLLM()
        self.assertEqual(golden_eval.cmd_classify(extractor=llm), 1)
        self.assertEqual(llm.calls, [])


class GoldenScoreTestCase(unittest.TestCase):
    def setUp(self):
        _clean_state()
        self.addCleanup(_clean_state)

    def test_confusion_matrix(self):
        _seed("R-401", "误杀的真卡", "delivered", channel="meeting")
        _seed("R-402", "正确拦截的噪音", "trashed", channel="meeting")
        _seed("R-403", "放行的真卡", "delivered", channel="meeting")
        _seed("R-404", "漏放的噪音", "trashed", channel="gmail")
        _seed("R-405", "未定论现役卡", "card_sent", channel="meeting")
        golden_eval.cmd_build()
        golden_eval.cmd_classify(extractor=_FakeLLM(items=[
            {"id": "R-401", "provenance": "screen", "speaker": "human"},
            {"id": "R-402", "provenance": "screen", "speaker": "assistant"},
            {"id": "R-403", "provenance": "audio", "speaker": "human"},
            {"id": "R-405", "provenance": "screen", "speaker": "human"},
        ]))
        self.assertEqual(golden_eval.cmd_score(), 0)
        report = json.loads((config.STATE_DIR / "golden" / "report.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(report["total"], 5)
        # 误杀：REAL + screen/human -> CORROBORATE 被拦，列出 id+title
        self.assertEqual(report["real_blocked"]["count"], 1)
        self.assertEqual(report["real_blocked"]["cards"],
                         [{"id": "R-401", "title": "误杀的真卡"}])
        # 正确拦截：NOISE + screen/assistant 被拦
        self.assertEqual(report["noise_blocked"]["count"], 1)
        # 放行的 REAL：audio/human -> FULL 出生
        self.assertEqual(report["real_born"], 1)
        # 漏放的 NOISE：gmail = channel_api 恒出生
        self.assertEqual(report["noise_born"]["count"], 1)
        self.assertEqual(report["noise_born"]["cards"][0]["id"], "R-404")
        # PENDING 单列，不进上面四格
        self.assertEqual(report["pending"]["count"], 1)
        self.assertEqual(report["born"], 2)      # R-403 + R-404
        self.assertEqual(report["blocked"], 3)   # R-401 R-402 R-405

    def test_unclassified_meeting_card_scores_as_limited_born(self):
        # 没跑 classify（缺 provenance/speaker）-> verdict(unknown,unknown)=
        # LIMITED，按出生计——verdict 是全函数，score 绝不因缺字段崩。
        cards = [{"id": "R-501", "title": "缺分类字段",
                  "label": golden_eval.LABEL_REAL, "channel": "meeting"}]
        report = golden_eval.score_cards(cards)
        self.assertEqual(report["real_born"], 1)
        self.assertEqual(report["real_blocked"]["count"], 0)

    def test_score_without_cards_fails_cleanly(self):
        self.assertEqual(golden_eval.cmd_score(), 1)

    def test_empty_registry_pipeline_still_succeeds(self):
        # 新装机器：注册表没有卡 -> build 空集，classify/score 照常走通
        #（全零报告），不误报「先跑 build」。
        self.assertEqual(golden_eval.cmd_build(), 0)
        self.assertEqual(golden_eval.cmd_classify(extractor=_FakeLLM()), 0)
        self.assertEqual(golden_eval.cmd_score(), 0)
        report = json.loads((config.STATE_DIR / "golden" / "report.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(report["total"], 0)


class GoldenCliTestCase(unittest.TestCase):
    def setUp(self):
        _clean_state()
        self.addCleanup(_clean_state)

    def test_cli_build_subcommand(self):
        _seed("R-101", "真卡", "delivered", channel="gmail")
        self.assertEqual(golden_eval._main(["build"]), 0)
        self.assertTrue((config.STATE_DIR / "golden" / "cards.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
