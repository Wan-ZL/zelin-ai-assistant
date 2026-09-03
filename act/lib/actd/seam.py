"""seam — the daemon namespace snapshot the act.lib.actd modules read (CONTRACT §58.3).

Why this exists (防腐 #2 + 判例兼容): ``act/actd.py`` is the entry layer and the
only file allowed to import ``act.executor`` / ``act.analyze`` /
``act.merge_review`` / ``act.radar_claude_sessions`` (the four grandfathered
entry-pair edges in qa/deps_baseline.txt); the pass logic lives DOWN here in
act/lib, which may never import upward. So the facade hands its namespace over
as a ``Daemon`` value **built per call** — every collaborator and every helper
the 80-odd actd test files patch on the facade (``executor``, ``save``,
``_log``, ``_merge_into_primary``, …) is read from the snapshot at call time,
which is exactly what ``patch.object(actd, name)`` expects. No module-global
injection (防腐 #3 — the JUDGE_RUNNER lesson): the seam is an explicit
parameter of every function in this package.

``append_note`` is the one-line idiom thirteen verbs share for the card-face
notes trail (§10 / §21 / §44.3-S); it lives here so no module re-spells it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Daemon:
    """One pass's view of the ``act.actd`` namespace (see module docstring).

    Collaborator modules are ``None`` when the daemon degraded their import
    (``act/actd.py`` try/except blocks) — callers keep the same ``is None``
    checks they always had.
    """
    # entry-layer collaborators (degrade to None)
    executor: Any
    analyze: Any
    merge_review: Any
    radar_claude_sessions: Any
    feedback: Any
    # daemon sinks / helpers
    log: Callable[[str], None]
    iso_now: Callable[[], str]
    save: Callable[[Any], Any]
    safe_unlink: Callable[[Any], None]
    write_applied_ack: Callable[[str, str], None]
    detached_actions: dict
    run_claude_agents: Callable[[], list]
    # facade verbs other verbs call through (each is a pinned patch seam)
    apply_decision: Callable[..., str]
    apply_capture: Callable[..., str]
    stop_session_tracked: Callable[..., tuple]
    stop_live_session: Callable[..., None]
    merge_into_primary: Callable[[str, list], None]
    apply_merge_verdict: Callable[[dict], None]
    promote_if_delivered: Callable[..., bool]


def append_note(req, tag: str) -> None:
    """Append one line to the card's notes trail（卡面留痕的统一写法）."""
    req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
