"""act/lib/state_audit.py — 把「未来时间戳」从 live `state/` 里清出来（CONTRACT §82.5）。

2026-09-18 一次泄漏到 live 安装的测试跑（根因 §82.2）除了抹掉看板，还留下一批
**假时钟戳**：`state/slack_mcp.marker` = `2027-10-23T11:32:23Z`、`state/ask_history.json`
与 `state/actd.log` 里成片的 `2027-` 行。§82.4 已经让**读者**把未来戳当缺席，所以
行为层面的伤害到此为止；但那些字节还躺在真账本里，每一次人看诊断、每一次
`grep` 都要重新绕过它们。本模块是那把扫帚。

**只搬不删（宪法第 2 条）**：命中的文件整份搬进
``state/backups/quarantine-<UTC 时间戳>/``（同名已存在就加 `-2`、`-3`，永不覆盖——
口径抄 `act/lib/store2/activate.py` 的备份命名），里面**按原相对路径铺开**，
隔离区**根目录**留一份 ``manifest.json`` 记原相对路径 / sha256 / 命中的那几个戳
（铺开之后被隔离的文件各在自己的子目录里，与这份回执不同层——正是这一点让源文件
名恰好是 `manifest.json` 时也挤不掉它）。搬错了 `mv` 回去就是了。

**出厂只看不动**：``report()`` 是纯读，``apply()`` 才搬。CLI 默认 `report`。

**卡片与工号一个字节都不碰（§44 单写者）**：`state/work_seq.json` 与
`state/store2_truth.json` 只上报、永不搬——registry 只有 actd 主循环一个写者，
旁路进程只读+回执；被夹具卡抬高过的工号按 §60.2 不许调低。`act/registry/` 与
`state/store2.db` 则连看都不看（见下面 SCAN_SUFFIXES：不扫、也不报）。

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

#: 只上报、永不搬的账本（§44 单写者 + §60.2 工号不可回退）。`store2.db` 今天落在
#: SCAN_SUFFIXES 之外因而不可达——留着是后缀集合将来扩张时的第二道墙，它只可能
#: 阻止一次搬运、不可能促成一次。
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


def _is_future(raw: str, ref: _dt.datetime) -> bool:
    """这一串认得出来、而且比 `ref` 还晚（解析不了 = 不算命中，不是证据）。"""
    parsed = maintenance.parse_iso(raw)
    return parsed is not None and maintenance.in_future(parsed, ref)


def future_stamps(text: str, now: Optional[_dt.datetime] = None) -> List[str]:
    """`text` 里比「现在」还晚的 ISO 戳（去重、保持出现顺序、封顶）。"""
    ref = _now(now)
    hits: List[str] = []
    for raw in _STAMP_RE.findall(text):
        if raw in hits:
            continue
        if _is_future(raw, ref):
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


def _scannable(path: Path, state_dir: Path) -> bool:
    """这一份该不该读：隔离区自己跳过，目录跳过，只认白名单后缀。**永不抛。**

    **`backups` 只在 `state/` 内部数层**：判据一度是 `"backups" in path.parts`，
    拿的是整条绝对路径。于是 home 自己住在某个叫 `backups` 的目录底下时
    （`<…>/backups/aiassistant` 完全是个正常路径），`state/` 里每一份文件都被当成
    隔离区跳过，一棵脏树被报成 `scanned=0` 的干净——虚报健康（宪法第 3 条），
    而且恰恰是这把扫帚存在的理由。改成数 `state/` 内部的层数之后，「隔离区里再
    深的嵌套下一轮也不重扫」这条仍然成立（隔离区就在 `state/backups/` 底下）。

    **`is_file()` 会抛**：pathlib 的 `_ignore_error` 只咽 ENOENT/ENOTDIR/EBADF/
    ELOOP，**EACCES 是往外抛的**。一个「列得出、stat 不了」的目录（`chmod 0444`：
    可读、不可执行）里的文件因此能把整轮体检变成 traceback——`0000` / `0111` 反而
    没事，因为那两种 `rglob` 自己就列不出来。一份权限怪的文件只属于它自己
    （宪法第 11 条）。
    """
    try:
        rel = path.relative_to(state_dir)
    except ValueError:                    # 不在 state/ 底下 → 与本轮无关
        return False
    if "backups" in rel.parts:
        return False
    if path.suffix not in SCAN_SUFFIXES:
        return False
    try:
        return path.is_file()
    except OSError:
        return False


def _candidates(state_dir: Path) -> Iterable[Path]:
    """`state/` 下该看一眼的文件（`state/` 内部带 `backups` 的跳过——那是隔离区）。

    列目录本身失败（权限、竞态下被删）只让这一轮扫到空，不抛（宪法第 11 条）；
    逐份的判决也不许抛，所以 `_scannable` 是个全函数（见它自己的 docstring）。
    """
    try:
        entries = sorted(state_dir.rglob("*"))
    except OSError:
        return []
    return [path for path in entries if _scannable(path, state_dir)]


def _root(home: Optional[Path]) -> Path:
    """本轮体检的 home；`None` = 这个进程自己的 `config.HOME`。"""
    return Path(home) if home else _config.HOME


def report(home: Optional[Path] = None, now: Optional[_dt.datetime] = None) -> dict:
    """纯读：`{"home", "scanned", "findings": [{path, stamps, report_only}]}`。"""
    root = _root(home)
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


def _quarantine_dir(root: Path, now: _dt.datetime) -> Optional[Path]:
    """`state/backups/quarantine-<ts>[-n]/`——已存在就换个名；**找不到空名就 `None`**。

    原来的循环 `for n in range(2, 100)` 在 99 个同秒候选全被占掉时是**掉出去**的，
    带着一个仍然存在的 `target` 回去，`mkdir(exist_ok=True)` 照样成功，于是这一轮
    搬进上一轮的隔离区、连 manifest 一起盖掉。够到那儿要同一秒里 `--apply` 99 次，
    实际到不了；但 §82.5 写的「永不覆盖」是无条件的，而**铺开 `rel` 之后盖的是
    同名相对路径**，正是本轮刚禁掉的那次不可恢复删除。宁可报「建不出隔离区」。
    """
    base = root / "state" / "backups"
    slug = "quarantine-%s" % _stamp_slug(now)
    for n in range(1, 100):
        target = base / (slug if n == 1 else "%s-%d" % (slug, n))
        if not target.exists():
            return target
    return None


def _sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _move_all(movable: list, target: Path) -> tuple:
    """(manifest 行, 搬成了的相对路径)。搬不动的那一份只记在它自己的 `error` 上。

    **按 `rel` 原样铺开，不拍平到 basename**：`state/` 是一棵树（`inbox/`、
    `fold_receipts/`、`notify_queue/`……），两个子目录下同名的账本拍平之后
    `shutil.move` 在 POSIX 上退化成 `os.rename`，后一份直接盖掉前一份的字节——
    manifest 还照记两行两个 sha256，等于回执在撒谎。那是一次**不可恢复的
    自动删除**（宪法第 2 条，§82.5 「只搬不删」），比它要治的病更重。铺开之后
    隔离区里的相对路径自己就是原位置，`manifest.json` 也永远被挤不掉。
    """
    manifest, moved = [], []
    for finding in movable:
        src = Path(finding["path"])
        digest = _sha256(src)
        dst = target / finding["rel"]
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
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


def _make_quarantine(root: Path, ref: _dt.datetime) -> tuple:
    """`(隔离区目录, None)`；建不出来就 `(None, 原因)`——一个字节都还没搬。

    `exist_ok=False`：让「搬进一个已经有东西的隔离区」在结构上不可能，而不是靠
    上面那个循环选对了名字。两者之间还有一道竞态（选名与 mkdir 之间有人建了同名），
    这样写会把它报成错误而不是静静复用。
    """
    target = _quarantine_dir(root, ref)
    if target is None:
        return None, "quarantine dir: 99 same-second names all taken"
    try:
        target.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return None, "quarantine dir: %s" % exc
    return target, None


def apply(home: Optional[Path] = None, now: Optional[_dt.datetime] = None) -> dict:
    """把命中的文件搬进隔离区并落 manifest；report-only 的那些只留在报告里。"""
    ref = _now(now)
    root = _root(home)
    found = report(root, ref)
    movable = [f for f in found["findings"] if not f["report_only"]]
    found["quarantined"] = []
    found["quarantine_dir"] = None
    if not movable:
        return found

    target, problem = _make_quarantine(root, ref)
    if problem:
        found["error"] = problem
        return found

    manifest, found["quarantined"] = _move_all(movable, target)
    found["quarantine_dir"] = str(target)
    trouble = _write_manifest(target, root, ref, manifest)
    if trouble:
        found["error"] = "manifest: %s" % trouble
    return found


def _print_finding(finding: dict) -> None:
    tag = " (report-only, §44/§60.2)" if finding["report_only"] else ""
    print("  %s%s  %s" % (finding["rel"], tag, ", ".join(finding["stamps"])))
    if finding.get("error"):
        print("    ! 搬不动：%s" % finding["error"])


def _outcome_line(result: dict, applied: bool) -> Optional[str]:
    """搬运那一段的一句话结论；`None` = 上面已经说完了，不必再补一句。

    劝 `--apply` 的前提是 `--apply` **真会搬走点什么**。owner live 装机的稳态恰恰
    不是：命中只剩 `work_seq.json` 这类 report-only 项（§44/§60.2 永久欠账），此时
    默认那一跑再劝一次 `--apply`，换来的是一个可证明的空操作——和 `--apply` 之后
    劝他再跑一次 `--apply` 是同一种虚报（宪法第 3 条）。判据看的是有没有可搬项，
    不是这一跑带没带 `--apply`。
    """
    if result.get("quarantine_dir"):
        return ("quarantined %d file(s) -> %s"
                % (len(result["quarantined"]), result["quarantine_dir"]))
    if not result["findings"] or result.get("error"):
        return None
    if applied or not any(not f["report_only"] for f in result["findings"]):
        return "nothing moved — every finding is report-only (§44/§60.2)"
    return "nothing moved (report mode) — re-run with --apply to quarantine"


def _print_human(result: dict, applied: bool = False) -> None:
    """人看的那一面。**失败必须出现在这里**：`--apply` 建不出隔离区、某一份搬不动、
    回执写不下去，机器面（`--json`）里都有，人看的这面从前一个字都不说，还会反过来
    劝他「re-run with --apply」——那是在虚报干净（宪法第 3 条）。"""
    findings = result["findings"]
    print("state_audit: home=%s scanned=%d future-stamped=%d"
          % (result["home"], result["scanned"], len(findings)))
    for finding in findings:
        _print_finding(finding)
    if result.get("error"):
        print("! %s" % result["error"])
    line = _outcome_line(result, applied)
    if line:
        print(line)


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
        _print_human(result, args.apply)
    return 0


if __name__ == "__main__":       # pragma: no cover - CLI 入口
    sys.exit(main())
