"""Usage report — aggregate the analytics event log for improvement decisions.

    python -m act.report            # last 30 days
    python -m act.report --days 7

Sections:
  1. 功能使用频次（7 天 / 30 天）— which features earn their keep
  2. 时段热力（小时 × 星期）— when the system is actually used
  3. 健康信号 — rework rate per requirement (提案/DoD 不清晰), auto-resume
     failures (无效重复), approve/reject/trash ratios, dispatch→review 时长
  4. 重复风暴 — same (event, req) fired >3 times within 1 hour (tonight's
     resume storm would light up here)
"""
from __future__ import annotations

import argparse
import datetime as _dt
from collections import Counter, defaultdict
from typing import Optional

from act.lib.analytics import read_events

_BAR = "█"


def _parse_ts(s: str) -> Optional[_dt.datetime]:
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except (ValueError, TypeError):
        return None


def _bar(n: int, peak: int, width: int = 24) -> str:
    if peak <= 0:
        return ""
    return _BAR * max(1, int(n / peak * width)) if n else ""


def build_report(days: int = 30) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    since = now - _dt.timedelta(days=days)
    since7 = now - _dt.timedelta(days=7)

    events = [e for e in read_events(since=since)]
    for e in events:
        e["_dt"] = _parse_ts(e.get("ts", ""))
    events = [e for e in events if e["_dt"] is not None]

    out: list[str] = []
    out.append(f"# Zelin's AI Assistant · 使用报告（近 {days} 天，事件数 {len(events)}）")
    out.append(f"生成于 {now.strftime('%Y-%m-%d %H:%M UTC')}\n")

    # 1. feature frequency
    c_all = Counter(e["event"] for e in events)
    c_7 = Counter(e["event"] for e in events if e["_dt"] >= since7)
    out.append("## 1 · 功能使用频次")
    out.append(f"{'事件':<26}{'7天':>6}{'30天':>7}")
    for ev, n in c_all.most_common():
        out.append(f"{ev:<26}{c_7.get(ev, 0):>6}{n:>7}")
    out.append("")

    # 2. time-of-day / day-of-week heat (local time)
    local_tz = _dt.datetime.now().astimezone().tzinfo
    hours = Counter()
    dows = Counter()
    for e in events:
        loc = e["_dt"].astimezone(local_tz)
        hours[loc.hour] += 1
        dows[loc.weekday()] += 1
    out.append("## 2 · 时段热力（本地时间）")
    peak = max(hours.values(), default=0)
    for h in range(24):
        n = hours.get(h, 0)
        if n:
            out.append(f"{h:02d}:00  {n:>4}  {_bar(n, peak)}")
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    peak = max(dows.values(), default=0)
    out.append("")
    for d in range(7):
        n = dows.get(d, 0)
        out.append(f"{names[d]}  {n:>4}  {_bar(n, peak)}")
    out.append("")

    # 3. health signals
    out.append("## 3 · 健康信号")
    rework = Counter(e.get("req") for e in events
                     if e["event"] == "inbox_rework" and e.get("req"))
    multi = {r: n for r, n in rework.items() if n > 1}
    if multi:
        out.append("⚠️ 多次打回（>1 次 = 提案/验收标准可能不清晰）:")
        for r, n in sorted(multi.items(), key=lambda kv: -kv[1]):
            out.append(f"   {r}: {n} 次")
    resume_fail = Counter(e.get("req") for e in events
                          if e["event"] in ("auto_resume", "resume_launch")
                          and e.get("ok") is False and e.get("req"))
    if resume_fail:
        out.append("⚠️ 自动恢复失败（无效重复的头号来源）:")
        for r, n in resume_fail.most_common(5):
            out.append(f"   {r}: {n} 次失败")
    exhausted = [e.get("req") for e in events if e["event"] == "auto_resume_exhausted"]
    if exhausted:
        out.append(f"🔴 恢复放弃（需人工）: {', '.join(str(x) for x in exhausted)}")

    n_appr = c_all.get("inbox_approve", 0)
    n_rej = c_all.get("inbox_reject", 0)
    n_trash = c_all.get("inbox_trash", 0)
    out.append(f"审批漏斗: 发卡 {c_all.get('card_sent', 0)} → 批准 {n_appr} · 拒绝 {n_rej} "
               f"· 删除 {n_trash} · 验收 {c_all.get('inbox_accept', 0)}")
    if n_appr + n_rej > 0 and n_rej / max(1, n_appr + n_rej) > 0.5:
        out.append("⚠️ 拒绝率 >50% —— 雷达/提案质量可能偏低，先看 summary 是否说人话")

    # dispatch -> review duration per req
    disp: dict[str, _dt.datetime] = {}
    durs: list[tuple[str, float]] = []
    for e in sorted(events, key=lambda x: x["_dt"]):
        r = e.get("req")
        if not r:
            continue
        if e["event"] == "dispatch":
            disp[r] = e["_dt"]
        elif e["event"] == "review_promoted" and r in disp:
            durs.append((r, (e["_dt"] - disp.pop(r)).total_seconds() / 60))
    if durs:
        out.append("执行时长（派发→待验收，分钟）:")
        for r, m in durs[-8:]:
            out.append(f"   {r}: {m:.0f} min")
    out.append("")

    # 4. repetition storms
    out.append("## 4 · 重复风暴（同一任务同一事件 1 小时内 >3 次）")
    buckets: dict[tuple, list] = defaultdict(list)
    for e in events:
        if e.get("req"):
            buckets[(e["event"], e["req"])].append(e["_dt"])
    storms = []
    for (ev, r), times in buckets.items():
        times.sort()
        for i in range(len(times)):
            j = i
            while j + 1 < len(times) and (times[j + 1] - times[i]).total_seconds() <= 3600:
                j += 1
            if j - i + 1 > 3:
                storms.append((ev, r, j - i + 1, times[i]))
                break
    if storms:
        for ev, r, n, t0 in sorted(storms, key=lambda s: -s[2]):
            out.append(f"🔴 {r} · {ev} × {n}（始于 {t0.astimezone(local_tz).strftime('%m-%d %H:%M')}）")
        out.append("   —— 这类风暴 = 某个机制在空转，优先修它")
    else:
        out.append("（无 —— 健康）")

    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="report")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args(argv)
    print(build_report(days=args.days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
