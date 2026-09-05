"""doctor 探针家族：§18 cron 链（CONTRACT §25 行目录；§17 D19 digest 行；
§23 ``cron=skipped_tcc``；``state/cron_probe.json`` FDA 探针）。

行：``cron ingest chain`` / ``cron write access``（最近一次 install.sh 改写
crontab 被 TCC 拒）/ ``cron digest``（legacy ``--now`` 形 WARN）/
``cron disk access``（cron 链自己写的 FDA 探针：新鲜且 read_ok=false 才 FAIL
``cron_fda_blocked``——#1 静默失败的唯一诚实信号）。macOS only。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from typing import List, Optional

from act.lib import config
from act.lib.checks.core import (FAIL, OK, WARN, CheckResult, pick,
                                 pinned_interpreter)

# cron ingest chain fires every 30 min; a probe older than this means either
# the chain stopped firing or it comes from an install predating the probe.
CRON_PROBE_FRESH_SECONDS = 2 * 3600
CRON_PROBE_PATH = config.STATE_DIR / "cron_probe.json"
# §17 D19: the installer's digest line never passes --now; a crontab line
# that does is the pre-D19 Monday form (or a hand edit) and forces a card
# every fire, past digest.frequency.
_LEGACY_DIGEST_NOW_RE = re.compile(r"act\.digest\s+--now\b")

_ROW = "cron disk access"


def _install_report_cron_status() -> str:
    """state/install_report.json 里 cron step 的 status；读不了/没有 = ""。"""
    try:
        data = json.loads((config.STATE_DIR / "install_report.json")
                          .read_text(encoding="utf-8"))
        for step in data.get("steps", []):
            if isinstance(step, dict) and step.get("name") == "cron":
                return str(step.get("status") or "")
    except Exception:  # noqa: BLE001 - 探针不许崩（宪法第 11 条）
        pass
    return ""


def cron_write_access_rows() -> List[CheckResult]:
    """§23 `cron=skipped_tcc`：最近一次 install.sh 想改写 crontab 但被 TCC 拒了
    （launchd 会话，Operation not permitted）——crontab 里的行可能是旧的，而
    ``check_cron`` 的两行按内容 pattern 判断，旧行照样匹配、照样绿。这行是唯一
    的窗口；下一次改写成功（cron=ok）它自动消失。2026-09-02 v0.48.12 实战。"""
    if _install_report_cron_status() != "skipped_tcc":
        return []
    daemon_py = pinned_interpreter() or "the daemon python (config/runtime.json)"
    return [CheckResult(
        "cron write access", WARN,
        pick("上次 install.sh 改写 crontab 被拒（Operation not permitted——launchd "
             "会话缺 Full Disk Access）；§18 的 cron 行可能停在旧版本",
             "last install.sh could not rewrite the crontab (Operation not permitted - "
             "the launchd session lacks Full Disk Access); the §18 cron lines may be stale"),
        pick("系统设置 > 隐私与安全性 > 完全磁盘访问权限：给 %s 打开，然后 bash "
             "install.sh。在终端里跑通不算数——Terminal 自带 FDA，launchd 会话没有",
             "System Settings > Privacy & Security > Full Disk Access: enable %s, then "
             "bash install.sh. A terminal-run install proving it works proves nothing - "
             "Terminal has its own FDA, the launchd session does not") % daemon_py,
    ).with_failure("cron_tcc_blocked")]


def _ingest_chain_row(text: str) -> CheckResult:
    if "screenpipe-export.sh" in text:
        return CheckResult("cron ingest chain", OK, "installed (CONTRACT §18)")
    return CheckResult(
        "cron ingest chain", FAIL,
        "missing from crontab - screen captures never become vault notes or radar cards",
        "bash install.sh (reinstalls the §18 cron lines)",
    ).with_failure("cron_missing")


def _digest_row(text: str) -> CheckResult:
    digest_lines = [ln for ln in text.splitlines()
                    if "act.digest" in ln and not ln.lstrip().startswith("#")]
    if any(_LEGACY_DIGEST_NOW_RE.search(ln) for ln in digest_lines):
        # §17 D19: a crontab line that still passes --now is the pre-D19
        # Monday form — --now bypasses the cadence gate, so this line forces
        # a card every fire no matter what digest.frequency says (default
        # off). Calling it "installed" here would be the lie the knob exists
        # to end; only `bash install.sh` replaces the line.
        return CheckResult(
            "cron digest", WARN,
            "legacy `act.digest --now` line - forces a card every fire, "
            "ignoring digest.frequency (default off)",
            "bash install.sh (replaces it with the daily self-gated line)",
        ).with_failure("cron_missing")
    if digest_lines:
        # the line fires daily; the cadence (default off) lives in config,
        # so "installed" says nothing about whether cards appear.
        return CheckResult(
            "cron digest", OK,
            "installed (daily 09:07; cadence = digest.frequency)")
    return CheckResult(
        "cron digest", WARN,
        "digest line missing from crontab",
        "bash install.sh",
    ).with_failure("cron_missing")


def check_cron(probes):
    text = probes.crontab()
    results = [_ingest_chain_row(text)]
    results.extend(cron_write_access_rows())
    results.append(_digest_row(text))
    results.append(check_cron_probe(probes, cron_installed="screenpipe-export.sh" in text))
    return results


# --------------------------------------------------------------------------- #
# cron disk access (state/cron_probe.json)
# --------------------------------------------------------------------------- #
def _no_probe_row(cron_installed: bool) -> CheckResult:
    if not cron_installed:
        return CheckResult(
            _ROW, WARN,
            pick("还没有探针数据（cron 链尚未安装）",
                 "no probe data (cron chain not installed yet)"),
            pick("bash install.sh，然后等 ~30 分钟让 cron 跑第一轮",
                 "bash install.sh, then wait ~30 min for the first cron run"))
    return CheckResult(
        _ROW, WARN,
        pick("还没有探针数据——装上这个版本后 cron 链还没跑过",
             "no probe yet - the cron chain has not run since this version was installed"),
        pick("重跑 bash install.sh（更新 cron 行），然后等 ~30 分钟",
             "rerun bash install.sh (updates the cron line), then wait ~30 min"))


def _read_probe() -> Optional[tuple]:
    """(ts, read_ok, protected_path) or None when the file is torn / hand-edited.

    schema 降级（read_ok 缺键/非 bool——半截/手改/旧版文件）与整体损坏同级
    处理：WARN unreadable，绝不据此给出「FDA 被禁」的红色确定性诊断 + 授权指引
    （shell writer 只写字面量 true/false）。"""
    try:
        data = json.loads(CRON_PROBE_PATH.read_text(encoding="utf-8"))
        ts = _dt.datetime.strptime(str(data.get("ts", "")), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
        read_ok = data.get("read_ok")
        if not isinstance(read_ok, bool):
            raise ValueError("read_ok missing or not a bool")
        return ts, read_ok, str(data.get("protected_path") or "")
    except Exception:  # noqa: BLE001 - torn/hand-edited file is the symptom
        return None


def _probe_verdict_row(probes, ts: float, read_ok: bool, probed: str) -> CheckResult:
    age = probes.now() - ts
    if age > CRON_PROBE_FRESH_SECONDS:
        return CheckResult(
            _ROW, WARN,
            "last cron probe %dh ago - the cron chain looks stopped" % int(age // 3600),
            "bash install.sh (reinstalls the cron lines); check crontab -l",
        ).with_failure("cron_missing")
    if not read_ok:
        return CheckResult(
            _ROW, FAIL,
            "cron CANNOT read %s - macOS Full Disk Access is blocking it; "
            "captures are silently lost" % (probed or "the vault"),
            # the board's Settings > dependency check row 「定时任务磁盘权限」 has the guided
            # 「去授权」 (copies /usr/sbin/cron + opens the pane) and prints the click-by-click
            # steps; name that surface, not the retired native app's page
            "System Settings > Privacy & Security > Full Disk Access > '+' > "
            "Cmd+Shift+G > /usr/sbin/cron (or click Grant on the board's Settings > "
            "dependency check 'Cron disk access' row: it copies the path and opens the pane)",
        ).with_failure("cron_fda_blocked")
    return CheckResult(_ROW, OK,
                       "cron read %s ok (probe %d min ago)" % (probed, int(age // 60)))


def check_cron_probe(probes, cron_installed: bool):
    """The cron FDA probe (§25): every cron chain run writes state/cron_probe.json
    with a real read attempt against the protected export target. This is the
    ONLY honest signal for the #1 silent failure — cron blocked by missing
    Full Disk Access writes nothing into ~/Documents and reports nothing.
    """
    if not CRON_PROBE_PATH.exists():
        return _no_probe_row(cron_installed)
    probe = _read_probe()
    if probe is None:
        return CheckResult(
            _ROW, WARN,
            pick("state/cron_probe.json 读不出来——等下一轮 cron 再看",
                 "state/cron_probe.json unreadable - wait for the next cron run"),
            pick("如果一直读不出来：重跑 bash install.sh",
                 "if it stays unreadable: rerun bash install.sh"))
    return _probe_verdict_row(probes, *probe)
