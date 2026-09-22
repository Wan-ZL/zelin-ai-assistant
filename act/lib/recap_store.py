"""act/lib/recap_store.py — ``state/recap/`` on disk + the add-only board projection (CONTRACT §63 / §63.10 / §63.11 / §63.12 / §63.14 / §63.15 / §63.16).

Layout (all under ``STATE_DIR/recap/``; the whole directory is disposable):

    sessions.json        cursor (frames/audio high-water ids), first-run marker,
                         per-minute presence buffer of not-yet-closed events,
                         the OPEN sessions the page shows as 进行中, the daily
                         generation counter and per-key failure counts
    recaps/<key>.json    one file per meeting (key = meeting:<start minute>-<app>);
                         **not a registry card** — no id/status/tier, no
                         recipient, no channel: nothing downstream can dispatch
                         or send it (tests/test_recap_no_egress.py pins the
                         absent keys)
    marks.json           server-owned local flags
                         {key: {copied_at, sent_at, dismissed_at, end_override}}
                         (web 「复制」/「标记已发送」/「忽略」/「改结束时间」);
                         read here for the projection, for the 活跃 / 已归档 /
                         已忽略 budgets and for the dismissed retention window;
                         ``end_override`` (§63.16) only rides into the row —
                         display-only, no judgement here reads it

§63.5 追记（2026-09-15，issue #301）：旧法条那句「无控制流读它 / no control
flow anywhere reads a mark」**自此失效**。marks 参与**恰好两处**判决——
:func:`projection` / :func:`lane_counts` 的分栏（filed 行不再挤掉活跃行，被切掉的
如实报数）与 :func:`prune` 的
已忽略保留窗——两处都只决定「这份笔记还在不在这台机器上」；marks 仍永不进
registry、永不触发发送 / 派发 / 卡片状态机，`server/recaps.py` 仍是它唯一的
写者（act 只读，读不动 = fail-open，只剩 90 天兜底）。

Writers: ``act/recap.py`` (cron `--once` and the actd-spawned `--generate` /
`--slack-draft` runs, serialized by the flock) owns sessions.json and
recaps/; ``server/recaps.py`` owns marks.json. The daemon only READS this
directory: :func:`attach` adds the add-only top-level ``recaps[]`` plus
``recap_counts`` (the true per-lane totals the caps cut down to; §2 兄弟字段) to
dashboard.json (history text stripped — only the §63.9 scalar handles
``history_versions`` ride along, newest first, capped) — the web 会议纪要
page's data; the §63.11 ``questions`` (the intent questions this recap's own
text implies) are computed **in the projection** and never stored — a question
set on disk would drift from the text it was derived from. The one thing actd writes lives OUTSIDE it: the §63.8 generate
request ledger ``state/recap_requests.json`` (act/lib/recap_requests.py),
projected per row as ``generate_request``. The §63.3 追记 ``problems`` /
``repairs`` rows ride on the recap file itself (act/recap.py writes them with
the lines) and reach the wire through :func:`_row` like every other field.

§63.12 追记（issue #300 的后半）：可发送长版的每一条带一个跨版稳定的节内标签
（`D1` / `S2`），它住在 `sections_*` 每一节的 add-only ``tags``（与 ``items`` 逐位
对齐，投影原样搬运）；这个 key 发到第几号记在记录的 add-only ``tag_seq`` 上
（单调、永不复用，因此**不进** ``history[]`` 条目——它是 key 的台账，不是某一版的正文）。
派发与判决全在 `act/recap.py` / `act/lib/recap_text.assign_tags`（本模块只搬运）。

Retention: recaps older than `recap.retention_days` (default 90) are pruned
on every cron round (防腐 #4: every new file family is born with a cap);
dismissed ones go earlier, on `recap.dismissed_retention_days` counted from
the dismissal (§63.3 追记 2026-09-15).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from act.lib import config, recap_intent, recap_requests, recap_sessions, recap_text

KEY_RE = re.compile(r"^meeting:\d{4}-\d{2}-\d{2}T\d{4}-[a-z0-9-]{1,32}$")
# Slack conversation ids: C… channel, D… DM, G… private group (uppercase alnum)
CHANNEL_ID_RE = re.compile(r"^[CDG][A-Z0-9]{6,20}$")

PROJECTION_CAP = 60
# §63.5 追记（issue #301）：已归档 / 已忽略 自己的预算——归档一行永不挤掉活跃的一行；
# 两个上限切掉多少，`recap_counts`（:func:`lane_counts`）如实报出来，页面照着说
FILED_PROJECTION_CAP = 60
# 栏 slug（add-only 词表；web RECAP_LANES 与 dashboard.json `recap_counts` 的键逐字同源）
RECAP_LANES: tuple = ("active", "archived", "dismissed")
LATE_SLICE_WINDOW_S = 48 * 3600
PRIOR_DAYS = 14
PRIOR_LIMIT = 3
DEFAULT_RETENTION_DAYS = 90
DEFAULT_DISMISSED_RETENTION_DAYS = 14
DEFAULT_MAX_PER_RUN = 2
DEFAULT_MAX_PER_DAY = 8
LANGUAGES: tuple = ("auto", "zh", "en")

# quality vocabulary (add-only): ok | needs_review | thin_transcript | no_audio | generation_failed
QUALITY_OK = "ok"
QUALITY_NEEDS_REVIEW = "needs_review"
QUALITY_THIN = "thin_transcript"
QUALITY_NO_AUDIO = "no_audio"
QUALITY_FAILED = "generation_failed"
# 同一份词表的 tuple 形（add-only，顺序无语义）——§63.9 回退读 history 条目上那个键时
# 拿它判「这是不是一个我们认得的值」，认不出就按 needs_review 兜（永不伪造 ok）
QUALITIES: tuple = (QUALITY_OK, QUALITY_NEEDS_REVIEW, QUALITY_THIN, QUALITY_NO_AUDIO, QUALITY_FAILED)


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def recap_dir() -> Path:
    return config.STATE_DIR / "recap"


def recaps_dir() -> Path:
    return recap_dir() / "recaps"


def sessions_path() -> Path:
    return recap_dir() / "sessions.json"


def marks_path() -> Path:
    return recap_dir() / "marks.json"


def lock_path() -> Path:
    return recap_dir() / ".lock"


def log_path() -> Path:
    # sibling of weekly_digest.log — the detached runs append here
    return config.STATE_DIR / "recap.log"


def ensure_dirs() -> None:
    recaps_dir().mkdir(parents=True, exist_ok=True)


def valid_key(key) -> bool:
    return isinstance(key, str) and bool(KEY_RE.match(key))


def recap_path(key: str) -> Path:
    if not valid_key(key):
        raise ValueError("bad recap key: %r" % (key,))
    return recaps_dir() / (key.replace(":", "_") + ".json")


# --------------------------------------------------------------------------- #
# json IO (atomic write; unreadable = default)
# --------------------------------------------------------------------------- #
def _read_json(path: Path, default):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return doc if isinstance(doc, dict) else default


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# settings (config.yaml `recap:` block + the three Settings-overridable flags)
# --------------------------------------------------------------------------- #
def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def slack_targets(raw) -> dict:
    """`recap.slack_draft.targets` {app-slug: channel_id}; entries whose value
    is not a Slack conversation id are dropped (no guessing, §63)."""
    return {str(k).lower(): v for k, v in _dict(raw).items()
            if isinstance(v, str) and CHANNEL_ID_RE.match(v)}


def settings(cfg: Optional[config.Config] = None) -> dict:
    """Effective recap settings: the three flat knobs (config.py, Settings
    overridable) + the tuning block read verbatim from config.yaml."""
    cfg = cfg or config.load_config()
    blk = _dict(_dict(cfg.raw).get("recap"))
    return {
        "enabled": bool(getattr(cfg, "recap_enabled", True)),
        "default_language": str(getattr(cfg, "recap_default_language", "auto")),
        # §63.10（issue #303）：出厂形状（`lines` = 快速五行）；单份纪要的形状由按钮传下来的
        # `--shape` 覆盖，认不出的值一律回落到默认形（recap_text.normalize_shape）
        "default_shape": recap_text.normalize_shape(blk.get("default_shape")),
        "slack_draft_enabled": bool(getattr(cfg, "recap_slack_draft_enabled", False)),
        "slack_targets": slack_targets(_dict(blk.get("slack_draft")).get("targets")),
        "options": recap_sessions.Options.from_mapping(blk),
        "max_per_run": max(1, recap_sessions.int_or(blk.get("max_per_run"), DEFAULT_MAX_PER_RUN)),
        "max_per_day": max(1, recap_sessions.int_or(blk.get("max_per_day"), DEFAULT_MAX_PER_DAY)),
        "retention_days": max(1, recap_sessions.int_or(blk.get("retention_days"), DEFAULT_RETENTION_DAYS)),
        # §63.3 追记（issue #301）：已忽略的那份走自己的短窗，从忽略那一刻算
        "dismissed_retention_days": max(1, recap_sessions.int_or(
            blk.get("dismissed_retention_days"), DEFAULT_DISMISSED_RETENTION_DAYS)),
        "db_path": str(blk.get("db_path") or "").strip() or None,
    }


# --------------------------------------------------------------------------- #
# sessions.json
# --------------------------------------------------------------------------- #
def load_state() -> Optional[dict]:
    """The cursor/buffer document, None on the very first run (no file)."""
    if not sessions_path().exists():
        return None
    return _read_json(sessions_path(), None)


def new_state(cursor: dict, now_iso: str) -> dict:
    return {"schema": 1, "first_run_at": now_iso, "cursor": dict(cursor),
            "events": [], "open": [], "day": {"date": "", "count": 0},
            "failures": {}, "updated_at": now_iso}


def save_state(state: dict) -> None:
    _write_json(sessions_path(), state)


# --------------------------------------------------------------------------- #
# recaps/<key>.json
# --------------------------------------------------------------------------- #
def new_record(session: recap_sessions.Session, key: str, status: str) -> dict:
    """The recap document skeleton. Deliberately carries NO recipient / channel
    / id / tier / status-machine field — it is a note, not a card (§0 第 4 条)."""
    return {
        "key": key, "app": session.app,
        "start": recap_sessions.iso_utc(session.start),
        "end": recap_sessions.iso_utc(session.end),
        "duration_min": int(round(session.span_s / 60.0)),
        "frames": int(session.frames), "audio_rows": int(session.audio_rows),
        "status": status, "version": 0, "partial": False, "generated_at": None,
        "en": None, "zh": None, "quality": None, "transcript_words": 0,
        # §63.10 追记（add-only）：这一版是哪种形状出的稿 + 可发送长版的分节正文 +
        # 两语言**粘出去的那份**（空的部分已略掉；缺席 = 老记录，页面回落到 en/zh 直接拼）。
        # 出生即 None（不是 "lines"）：还没出过稿的记录不该替配置 `recap.default_shape`
        # 做主——形状由第一次出稿写死（act/recap.record_shape 的那条优先级链）
        "shape": None, "sections_en": None, "sections_zh": None,
        "copy_en": None, "copy_zh": None,
        "note": None, "history": [], "slack_draft": None,
        # §63.11 追记（add-only，issue #302）：`baseline` = **我们见过的第一版**的可粘正文
        # （`{version, generated_at, shape, copy_en, copy_zh}`，只写一次、永不覆盖——
        # history 的帽是 5 版，第一版早晚会被挤掉，而「转写原版」必须一直在）；
        # `intent` = 这一版是按哪组答案出的（`{answers, at, version}`，每版重写、没答案 = None）
        "baseline": None, "intent": None,
        # §63.12 追记（add-only，issue #300 的后半）：这个 key 的逐条标签计数器
        # `{字母: 已经发到第几号}`（D/S/P/L/C/O，truth = `recap_text.SECTION_LETTERS`）。
        # **单调、永不回退、永不复用**——一条被删掉的条目的标签不会再发给别人；它是这个
        # key 的台账而不是某一版的正文，所以不进 `history[]` 条目，回退也不让它回头
        "tag_seq": {},
        # §63.14 追记（add-only）：术语表在这一版的转写里换了几处听错的词（`fill_record` 每版
        # 重写成整数；还没出过稿 / 回退出来的版本 = None——那个数属于产出正文的那一次生成）
        "glossary_hits": None,
        # §63.3 追记（add-only）：needs_review 的结构化原因与落地前的长度修剪台账
        "problems": [], "repairs": [],
        # §63.9 追记（add-only）：这一版的正文回退自第几版（生成出来的版本 = None）
        "reverted_from": None,
    }


def load_recap(key: str) -> Optional[dict]:
    if not valid_key(key):
        return None
    return _read_json(recap_path(key), None)


def save_recap(rec: dict) -> None:
    _write_json(recap_path(rec["key"]), rec)


def _start_ts(rec: dict) -> float:
    return recap_sessions.parse_ts(rec.get("start")) or 0.0


def list_recaps() -> list:
    """Every stored recap, newest start first (unreadable files skipped)."""
    ensure_dirs()
    recs = []
    for p in recaps_dir().glob("meeting_*.json"):
        rec = _read_json(p, None)
        if rec and valid_key(rec.get("key")):
            recs.append(rec)
    recs.sort(key=_start_ts, reverse=True)
    return recs


def closed_intervals(now: float, within_s: float = LATE_SLICE_WINDOW_S) -> list:
    """[(key, start_ts, end_ts)] of CLOSED recaps recent enough for a late
    transcript slice to still matter."""
    out = []
    for rec in list_recaps():
        end = recap_sessions.parse_ts(rec.get("end")) or 0.0
        if rec.get("status") == recap_sessions.CLOSED and now - end <= within_s:
            out.append((rec["key"], _start_ts(rec), end))
    return out


def prior_lines(rec: dict) -> list:
    """一份旧纪要喂回 prompt 时的英文行（§63.10）：五行形直接给那五行，可发送长版给
    **渲染出来的那份文档**按行切开——两种形状都能给下一场会的「较上次变化」定锚，
    否则一份 sections 纪要会在下一次生成里消失得无影无踪。"""
    if has_lines(rec.get("en")):
        return list(rec["en"])
    body = rec.get("copy_en")
    if not isinstance(body, str) or not body.strip():
        body = recap_text.render_sections(rec.get("sections_en") or [], "en")
    return [line for line in body.split("\n") if line.strip()]


def priors_for(start_ts: float, timezone: str) -> list:
    """≤ 3 earlier CLOSED recaps with text within 14 days, newest first —
    the 'Changed since last plan' reference: [{"date", "en"}]（正文按
    :func:`prior_lines` 取，两种形状都在里面）。"""
    lo = start_ts - PRIOR_DAYS * 86400
    out = []
    for rec in list_recaps():
        s = _start_ts(rec)
        if has_text(rec) and rec.get("status") == recap_sessions.CLOSED and lo <= s < start_ts:
            out.append({"date": recap_sessions.local_dt(s, timezone).strftime("%Y-%m-%d"),
                        "en": prior_lines(rec)})
    return out[:PRIOR_LIMIT]


def _dismissed_expired(rec: dict, marks: dict, cutoff: float) -> bool:
    """这份被忽略的 CLOSED 纪要过了短窗吗？没 marks（读不动 / 没传窗口）、OPEN 行、
    时间戳解析不出 = False——fail open，多删一份纪要比留一份贵得多。"""
    if not marks or rec.get("status") != recap_sessions.CLOSED:
        return False
    at = recap_sessions.parse_ts(_dict(marks.get(rec.get("key"))).get("dismissed_at"))
    return at is not None and at <= cutoff


def prune(now: float, retention_days: int, dismissed_days: Optional[int] = None) -> int:
    """Delete recaps past their retention; returns count.

    Two windows, whichever comes first (§63.3 追记 2026-09-15，issue #301):
    the 90-day backstop on the meeting's own start (unchanged, applies to
    everything), and — when ``dismissed_days`` is given — ``dismissed_days``
    counted from the moment the recap was 忽略 (marks.json ``dismissed_at``,
    server-owned; read-only here). Only CLOSED recaps take the short window:
    a mark stamped while a meeting is still OPEN must never delete the text
    that lands afterwards. Unreadable / hand-mangled marks = **fail open**
    (no short window at all), never an extra deletion.
    """
    cutoff = now - retention_days * 86400
    marks = load_marks() if dismissed_days else {}
    dismissed_cutoff = now - max(1, int(dismissed_days or 1)) * 86400
    removed = 0
    for rec in list_recaps():
        if _start_ts(rec) < cutoff or _dismissed_expired(rec, marks, dismissed_cutoff):
            recap_path(rec["key"]).unlink(missing_ok=True)
            removed += 1
    return removed


# --------------------------------------------------------------------------- #
# marks.json (server-owned; read-only here)
# --------------------------------------------------------------------------- #
def load_marks() -> dict:
    return _read_json(marks_path(), {})


# --------------------------------------------------------------------------- #
# projection — dashboard.json top-level `recaps[]` (add-only)
# --------------------------------------------------------------------------- #
def has_lines(value) -> bool:
    """5 行正文在不在（非空 list）——§63.9 的投影句柄与回退用**同一个**判据，
    否则面板会列出一个点下去回退不了的版本。"""
    return isinstance(value, list) and bool(value)


def has_text(rec) -> bool:
    """这份记录（或一条 history 条目）有没有可复制的正文——**两种形状都算**
    （§63.10）：五行形看 ``en``，可发送长版看 ``sections_en`` **渲染出来那份非空**。
    凡是「有正文吗」的判决都走这里（history 入库、Slack 草稿闸、通知、投影句柄、
    回退目标），否则一份 sections 纪要会在每一处都被当成「没出稿」。

    长版这一支为什么要渲染一遍而不是只看列表非空：「空」在这个系统里必须是**一个**
    判据。手改坏的 `sections_en`（一列数字）渲染不出任何东西，只看列表非空会让
    通知说「已生成」、面板给一个空 `<pre>` 配一颗可用的复制键、Slack 草稿正文是
    空串——而 server 的 `_shaped_entries`（看 ``en`` / ``copy_en``）又把同一条
    history 条目丢掉，面板于是数得出一版却打不开它。

    §63.11 追记：实现搬到 `recap_text.has_body`（渲染器那一处）——本函数仍是这条判决的
    公开名与法条引用点，同层的 `recap_intent` 问的也是同一个判据（lib 内不起第二套
    「空」的定义；`recap_store` 不能被 `recap_intent` 反过来 import，那是一个环）。"""
    return recap_text.has_body(rec)


def _is_version(value) -> bool:
    """真整数的版本号（bool 是 int 子类——手改过的 wire 上 `true` 真出现过）。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _iso_or_none(value):
    return value if isinstance(value, str) else None


def _version_handle(entry) -> Optional[dict]:
    """一条 history 条目 → 标量句柄 ``{version, generated_at, partial}``，或 None
    （非 dict / 版本号不是真整数 / 没有正文——那三种都不是「能回退到的一版」）。"""
    if not isinstance(entry, dict):
        return None
    if not _is_version(entry.get("version")) or not has_text(entry):
        return None
    return {"version": entry["version"], "generated_at": _iso_or_none(entry.get("generated_at")),
            "partial": bool(entry.get("partial"))}


def history_versions(rec: dict) -> list:
    """``history[]`` → ``[{version, generated_at, partial}]``，oldest first（§63.9，issue #300）。

    **只发标量**：正文一个字都不进这一层。60 行 × 5 条 history × 两语言 × 5 行会把
    10 s 一轮的看板轮询撑成几百 KB，而「上一版长什么样」是点开面板那一下才需要的东西
    ——正文由 ``GET /api/recaps/history?key=…`` 单份按需读（§49 追记）。
    只列**回退得到的**条目：面板上能点的每一项都必须真能回退，手改坏的 / 无正文的
    条目静默跳过（``history_count`` 仍是原始条数，两个数可以不等 = 诚实）。
    """
    handles = [_version_handle(entry) for entry in (rec.get("history") or [])]
    return [handle for handle in handles if handle is not None]


def _end_override(value):
    """marks.json 里手改的结束时间：解析得出的 ISO 字符串原样，其余（None / 手改坏的）= None。"""
    return value if isinstance(value, str) and recap_sessions.parse_ts(value) is not None else None


def _row(rec: dict, marks: dict, requests: Optional[dict] = None) -> dict:
    row = {k: v for k, v in rec.items() if k != "history"}
    row["history_count"] = len(rec.get("history") or [])
    # §63.9 add-only（issue #300）：存着的每一版有个句柄（版本号 + 时刻 + 阶段稿旗）——
    # 「已更新」badge 以前是条死路，现在面板据它列出可看 / 可回退的版本
    row["history_versions"] = history_versions(rec)
    mark = _dict(marks.get(rec.get("key")))
    row["copied_at"] = mark.get("copied_at")
    row["sent_at"] = mark.get("sent_at")
    # §63.5 追记 add-only（issue #301）：已忽略的时刻（无 = None，键恒在）
    row["dismissed_at"] = mark.get("dismissed_at")
    # §63.16 add-only：owner 手改的结束时间（server 独写 marks.json；解析不出的值 = None，键恒在）
    # ——只进投影，记录上的 `end` 仍是录制到的那一刻，生成 / 晚到切片都不读它
    row["end_override"] = _end_override(mark.get("end_override"))
    # §63.8 add-only：「重新生成 / 现在生成」回执（actd 台账 × 本文件的 generated_at；无请求 = None）
    # §63.15：「丢了」的判线按这一行的转写词数伸缩（OPEN 行 / 老记录没有词数 = 地板）
    row["generate_request"] = recap_requests.projection(rec.get("key"), rec.get("generated_at"),
                                                        requests if requests is not None else {},
                                                        words=rec.get("transcript_words"))
    return row


def filed(row: dict) -> bool:
    """已归档（`sent_at`，标记已发送派生）或已忽略（`dismissed_at`）= 离开活跃栏的行
    （§63.5 追记，issue #301）。"""
    return bool(row.get("sent_at") or row.get("dismissed_at"))


def lane(row: dict) -> str:
    """这一行落在哪一栏（§63.5 追记，issue #301；`web/.../recapText.recapLane` 逐字镜像）：
    已忽略优先于已归档——它是「这场会不需要纪要」的判决，不是「已经发出去了」。"""
    if row.get("dismissed_at"):
        return "dismissed"
    if row.get("sent_at"):
        return "archived"
    return "active"


def _filed_ts(row: dict) -> float:
    """被归档 / 忽略的时刻（两个戳取晚的；解析不出 = 回落到会议 start）。filed 预算按它
    取最近的，不按会议 start——刚按下的那一行必须还在投影里，撤销才有东西可撤。"""
    stamps = [recap_sessions.parse_ts(row.get("dismissed_at")),
              recap_sessions.parse_ts(row.get("sent_at"))]
    known = [t for t in stamps if t is not None]
    return max(known) if known else _start_ts(row)


def _has_prior(row: dict, starts: list) -> bool:
    """这一行之前 14 天内有没有另一份**有正文的** CLOSED 纪要——判据与
    :func:`priors_for` 的窗口逐字同源（§63.11 的 `prior` 问题只在真有上一份时才问）。
    算在已经读进内存的行上，不为每一行再扫一遍磁盘（60 行 × 一次全目录读 = O(n²) 读盘）。"""
    start = _start_ts(row)
    lo = start - PRIOR_DAYS * 86400
    return any(lo <= ts < start for ts in starts)


def _with_questions(rows: list) -> list:
    """§63.11 add-only、**projection-only** 的 `questions`：这一份纪要该被问的问题
    （`recap_intent.derive`，纯规则、零模型）。投影层算、不落盘——问题是那一版正文的
    函数，存一份就会和正文漂移；web 只渲染 wire 上的这份数据（防腐 #10）。"""
    starts = [_start_ts(row) for row in rows
              if has_text(row) and row.get("status") == recap_sessions.CLOSED]
    for row in rows:
        row["questions"] = recap_intent.derive(row, _has_prior(row, starts))
    return rows


def all_rows() -> list:
    """全部投影行（**未切预算**），newest first：已出稿 recap + sessions.json 里还没有文件的
    OPEN 会话（同 key 以文件为准——一份 partial 的「现在生成」盖过裸 OPEN 行），history 剥掉、
    server-owned marks 与 §63.8 生成回执并入、§63.11 的 `questions` 现算。:func:`projection`
    与 :func:`lane_counts` 的共同上游（一次读盘两用）。"""
    marks = load_marks()
    requests = recap_requests.load()
    rows = {r["key"]: _row(r, marks, requests) for r in list_recaps()}
    for o in open_rows(load_state() or {}):
        rows.setdefault(o["key"], _row(o, marks, requests))
    return _with_questions(sorted(rows.values(), key=_start_ts, reverse=True))


def lane_counts(rows: Optional[list] = None) -> dict:
    """三栏的**真实**总数（切预算之前算，空栏也有键），照 §2 `counts.completed` 的先例：
    行可以被上限切掉，计数不许跟着缩水——页面据此说出「另有 N 条更早的没列出来」
    （§63.5 追记，issue #301；宪法第 3 条：界面不许悄悄少东西）。"""
    rows = all_rows() if rows is None else rows
    out = {name: 0 for name in RECAP_LANES}
    for row in rows:
        out[lane(row)] += 1
    return out


def projection(limit: int = PROJECTION_CAP, filed_limit: int = FILED_PROJECTION_CAP,
               rows: Optional[list] = None) -> list:
    """:func:`all_rows` 切两份预算后的 `recaps[]`，合并后仍 newest first。

    Two budgets, not one (§63.5 追记，issue #301): the active rows get
    ``limit`` and the filed ones (已归档 / 已忽略) get ``filed_limit`` — filing
    a recap away must never push a live one off the page. The filed budget goes
    to the **most recently filed** rows (:func:`_filed_ts`), not to the newest
    meetings: archiving an old recap must keep it on the page so the one-click
    「取消已发送」undo still has a row to act on. Whatever the caps do cut is
    disclosed, not swallowed — :func:`lane_counts` keeps the true totals.
    """
    ordered = all_rows() if rows is None else rows
    active = [r for r in ordered if not filed(r)][:limit]
    filed_rows = sorted([r for r in ordered if filed(r)], key=_filed_ts, reverse=True)[:filed_limit]
    return sorted(active + filed_rows, key=_start_ts, reverse=True)


def open_rows(state: dict) -> list:
    """The OPEN session rows of sessions.json that are well-formed."""
    return [o for o in (state.get("open") or [])
            if isinstance(o, dict) and valid_key(o.get("key"))]


def attach(dash: dict) -> dict:
    """Set ``dash["recaps"]`` and ``dash["recap_counts"]`` (both add-only; a
    failure leaves the key absent — the board must never die for a recap file).

    ``recap_counts`` = the true per-lane totals before the caps cut anything
    (§63.5 追记 2026-09-15, issue #301), so the page can say how many older
    recaps it is not listing instead of silently losing them."""
    try:
        rows = all_rows()
        dash["recaps"] = projection(rows=rows)
        dash["recap_counts"] = lane_counts(rows)
    except Exception:  # noqa: BLE001 - projection is best-effort
        pass
    return dash


# --------------------------------------------------------------------------- #
# inbox special forms → `python -m act.recap` argv tail (actd spawns detached)
# --------------------------------------------------------------------------- #
def _note_argv(decision: dict) -> Optional[list]:
    """`--note …` 或空（没带）；畸形（非字符串 / 超 500 字）= None = 整条 noop。"""
    note = decision.get("note")
    if note is None:
        return []
    if not isinstance(note, str) or len(note) > 500:
        return None
    return ["--note", note]


def _shape_argv(decision: dict) -> Optional[list]:
    """§63.10（issue #303）：形状只认字面量 lines / sections——别的值 = 畸形 = noop
    （actd 永不猜；缺席 = 这份纪要上一次用的形状，由持锁的写者决定）。"""
    shape = decision.get("shape")
    if shape is None:
        return []
    return ["--shape", shape] if shape in recap_text.SHAPES else None


def _answers_argv(decision: dict) -> Optional[list]:
    """§63.11（issue #302）：`answers` 是**字符串列表**（`["split1=drop", "aud=send"]`，
    形状与词表 truth = `recap_intent.answers_ok`）→ `--answers <紧凑 JSON>`。
    一条认不出 = 整条 noop：半组答案会悄悄按另一种意图重写这份纪要。"""
    answers = decision.get("answers")
    if answers is None:
        return []
    if not recap_intent.answers_ok(answers):
        return None
    return ["--answers", json.dumps(list(answers), ensure_ascii=False, separators=(",", ":"))]


def _generate_argv(decision: dict) -> Optional[list]:
    note, shape = _note_argv(decision), _shape_argv(decision)
    answers = _answers_argv(decision)
    if note is None or shape is None or answers is None:
        return None
    partial = ["--partial"] if decision.get("partial") is True else []
    return ["--generate", decision["meeting_key"]] + note + partial + shape + answers


def _slack_draft_argv(decision: dict) -> Optional[list]:
    channel = decision.get("channel_id")
    if not (isinstance(channel, str) and CHANNEL_ID_RE.match(channel)):
        return None
    return ["--slack-draft", decision["meeting_key"], "--channel-id", channel]


def _revert_argv(decision: dict) -> Optional[list]:
    """§63.9（issue #300）：``recap_revert {meeting_key, version}`` → ``--revert KEY
    --to-version N``。version 必须是 ≥ 1 的真整数（bool 不算——wire 上 `true` 曾经
    真的出现过）；这里不查这一版存不存在，那是持锁的写者的事（act/recap.revert）。"""
    version = decision.get("version")
    if not _is_version(version) or version < 1:
        return None
    return ["--revert", decision["meeting_key"], "--to-version", str(version)]


_INBOX_BUILDERS = {"recap_generate": _generate_argv, "recap_slack_draft": _slack_draft_argv,
                   "recap_revert": _revert_argv}
INBOX_ACTIONS = frozenset(_INBOX_BUILDERS)


def inbox_argv(decision) -> Optional[list]:
    """``recap_generate {meeting_key, note?, partial?, shape?, answers?}`` / ``recap_slack_draft
    {meeting_key, channel_id}`` / ``recap_revert {meeting_key, version}`` → argv
    tail, or None when malformed (actd acks noop). None of the three carries a
    recipient: the channel_id of a draft names the owner's own Slack draft box
    target, never a send."""
    if not isinstance(decision, dict) or not valid_key(decision.get("meeting_key")):
        return None
    builder = _INBOX_BUILDERS.get(str(decision.get("action")))
    return builder(decision) if builder else None
