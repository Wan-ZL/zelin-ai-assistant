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
import shlex
import shutil
import subprocess
import sys
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


class GmailCommandBackendTestCase(unittest.TestCase):
    """§14bis fetch_command 后备通道 — fetch_via_command 契约 + scan 接线.

    钉住的契约：无 app password 时命令通道照样产卡（这正是它存在的意义）；
    uid <= marker 丢弃但仍推进 marker；命令挂掉/输出不是 JSON 数组 ->
    (None, 原 marker, 错误码)，绝不与「没有新邮件」混淆；noreply 预过滤
    在 dict 层生效。真 subprocess（sandbox 里的小 python 脚本），不 mock。
    """

    def setUp(self):
        _clean_state()
        self.addCleanup(_clean_state)
        self.cfg = config.Config()
        # command mode must work WITHOUT an app password — that's the point.
        self._orig_pw = radar_gmail.get_app_password
        radar_gmail.get_app_password = lambda cfg=None: None
        self.addCleanup(setattr, radar_gmail, "get_app_password", self._orig_pw)

    def _script(self, body: str) -> str:
        """Drop a python fetcher into the sandbox; return its fetch_command."""
        p = config.STATE_DIR / "fake_fetcher.py"
        p.write_text(body, encoding="utf-8")
        return f"{shlex.quote(sys.executable)} {shlex.quote(str(p))}"

    def test_scan_via_command_files_card_without_password(self):
        mail = dict(MSG, gmail_thread_id="777")
        cmd = self._script(
            "import json, os, pathlib\n"
            # side-channel the env marker so the test can assert it was passed
            f"pathlib.Path({str(config.STATE_DIR / 'seen_uid.txt')!r})"
            ".write_text(os.environ['GMAIL_RADAR_LAST_UID'])\n"
            f"print(json.dumps([{mail!r}]))\n")
        self.cfg.gmail_fetch_command = cmd
        llm = _FakeLLM(
            extraction=[{"summary": "回复 manager 的季度报告请求", "type": "comms",
                         "tier": "T1", "needs_reply": True, "plan": [],
                         "from": MSG["from"], "subject": MSG["subject"],
                         "message_id": MSG["message_id"]}],
            decision={"action": "new_proposal", "confidence": "high"})
        self.assertEqual(radar_gmail.scan(self.cfg, extractor=llm), 1)
        (req,) = registry.load_all()
        self.assertEqual(req.sources[0]["channel"], "gmail")
        self.assertEqual(req.sources[0]["gmail_thread_id"], "777")
        # marker advanced to the fetched uid; env carried the previous marker
        self.assertEqual(radar_gmail._load_last_uid(), MSG["uid"])
        self.assertEqual(
            (config.STATE_DIR / "seen_uid.txt").read_text(), "0")

    def test_uid_at_or_below_marker_dropped_but_marker_advances(self):
        old = dict(MSG, uid=41, subject="旧邮件")
        new = dict(MSG, uid=43, subject="新邮件")
        cmd = self._script(
            f"import json\nprint(json.dumps([{old!r}, {MSG!r}, {new!r}]))\n")
        radar_gmail._save_last_uid(42)
        msgs, newest, err = radar_gmail.fetch_via_command(cmd, 42)
        self.assertIsNone(err)
        self.assertEqual(newest, 43)
        self.assertEqual([m["uid"] for m in msgs], [43])

    def test_command_failure_is_not_empty_mailbox(self):
        cmd = self._script("import sys\nsys.exit(2)\n")
        msgs, newest, err = radar_gmail.fetch_via_command(cmd, 7)
        self.assertIsNone(msgs)
        self.assertEqual(newest, 7)
        self.assertEqual(err, "command_failed")
        # scan(): failed command -> 0 cards, marker untouched
        radar_gmail._save_last_uid(7)
        self.cfg.gmail_fetch_command = cmd
        self.assertEqual(radar_gmail.scan(self.cfg, extractor=_FakeLLM()), 0)
        self.assertEqual(radar_gmail._load_last_uid(), 7)

    def test_garbage_stdout_is_bad_output(self):
        cmd = self._script("print('definitely not json')\n")
        msgs, _newest, err = radar_gmail.fetch_via_command(cmd, 0)
        self.assertIsNone(msgs)
        self.assertEqual(err, "command_bad_output")

    def test_noreply_prefilter_applies_to_dict_messages(self):
        noise = dict(MSG, uid=50, **{"from": "noreply@spam.io"})
        cmd = self._script(f"import json\nprint(json.dumps([{noise!r}]))\n")
        msgs, newest, err = radar_gmail.fetch_via_command(cmd, 0)
        self.assertIsNone(err)
        self.assertEqual(msgs, [])          # filtered out…
        self.assertEqual(newest, 50)        # …but still advances the marker

    def test_no_password_and_no_command_still_noops(self):
        self.cfg.gmail_fetch_command = None
        self.assertEqual(radar_gmail.scan(self.cfg, extractor=_FakeLLM()), 0)
        self.assertEqual(registry.load_all(), [])


if __name__ == "__main__":
    unittest.main()
