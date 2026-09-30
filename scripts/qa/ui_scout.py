#!/usr/bin/env python3
"""ui_scout 巡检报告的读者与判决（docs/CONTRACT.md §79.5 / §79.6）。

跑者住 `web/e2e/ui_scout.spec.ts`（node 侧，要浏览器）；这一份只读它落下的
`report.json`，所以它在任何一台只有 python 的机器上都跑得动：

    python3 scripts/qa/ui_scout.py --summary        # 一行摘要
    python3 scripts/qa/ui_scout.py --check          # error > 0 即退 1（默认动作）
    python3 scripts/qa/ui_scout.py --issue-plan     # 该开哪些 issue、哪些已经有了
    python3 scripts/qa/ui_scout.py --run <目录>     # 指定某一次巡检（默认最近一次）

判决口径（§79.6）：只有**确定性判官**的 error 会让退出码变 1。warn 是「值得看一眼」，
info 是驾驶员自己说的话与行程表登记的已知沙箱事实——两者都不判红，否则这份报告会在一周内
被训练成「反正都是黄的」。

去重（§79.4）：指纹由跑者算好写在每条发现里（TS 侧 `core/findings.ts` 是唯一实现，
这里只读不重算）。`--issue-plan` 拿指纹去比对已开的 issue：命中就说「已经有了，去补一条
评论」，没命中才给一份可以直接 `gh issue create` 的标题与正文。本脚本**自己永不开 issue、
永不发评论**——对外动作是 owner 的一次点击（§65）。
"""

import argparse
import json
import os
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
REPORTS_ROOT = os.path.join(REPO_ROOT, ".ui-scout", "reports")
REPORT_NAME = "report.json"

SEVERITIES = ("error", "warn", "info")
# 判红门槛：**镜像** web/e2e/ui_scout/core/findings.ts 的 FAIL_SEVERITY（那边是真源，
# 跑者的「巡检判决」也读它）；tests/test_ui_scout_pilot_protocol.py 逐字钉着两端。
FAIL_ON_DEFAULT = "error"
# issue 正文里的机读落款：`--issue-plan` 靠它认出「这条发现已经有 issue 了」。
FINGERPRINT_MARKER = "ui_scout-fingerprint:"
ISSUE_LABEL = "ui_scout"


class ReportError(Exception):
    """报告不存在、不是 JSON、或不是一份 ui_scout 报告。"""


# --------------------------------------------------------------------------- #
# 读报告
# --------------------------------------------------------------------------- #
def latest_run(root=REPORTS_ROOT):
    """最近一次巡检的目录（目录名是 ISO 时间戳，字典序即时间序）；一次都没跑过返回 None。"""
    try:
        names = sorted(name for name in os.listdir(root)
                       if os.path.isfile(os.path.join(root, name, REPORT_NAME)))
    except OSError:
        return None
    return os.path.join(root, names[-1]) if names else None


def load_report(run_dir):
    """`<run_dir>/report.json` → dict。任何读不动/形状不对都抛 ReportError（带路径）。"""
    path = os.path.join(run_dir, REPORT_NAME)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except OSError as exc:
        raise ReportError("读不到 %s：%s" % (path, exc))
    except ValueError as exc:
        raise ReportError("%s 不是合法 JSON：%s" % (path, exc))
    if not isinstance(doc, dict) or not isinstance(doc.get("findings"), list):
        raise ReportError("%s 不像一份 ui_scout 报告（缺 findings 数组）" % path)
    return doc


def findings_at(report, severity):
    """报告里某一档严重度的发现（顺序保持跑者写下的顺序）。"""
    return [item for item in report["findings"]
            if isinstance(item, dict) and item.get("severity") == severity]


def counts(report):
    """三档各多少条——从 findings 现算，不信报告自带的 counts（它可能是旧版本写的）。"""
    return dict((name, len(findings_at(report, name))) for name in SEVERITIES)


# --------------------------------------------------------------------------- #
# 摘要与判决
# --------------------------------------------------------------------------- #
def summary_line(report):
    """一行摘要，形状照 §58 家族其它门的口吻（大写动词 + key=value）。"""
    tally = counts(report)
    return ("UI_SCOUT pilot=%s journeys=%d steps=%d error=%d warn=%d info=%d" % (
        report.get("pilot", "?"),
        len(report.get("journeys") or []),
        sum(len(journey.get("steps") or []) for journey in report.get("journeys") or []),
        tally["error"], tally["warn"], tally["info"]))


def severities_upto(fail_on):
    """判红/入计划的档位集合。`never` = 空（只报告，不判红，也不给 issue 计划）。"""
    if fail_on not in SEVERITIES:
        return ()
    return SEVERITIES[:SEVERITIES.index(fail_on) + 1]


def verdict_lines(report, fail_on):
    """(退出码, 要打印的行)。`fail_on` 是最低判红档位，"never" = 只报告不判红。"""
    tally = counts(report)
    guilty = [name for name in severities_upto(fail_on) if tally[name]]
    lines = [summary_line(report)]
    for severity in SEVERITIES:
        for item in findings_at(report, severity):
            lines.append("  [%s] %s / %s: %s" % (
                severity, item.get("journey", "?"), item.get("oracle", "?"), item.get("summary", "")))
    lines.append(("ui_scout: FAIL — %s" % "、".join(guilty)) if guilty else "ui_scout: OK")
    return (1 if guilty else 0), lines


# --------------------------------------------------------------------------- #
# issue 计划（只打印，永不动手）
# --------------------------------------------------------------------------- #
def flatten(text, limit):
    """页面来的文本 → 一行。控制字符全换成空格，再截断。

    这段字节要进 issue 标题、还要被打印成一条建议粘贴的 gh 命令：控制字符在终端里能改写
    光标，换行能让「一条」命令实际上是两条。进门先拍平。
    """
    flat = "".join(" " if ch < " " or ch == "\x7f" else ch for ch in str(text or ""))
    return flat.strip()[:limit]


def issue_title(item):
    return "ui_scout: %s（%s / %s）" % (
        flatten(item.get("summary", ""), 80), item.get("journey", "?"), item.get("oracle", "?"))


def issue_body(item, report):
    """正文 = 一条发现 + 复现坐标 + 机读落款。照着就能复现，指纹让下一次跑认出它。"""
    return "\n".join([
        item.get("detail", ""),
        "",
        "- 行程：`%s`（第 %s 步）" % (item.get("journey", "?"), item.get("step", "?")),
        "- 判官：`%s`" % item.get("oracle", "?"),
        "- URL：`%s`" % item.get("url", ""),
        "- 截图：`%s`" % (item.get("screenshot") or "（这一条没有截图）"),
        "- 驾驶员：`%s`，巡检时间 %s" % (report.get("pilot", "?"), report.get("startedAt", "?")),
        "",
        "%s %s" % (FINGERPRINT_MARKER, item.get("fingerprint", "")),
    ])


def _default_gh(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=60)


def open_issue_fingerprints(runner=None):
    """已开 issue 正文里的指纹集合。gh 不可用/出错一律返回空集（宁可多提醒一次）。"""
    argv = ["gh", "issue", "list", "--state", "open", "--limit", "200",
            "--search", FINGERPRINT_MARKER, "--json", "number,title,body"]
    try:
        proc = (runner or _default_gh)(argv)
    except Exception:
        return {}
    if getattr(proc, "returncode", 1) != 0:
        return {}
    try:
        rows = json.loads(proc.stdout or "[]")
    except ValueError:
        return {}
    return _index_by_fingerprint(rows)


def body_fingerprint(body):
    """issue 正文里落款的那个指纹（`ui_scout-fingerprint: <8 位 hex>`）；没有就 None。"""
    _, marker, tail = str(body or "").partition(FINGERPRINT_MARKER)
    if not marker:
        return None
    token = tail.split()[0] if tail.split() else ""
    return token if len(token) == 8 and all(ch in "0123456789abcdef" for ch in token) else None


def _index_by_fingerprint(rows):
    out = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        token = body_fingerprint(row.get("body"))
        if token:
            out.setdefault(token, row)
    return out


def issue_plan(report, fail_on="error", runner=None):
    """[{fingerprint, title, body, existing}]——existing 是已开 issue 的那一行，或 None。"""
    known = open_issue_fingerprints(runner)
    plan = []
    for severity in severities_upto(fail_on):
        for item in findings_at(report, severity):
            fingerprint = item.get("fingerprint", "")
            plan.append({
                "fingerprint": fingerprint,
                "title": issue_title(item),
                "body": issue_body(item, report),
                "existing": known.get(fingerprint),
            })
    return plan


# 正文里的 heredoc 定界符：引号版 = 壳不做任何展开，正文里的 `$` 与反引号原样进 issue。
_HEREDOC = "ZAI_UI_SCOUT_BODY"


def _heredoc_safe(body):
    """正文里不许有一行**正好是**定界符——否则 heredoc 提前收口，后面的正文就被壳当命令读了。

    正文是页面文本拼出来的（detail 里带着元素名、报错原文），所以这不是假想：顶一个空格
    就不再是「定界符独占一行」，issue 里也几乎看不出差别。
    """
    return [(" " + line) if line.strip() == _HEREDOC else line for line in body.splitlines()]


def create_command(entry):
    """一条**可以直接粘进终端跑**的 gh 命令。

    标题走 `shlex.quote` 而不是 `json.dumps`：JSON 的引号不是壳的引号——双引号里反引号和
    `$(…)` 照样展开，而标题是从页面文本拼出来的（console.error 原文、告警文案、被裁的标签
    都能进来）。一条我们建议人去粘贴的命令，必须自己扛住这件事：正文早就用引号版 heredoc
    防住了，标题当初没有。
    """
    return ["gh issue create --label %s --title %s --body \"$(cat <<'%s'"
            % (ISSUE_LABEL, shlex.quote(entry["title"]), _HEREDOC)] \
        + _heredoc_safe(entry["body"]) + [_HEREDOC, ')"']


def plan_lines(plan):
    """计划 → 人话。已经有 issue 的说去补评论，没有的给一条可以直接跑的 gh 命令。"""
    if not plan:
        return ["ui_scout: 没有够格开 issue 的发现。"]
    lines = []
    for entry in plan:
        existing = entry["existing"]
        if existing:
            lines.append("已有 #%s —— 去那条底下补一句，别再开新的：%s"
                         % (existing.get("number", "?"), entry["title"]))
            continue
        lines.append("新开：%s" % entry["title"])
        lines.extend(create_command(entry))
        lines.append("")
    return lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser():
    parser = argparse.ArgumentParser(prog="ui_scout", description="read a ui_scout run report")
    parser.add_argument("--run", default=None, help="巡检目录（默认最近一次）")
    # 三个动作互斥：`--check --summary` 从前是静默按 summary 走，人以为自己要的是判决。
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="判决（默认动作）")
    action.add_argument("--summary", action="store_true", help="只打一行摘要")
    action.add_argument("--issue-plan", action="store_true", help="该开哪些 issue（只打印）")
    parser.add_argument("--fail-on", default=FAIL_ON_DEFAULT, choices=list(SEVERITIES) + ["never"],
                        help="最低判红档位（默认 %s）" % FAIL_ON_DEFAULT)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    run_dir = args.run or latest_run()
    if not run_dir:
        print("ui_scout: 还没有任何巡检报告——先跑 `cd web && npm run ui-scout`", file=sys.stderr)
        return 2
    try:
        report = load_report(run_dir)
    except ReportError as exc:
        print("ui_scout: %s" % exc, file=sys.stderr)
        return 2
    if args.summary:
        print(summary_line(report))
        return 0
    if args.issue_plan:
        print("\n".join(plan_lines(issue_plan(report, args.fail_on))))
        return 0
    code, lines = verdict_lines(report, args.fail_on)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
