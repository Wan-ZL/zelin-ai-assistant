"""Gmail radar 走统一 triage gate (v0.18.1 B1) — act/radar_gmail.scan().

Gmail 曾是唯一绕过 v0.17 统一三选一闸门的雷达：旧 scan() 对每个 LLM 提取项
无条件 merge_or_new(status="card_sent")，永远进提案列，也没有 ignore/lineage。
现在它和 radar_slack 一样，先过 quick_capture.triage → apply_triage 再落库。
全部用注入的 fake extractor（绝不 spawn 真 claude）+ 注入的 fetcher（不碰
IMAP），跑在 sandbox AIASSISTANT_HOME（tests/__init__.py）里。钉住的契约：

- new_proposal(high)  -> 一张 card_sent 提案卡（gate 被咨询过）；
- new_proposal(low)   -> 一张 detected/备选卡（triage 降级预设的 card_sent）；
- ignore              -> 零卡（纯 FYI 邮件不再无条件成卡）；
- relates_to 开卡      -> 折叠为备注 + 来源，不出新卡；
- 同一封邮件出现两次   -> 标题去重（merge_or_new），只留一张卡。
"""
import json
import shutil
import subprocess
import unittest

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar_gmail
from act.lib import config, registry


def _proc(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr="")


class _FakeLLM:
    """One injectable for BOTH calls of a Gmail pass: extraction + triage.

    Triage prompts are recognized by the shared gate's ``入库把关`` marker and
    get ``decision``; everything else is an extraction prompt and gets the
    JSON-encoded ``extraction`` array. Records prompts for assertions.
    """

    def __init__(self, extraction=None, decision=None):
        self.extraction = extraction if extraction is not None else []
        self.decision = decision
        self.calls: list[str] = []
        self.triage_calls: list[str] = []

    def __call__(self, prompt: str):
        self.calls.append(prompt)
        if "入库把关" in prompt:                     # the shared triage prompt
            self.triage_calls.append(prompt)
            if self.decision is None:               # legacy shape -> fallback
                return _proc(json.dumps(self.extraction, ensure_ascii=False))
            return _proc(json.dumps(self.decision, ensure_ascii=False))
        return _proc(json.dumps(self.extraction, ensure_ascii=False))


def _seed(req_id, title, status, **kw) -> registry.Requirement:
    r = registry.Requirement(id=req_id, title=title, status=status, **kw)
    registry.save(r)
    return r


def _clean_state():
    config.ensure_state_dirs()
    if config.REGISTRY_DIR.exists():
        shutil.rmtree(config.REGISTRY_DIR)
    p = config.STATE_DIR / radar_gmail.STATE_FILE
    if p.exists():
        p.unlink()
    if config.CONFIG_PATH.exists():
        config.CONFIG_PATH.unlink()


MSG = {"uid": 42, "from": "manager@corp.com", "subject": "季度报告",
       "date": "Fri, 11 Jul 2026 09:00:00 +0000",
       "message_id": "<abc@mail>", "body": "please send the Q3 report"}


class GmailTriageTestCase(unittest.TestCase):
    def setUp(self):
        _clean_state()
        self.addCleanup(_clean_state)
        self.cfg = config.Config()
        # fetcher is injected, so connect_ex is never reached; still, scan()
        # early-returns unless an app password resolves — stub it truthy.
        self._orig_pw = radar_gmail.get_app_password
        radar_gmail.get_app_password = lambda cfg=None: "app-pw"
        self.addCleanup(setattr, radar_gmail, "get_app_password", self._orig_pw)

    def _extraction(self, **over) -> list:
        item = {"summary": "回复 manager 的季度报告请求", "type": "comms",
                "tier": "T1", "needs_reply": True, "plan": ["起草回复"],
                "from": MSG["from"], "subject": MSG["subject"],
                "message_id": MSG["message_id"]}
        item.update(over)
        return [item]

    def _scan(self, llm, messages=None) -> int:
        msgs = [MSG] if messages is None else messages
        newest = max((m["uid"] for m in msgs), default=0)
        return radar_gmail.scan(
            self.cfg,
            fetcher=lambda cfg, last_uid: (list(msgs), newest),
            extractor=llm)

    def test_high_confidence_new_proposal_files_card_sent(self):
        llm = _FakeLLM(extraction=self._extraction(),
                       decision={"action": "new_proposal", "confidence": "high"})
        self.assertEqual(self._scan(llm), 1)
        self.assertEqual(len(llm.triage_calls), 1)   # gate WAS consulted
        (req,) = registry.load_all()
        self.assertEqual(req.status, "card_sent")
        self.assertEqual(req.sources[0]["channel"], "gmail")

    def test_low_confidence_new_proposal_lands_in_backlog(self):
        llm = _FakeLLM(extraction=self._extraction(),
                       decision={"action": "new_proposal", "confidence": "low"})
        self.assertEqual(self._scan(llm), 1)
        (req,) = registry.load_all()
        self.assertEqual(req.status, "detected")     # 备选, not 提案, not lost

    def test_informational_mail_files_no_card(self):
        llm = _FakeLLM(extraction=self._extraction(),
                       decision={"action": "ignore", "reason": "纯 FYI 通知"})
        self.assertEqual(self._scan(llm), 0)
        self.assertEqual(registry.load_all(), [])
        self.assertEqual(len(llm.triage_calls), 1)   # gate WAS consulted

    def test_relates_to_open_card_folds_no_new_card(self):
        _seed("R-030", "季度报告", "card_sent",
              sources=[{"who": "manager", "channel": "meeting",
                        "date": "2026-07-01", "quote": "季度报告"}])
        llm = _FakeLLM(extraction=self._extraction(),
                       decision={"action": "relates_to", "req": "R-030",
                                 "note": "邮件又催了一遍", "needs_action": True})
        self.assertEqual(self._scan(llm), 0)         # folded, not a new card
        self.assertEqual(len(registry.load_all()), 1)
        self.assertIn("[radar] 邮件又催了一遍", registry.load("R-030").notes)

    def test_same_message_twice_dedupes_to_one_card(self):
        decision = {"action": "new_proposal", "confidence": "high"}
        self.assertEqual(
            self._scan(_FakeLLM(extraction=self._extraction(), decision=decision)), 1)
        # second pass sees the same mail again -> title-based merge_or_new folds
        # it into the existing card instead of producing a duplicate.
        self._scan(_FakeLLM(extraction=self._extraction(), decision=decision))
        cards = [r for r in registry.load_all() if r.status == "card_sent"]
        self.assertEqual(len(cards), 1)


if __name__ == "__main__":
    unittest.main()
