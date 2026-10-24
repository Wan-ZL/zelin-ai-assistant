#!/usr/bin/env python3
"""Demo-data seeder: writes an ENTIRELY FICTIONAL ``state/recap/`` (meeting recaps).

Purpose: start the board server (``python3 -m server``) against a throwaway
AIASSISTANT_HOME for the web visual goldens, the e2e specs, the README
screenshots, ``scripts/dev-preview.sh`` and the README audit's ``--render``
mode. Never point it at a real home: it overwrites the recap files it owns.

    python3 scripts/demo_seed.py /tmp/assistant-demo                  # scene "initial"
    python3 scripts/demo_seed.py /tmp/assistant-demo --scene generating
    python3 scripts/demo_seed.py /tmp/assistant-demo --now 2026-09-02T12:00:00Z   # fixed clock
    python3 scripts/demo_seed.py /tmp/assistant-demo --check          # validate what is there

v2.0.0 (lite 2.0): the 1.x seeder wrote a ``state/dashboard.json`` full of
cards for the retired board; the board, its cards and dashboard.json left with
the Act pipeline (full text at git tag ``full``). The fixture is now what the
lite app shows: the meeting recaps page (CONTRACT §63), read by the server
through ``act/lib/recap_store`` (``GET /api/recaps``). Files written, all under
``<home>/state/``:

    recap/recaps/<key>.json   one closed recap per meeting (act/recap.py's record shape)
    recap/sessions.json       the cursor document with one OPEN meeting (进行中)
    recap/marks.json          server-owned flags: one archived (sent_at), one dismissed
    recap_requests.json       scene "generating" only: one §63.8 request in flight

Every recap carries both languages (``en`` / ``zh``, or ``sections_en`` /
``sections_zh``), so there is no language flag: the UI language is the
viewer's choice. The paste-ready ``copy_*`` text is rendered by the real
``act/lib/recap_text.render_for`` and the ``needs_review`` problems by the
real ``validate_detail_for``, so the fixture cannot drift from the daemon's
own output. ``--check`` round-trips the written files through
``act/lib/recap_store.snapshot`` (the exact payload of GET /api/recaps).

All names, projects and lines below are fictional (inkweld, example-bench,
alex.doe, sam.rivera, jordan.lee); never real coworker or company data.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from act.lib import recap_text  # noqa: E402  (stdlib-only module; no PyYAML needed)

SCENES = ("initial", "empty", "generating")
DEFAULT_SCENE = "initial"

# act/lib/recap_store vocabularies (tests/test_demo_seed.py pins these equal to the
# real constants; repeated here so seeding needs no PyYAML).
QUALITIES = ("ok", "needs_review", "thin_transcript", "no_audio", "generation_failed")
STATUS_CLOSED = "closed"
STATUS_OPEN = "open"


# --------------------------------------------------------------------------- #
# time helpers
# --------------------------------------------------------------------------- #
def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key(start: dt.datetime, app: str) -> str:
    return "meeting:%s-%s" % (start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H%M"), app)


def _ago(now: dt.datetime, hours: float) -> dt.datetime:
    """A meeting start ``hours`` before ``now``, floored to the 5-minute mark (always in the past)."""
    t = (now - dt.timedelta(hours=hours)).astimezone(dt.timezone.utc)
    return t.replace(minute=t.minute - t.minute % 5, second=0, microsecond=0)


# --------------------------------------------------------------------------- #
# the fictional meetings
# --------------------------------------------------------------------------- #
LINES_SYNC = {
    "en": ["Decided: ship the inkweld offline mode behind a flag in the next release.",
           "Split: alex.doe owns the sync queue; sam.rivera owns the conflict banner.",
           "Deadline: flag build ready for internal testing by Friday.",
           "Changed since last plan: conflict banner moved ahead of export polish.",
           "Open: whether offline edits count toward the storage quota."],
    "zh": ["定了：inkweld 离线模式下个版本带开关上线。",
           "分工：alex.doe 负责同步队列；sam.rivera 负责冲突提示条。",
           "截止：周五前出可内测的开关版本。",
           "较上次变化：冲突提示条排到导出优化之前。",
           "待定：离线编辑算不算进存储配额。"],
}

LINES_BENCH = {
    "en": ["Decided: example-bench keeps the 62-task held-out split frozen.",
           "Split: jordan.lee reruns the baseline; alex.doe writes the scoring rules.",
           "Deadline: none set",
           "Changed since last plan: nothing new",
           "Open: whether to add a second judge model for ties."],
    "zh": ["定了：example-bench 的 62 题留出集保持冻结。",
           "分工：jordan.lee 重跑基线；alex.doe 写评分规则。",
           "截止：未定",
           "较上次变化：无",
           "待定：平局时要不要加第二个评审模型。"],
}

# a first draft that the daemon's gate rejected: reported speech + an over-long line
LINES_REVIEW = {
    "en": ["Decided: the onboarding survey moves from five questions to three.",
           "Split: sam.rivera said she would draft the copy; jordan.lee wires the form.",
           "Deadline: copy draft before the design review on Wednesday.",
           "Changed since last plan: the survey no longer asks for a job title.",
           "Open: whether the survey shows on mobile in the first release, which depends on "
           "the layout work that has not been scheduled yet."],
    "zh": ["定了：新手引导问卷从五题减到三题。",
           "分工：sam.rivera 写文案；jordan.lee 接表单。",
           "截止：周三设计评审前交文案初稿。",
           "较上次变化：问卷不再问职位。",
           "待定：首个版本里移动端是否显示问卷。"],
}

SECTIONS_PLANNING = {
    "en": [
        {"key": "decided", "modality": "decided",
         "items": ["Quarter goal for inkweld is collaborative comments, not offline mode.",
                   "Weekly planning moves to Tuesdays."],
         "tags": ["D1", "D2"]},
        {"key": "split", "modality": "proposed",
         "items": ["alex.doe drafts the comments data model.",
                   "jordan.lee estimates the notification work."],
         "tags": ["S1", "S2"]},
        {"key": "deadline", "modality": "decided",
         "items": ["Data model draft reviewed before the next planning meeting."],
         "tags": ["L1"]},
        {"key": "open", "modality": "open",
         "items": ["Whether comments ship to the free plan on day one."],
         "tags": ["O1"]},
    ],
    "zh": [
        {"key": "decided", "modality": "decided",
         "items": ["inkweld 本季度目标是协作评论，不是离线模式。", "周会改到每周二。"],
         "tags": ["D1", "D2"]},
        {"key": "split", "modality": "proposed",
         "items": ["alex.doe 起草评论数据模型。", "jordan.lee 估算通知相关工作量。"],
         "tags": ["S1", "S2"]},
        {"key": "deadline", "modality": "decided",
         "items": ["下次规划会之前评审数据模型草案。"],
         "tags": ["L1"]},
        {"key": "open", "modality": "open",
         "items": ["免费版首日是否开放评论。"],
         "tags": ["O1"]},
    ],
}

# the first version of the planning recap (kept in history + baseline)
SECTIONS_PLANNING_V1 = {
    "en": [
        {"key": "decided", "modality": "decided",
         "items": ["Quarter goal for inkweld is collaborative comments."], "tags": ["D1"]},
        {"key": "split", "modality": "floated",
         "items": ["alex.doe may draft the comments data model."], "tags": ["S1"]},
        {"key": "open", "modality": "open",
         "items": ["Whether comments ship to the free plan on day one."], "tags": ["O1"]},
    ],
    "zh": [
        {"key": "decided", "modality": "decided",
         "items": ["inkweld 本季度目标是协作评论。"], "tags": ["D1"]},
        {"key": "split", "modality": "floated",
         "items": ["alex.doe 可能起草评论数据模型。"], "tags": ["S1"]},
        {"key": "open", "modality": "open",
         "items": ["免费版首日是否开放评论。"], "tags": ["O1"]},
    ],
}


def _record(start: dt.datetime, minutes: int, app: str) -> dict:
    """act/lib/recap_store.new_record's skeleton for a closed meeting."""
    end = start + dt.timedelta(minutes=minutes)
    return {
        "key": _key(start, app), "app": app, "start": _iso(start), "end": _iso(end),
        "duration_min": minutes, "frames": minutes * 30, "audio_rows": minutes * 12,
        "status": STATUS_CLOSED, "version": 0, "partial": False, "generated_at": None,
        "en": None, "zh": None, "quality": None, "transcript_words": 0,
        "shape": None, "sections_en": None, "sections_zh": None,
        "copy_en": None, "copy_zh": None,
        "note": None, "history": [], "slack_draft": None,
        "baseline": None, "intent": None, "tag_seq": {},
        "problems": [], "repairs": [], "reverted_from": None,
    }


def _with_lines(rec: dict, lines: dict, generated: dt.datetime, version: int = 1) -> dict:
    rec.update({"version": version, "generated_at": _iso(generated), "shape": recap_text.SHAPE_LINES,
                "en": list(lines["en"]), "zh": list(lines["zh"]),
                "copy_en": recap_text.render_for(recap_text.SHAPE_LINES, lines["en"], "en"),
                "copy_zh": recap_text.render_for(recap_text.SHAPE_LINES, lines["zh"], "zh"),
                "transcript_words": rec["duration_min"] * 140})
    problems = recap_text.validate_detail_for(recap_text.SHAPE_LINES, lines)
    rec["problems"] = problems
    rec["quality"] = "needs_review" if problems else "ok"
    return rec


def _with_sections(rec: dict, sections: dict, generated: dt.datetime, version: int) -> dict:
    payload = {"en": sections["en"], "zh": sections["zh"]}
    rec.update({"version": version, "generated_at": _iso(generated),
                "shape": recap_text.SHAPE_SECTIONS,
                "sections_en": copy.deepcopy(sections["en"]),
                "sections_zh": copy.deepcopy(sections["zh"]),
                "copy_en": recap_text.render_for(recap_text.SHAPE_SECTIONS, sections["en"], "en"),
                "copy_zh": recap_text.render_for(recap_text.SHAPE_SECTIONS, sections["zh"], "zh"),
                "transcript_words": rec["duration_min"] * 150})
    problems = recap_text.validate_detail_for(recap_text.SHAPE_SECTIONS, payload)
    rec["problems"] = problems
    rec["quality"] = "needs_review" if problems else "ok"
    return rec


def _history_entry(rec: dict) -> dict:
    """act/recap._push_history's entry for the record's current version."""
    return {"version": rec["version"], "generated_at": rec["generated_at"],
            "en": rec["en"], "zh": rec["zh"], "partial": bool(rec["partial"]),
            "quality": rec["quality"], "repairs": list(rec["repairs"]),
            "shape": rec["shape"], "sections_en": rec["sections_en"],
            "sections_zh": rec["sections_zh"], "copy_en": rec["copy_en"], "copy_zh": rec["copy_zh"]}


def _planning(now: dt.datetime) -> dict:
    """A sendable (sections) recap regenerated once: history + baseline + intent."""
    start = _ago(now, 3)
    rec = _with_sections(_record(start, 50, "meet"), SECTIONS_PLANNING_V1,
                         start + dt.timedelta(minutes=58), 1)
    rec["history"] = [_history_entry(rec)]
    rec["baseline"] = {"version": 1, "generated_at": rec["generated_at"], "shape": rec["shape"],
                       "copy_en": rec["copy_en"], "copy_zh": rec["copy_zh"]}
    rec = _with_sections(rec, SECTIONS_PLANNING, start + dt.timedelta(minutes=75), 2)
    rec["intent"] = {"answers": ["s1=decided"], "at": _iso(start + dt.timedelta(minutes=70)),
                     "version": 2}
    rec["tag_seq"] = {"D": 2, "S": 2, "L": 1, "O": 1}
    return rec


def _closed_recaps(now: dt.datetime) -> list:
    sync_start = _ago(now, 1.5)
    bench_start = _ago(now, 26)
    review_start = _ago(now, 22)
    thin_start = _ago(now, 50)
    noaudio_start = _ago(now, 46)
    sent_start = _ago(now, 74)
    dismissed_start = _ago(now, 96)
    thin = _record(thin_start, 12, "slack-huddle")
    thin.update({"version": 1, "generated_at": _iso(thin_start + dt.timedelta(minutes=14)),
                 "quality": "thin_transcript", "transcript_words": 120})
    noaudio = _record(noaudio_start, 25, "zoom")
    noaudio.update({"version": 1, "generated_at": _iso(noaudio_start + dt.timedelta(minutes=27)),
                    "quality": "no_audio", "audio_rows": 0})
    dismissed = _record(dismissed_start, 8, "facetime")
    dismissed.update({"version": 1, "generated_at": _iso(dismissed_start + dt.timedelta(minutes=10)),
                      "quality": "thin_transcript", "transcript_words": 40})
    return [
        _with_lines(_record(sync_start, 35, "zoom"), LINES_SYNC, sync_start + dt.timedelta(minutes=40)),
        _planning(now),
        _with_lines(_record(bench_start, 45, "teams"), LINES_BENCH, bench_start + dt.timedelta(minutes=50)),
        _with_lines(_record(review_start, 30, "zoom"), LINES_REVIEW, review_start + dt.timedelta(minutes=33)),
        thin,
        noaudio,
        _with_lines(_record(sent_start, 40, "meet"), LINES_SYNC, sent_start + dt.timedelta(minutes=45)),
        dismissed,
    ]


def _open_session(now: dt.datetime) -> dict:
    """One meeting still running (进行中): a sessions.json OPEN row, no text yet."""
    start = (now - dt.timedelta(minutes=18)).replace(second=0, microsecond=0)
    rec = _record(start, 18, "zoom")
    rec.update({"status": STATUS_OPEN, "end": _iso(now.replace(microsecond=0))})
    return rec


# --------------------------------------------------------------------------- #
# build: scene -> {relative path under <home>/state: document}
# --------------------------------------------------------------------------- #
def _marks(now: dt.datetime, closed: list) -> dict:
    """marks.json: the 7th recap archived (sent), the 8th dismissed; nothing for an empty scene."""
    if not closed:
        return {}
    archived, dismissed = closed[6], closed[7]
    return {archived["key"]: {"copied_at": _iso(now - dt.timedelta(days=3)),
                              "sent_at": _iso(now - dt.timedelta(days=3))},
            dismissed["key"]: {"dismissed_at": _iso(now - dt.timedelta(days=4))}}


def _sessions(now: dt.datetime, closed: list, open_rows: list) -> dict:
    return {"schema": 1, "first_run_at": _iso(now - dt.timedelta(days=30)),
            "cursor": {"frame": 0, "audio": 0}, "events": [], "open": open_rows,
            "day": {"date": now.strftime("%Y-%m-%d"), "count": len(closed)},
            "failures": {}, "updated_at": _iso(now)}


def build(scene: str = DEFAULT_SCENE, now: dt.datetime | None = None) -> dict:
    """The files a scene writes, keyed by their path relative to ``<home>/state``."""
    if scene not in SCENES:
        raise ValueError("unknown scene %r (one of %s)" % (scene, ", ".join(SCENES)))
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    empty = scene == "empty"
    closed = [] if empty else _closed_recaps(now)
    files = {"recap/recaps/%s.json" % rec["key"].replace(":", "_"): rec for rec in closed}
    files["recap/sessions.json"] = _sessions(now, closed, [] if empty else [_open_session(now)])
    files["recap/marks.json"] = _marks(now, closed)
    if scene == "generating":
        target = closed[3]["key"]       # regenerate the needs_review draft
        files["recap_requests.json"] = {target: {"requested_at": _iso(now - dt.timedelta(seconds=40)),
                                                 "launch": "running", "note": None}}
    return files


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _check_strings(problems: list, where: str, rec: dict) -> None:
    for field in ("key", "app", "start", "end", "status"):
        if not isinstance(rec.get(field), str) or not rec.get(field):
            problems.append("%s: %s must be a non-empty string" % (where, field))


def _check_vocab(problems: list, where: str, rec: dict) -> None:
    if rec.get("status") == STATUS_CLOSED and rec.get("quality") not in QUALITIES:
        problems.append("%s: quality %r is not one of %s" % (where, rec.get("quality"), QUALITIES))
    shape = rec.get("shape")
    if shape is not None and shape not in recap_text.SHAPES:
        problems.append("%s: shape %r is not one of %s" % (where, shape, recap_text.SHAPES))


def _gate_payload(rec: dict) -> dict:
    if rec.get("shape") == recap_text.SHAPE_SECTIONS:
        return {"en": rec.get("sections_en"), "zh": rec.get("sections_zh")}
    return {"en": rec.get("en"), "zh": rec.get("zh")}


def _check_gate(problems: list, where: str, rec: dict) -> None:
    """A record that says quality ok must pass the daemon's own gate."""
    if rec.get("quality") != "ok":
        return
    for finding in recap_text.validate_detail_for(rec.get("shape"), _gate_payload(rec)):
        problems.append("%s: quality ok but the gate says %s" % (where, finding["text"]))


def _check_record(problems: list, where: str, rec: dict) -> None:
    _check_strings(problems, where, rec)
    _check_vocab(problems, where, rec)
    _check_gate(problems, where, rec)


def validate(files: dict) -> list:
    """Shape problems of a built scene ([] = clean)."""
    problems: list = []
    for rel, doc in sorted(files.items()):
        if rel.startswith("recap/recaps/"):
            _check_record(problems, rel, doc)
            if rel != "recap/recaps/%s.json" % str(doc.get("key", "")).replace(":", "_"):
                problems.append("%s: file name does not match key %r" % (rel, doc.get("key")))
    for row in files.get("recap/sessions.json", {}).get("open", []):
        _check_record(problems, "sessions.json open %s" % row.get("key"), row)
    return problems


def snapshot(home: Path) -> dict:
    """GET /api/recaps for ``home`` (needs PyYAML: act/lib/recap_store imports config)."""
    from act.lib import recap_store
    return recap_store.snapshot(Path(home) / "state")


def check_home(home: Path) -> list:
    """Problems with what is on disk under ``home``: shape + the server's own projection."""
    state = Path(home) / "state"
    files = {}
    for path in sorted(state.glob("recap/recaps/*.json")) + [state / "recap" / "sessions.json",
                                                              state / "recap" / "marks.json"]:
        if path.exists():
            files[str(path.relative_to(state))] = json.loads(path.read_text(encoding="utf-8"))
    problems = validate(files)
    keys = {doc["key"] for rel, doc in files.items() if rel.startswith("recap/recaps/")}
    keys |= {row["key"] for row in files.get("recap/sessions.json", {}).get("open", [])}
    listed = {row["key"] for row in snapshot(home)["recaps"]}
    problems += ["%s is not in GET /api/recaps" % key for key in sorted(keys - listed)]
    return problems


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def seed(home: Path, scene: str = DEFAULT_SCENE, now: dt.datetime | None = None) -> dict:
    """Write a scene under ``home/state`` (replacing the recap files it owns)."""
    files = build(scene, now)
    state = Path(home) / "state"
    recaps = state / "recap" / "recaps"
    recaps.mkdir(parents=True, exist_ok=True)
    for old in recaps.glob("*.json"):
        old.unlink()
    (state / "recap_requests.json").unlink(missing_ok=True)
    for rel, doc in files.items():
        path = state / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    return files


def summary_line(files: dict) -> str:
    recaps = [doc for rel, doc in files.items() if rel.startswith("recap/recaps/")]
    opened = files.get("recap/sessions.json", {}).get("open", [])
    marks = files.get("recap/marks.json", {})
    return "demo recaps: %d closed, %d open, %d archived, %d dismissed, %d generating" % (
        len(recaps), len(opened), sum(1 for m in marks.values() if m.get("sent_at")),
        sum(1 for m in marks.values() if m.get("dismissed_at")),
        len(files.get("recap_requests.json", {})))


def _parse_now(parser: argparse.ArgumentParser, value):
    """``--now 2026-09-02T12:00:00Z`` → aware UTC datetime; absent → None (the real clock)."""
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        parser.error("--now must look like 2026-09-02T12:00:00Z")


def _report(problems: list, ok_line: str) -> int:
    for problem in problems:
        print("PROBLEM: " + problem, file=sys.stderr)
    print(ok_line)
    return 1 if problems else 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("home", help="the throwaway AIASSISTANT_HOME to write state/recap/ into")
    parser.add_argument("--scene", default=DEFAULT_SCENE, choices=SCENES)
    parser.add_argument("--now", default=None,
                        help="ISO-8601 UTC clock to build the scene from (default: the real now); "
                             "a fixed value gives byte-identical files run to run")
    parser.add_argument("--check", action="store_true",
                        help="validate the recaps already under HOME (shape + GET /api/recaps round trip)")
    args = parser.parse_args(argv)
    home = Path(args.home).expanduser()
    if args.check:
        problems = check_home(home)
        return _report(problems, "demo recaps: %s" % ("ok" if not problems else "%d problem(s)" % len(problems)))
    files = seed(home, args.scene, _parse_now(parser, args.now))
    return _report(validate(files), summary_line(files) + " -> %s" % (home / "state" / "recap"))


if __name__ == "__main__":
    sys.exit(main())
