"""act/lib/state_audit.py — 把「未来时间戳」从 live `state/` 里清出来（CONTRACT §82.5）。

2026-09-18 一次泄漏到 live 安装的测试跑（根因 §82.2）除了抹掉看板，还留下一批
**假时钟戳**：`state/slack_mcp.marker` = `2027-10-23T11:32:23Z`、`state/ask_history.json`
与 `state/actd.log` 里成片的 `2027-` 行。§82.4 已经让**读者**把未来戳当缺席，所以
行为层面的伤害到此为止；但那些字节还躺在真账本里，每一次人看诊断、每一次
`grep` 都要重新绕过它们。本模块是那把扫帚。

**只搬不删（宪法第 2 条）**：命中的文件整份搬进
``state/backups/quarantine-<UTC 时间戳>/``（同名已存在就加 `-2`、`-3`，永不覆盖——
口径抄 `act/lib/store2/activate.py` 的备份命名），同目录留一份 ``manifest.json``
记原路径 / sha256 / 命中的那几个戳。搬错了 `mv` 回去就是了。

**出厂只看不动**：``report()`` 是纯读，``apply()`` 才搬。CLI 默认 `report`。

**卡片一个字节都不碰（§44 单写者）**：`act/registry/` 与 `state/store2.db` 只上报、
永不搬——registry 只有 actd 主循环一个写者，旁路进程只读+回执。`state/work_seq.json`
被夹具卡抬高过的工号同理只上报：§60.2 不许把它调低。

**永不抛（宪法第 11 条）**：单个文件读不动 / 解析不了只属于它自己，整轮照走完；
退出码恒 0（这是一把诊断扫帚，不是门）。

CLI：``python3 -m act.lib.state_audit [--apply] [--home PATH] [--json]``。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional

from act.lib import config as _config
from act.lib import maintenance

#: 只扫这些后缀 + 无后缀的 `*.marker`——节流戳与小账本都在这里。`.db` / `.log` /
#: 媒体一律跳过：前两者有自己的写者与保留期，日志里的 2027 行搬走等于丢历史。
SCAN_SUFFIXES = (".marker", ".json", ".txt")

#: 只上报、永不搬的子树（§44 单写者 + §60.2 工号不可回退）。
REPORT_ONLY_NAMES = ("work_seq.json", "store2.db", "store2_truth.json")

#: 一份账本最多报几个命中戳（证据行要能读，不是 dump）。
MAX_STAMPS_PER_FILE = 5

#: 文件里认得出的 ISO 戳（`2027-10-23T11:32:23Z` / 带偏移 / 带小数秒都算）。
_STAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")

#: 一个文件最多读这么多字节——`state/` 里的账本都是小文件，真有个大家伙
#: （日志、媒体）也不该被整份读进内存（防腐 #4 的读侧同款克制）。
MAX_READ_BYTES = 2 * 1024 * 1024


def _now(now: Optional[_dt.datetime] = None) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc)


def _stamp_slug(now: _dt.datetime) -> str:
    return now.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def future_stamps(text: str, now: Optional[_dt.datetime] = None) -> List[str]:
    """`text` 里比「现在」还晚的 ISO 戳（去重、保持出现顺序、封顶）。"""
    ref = _now(now)
    hits: List[str] = []
    for raw in _STAMP_RE.findall(text):
        if raw in hits:
            continue
        parsed = maintenance.parse_iso(raw)
        if parsed is not None and maintenance.in_future(parsed, ref):
            hits.append(raw)
        if len(hits) >= MAX_STAMPS_PER_FILE:
            break
    return hits


def _read(path: Path) -> Optional[str]:
    """小文本文件的内容；读不动 / 太大 / 不是文本 → None（永不抛）。"""
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _candidates(state_dir: Path) -> Iterable[Path]:
    """`state/` 下该看一眼的文件（backups/ 自己跳过——那是隔离区）。"""
    try:
        entries = sorted(state_dir.rglob("*"))
    except OSError:
        return []
    out = []
    for path in entries:
        if "backups" in path.parts or not path.is_file():
            continue
        if path.suffix in SCAN_SUFFIXES:
            out.append(path)
    return out


def report(home: Optional[Path] = None, now: Optional[_dt.datetime] = None) -> dict:
    """纯读：`{"home", "scanned", "findings": [{path, stamps, report_only}]}`。"""
    root = Path(home) if home else _config.HOME
    state_dir = root / "state"
    findings = []
    scanned = 0
    for path in _candidates(state_dir):
        scanned += 1
        text = _read(path)
        if not text:
            continue
        stamps = future_stamps(text, now)
        if stamps:
            findings.append({
                "path": str(path),
                "rel": str(path.relative_to(root)),
                "stamps": stamps,
                "report_only": path.name in REPORT_ONLY_NAMES,
            })
    return {"home": str(root), "scanned": scanned, "findings": findings}


def _quarantine_dir(root: Path, now: _dt.datetime) -> Path:
    """`state/backups/quarantine-<ts>[-n]/`——已存在就换个名，永不覆盖。"""
    base = root / "state" / "backups"
    slug = "quarantine-%s" % _stamp_slug(now)
    target = base / slug
    for n in range(2, 100):
        if not target.exists():
            break
        target = base / ("%s-%d" % (slug, n))
    return target


def _sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _move_all(movable: list, target: Path) -> tuple:
    """(manifest 行, 搬成了的相对路径)。搬不动的那一份只记在它自己的 `error` 上。"""
    manifest, moved = [], []
    for finding in movable:
        src = Path(finding["path"])
        digest = _sha256(src)
        try:
            shutil.move(str(src), str(target / src.name))
        except OSError as exc:
            finding["error"] = str(exc)
            continue
        manifest.append({"rel": finding["rel"], "sha256": digest,
                         "stamps": finding["stamps"]})
        moved.append(finding["rel"])
    return manifest, moved


def _write_manifest(target: Path, root: Path, ref: _dt.datetime,
                    manifest: list) -> Optional[str]:
    """回执落盘；失败返回原因字符串（文件已经搬走了，这一步失败不该反噬）。"""
    try:
        (target / "manifest.json").write_text(
            json.dumps({"at": _stamp_slug(ref), "home": str(root),
                        "moved": manifest}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError as exc:
        return str(exc)
    return None


def apply(home: Optional[Path] = None, now: Optional[_dt.datetime] = None) -> dict:
    """把命中的文件搬进隔离区并落 manifest；report-only 的那些只留在报告里。"""
    ref = _now(now)
    root = Path(home) if home else _config.HOME
    found = report(root, ref)
    movable = [f for f in found["findings"] if not f["report_only"]]
    found["quarantined"] = []
    found["quarantine_dir"] = None
    if not movable:
        return found

    target = _quarantine_dir(root, ref)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        found["error"] = "quarantine dir: %s" % exc
        return found

    manifest, found["quarantined"] = _move_all(movable, target)
    found["quarantine_dir"] = str(target)
    problem = _write_manifest(target, root, ref, manifest)
    if problem:
        found["error"] = "manifest: %s" % problem
    return found


def _print_human(result: dict) -> None:
    findings = result["findings"]
    print("state_audit: home=%s scanned=%d future-stamped=%d"
          % (result["home"], result["scanned"], len(findings)))
    for f in findings:
        tag = " (report-only, §44/§60.2)" if f["report_only"] else ""
        print("  %s%s  %s" % (f["rel"], tag, ", ".join(f["stamps"])))
    if result.get("quarantine_dir"):
        print("quarantined %d file(s) -> %s"
              % (len(result["quarantined"]), result["quarantine_dir"]))
    elif findings:
        print("nothing moved (report mode) — re-run with --apply to quarantine")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m act.lib.state_audit",
        description="未来时间戳体检：默认只看，--apply 才搬进 state/backups/（CONTRACT §82.5）")
    ap.add_argument("--apply", action="store_true",
                    help="把命中的文件搬进隔离区（原文件不删，只是换了位置）")
    ap.add_argument("--home", help="AIASSISTANT_HOME（默认 = config.HOME）")
    ap.add_argument("--json", action="store_true", help="一行 JSON 而不是人话")
    args = ap.parse_args(argv)

    home = Path(args.home).expanduser() if args.home else None
    result = apply(home) if args.apply else report(home)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":       # pragma: no cover - CLI 入口
    sys.exit(main())
