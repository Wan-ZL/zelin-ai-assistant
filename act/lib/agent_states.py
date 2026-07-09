"""Single source of truth for the `claude agents` state-name sets.

dashboard.py partitions running/needs_input/review cards by these sets and
actd.py's reconcile loop decides resume/promotion with them. They used to be
two hand-copied literals (actd._LIVE_STATES vs dashboard._RUNNING/_BLOCKED/
_DONE_STATES) — one claude CLI state-string rename away from silent drift:
a live agent misclassified as dead triggers a resume storm (duplicate agents),
a dead one misclassified as live never resumes.

``_LIVE_STATES`` is DERIVED as ``_RUNNING_STATES | {"idle"}`` on purpose, not
kept as a fourth literal. "idle" means the agent's PROCESS is still alive but
it is not actively working, and the two consumers need opposite answers:
- reconcile (actd) must treat idle as live — resuming a session whose process
  is alive spawns a duplicate agent;
- the dashboard must NOT treat idle as running — e.g. a review card whose
  agent merely idles stays in 待验收 instead of flipping to review-active.
That asymmetry is the dashboard behavior shipped today, kept as-is.

Sets are frozenset so no importer can mutate the shared source, and every
name is lowercase because _norm_agent lowercases roster states before
matching — a mixed-case entry here would silently never match.
"""
from __future__ import annotations

_RUNNING_STATES = frozenset(
    {"working", "running", "executing", "active", "busy", "in_progress"}
)
_BLOCKED_STATES = frozenset(
    {"blocked", "waiting", "needs_input", "paused", "waiting_for_input"}
)
_DONE_STATES = frozenset(
    {"done", "completed", "finished", "exited", "complete", "success"}
)

# live = "do not resume": actively running, plus idle (process alive).
_LIVE_STATES = _RUNNING_STATES | {"idle"}
