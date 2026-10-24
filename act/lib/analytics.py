"""act/lib/analytics.py — retired telemetry surface (CONTRACT §78; v2.0.0).

Lite 2.0 has zero telemetry upload surface: the local event log, the consent
markers, the Supabase uploader and the insights workflow retired with the Act
pipeline (full text in tag ``full``). ``log_event`` keeps its signature as a
local no-op so a stray caller (or an older module during an upgrade) never
breaks; nothing is written, nothing leaves the machine.
"""
from __future__ import annotations


def log_event(event: str, **fields) -> None:
    """No-op since v2.0.0 (§78: no telemetry). Accepts and drops everything."""
    del event, fields
