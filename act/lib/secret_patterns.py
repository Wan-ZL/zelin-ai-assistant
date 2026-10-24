"""Built-in secret shapes + the mask (CONTRACT §19：密钥不出 Mac).

``sanitize.scrub`` masks these in every outbound prompt. This module imports
nothing from act/ so any layer can depend on it. v2.0.0: its second reader —
``analytics.clip_content`` with the position helpers (``secret_positions`` /
``mask_positions``) — retired with the telemetry upload surface (§78 zero
telemetry; full text in tag ``full``), so only the pattern list and the mask
remain.
"""
from __future__ import annotations

import re

MASK = "[脱敏]"

# Safe, high-precision (low false-positive) shapes.
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),                 # Anthropic keys
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                       # OpenAI-style
    re.compile(r"xox[bpasr]-[A-Za-z0-9\-]{8,}"),              # Slack tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),                          # AWS access key id
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),                # GitHub tokens
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]
