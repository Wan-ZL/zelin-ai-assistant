"""``state/deploy_state.json`` reader (CONTRACT §56 合并即上岗).

The file is WRITTEN by scripts/auto-deploy.sh (one flat object of strings,
atomic tmp+rename, rewritten every run) and READ here for two consumers:

- the dashboard projection — add-only top-level key ``deploy_state`` (§2
  sibling field, same convention as ``update_available`` / ``device_label``:
  the key is absent when the file is absent or unreadable);
- ``act.doctor`` — the ``auto-deploy`` row (OK for deployed/up_to_date, WARN
  for every other outcome, no row when the file does not exist: this machine
  simply does not run the agent).

Field by field type-checking, never raising: the writer is a shell script and
a torn/half-edited file must not take the dashboard pass down (§0 第 11 条).
Unknown keys are dropped, unknown ``status`` values are kept verbatim
(add-only: readers tolerate what they do not know).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from act.lib import config

PATH: Path = config.STATE_DIR / "deploy_state.json"

# Projected keys (all strings). The script also keeps ``notified_sha`` as
# private bookkeeping; it is not part of the projection.
FIELDS = ("status", "version", "head", "prev", "last_deployed", "last_run",
          "detail", "failed_sha")

# Outcomes that mean "nothing needs a human" — everything else is a WARN row.
HEALTHY = frozenset({"deployed", "up_to_date"})


def read(path: Optional[Path] = None) -> Optional[dict]:
    """The sanitized deploy state, or None when absent/unreadable/not a dict."""
    target = path or PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    out = {}
    for key in FIELDS:
        value = raw.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    return out or None


def attach(dash: dict, path: Optional[Path] = None) -> dict:
    """Set ``dash["deploy_state"]`` when a readable state exists (add-only)."""
    state = read(path)
    if state:
        dash["deploy_state"] = state
    return dash
