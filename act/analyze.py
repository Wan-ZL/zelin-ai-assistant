"""analyze — turn a terse debt item into a full, approvable proposal (CONTRACT §8).

``expand_debt(req)`` builds a prompt from the debt's title + notes + sources and
asks a headless ``claude -p --output-format text`` run for JSON. The run gets a
read-only tool whitelist (WebFetch/WebSearch + Slack read MCP tools) so it can
open links referenced in the debt item and ground the proposal in their actual
content — never any tool that sends messages or edits files:

    {summary, plan, cost_estimate_usd, target_repo, target_kind}

The result is folded back onto the requirement and its status is advanced to
``card_sent`` so it surfaces in the approval queue. Any failure (claude missing,
non-zero exit, unparseable output) falls back to a minimal card flagged for
manual attention — the debt item is never lost.

Run standalone: ``python -m act.analyze <req_id>``.
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Callable, Optional

from act.lib import analytics, config, sanitize
from act.lib.registry import Requirement, State, load, save
# Reuse the executor's single key-resolution path — do NOT duplicate the
# ANTHROPIC_API_KEY-from-~/.config fallback with divergent logic.
from act.executor import _runner_env


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #
def _sources_text(sources) -> str:
    if not sources:
        return "(no sources)"
    out = []
    for s in sources:
        if not isinstance(s, dict):
            continue
        chan = s.get("channel", "?")
        date = s.get("date", "?")
        quote = s.get("quote") or s.get("ref") or ""
        out.append(f"  - [{chan} {date}] {quote}")
    return "\n".join(out) or "(no sources)"


def routing_rules_text(cfg=None) -> str:
    """Repo inventory + target-repo routing rules.

    Shared verbatim between the debt-expansion prompt below and the quick-capture
    prompt (act/lib/quick_capture.py, CONTRACT §13) — single source, do not fork.
    """
    from act.lib import config as _config
    from act.lib.repos import inventory_text
    if cfg is None:
        cfg = _config.load_config()
    workbench = str(cfg.target_repo_path)
    return (
        "EXISTING REPOS under ~/Projects (name — what it is):\n"
        f"{inventory_text()}\n\n"
        "TARGET-REPO ROUTING RULES (judge, don't default):\n"
        f"- Work that belongs to one of the repos above -> that repo's path.\n"
        "- Paperwork / research / compliance / comms drafts / analysis docs -> "
        '文书类默认 chat 交付（delivery_mode="chat"，不落盘）；仅当明确需要长期'
        f"留存时才 -> {workbench}\n"
        "- A brand-new product/tool -> propose a NEW path under ~/Projects "
        "(short kebab-case name).\n"
        f"- When unsure, prefer {workbench} over guessing an unrelated "
        "project repo (curated repos must stay clean)."
    )


def _has_quick_capture_source(sources) -> bool:
    return any(isinstance(s, dict) and s.get("channel") == "quick_capture"
               for s in sources or [])


def build_expand_prompt(req: Requirement, cfg=None) -> str:
    from act.lib import config as _config
    if cfg is None:
        cfg = _config.load_config()
    qc_note = ""
    if _has_quick_capture_source(req.sources):
        qc_note = (
            "NOTE: channel=quick_capture 的来源是 Zelin 口述的一句话指令，上下文"
            "不全——需要你补全上下文，把它扩成一份完整、可批准的提案。\n\n"
        )
    return (
        "You are expanding a terse internal to-do ('debt item') into a concrete, "
        "approvable work proposal for Zelin (a solo ML engineer).\n\n"
        f"TITLE: {req.title}\n"
        f"TYPE: {req.type or 'unspecified'}\n"
        f"NOTES: {req.notes or '(none)'}\n"
        f"SOURCES (verbatim, for grounding):\n{_sources_text(req.sources)}\n\n"
        f"{qc_note}"
        "TOOL USE (mandatory when URLs appear in TITLE/NOTES/SOURCES): you have "
        "read-only tools — read the linked content FIRST, then write the proposal "
        "based on what you read. Do NOT just paraphrase the link.\n"
        "- Ordinary web pages: use WebFetch.\n"
        "- Slack message links of the form "
        "https://<team>.slack.com/archives/<CHANNEL>/p<digits>: use "
        "mcp__slack__slack_read_thread with channel_id=<CHANNEL> and message_ts "
        "derived from the digits after 'p' by inserting a decimal point 6 digits "
        'from the end (e.g. p1700000123456789 -> "1700000123.456789").\n'
        "- If a fetch fails or a tool is unavailable: say explicitly in "
        '"summary" that the link could not be read, and include a step like '
        "\"需要 Zelin 贴出链接内容\" in \"plan\". Never pretend you read it, and "
        "never merely restate the URL.\n\n"
        f"{routing_rules_text(cfg)}\n\n"
        "Return ONLY a single JSON object (no prose, no code fence) with exactly "
        "these keys:\n"
        '  "summary": string — one plain-language sentence, NO jargon, saying what '
        "this is and what happens once it's approved.\n"
        '  "plan": array of strings — the concrete steps to deliver it.\n'
        '  "cost_estimate_usd": number or null — rough API/compute cost, null if ~0.\n'
        '  "target_repo": string (REQUIRED) — absolute path chosen per the routing '
        "rules above.\n"
        '  "target_kind": "new" or "existing" — whether that path is a brand-new repo '
        "or an existing one.\n"
        '  "delivery_mode": "chat" or "repo" — how the result is delivered. '
        "chat = Slack/邮件回复稿、周报/汇报正文、一次性解释/分析/问答（此时 "
        '"definition_of_done" 必须写成"会话中给出最终可直接粘贴的成稿"这类表述，'
        "不得出现\"存入 xx repo/建分支\"字样）；repo = 代码、脚本、要长期留存引用"
        "的文档、多文件产出。\n"
        '  "definition_of_done": array of 1-3 strings — 大白话验收标准：从 Zelin 的角度'
        "说清\"怎样才算办完\"（产出物是什么、要到什么程度、送到哪里）。例如：\"一份可直接发 "
        '团队频道的总结草稿\"、\"注意事项已写进训练备忘\"。\n'
    )


# --------------------------------------------------------------------------- #
# tolerant JSON extraction
# --------------------------------------------------------------------------- #
def _extract_json(text: str) -> Optional[dict]:
    """Find and parse the first balanced ``{...}`` object in ``text``."""
    if not text:
        return None
    # fast path: whole thing is JSON
    stripped = text.strip()
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except (ValueError, TypeError):
        pass
    # scan for the first balanced brace block
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        chunk = text[start:i + 1]
                        try:
                            obj = json.loads(chunk)
                            if isinstance(obj, dict):
                                return obj
                        except (ValueError, TypeError):
                            break  # try the next '{'
        start = text.find("{", start + 1)
    return None


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
# Read-only research tools for the headless expansion agent. NEVER add
# slack_send_message / Bash / Edit here — the research phase must not send
# anything outward or touch local files (red line).
_EXPAND_ALLOWED_TOOLS = ",".join([
    "WebFetch",
    "WebSearch",
    "mcp__slack__slack_read_thread",
    "mcp__slack__slack_read_channel",
    "mcp__slack__slack_search_public_and_private",
    "mcp__slack__slack_read_user_profile",
])


def _default_runner(prompt: str) -> subprocess.CompletedProcess:
    prompt, _ = sanitize.scrub(prompt)
    return subprocess.run(
        # NOTE: prompt must come BEFORE --allowedTools — the claude CLI parses
        # --allowedTools as variadic and would swallow a trailing positional
        # prompt ("Input must be provided..." error, verified 2026-07-07).
        [
            "claude", "-p", prompt,
            "--output-format", "text",
            "--allowedTools", _EXPAND_ALLOWED_TOOLS,
        ],
        capture_output=True,
        text=True,
        timeout=420,  # agent may make multiple tool round-trips
        env=_runner_env(),
    )


# --------------------------------------------------------------------------- #
# apply helpers
# --------------------------------------------------------------------------- #
def _coerce_plan(plan) -> list:
    if isinstance(plan, list):
        return [str(p) for p in plan if str(p).strip()]
    if isinstance(plan, str) and plan.strip():
        return [ln.strip() for ln in plan.splitlines() if ln.strip()] or [plan.strip()]
    return []


def _coerce_cost(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _apply_expansion(req: Requirement, data: dict) -> None:
    summary = str(data.get("summary") or "").strip()
    if summary:
        req.summary = summary
    elif not req.summary:
        req.summary = req.title

    plan = _coerce_plan(data.get("plan"))
    if plan:
        req.plan = plan
    elif not req.plan:
        req.plan = [req.title]

    cost = _coerce_cost(data.get("cost_estimate_usd"))
    if cost is not None:
        req.cost_estimate_usd = cost

    tr = data.get("target_repo")
    if isinstance(tr, str) and tr.strip():
        req.target_repo = tr.strip()

    tk = data.get("target_kind")
    if isinstance(tk, str) and tk.strip().lower() in ("new", "existing"):
        req.target_kind = tk.strip().lower()

    # delivery_mode: "chat" | "repo" — anything illegal falls back to "repo"
    # (v0.10 contract; attribute-set so this works even before the registry
    # field lands in the Wire pass).
    dm = data.get("delivery_mode")
    dm = dm.strip().lower() if isinstance(dm, str) else ""
    req.delivery_mode = dm if dm in ("chat", "repo") else "repo"

    dod = data.get("definition_of_done")
    if isinstance(dod, list):
        items = [str(x).strip() for x in dod if str(x).strip()]
        if items:
            req.definition_of_done = items[:3]


def _apply_fallback(req: Requirement) -> None:
    if not req.summary:
        req.summary = req.title
    if not req.plan:
        req.plan = [req.title]
    tag = "(auto-expand failed, needs manual)"
    req.notes = (req.notes + " " + tag).strip() if req.notes else tag


# --------------------------------------------------------------------------- #
# public
# --------------------------------------------------------------------------- #
def expand_debt(
    req: Requirement,
    cfg: Optional[config.Config] = None,
    runner: Optional[Callable[[str], subprocess.CompletedProcess]] = None,
) -> Requirement:
    """Expand a debt item into a full proposal and advance it to ``card_sent``.

    ``runner`` is injectable for tests; it receives the prompt and returns a
    ``CompletedProcess``-like object with ``.stdout`` / ``.returncode``.
    """
    if cfg is None:
        cfg = config.load_config()
    if runner is None:
        runner = _default_runner

    prompt = build_expand_prompt(req, cfg)
    try:
        proc = runner(prompt)
        stdout = getattr(proc, "stdout", "") or ""
        rc = getattr(proc, "returncode", 0)
        data = _extract_json(stdout) if rc == 0 else None
        if data is None:
            _apply_fallback(req)
        else:
            _apply_expansion(req, data)
    except Exception:  # noqa: BLE001 - never lose the debt item
        _apply_fallback(req)

    req.set_status(State.CARD_SENT)
    save(req)
    _log_card(req)
    return req


def _log_card(req):
    analytics.log_event("card_sent", req=req.id, via="raise")


def _main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m act.analyze <req_id>")
        return 2
    req_id = argv[0]
    req = load(req_id)
    if req is None:
        print(f"error: requirement {req_id} not found in registry")
        return 1
    expand_debt(req)
    print(f"expanded {req_id} -> {req.status} (summary={req.summary!r})")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
