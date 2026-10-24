"""secrets — the Anthropic credential under ``<AIASSISTANT_HOME>/config/secrets/`` (CONTRACT §19).

The web Settings page writes pasted keys here (dir 0700, files 0600). v2.0.0
keeps exactly one credential — the Anthropic API key every headless ``claude -p``
runs on (§78: ``--bare`` never reads OAuth or the keychain, so a key file is the
only way in). Resolution order:

    1. config/secrets/anthropic-api-key.txt   (exists and non-empty)
    2. ~/.config/anthropic-key.txt            (the pre-app location, still honoured)

Never the owner's Claude Code login or the macOS keychain. The Slack / Gmail
credential files retired with the radars (full text in tag ``full``); files left
on disk are simply never read.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Union

from act.lib import config

# Directory + canonical file name (CONTRACT §19 — server/secrets_store.py mirrors it).
SECRETS_DIR: Path = config.HOME / "config" / "secrets"
ANTHROPIC_API_KEY_FILE = "anthropic-api-key.txt"
# §19 second tier: the location ingest used before the app had a Settings page.
LEGACY_ANTHROPIC_KEY_PATH = "~/.config/anthropic-key.txt"

_DIR_MODE = 0o700
_FILE_MODE = 0o600

# origins already warned about in this process — the multiline warning fires once per file.
_warned_multiline: set = set()


def _first_token_line(text: str, origin) -> Optional[str]:
    """契约形态是「一行纯 token」（§19）：多行内容取首个非空行，其余按畸形丢弃并 log
    一条（只报来源，绝不带凭证值）。"""
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    if not lines:
        return None
    if len(lines) > 1 and str(origin) not in _warned_multiline:
        _warned_multiline.add(str(origin))
        try:
            print(f"[secrets] WARNING: {origin} has {len(lines)} non-empty lines"
                  " — expected a single-line token (CONTRACT §19); using the first line only",
                  file=sys.stderr)
        except Exception:  # noqa: BLE001 - warning must never break resolution
            pass
    return lines[0]


def read_secret(name: str) -> Optional[str]:
    """First token line of ``SECRETS_DIR/<name>``, or None (missing/empty)."""
    try:
        raw = (SECRETS_DIR / name).read_text(encoding="utf-8")
    except OSError:
        return None
    return _first_token_line(raw, SECRETS_DIR / name)


def write_secret(name: str, value: str) -> Path:
    """Write a secret file, enforcing dir 0700 / file 0600. Returns the path."""
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(SECRETS_DIR, _DIR_MODE)
    except OSError:
        pass
    path = SECRETS_DIR / name
    token = _first_token_line(str(value), f"write_secret({name})") or ""
    path.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(path, _FILE_MODE)
    except OSError:
        pass
    return path


def read_path(path: Union[str, Path, None]) -> Optional[str]:
    """First token line of a credential FILE at ``path``, or None."""
    if not path:
        return None
    p = Path(str(path)).expanduser()
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return None
    return _first_token_line(raw, p)


def resolve_credential(
    secret_name: str,
    explicit_path: Union[str, Path, None] = None,
    legacy_default: Union[str, Path, None] = None,
) -> Optional[str]:
    """Return the credential CONTENT per the §19 order (secrets → explicit → legacy).
    Empty files count as missing."""
    return (read_secret(secret_name) or read_path(explicit_path)
            or read_path(legacy_default))


def resolve_anthropic_key() -> Optional[str]:
    """The Anthropic API key per §19 (secrets file, then ~/.config/anthropic-key.txt)."""
    return resolve_credential(ANTHROPIC_API_KEY_FILE, None, LEGACY_ANTHROPIC_KEY_PATH)
