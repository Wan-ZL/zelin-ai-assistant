"""act/recap.py — meeting recaps: deterministic sessions in, a copy-only note out (CONTRACT §63 / §63.10 / §63.11 / §63.12 / §63.13 / §63.14 / §63.15).

Hangs off the existing 30-minute screenpipe cron chain
(``ingest/process-screenpipe.sh`` runs ``python -m act.recap --once`` before
its own PID lock — no new daemon, no crontab change). One round:

  1. first run: record the engine DB's high-water ids as the marker and stop
     (no backfill — the manager-pack lesson);
  2. read frames / audio_transcriptions **by id range** since the cursor,
     fold them into per-minute presence buckets (act/lib/recap_sessions.py);
  3. audio that lands inside an already-CLOSED meeting = late slice →
     regenerate that recap (version + 1, old text into history);
  4. cluster (gap > 5 min), cut > 4 h segments, judge each session OPEN /
     CLOSED (quiet ≥ 5 min + no pending transcript, forced at 120 min);
  5. CLOSED + eligible → one sealed model call (argv pinned by
     tests/test_recap_no_egress.py: ``--tools ""``, no MCP servers), a
     deterministic validator with one retry then the §63.3 追记 length repair,
     ``state/recap/recaps/<key>.json``, a notification (reasons and trims ride
     on the record); OPEN → listed as 进行中, no model call;
  6. prune recaps past retention — the 90-day backstop on the meeting start
     plus the §63.3 追记 short window on 已忽略 (marks.json `dismissed_at`) —
     save the cursor/buffer.

Nothing here can send: the recap is not a card (no registry, no dispatch), the
JSON has no recipient / channel field, and the only exit is the clipboard on
the web 会议纪要 page. The optional Slack **draft** (§63.4; Settings toggle,
default off) is a second, whitelisted call (act/lib/recap_slack_draft.py)
that puts the text in the owner's own draft box — sending stays manual.

§63.10 (issue #303): a recap is generated in one of **two shapes** — the
5-line ``lines`` form above (the quick personal note) or the sendable
``sections`` form (sections with a modality each, items numbered continuously;
``act/lib/recap_text.py``). The shape rides with the request (``--shape``),
falls back to the record's previous shape, then to config
``recap.default_shape``; everything downstream of a recap having text (history,
the notification, the Slack draft body, the 「较上次变化」 anchor for the next
meeting) asks ``recap_store.has_text`` / :func:`copy_body`, so a sections recap
is never silently treated as a recap that never landed.

§63.11 (issue #302): a regeneration can also carry the owner's **answers** to
the questions ``act/lib/recap_intent.py`` derives from the version on the
record (``--answers '["split1=drop","aud=send"]'``). The answers reach the
model as instructions (numbers only; the items they name ride in the UNTRUSTED
fence), ``prior=drop`` is executed deterministically rather than asked for, and
the version they replace is kept forever as the record's ``baseline`` — the
history cap would otherwise evict the first version on the fifth regeneration.

§63.12 (issue #300 的后半): every item of the sendable shape carries a stable
section-scoped tag (`D1` / `S2`) that survives a regeneration, so a commitment
can be cited later. The previous version's tagged items ride into the prompt
inside the UNTRUSTED fence (:func:`_tag_args`) and the model is asked to keep a
tag on the same commitment — but the tag is decided **here**
(:func:`_tagged` → ``recap_text.assign_tags``): an unknown / duplicate /
foreign claim is discarded and re-assigned, and the per-letter counter on the
record (``tag_seq``) never goes back, so a tag is never reused inside one key.

§63.13 (issue #440): the sendable shape's items carry their **own modality** and
a **transcript anchor** (the ``[HH:MM]`` stamp of the transcript line + a
verbatim fragment) — the sections prompt gets the transcript stamped per row
(:func:`_transcript_view`), the anchors are checked against that transcript
(``recap_text.anchor_context``) and never enter the pasted body. §63.14: the
glossary (``act/lib/recap_glossary.py``) rewrites misheard terms in the
transcript **before** it reaches the model (:func:`_source`; count on the
record as ``glossary_hits``) and its spellings ride in the fenced prompt.
§63.15: the model timeout scales with the transcript's word count
(``act/lib/recap_timing.py``; the §63.8 lost line follows it).

Other entry points (spawned detached by actd for the inbox special forms):
``--generate <key> [--note …] [--partial] [--shape …] [--answers …]``, ``--slack-draft <key>
--channel-id <C…>`` and ``--revert <key> --to-version <n>`` (§63.9: a stored
version's text becomes version + 1 — no model call, and this module stays the
only writer of ``recaps/``, the server never touches a recap file). All runs
serialize on a flock; the cron round gives up immediately when another run
holds it.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

from act import llm
from act.lib import (
    analytics,
    config,
    failures,
    logcap,
    notify,
)
from act.lib import recap_glossary as glossary_mod
from act.lib import recap_intent as intent
from act.lib import recap_sessions as sessions
from act.lib import recap_slack_draft as slack_draft
from act.lib import recap_store as store
from act.lib import recap_text as text
from act.lib import recap_timing as timing

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows: no flock, the cron chain is macOS-only anyway
    fcntl = None  # type: ignore[assignment]

# §63.15：模型调用的超时自此随转写词数伸缩（truth = act/lib/recap_timing.py）；这个名字
# 留作**地板**（词数未知时的那一档 = 原来的定值 240 s），`fill_record` 按词数算真值
LLM_TIMEOUT_S = timing.LLM_TIMEOUT_BASE_S
DRAFT_TIMEOUT_S = 180
MAX_GENERATION_FAILURES = 3
HISTORY_CAP = 5
LOCK_WAIT_S = timing.LOCK_WAIT_S
NOTIFY_KIND = "recap_ready"


# --------------------------------------------------------------------------- #
# log
# --------------------------------------------------------------------------- #
def _log(msg: str) -> None:
    try:
        store.ensure_dirs()
        with store.log_path().open("a", encoding="utf-8", errors="replace") as fh:
            fh.write("%s  %s\n" % (_dt.datetime.now().isoformat(timespec="seconds"), msg))
        logcap.cap(store.log_path())
    except OSError:
        pass


def _iso(ts: float) -> str:
    return sessions.iso_utc(ts)


# --------------------------------------------------------------------------- #
# flock — every writer of state/recap/ goes through here
# --------------------------------------------------------------------------- #
class Lock:
    """``with Lock(wait_s) as ok:`` — ok False = someone else holds it."""

    def __init__(self, wait_s: float = 0.0) -> None:
        self.wait_s = wait_s
        self.fh = None

    def __enter__(self) -> bool:
        store.ensure_dirs()
        if fcntl is None:
            return True
        self.fh = store.lock_path().open("a")
        deadline = time.time() + self.wait_s
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                if time.time() >= deadline:
                    return False
                time.sleep(0.5)

    def __exit__(self, *exc) -> None:
        if self.fh is not None:
            self.fh.close()


# --------------------------------------------------------------------------- #
# generation (the sealed model call)
# --------------------------------------------------------------------------- #
def voice_profile_text() -> Optional[str]:
    """docs/VOICE.md two-level fallback (mirrors executor.resolve_voice_profile
    — entrypoints may not import each other): private state/voice-profile.md,
    else the shipped config/voice-profile.default.md, else None."""
    for p in (config.STATE_DIR / "voice-profile.md",
              config.HOME / "config" / "voice-profile.default.md"):
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")[:4000]
    return None


def _call_model(prompt: str, runner, cfg, extra_argv, timeout: float) -> str:
    """§59 single LLM boundary; the recap's argv tail is the no-egress shape."""
    proc = llm.run(prompt, mode=llm.MODE_PIPELINE, runner=runner, timeout=timeout,
                   extra_argv=extra_argv, cwd=config.headless_cwd(), cfg=cfg)
    if proc.returncode != 0:
        raise RuntimeError("claude exit %s: %s" % (proc.returncode,
                                                   (proc.stderr or proc.stdout or "")[-160:]))
    return proc.stdout or ""


_SHAPE_MISS = {
    text.SHAPE_LINES: "output was not the JSON object {\"en\": [5], \"zh\": [5]}",
    text.SHAPE_SECTIONS: ("output was not the JSON object "
                          "{\"en\": [{key, modality, items}], \"zh\": [...]}"),
}


def _attempt(args: dict, runner, cfg, problems: Optional[list] = None,
             drop_prior: bool = False, timeout: Optional[float] = None,
             context: Optional[dict] = None) -> "tuple[Optional[dict], list]":
    shape = text.normalize_shape(args.get("shape"))
    raw = _call_model(text.build_prompt(problems=problems, **args), runner, cfg,
                      text.NO_EGRESS_ARGV, timeout or LLM_TIMEOUT_S)
    parsed = text.parse_for(shape, raw)
    if parsed is None:
        return None, [_SHAPE_MISS[shape]]
    # §63.11：`prior=drop` **在校验之前**落地——那一行 / 那一节我们要自己覆盖掉，
    # 所以判决与重试引回的问题都该是**真正落地的那份文本**的问题，而不是一段
    # 马上被替掉的散文的问题（问题行与存下来的正文对不上就是一条假回执）
    if drop_prior:
        parsed = text.drop_prior(shape, parsed)
    # §63.13：`context` = 模型看到的那份转写的对照（戳 + 归一正文），逐条锚据它判「原话在不在」
    return parsed, text.validate_for(shape, parsed, context)


def _after_retry(best: Optional[dict], shape: str,
                 context: Optional[dict] = None) -> "tuple[Optional[dict], str, list, list]":
    """The retry also failed: try the §63.3 追记 deterministic length repair, and
    failing that store the model's OWN version (never half-trimmed text) with the
    structured findings behind 需复核.

    §63.10: the length repair is the 5-line form's own move (it trims a labelled
    line back to its cap); the sendable shape goes straight to 需复核 with its
    findings rather than inventing a second trimming rule for items."""
    if best is None:
        return None, store.QUALITY_FAILED, [], []   # 两次都不是 JSON：无正文可复核
    if shape == text.SHAPE_SECTIONS:
        return best, store.QUALITY_NEEDS_REVIEW, text.validate_sections_detail(best, context), []
    repaired, repairs = text.repair_lengths(best)
    if repairs and not text.validate_detail(repaired):
        return repaired, store.QUALITY_OK, [], repairs
    return best, store.QUALITY_NEEDS_REVIEW, text.validate_detail(best), []


def generate_lines(args: dict, runner, cfg, drop_prior: bool = False,
                   timeout: Optional[float] = None,
                   context: Optional[dict] = None) -> "tuple[Optional[dict], str, list, list]":
    """``(lines, quality, problems, repairs)`` — one call, one retry with the
    violations quoted back, then the §63.3 追记 deterministic repair: a failure
    that is nothing but a few characters over a cap is trimmed back instead of
    costing a round trip or a human (issue #298). Still failing = 需复核 with
    the structured findings on the record — the owner can copy and fix by hand.
    ``drop_prior`` (§63.11) is the owner's ``prior=drop`` answer, applied to
    every attempt deterministically instead of being asked for. ``timeout``
    (§63.15) is the per-call budget the caller derived from the transcript's
    word count (``recap_timing.llm_timeout_s``); None = the floor. ``context``
    (§63.13) is ``recap_text.anchor_context`` of the transcript the model saw —
    the sendable shape's item anchors are checked against it on every attempt."""
    parsed, problems = _attempt(args, runner, cfg, drop_prior=drop_prior, timeout=timeout,
                                context=context)
    if not problems:
        return parsed, store.QUALITY_OK, [], []
    retry, problems = _attempt(args, runner, cfg, problems, drop_prior=drop_prior, timeout=timeout,
                               context=context)
    if not problems:
        return retry, store.QUALITY_OK, [], []
    return _after_retry(retry or parsed, text.normalize_shape(args.get("shape")), context)


def _when(rec: dict, tz: str) -> str:
    start = sessions.parse_ts(rec["start"]) or 0.0
    end = sessions.parse_ts(rec["end"]) or start
    a, b = sessions.local_dt(start, tz), sessions.local_dt(end, tz)
    return "%s %s–%s (%s)" % (a.strftime("%Y-%m-%d"), a.strftime("%H:%M"), b.strftime("%H:%M"), tz)


def _push_history(rec: dict) -> None:
    """The previous text (if any) moves into history, capped at HISTORY_CAP.

    §63.9 追记（issue #300）: the entry also carries the version's own
    ``quality`` (add-only) — a revert has to restore the badge the text was
    born with, and guessing ``ok`` for an entry that was 需复核 would be the
    one lie this feature must not tell. Entries written before this key exists
    fall back to 需复核 on revert (act/recap._entry_quality).

    §63.6 追记 2026-09-15 修正（R-216 / D77）: the entry also carries the
    version's own ``repairs`` (add-only) — a trim is a birth fact of that text
    exactly like ``quality``, and it cannot be recomputed later (the trimmed
    line validates clean), so a revert that lost it would present machine-cut
    text as untouched (§63.3 追记「修剪永远露在面上」). ``problems`` stay out:
    the revert recomputes them over the restored text instead.

    §63.10 追记（issue #303）: the gate is ``store.has_text`` and the entry
    carries the shape's own payload (add-only ``shape`` / ``sections_*`` /
    ``copy_*``). Gating on ``en`` alone would have thrown away every version of
    a sendable recap — its ``en`` is None — and with it the §63.2 late-slice
    judgement that a regeneration must never lose the previous text."""
    if not store.has_text(rec):
        return
    # §63.13 追记：条目自此也带出生时的 `problems`（add-only）——`anchor_unverified` 这一种发现要
    # 对着转写才算得出，回退时没有转写可对，只能从出生台账带回（其余 code 回退时照旧重算，见
    # `_carried_problems`）；D77 那句「不存算得出来的东西」对算不出来的那一种自此不再成立
    entry = {"version": rec.get("version"), "generated_at": rec.get("generated_at"),
             "en": rec["en"], "zh": rec["zh"], "partial": bool(rec.get("partial")),
             "quality": rec.get("quality"), "repairs": list(rec.get("repairs") or []),
             "problems": list(rec.get("problems") or []),
             "shape": text.normalize_shape(rec.get("shape")),
             "sections_en": rec.get("sections_en"), "sections_zh": rec.get("sections_zh"),
             "copy_en": rec.get("copy_en"), "copy_zh": rec.get("copy_zh")}
    _capture_baseline(rec)
    rec["history"] = (rec.get("history") or [])[-(HISTORY_CAP - 1):] + [entry]


def _capture_baseline(rec: dict) -> None:
    """§63.11（issue #302）：`baseline` = **我们见过的第一版**的可粘正文，只写一次。

    为什么不靠 `history[]`：帽是 :data:`HISTORY_CAP` 版，第五次重新生成就把第一版
    挤出去了（面板自己都这么说，§63.9），而「转写原版」是这一条 issue 的另一半——
    第二版按 owner 的答案出，第一版必须**永久**留着才对照得出「转写说了什么 /
    我选择记下什么」。写的是渲染好的 `copy_*`（粘出去的那一份，所见即所复制），
    不是 `en` / `zh` 的原始数组：切换要的就是那两段正文，存第二种结构只会漂移。
    出生时机是**这一版即将被替掉**的那一刻，所以本节之前生成的老记录也能拿到一份
    诚实的 baseline（它那一版从来没有被任何答案 steer 过）。"""
    if rec.get("baseline") is not None:
        return
    rec["baseline"] = {"version": rec.get("version"), "generated_at": rec.get("generated_at"),
                       "shape": text.normalize_shape(rec.get("shape")),
                       "copy_en": copy_body(rec, "en") or None,
                       "copy_zh": copy_body(rec, "zh") or None}


def copy_body(rec: dict, lang: str) -> str:
    """粘出去的那一份正文（§63.10 的单点）：记录上存着的 ``copy_<lang>`` 优先
    （出稿时按当时的形状算好、空的部分已略掉），缺键 / 老记录现算一遍。永不抛。"""
    stored = rec.get("copy_" + lang)
    if isinstance(stored, str) and stored.strip():
        return stored
    shape = text.normalize_shape(rec.get("shape"))
    payload = rec.get("sections_" + lang) if shape == text.SHAPE_SECTIONS else rec.get(lang)
    return text.render_for(shape, payload, lang)


def _write_copy_bodies(rec: dict) -> None:
    """两语言的 ``copy_*``（add-only）——粘出去的那份正文由**出稿这一处**算好写进
    记录：页面照着显示与复制，不在 TypeScript 里再实现一遍略掉空行的规则
    （防腐 #10：渲染不起第二套）。无正文 = None。"""
    shape = text.normalize_shape(rec.get("shape"))
    sections = shape == text.SHAPE_SECTIONS
    for lang in ("en", "zh"):
        payload = rec.get("sections_" + lang) if sections else rec.get(lang)
        rec["copy_" + lang] = text.render_for(shape, payload, lang) or None


def _set_payload(rec: dict, lines: Optional[dict], shape: str) -> None:
    """§63.10：一版只有一种形状——本形状的正文键写上，**另一形的一并清掉**，
    再重算粘出去的那份。不清的话面板会拿着上一版的五行去显示这一版的分节稿
    （「所见即所复制」当场作废）。"""
    rec["shape"] = shape
    sections = shape == text.SHAPE_SECTIONS
    for lang in ("en", "zh"):
        payload = (lines or {}).get(lang)
        rec[lang] = None if sections else payload
        rec["sections_" + lang] = payload if sections else None
    _write_copy_bodies(rec)


def _apply_lines(rec: dict, lines: Optional[dict], quality: str, note: Optional[str],
                 partial: bool, now: float, problems: Optional[list] = None,
                 repairs: Optional[list] = None, shape: str = text.DEFAULT_SHAPE,
                 answers: Optional[list] = None) -> None:
    """Version bump with the new (or absent) lines. ``problems`` / ``repairs``
    are the §63.3 追记 add-only receipts (structured findings behind 需复核 and
    the length trims applied before it) — always rewritten, so a clean new
    version clears the previous one's reasons. ``reverted_from`` (§63.9) is
    rewritten to None for the same reason: a freshly generated version is not
    a restored one, and a stale handle would make the panel say「回退自第 N 版」
    about text the model just wrote. ``intent`` (§63.11) is rewritten every
    version too — the answers belong to the generation that produced THIS text,
    and no answers = None (never the previous version's answer sheet)."""
    _push_history(rec)
    rec["version"] = int(rec.get("version") or 0) + 1
    rec["generated_at"] = _iso(now)
    rec["partial"] = bool(partial)
    rec["note"] = note
    rec["quality"] = quality
    _set_payload(rec, lines, text.normalize_shape(shape))
    rec["problems"] = list(problems or [])
    rec["repairs"] = list(repairs or [])
    rec["reverted_from"] = None
    rec["intent"] = ({"answers": list(answers), "at": _iso(now), "version": rec["version"]}
                     if answers else None)


def record_shape(rec: dict, st: dict, shape: Optional[str] = None) -> str:
    """这一次出稿用哪种形状（§63.10）：按钮传下来的 > 这份纪要上一版的形状 >
    配置 ``recap.default_shape``。粘性是故意的——为一场会选过「可发送长版」之后，
    晚到切片的自动重生成不该把它变回五行。"""
    for candidate in (shape, rec.get("shape"), st.get("default_shape")):
        if candidate in text.SHAPES:
            return candidate
    return text.DEFAULT_SHAPE


def _previous_sections(rec: dict) -> dict:
    """记录上**这一版**的分节正文（§63.12 标签的来源；`_push_history` 马上要把它压进历史）。"""
    return {"en": rec.get("sections_en") or [], "zh": rec.get("sections_zh") or []}


def _tag_args(rec: dict, shape: str) -> dict:
    """§63.12：上一版的条目连同标签，进 `build_prompt` 的 UNTRUSTED 围栏（缺 = 这一块
    根本不进 prompt）。只对可发送长版成立——五行形没有条目，它的身份是位置（§63.9）。"""
    if shape != text.SHAPE_SECTIONS:
        return {}
    block = text.tagged_items_block(rec.get("sections_en"))
    return {"tagged": block} if block else {}


def _tagged(rec: dict, lines, shape: str):
    """§63.12：给可发送长版的每一条派标签，并把这个 key 的计数器推到新的高水位。

    **标签在这里定下来，不在模型那边**（`text.assign_tags`：模型报的每一个都要过闸，
    丢掉的按相似度回挂，剩下的发新号）。五行形与没出正文的那几种原样退回。计数器写在
    记录上而不在版本里：它是这个 key 的台账，回退 / 换形状都不许让它回头。"""
    if lines is None or shape != text.SHAPE_SECTIONS:
        return lines
    tagged, seq = text.assign_tags(lines, _previous_sections(rec), rec.get("tag_seq"))
    rec["tag_seq"] = seq
    return tagged


def _intent_args(rec: dict, answers: list) -> dict:
    """§63.11：答案 → `build_prompt` 的两个可选块。**在正文被替掉之前算**——
    `split<n>` 指的是记录上**这一版**的第 n 条分工，答案是对着它答的。"""
    if not answers:
        return {}
    return {"intent": intent.prompt_block(answers),
            "baseline": intent.baseline_block(intent.split_subjects(rec), copy_body(rec, "en"))}


def _source(rec: dict, conn, cfg) -> dict:
    """这份记录区间里的转写，连同它的派生物：``rows`` = 引擎里逐行的 ``(ts, text)``（§63.13
    的逐条锚按行找原话），``plain`` = 拼起来的正文（词数与 prompt 用它），``hits`` /
    ``glossary`` = §63.14 术语表在这份转写里换了几处、以及进 prompt 的正确拼法清单。
    术语替换**逐行**做、在进模型之前做——owner 明确列出的听错形不交给模型再赌一次。"""
    start, end = sessions.parse_ts(rec["start"]) or 0.0, sessions.parse_ts(rec["end"]) or 0.0
    glossary = glossary_mod.load(cfg)
    rows, hits = glossary_mod.apply_rows(sessions.transcript_rows_between(conn, start, end), glossary)
    return {"start": start, "end": end, "rows": rows, "hits": hits,
            "plain": "\n".join(line for _ts, line in rows),
            "glossary": glossary_mod.prompt_block(glossary)}


def _transcript_view(src: dict, shape: str, tz: str) -> tuple:
    """``(进 prompt 的转写, 校验用的对照)``（§63.13）：可发送长版的转写逐行带 ``[HH:MM]`` 戳
    （条目的锚要指得到自己靠的那一行），对照 = 戳的集合 + 归一正文（锚的原话在不在转写里）；
    五行形照旧给素文、对照 None（§63.3 的模板禁时间戳，一个字符没动）。"""
    if shape != text.SHAPE_SECTIONS:
        return src["plain"], None
    stamps = [sessions.stamp(ts, tz) for ts, _line in src["rows"]]
    return sessions.stamped_transcript(src["rows"], tz), text.anchor_context(src["plain"], stamps)


def fill_record(rec: dict, conn, st: dict, runner, cfg, now: float,
                note: Optional[str] = None, partial: bool = False,
                shape: Optional[str] = None, answers: Optional[list] = None) -> dict:
    """Read the transcript for the record's interval and (re)generate its
    text in place, in the shape :func:`record_shape` resolves (§63.10).
    ``answers`` (§63.11) are the owner's picks on the questions derived from the
    version currently on the record — malformed ones are dropped whole
    (``recap_intent.clean_answers``), never half-applied. §63.12: the sendable
    shape's items come back with their **stable tags** — the previous version's
    tagged items ride in the prompt's untrusted fence (:func:`_tag_args`) and
    the final tags are decided here, on the storage side (:func:`_tagged`).
    Thin / silent meetings never reach the model."""
    tz = st["options"].timezone
    shape = record_shape(rec, st, shape)
    answers = intent.clean_answers(answers)
    src = _source(rec, conn, cfg)
    words = text.transcript_words(src["plain"])
    rec["transcript_words"] = words
    # §63.14 add-only：术语表在这份转写里换了几处听错的词（只有计数，宪法第 9 条）
    rec["glossary_hits"] = src["hits"]
    problems, repairs = [], []
    if words == 0:
        lines, quality = None, store.QUALITY_NO_AUDIO
    elif words < text.MIN_TRANSCRIPT_WORDS:
        lines, quality = None, store.QUALITY_THIN
    else:
        transcript, context = _transcript_view(src, shape, tz)
        args = {"transcript": transcript, "priors": store.priors_for(src["start"], tz),
                "voice_profile": voice_profile_text(), "note": note, "partial": partial,
                "shape": shape, "glossary": src["glossary"],
                "meta": {"when": _when(rec, tz), "app": rec["app"],
                         "duration_min": rec["duration_min"]},
                **_tag_args(rec, shape), **_intent_args(rec, answers)}
        # §63.15：超时按这份转写的词数算（地板 = 原来的定值），重试用同一个数
        lines, quality, problems, repairs = generate_lines(args, runner, cfg,
                                                           drop_prior=intent.drops_prior(answers),
                                                           timeout=timing.llm_timeout_s(words),
                                                           context=context)
        lines = _tagged(rec, lines, shape)
    _apply_lines(rec, lines, quality, note, partial, now, problems, repairs, shape=shape,
                 answers=answers)
    return rec


def _row_label(rec: dict, tz: str) -> str:
    start = sessions.parse_ts(rec["start"]) or 0.0
    end = sessions.parse_ts(rec["end"]) or start
    return "%s–%s · %s · %s min" % (sessions.local_dt(start, tz).strftime("%H:%M"),
                                    sessions.local_dt(end, tz).strftime("%H:%M"),
                                    rec["app"], rec["duration_min"])


def _announce(rec: dict, st: dict) -> None:
    """Notification (§28 relay; only when there is text to copy) + metadata-
    only analytics — never the recap text, never the transcript."""
    if store.has_text(rec):
        label = _row_label(rec, st["options"].timezone)
        notify.notify(failures.pick("会议纪要已生成", "Meeting recap ready"),
                      failures.pick("%s —— 打开看板「会议纪要」复制" % label,
                                    "%s — open the board's Meeting recaps page to copy" % label),
                      kind=NOTIFY_KIND)
    analytics.log_event("recap_generated", app=rec.get("app"), duration_min=rec.get("duration_min"),
                        words=rec.get("transcript_words"), quality=rec.get("quality"),
                        version=rec.get("version"), partial=rec.get("partial") or None,
                        shape=rec.get("shape"), glossary_hits=rec.get("glossary_hits"))


# --------------------------------------------------------------------------- #
# Slack draft (§63.4) — only reachable when the toggle is on
# --------------------------------------------------------------------------- #
def _draft_language(st: dict, cfg) -> str:
    lang = st["default_language"]
    if lang == "auto":
        lang = "en" if getattr(cfg, "language", "zh") == "en" else "zh"
    return lang if lang in ("en", "zh") else "en"


def _draft_body(rec: dict, st: dict, cfg) -> str:
    """草稿正文 = 那一语言粘出去的那份（§63.10 两种形状同一条路）；那一版缺这门
    语言就退到另一门——空草稿比一份英文草稿差得多。"""
    lang = _draft_language(st, cfg)
    body = copy_body(rec, lang) or copy_body(rec, "en" if lang == "zh" else "zh")
    return body if body.strip() else ""


def post_slack_draft(rec: dict, channel_id: str, st: dict, runner, cfg, now: float) -> dict:
    """Whitelisted call → ``rec["slack_draft"]`` receipt. Disabled toggle or a
    recap without text short-circuits without any model call.

    §63.10：「有正文」= ``store.has_text`` **且**渲染出来的那份非空。两道判据同源
    （两边都是 `recap_text.render_*`），第二道是兜底：一份正文渲染成空串的纪要
    宁可诚实地 ``failed``，也不许把一个空正文送进模型、把一份空草稿放进人的草稿箱。"""
    body = _draft_body(rec, st, cfg) if st["slack_draft_enabled"] and store.has_text(rec) else ""
    if not st["slack_draft_enabled"]:
        receipt = {"status": slack_draft.STATUS_DISABLED, "channel_link": None}
    elif not body:
        receipt = {"status": slack_draft.STATUS_FAILED, "channel_link": None}
    else:
        try:
            raw = _call_model(slack_draft.build_prompt(channel_id, body), runner, cfg,
                              slack_draft.ALLOWLIST_ARGV, DRAFT_TIMEOUT_S)
            receipt = slack_draft.parse_result(raw)
        except Exception as exc:  # noqa: BLE001 - a draft failure is a row badge, not a crash
            _log("slack draft failed for %s: %s" % (rec["key"], exc))
            receipt = {"status": slack_draft.STATUS_FAILED, "channel_link": None}
    receipt["at"] = _iso(now)
    rec["slack_draft"] = receipt
    return receipt


def _auto_draft(rec: dict, st: dict, runner, cfg, now: float) -> None:
    """The CLOSED-time hook: configured target → draft; none → 未投草稿."""
    if not st["slack_draft_enabled"] or not store.has_text(rec):
        return
    channel = slack_draft.resolve_target(st["slack_targets"], rec["app"])
    if channel is None:
        rec["slack_draft"] = {"status": slack_draft.STATUS_NO_TARGET, "channel_link": None,
                              "at": _iso(now)}
        return
    post_slack_draft(rec, channel, st, runner, cfg, now)


# --------------------------------------------------------------------------- #
# the cron round
# --------------------------------------------------------------------------- #
def _read_new(conn, state: dict, opts: sessions.Options) -> list:
    """Advance the cursor; return the new presence buckets (frames ∪ audio)."""
    frames, f_last = sessions.read_frame_events(conn, state["cursor"].get("frames", 0), opts.rules)
    audio, a_last = sessions.read_audio_events(conn, state["cursor"].get("audio", 0))
    state["cursor"] = {"frames": f_last, "audio": a_last}
    return frames + audio


def _day_count(state: dict, now: float, tz: str) -> int:
    """Today's generation count (local date); a new day resets it."""
    today = sessions.local_dt(now, tz).strftime("%Y-%m-%d")
    day = state.get("day") if isinstance(state.get("day"), dict) else {}
    if day.get("date") != today:
        state["day"] = {"date": today, "count": 0}
    return int(state["day"].get("count") or 0)


def _bump_day(state: dict) -> None:
    state["day"]["count"] = int(state["day"].get("count") or 0) + 1


def _regenerate_late(hits: dict, conn, st: dict, runner, cfg, now: float, summary: dict) -> None:
    """Late transcript slices → version + 1 on the CLOSED recap they belong to."""
    for key in sorted(hits):
        rec = store.load_recap(key)
        if not rec:
            continue
        try:
            fill_record(rec, conn, st, runner, cfg, now)
            store.save_recap(rec)
            summary["regenerated"] += 1
            _log("late slice: regenerated %s (v%s)" % (key, rec["version"]))
        except Exception as exc:  # noqa: BLE001 - one recap's failure stays its own
            _log("late slice regeneration failed for %s: %s" % (key, exc))


def _record_for(session: sessions.Session, key: str, status: str) -> dict:
    """Existing recap (a partial 现在生成, or a re-close) or a fresh skeleton."""
    rec = store.load_recap(key) or store.new_record(session, key, status)
    rec.update({"status": status, "end": _iso(session.end), "frames": int(session.frames),
                "audio_rows": int(session.audio_rows),
                "duration_min": int(round(session.span_s / 60.0))})
    return rec


def _generate_closed(session: sessions.Session, key: str, conn, st: dict, runner, cfg,
                     now: float, state: dict, summary: dict) -> bool:
    """Try to produce the CLOSED recap; True = consumed (done or given up)."""
    rec = _record_for(session, key, sessions.CLOSED)
    try:
        fill_record(rec, conn, st, runner, cfg, now)
    except Exception as exc:  # noqa: BLE001 - retried next round, given up after N
        n = int(state["failures"].get(key, 0)) + 1
        state["failures"][key] = n
        _log("generation failed for %s (%d/%d): %s" % (key, n, MAX_GENERATION_FAILURES, exc))
        if n < MAX_GENERATION_FAILURES:
            return False
        # §63.10：放弃的那一版也带着**解析出来的**形状落地——不传就回落到参数默认的
        # 五行形，于是一次超时能把一份选定的可发送长版永久打回五行（`record_shape`
        # 的第二级读的就是记录上这个键），而且没有任何一句话说过这件事
        _apply_lines(rec, None, store.QUALITY_FAILED, None, False, now,
                     shape=record_shape(rec, st))
    state["failures"].pop(key, None)
    _auto_draft(rec, st, runner, cfg, now)
    store.save_recap(rec)
    summary["generated"] += 1
    _bump_day(state)
    _announce(rec, st)
    _log("recap %s: %s (%s words, %s)" % (key, rec["quality"], rec["transcript_words"], rec["app"]))
    return True


def _pending(conn, session: sessions.Session, opts: sessions.Options) -> int:
    return sessions.pending_chunks_between(conn, session.start - opts.gap_s, session.end)


class _Budget:
    """max_per_run × max_per_day — a CLOSED meeting over the cap is held in the
    buffer for the next round, never dropped."""

    def __init__(self, per_run: int, day_left: int) -> None:
        self.per_run, self.day_left = int(per_run), int(day_left)

    def exhausted(self) -> bool:
        return self.per_run <= 0 or self.day_left <= 0

    def take(self) -> None:
        self.per_run -= 1
        self.day_left -= 1


def _closed_outcome(s: sessions.Session, key: str, conn, st: dict, runner, cfg, now: float,
                    state: dict, summary: dict, budget: _Budget) -> bool:
    """CLOSED session → True when its events may leave the buffer (generated,
    given up, or never a meeting); False = hold for the next round."""
    if not s.eligible(st["options"]):
        return True  # quiet + too small = never a meeting; drop it
    if budget.exhausted():
        return False
    budget.take()
    return _generate_closed(s, key, conn, st, runner, cfg, now, state, summary)


def _process_sessions(clusters: list, conn, st: dict, runner, cfg, now: float,
                      state: dict, summary: dict) -> "tuple[list, list]":
    """Judge every session; returns (buffer events to keep, OPEN rows)."""
    opts, keep, open_rows = st["options"], [], []
    budget = _Budget(st["max_per_run"], st["max_per_day"] - _day_count(state, now, opts.timezone))
    for s in clusters:
        key = s.key(opts.timezone)
        if sessions.verdict(s, now, _pending(conn, s, opts), opts) == sessions.OPEN:
            keep += s.events
            if s.eligible(opts):
                open_rows.append(_record_for(s, key, sessions.OPEN))
        elif not _closed_outcome(s, key, conn, st, runner, cfg, now, state, summary, budget):
            keep += s.events
    return keep, open_rows


def _round(conn, state: dict, st: dict, runner, cfg, now: float, summary: dict) -> None:
    opts = st["options"]
    new_events = _read_new(conn, state, opts)
    hits, fresh = sessions.late_slices(new_events, store.closed_intervals(now), opts.gap_s)
    _regenerate_late(hits, conn, st, runner, cfg, now, summary)
    clusters = sessions.sessions_from(sessions.merge_buckets(list(state.get("events") or []) + fresh), opts)
    keep, open_rows = _process_sessions(clusters, conn, st, runner, cfg, now, state, summary)
    state["events"] = sessions.merge_buckets(keep)
    state["open"] = open_rows
    summary["open"] = len(open_rows)
    summary["pruned"] = store.prune(now, st["retention_days"], st["dismissed_retention_days"])


def _run_locked(conn, st: dict, runner, cfg, now: float, summary: dict) -> None:
    state = store.load_state()
    if state is None:
        store.save_state(store.new_state(sessions.max_ids(conn), _iso(now)))
        summary["first_run"] = True
        _log("first run: marker set at now, no backfill")
        return
    _round(conn, state, st, runner, cfg, now, summary)
    state["updated_at"] = _iso(now)
    store.save_state(state)


def _boot(cfg, now: Optional[float]) -> tuple:
    cfg = cfg or config.load_config()
    return cfg, store.settings(cfg), (time.time() if now is None else float(now))


def _open_db(st: dict):
    """Read-only connection to the engine DB (config `recap.db_path` or
    ~/.screenpipe/db.sqlite); FileNotFoundError when it is not there."""
    db = Path(st["db_path"]) if st["db_path"] else sessions.default_db_path()
    if not db.exists():
        raise FileNotFoundError(str(db))
    return sessions.connect_readonly(db)


def _with_db(conn, st: dict, fn):
    """Run ``fn(conn)`` on the given connection or a fresh one that is closed
    afterwards (tests pass their fixture connection)."""
    own = conn is None
    conn = conn or _open_db(st)
    try:
        return fn(conn)
    finally:
        if own:
            conn.close()


def run_once(now: Optional[float] = None, conn=None, runner=None, cfg=None) -> dict:
    """One cron round. ``conn`` / ``runner`` / ``cfg`` are the injection seams
    (tests hand in a fixture sqlite and a fake runner; no real claude)."""
    cfg, st, now = _boot(cfg, now)
    summary = {"first_run": False, "open": 0, "generated": 0, "regenerated": 0,
               "pruned": 0, "skipped": None}
    if not st["enabled"]:
        summary["skipped"] = "disabled"
        return summary
    try:
        _with_db(conn, st, lambda c: _run_locked(c, st, runner, cfg, now, summary))
    except FileNotFoundError:
        summary["skipped"] = "no_db"  # no engine DB on this machine: nothing to do
    return summary


# --------------------------------------------------------------------------- #
# inbox-spawned entry points
# --------------------------------------------------------------------------- #
def _open_session_record(key: str) -> Optional[dict]:
    rows = store.open_rows(store.load_state() or {})
    return next((o for o in rows if o.get("key") == key), None)


def _target_record(key: str) -> Optional[dict]:
    rec = store.load_recap(key) or _open_session_record(key)
    if rec is None:
        _log("generate: unknown key %s" % key)
    return rec


def generate(key: str, note: Optional[str] = None, partial: bool = False,
             now: Optional[float] = None, conn=None, runner=None, cfg=None,
             shape: Optional[str] = None, answers: Optional[list] = None) -> Optional[dict]:
    """「重新生成」(CLOSED, with the owner's note) / 「现在生成」(OPEN, partial);
    ``shape`` (§63.10) is the 快速五行 / 可发送长版 pick, sticky on the record;
    ``answers`` (§63.11) are the owner's answers to the intent questions — the
    second version is generated from them and the first stays as ``baseline``."""
    cfg, st, now = _boot(cfg, now)
    rec = _target_record(key)
    if rec is None:
        return None
    partial = bool(partial) or rec.get("status") == sessions.OPEN
    _with_db(conn, st, lambda c: fill_record(rec, c, st, runner, cfg, now, note=note,
                                             partial=partial, shape=shape, answers=answers))
    store.save_recap(rec)
    _announce(rec, st)
    _log("generate %s: v%s %s partial=%s shape=%s answers=%d"
         % (key, rec["version"], rec["quality"], partial, rec.get("shape"),
            len((rec.get("intent") or {}).get("answers") or [])))
    return rec


def _restorable(entry: dict, version: int) -> bool:
    """Is this entry version ``version`` **with** text? (An entry without ``en``
    is not a version anyone can go back to; same predicate as the projection
    handles — ``recap_store.has_lines``.)"""
    return entry.get("version") == version and store.has_text(entry)


def _history_entry(rec: dict, version: int) -> Optional[dict]:
    """The stored version ``version`` (newest entry wins on a duplicate), else None."""
    matches = [entry for entry in (rec.get("history") or [])
               if isinstance(entry, dict) and _restorable(entry, version)]
    return matches[-1] if matches else None


def _copy_list(value) -> Optional[list]:
    """history 条目上的一个正文键 → 自己的副本；不是 list = None（手改过的文件里
    什么都可能有，回退永不因为一条坏条目抛）。"""
    return list(value) if isinstance(value, list) else None


def _entry_quality(entry: dict) -> str:
    """The badge the restored text was born with; an unknown / absent value
    (entries written before §63.9 added the key) becomes 需复核 — never a
    fabricated ``ok``. 「粘贴前请通读一遍」is the safe direction to be wrong in."""
    quality = entry.get("quality")
    return quality if quality in store.QUALITIES else store.QUALITY_NEEDS_REVIEW


def _sections_body(rec: dict) -> Optional[dict]:
    """§63.10 长版的两语正文，形制照 :func:`recap_text.validate_sections_detail`
    要的那一份；手改过的条目里节结构坏掉 = None（上面读成空台账，不是崩掉的回退）。"""
    body = {"en": rec.get("sections_en"), "zh": rec.get("sections_zh")}
    return body if all(text.sections_wellformed(v) for v in body.values() if v) else None


def _lines_body(rec: dict) -> Optional[dict]:
    """五行形的两语正文；哪一行不是字符串 = None（同上，坏正文不判、也不抛）。"""
    body = {"en": rec.get("en") or [], "zh": rec.get("zh") or []}
    return body if all(isinstance(line, str) for lines in body.values() for line in lines) else None


def _restored_problems(rec: dict) -> list:
    """The §63.3 追记 findings for the text a revert just put on the record —
    computed over those lines **in their own shape** (§63.10: the sendable long
    form is judged by :func:`recap_text.validate_sections_detail`), never
    copied from another version, and only behind 需复核 (an ``ok`` version
    validated clean at birth or after its trim; a reason list under an ``ok``
    badge would contradict it). A 需复核 badge with no reason is the very state
    issue #298 was filed about (§63.6 追记 2026-09-15 修正, R-216 / D77).
    Mangled text in a hand-edited entry = empty ledger, never a crashed revert
    (宪法第 11 条)."""
    if rec.get("quality") != store.QUALITY_NEEDS_REVIEW:
        return []
    shape = text.normalize_shape(rec.get("shape"))
    body = _sections_body(rec) if shape == text.SHAPE_SECTIONS else _lines_body(rec)
    return [] if body is None else text.validate_detail_for(shape, body)


def _carried_problems(entry: dict) -> list:
    """§63.13：回退时**只**从条目的出生台账（add-only ``problems``，§63.13 起 `_push_history` 存入）
    带回 `anchor_unverified` 那几行——它们要对着模型当时看到的转写才算得出，`_restored_problems`
    没有转写可对；其余 code 都能重算，不搬（把存着的发现挪过来是 D77 明令的撒谎）。本键之前入库
    的条目 / 非 dict 的行 = 空。"""
    rows = entry.get("problems") if isinstance(entry, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("code") == text.CODE_ANCHOR_UNVERIFIED]


def _entry_repairs(entry: dict) -> list:
    """The trims the restored version was born with (add-only entry key, §63.6
    追记 2026-09-15 修正); entries from before the key — or junk — restore none."""
    repairs = entry.get("repairs")
    return [r for r in repairs if isinstance(r, dict)] if isinstance(repairs, list) else []


def _restore_payload(rec: dict, entry: dict) -> None:
    """§63.10：把那一版的**形状**连同正文一起搬回来（另一形的键清空），粘出去的正文
    重算一遍（存着的 `copy_*` 可能来自本键之前入库的条目 = 缺）。形状按条目**真正带着
    的正文**认，不按它那个 `shape` 字符串——手改过的条目里两者可以打架，而回退出来的
    那一版必须真的有正文。"""
    sections = store.has_lines(entry.get("sections_en"))
    for lang in ("en", "zh"):
        rec[lang] = None if sections else _copy_list(entry.get(lang))
        rec["sections_" + lang] = _copy_list(entry.get("sections_" + lang)) if sections else None
    rec["shape"] = text.SHAPE_SECTIONS if sections else text.SHAPE_LINES
    _write_copy_bodies(rec)


def _apply_history_entry(rec: dict, entry: dict, now: float) -> None:
    """Non-destructive revert (§63.9): the CURRENT text goes into history first,
    then the chosen entry's text becomes version + 1 with a fresh
    ``generated_at``. Nothing is overwritten, so a revert is itself revertible.

    ``note`` is cleared (the correction note belonged to a generation that is
    no longer what the record says). ``problems`` are recomputed over the
    restored text (:func:`_restored_problems`) and ``repairs`` come back from
    the entry's own add-only key (:func:`_entry_repairs`) — §63.6 追记
    2026-09-15 修正: an empty ledger was only honest as long as the reason was
    unknowable; for the text now on the record it is not."""
    _push_history(rec)
    rec["version"] = int(rec.get("version") or 0) + 1
    rec["generated_at"] = _iso(now)
    rec["partial"] = bool(entry.get("partial"))
    _restore_payload(rec, entry)
    rec["quality"] = _entry_quality(entry)
    rec["note"] = None
    rec["problems"] = _restored_problems(rec)
    if rec["quality"] == store.QUALITY_NEEDS_REVIEW:
        rec["problems"] += _carried_problems(entry)       # §63.13：对不着转写的那一种从出生台账带回
    rec["repairs"] = _entry_repairs(entry)
    # §63.14：术语替换的计数属于产出那份正文的那一次生成，回退搬不回来 = None（不写 0：
    # 「换了 0 处」是一个我们没有资格说的数）
    rec["glossary_hits"] = None
    # add-only：这一版的正文是从第几版搬回来的（面板据它说「由第 N 版回退而来」）
    rec["reverted_from"] = int(entry["version"])


def revert(key: str, to_version, now: Optional[float] = None) -> Optional[dict]:
    """「回退到这一版」(§63.9, issue #300) — copy a stored version's text back
    onto the record as a new version. No model call, no network, no config: the
    data is already on disk (so no ``runner`` / ``cfg`` seam either). Unknown
    key / no such stored version = honest None (the caller exits 1 and the
    reason is in state/recap.log); the record is never touched on a miss."""
    now = time.time() if now is None else float(now)
    rec = store.load_recap(key)
    if rec is None:
        _log("revert: unknown key %s" % key)
        return None
    try:
        version = int(to_version)
    except (TypeError, ValueError):
        _log("revert %s: bad target version %r" % (key, to_version))
        return None
    entry = _history_entry(rec, version)
    if entry is None:
        _log("revert %s: no stored version %s (stored: %s)"
             % (key, version, [h["version"] for h in store.history_versions(rec)]))
        return None
    _apply_history_entry(rec, entry, now)
    store.save_recap(rec)
    # 元数据（宪法第 9 条：正文永不进 analytics）
    analytics.log_event("recap_reverted", app=rec.get("app"), version=rec.get("version"),
                        reverted_from=rec.get("reverted_from"), quality=rec.get("quality"))
    _log("revert %s: v%s restores v%s (%s)" % (key, rec["version"], version, rec["quality"]))
    return rec


def slack_draft_for(key: str, channel_id: str, now: Optional[float] = None,
                    runner=None, cfg=None) -> Optional[dict]:
    """「投到 Slack 草稿」with an explicit conversation pick."""
    cfg, st, now = _boot(cfg, now)
    rec = store.load_recap(key)
    if rec is None or not store.CHANNEL_ID_RE.match(str(channel_id or "")):
        _log("slack draft: unknown key or bad channel id (%s)" % key)
        return None
    receipt = post_slack_draft(rec, channel_id, st, runner, cfg, now)
    store.save_recap(rec)
    _log("slack draft %s: %s" % (key, receipt["status"]))
    return receipt


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _ok(result) -> int:
    """按钮入口的退出码：None（未知 key / 畸形 / 这一版不存在）= 1，其余 0。"""
    return 0 if result else 1


def _cli_answers(raw) -> list:
    """``--answers '["split1=drop"]'`` → 答案表；坏 JSON / 词表外 = ``[]``（这一次
    照没有答案出稿，理由进日志——一个畸形的答案表绝不静默改写这份纪要，宪法第 11 条）。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    answers = intent.clean_answers(parsed)
    if not answers:
        _log("generate: ignoring malformed --answers %r" % (str(raw)[:120],))
    return answers


def _dispatch(args) -> int:
    if args.generate:
        return _ok(generate(args.generate, note=args.note, partial=args.partial,
                            shape=args.shape, answers=_cli_answers(args.answers)))
    if args.slack_draft:
        return _ok(slack_draft_for(args.slack_draft, args.channel_id))
    if args.revert:
        return _ok(revert(args.revert, args.to_version))
    summary = run_once()
    print("recap: %s" % summary)
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m act.recap", description="meeting recaps (§63)")
    ap.add_argument("--once", action="store_true", help="one cron round (default)")
    ap.add_argument("--generate", metavar="KEY", help="(re)generate one recap")
    ap.add_argument("--note", help="owner correction for --generate (≤500 chars)")
    ap.add_argument("--partial", action="store_true", help="OPEN session: recap so far")
    ap.add_argument("--shape", choices=list(text.SHAPES), default=None,
                    help="output shape for --generate (default: the record's, then config)")
    ap.add_argument("--answers", default="",
                    help='§63.11 intent answers for --generate, compact JSON: \'["split1=drop"]\'')
    ap.add_argument("--slack-draft", metavar="KEY", help="place the recap as a Slack draft")
    ap.add_argument("--channel-id", default="", help="Slack conversation id for --slack-draft")
    ap.add_argument("--revert", metavar="KEY", help="restore a stored version (§63.9)")
    ap.add_argument("--to-version", type=int, default=0, help="stored version for --revert")
    args = ap.parse_args(argv)
    wait = 0.0 if not (args.generate or args.slack_draft or args.revert) else LOCK_WAIT_S
    try:
        with Lock(wait) as ok:
            if not ok:
                _log("another recap run holds the lock — skipping")
                return 0
            return _dispatch(args)
    except Exception:  # noqa: BLE001 - the cron chain must never see a traceback exit
        _log("run crashed:\n" + traceback.format_exc())
        print("recap: failed (see %s)" % store.log_path(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
