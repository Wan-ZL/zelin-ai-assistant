"""act/lib/recap_slack_draft.py — optional Slack DRAFT delivery of a recap (CONTRACT §63.4).

Off by default (`recap.slack_draft.enabled: false`). When the owner turns it
on, a CLOSED recap is placed as a **draft** in his own Slack「Drafts & Sent」,
attached to the target conversation — the send button stays under his finger.
Slack's public Web API has no drafts endpoint, so the only path is the
user-level Slack MCP's ``slack_send_message_draft`` via a headless claude
call whose argv is a **whitelist, not a prompt** (pinned by
tests/test_recap_slack_draft_allowlist.py)::

    claude -p <prompt> --output-format text
           --allowedTools mcp__slack__slack_send_message_draft,mcp__slack__slack_search_users

No ``--dangerously-skip-permissions``: any other tool the model reaches for is
refused by the CLI in headless mode. ``slack_send_message`` (a real send),
schedule and reaction tools are NOT in the list — :func:`allowlist_is_sealed`
is the test's assertion helper.

Target resolution never guesses: config `recap.slack_draft.targets`
{app-slug: channel_id} or the page's explicit「投到 Slack 草稿」pick; neither
→ the recap row says 未投草稿：无目标会话. Slack keeps one attached draft per
conversation: ``draft_already_exists`` is recorded as Slack 已有草稿 — never
overwritten, never retried, and a regenerated recap is not re-posted by
itself.
"""
from __future__ import annotations

import re
from typing import Optional

from act.lib import recap_text, sanitize

ALLOWED_TOOLS: tuple = ("mcp__slack__slack_send_message_draft", "mcp__slack__slack_search_users")
ALLOWLIST_ARGV: tuple = ("--allowedTools", ",".join(ALLOWED_TOOLS))

# tool-name / flag fragments that would mean egress or side effects — none may
# appear among the flags or the allowed tool names (the prompt is data, not law)
FORBIDDEN_FRAGMENTS: tuple = ("schedule", "reaction", "post_message", "chat_post", "dangerously")

# slack_draft.status vocabulary (add-only)
STATUS_POSTED = "posted"
STATUS_EXISTS = "draft_already_exists"
STATUS_FAILED = "failed"
STATUS_NO_TARGET = "no_target"
STATUS_DISABLED = "disabled"

_LINK = re.compile(r"^https://[A-Za-z0-9.-]+\.slack\.com/\S*$")


def allowlist_is_sealed(argv: list) -> bool:
    """True when the argv carries exactly our whitelist and no flag that could
    send, schedule, react or skip permissions (the test's one-line oracle;
    the prompt value after ``-p`` is data and is not inspected)."""
    flags = [str(a) for a in argv if str(a).startswith("--")]
    if "--allowedTools" not in flags:
        return False
    tools = str(argv[list(argv).index("--allowedTools") + 1])
    if tuple(tools.split(",")) != ALLOWED_TOOLS:
        return False
    joined = " ".join(flags) + " " + tools
    return not any(frag in joined for frag in FORBIDDEN_FRAGMENTS)


def resolve_target(targets: dict, app: str, explicit: Optional[str] = None) -> Optional[str]:
    """Explicit pick wins; else the configured channel for this meeting app;
    else None (no target → no draft, by design)."""
    if explicit:
        return str(explicit)
    return targets.get(str(app or "").lower())


def build_prompt(channel_id: str, text: str) -> str:
    """The instruction for the whitelisted call: create ONE draft with the
    recap text verbatim, never send, report one JSON line."""
    return (
        "Create a Slack DRAFT (not a message) in conversation %s using the tool "
        "mcp__slack__slack_send_message_draft, with exactly the text between the "
        "fence below — no additions, no formatting, no mentions. Do NOT send, "
        "schedule or react. If the tool reports that a draft already exists for "
        "this conversation, do nothing else.\n"
        "Then reply with ONE JSON line and nothing more: "
        "{\"status\": \"posted\" | \"draft_already_exists\" | \"failed\", "
        "\"channel_link\": \"<permalink to the conversation or empty string>\"}\n\n"
        "%s\n" % (channel_id, sanitize.fence_untrusted(text))
    )


def _field(doc: dict, key: str) -> str:
    return str(doc.get(key) or "").strip()


def parse_result(raw: str) -> dict:
    """Model reply → {"status", "channel_link"}; anything unparsable or off-
    vocabulary is ``failed`` with no link (fail-closed, never a fake posted)."""
    doc = recap_text.json_object(raw)
    if doc is None:
        return {"status": STATUS_FAILED, "channel_link": None}
    status, link = _field(doc, "status"), _field(doc, "channel_link")
    if status not in (STATUS_POSTED, STATUS_EXISTS):
        status = STATUS_FAILED
    return {"status": status, "channel_link": link if _LINK.match(link) else None}
