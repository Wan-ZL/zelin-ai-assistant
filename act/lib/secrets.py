"""secrets — credential files under ``<AIASSISTANT_HOME>/config/secrets/`` (CONTRACT §19).

The Mac app's settings window writes pasted tokens here (dir 0700, files 0600);
every Python reader resolves credentials in the SAME fixed order:

    1. secrets file (exists and non-empty)      config/secrets/<name>
    2. explicit path from config.yaml           e.g. sources.slack_token_path
    3. legacy default path                      e.g. ~/Desktop/Keys/slack-user-token.txt

Fixed file names (one line, just the token):
    slack-user-token.txt / gmail-app-password.txt / anthropic-api-key.txt

The legacy paths stay as the final fallback so Zelin's existing setup keeps
working unchanged — nothing breaks when config/secrets/ is empty. That tier is
DEPRECATED (CONTRACT §19): resolving through it logs a one-line stderr warning
plus a ``legacy_secret_path`` analytics event (name only, never the credential),
because ~/Desktop is iCloud-synced on default macOS setups.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional, Union

from act.lib import config

# Directory + canonical file names (CONTRACT §19 — must match the Swift app).
SECRETS_DIR: Path = config.HOME / "config" / "secrets"

SLACK_TOKEN_FILE = "slack-user-token.txt"
GMAIL_APP_PASSWORD_FILE = "gmail-app-password.txt"
ANTHROPIC_API_KEY_FILE = "anthropic-api-key.txt"

_DIR_MODE = 0o700
_FILE_MODE = 0o600


# origins already warned about in this process — radars poll in a loop, so the
# multiline warning must fire once per file, not once per scan.
_warned_multiline: set = set()


def _first_token_line(text: str, origin) -> Optional[str]:
    """契约形态是「各一行纯 token」（§19）：多行内容取首个非空行，其余按
    畸形丢弃并 log 一条（只报来源，绝不带凭证值）。整段带 ``\\n`` 返回只会
    变成难排查的 IMAP/HTTP auth 失败——用户误粘的第二行（注释/警告文字）
    不能混进凭证值。"""
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    if not lines:
        return None
    if len(lines) > 1 and str(origin) not in _warned_multiline:
        _warned_multiline.add(str(origin))
        try:
            print(
                f"[secrets] WARNING: {origin} has {len(lines)} non-empty lines"
                " — expected a single-line token (CONTRACT §19);"
                " using the first line only",
                file=sys.stderr,
            )
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
    """Write a secret file, enforcing dir 0700 / file 0600. Returns the path.

    多行粘贴只保留首个非空行（§19 一行 token）——写成两行的文件会让所有
    读者拿到带 ``\\n`` 的"凭证"。"""
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


def _read_path(path: Union[str, Path, None]) -> Optional[str]:
    """First token line of a credential FILE at ``path``, or None."""
    if not path:
        return None
    p = Path(str(path)).expanduser()
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return None
    return _first_token_line(raw, p)


# names already warned about in this process — radars poll in a loop, so the
# deprecation line must fire once per credential, not once per scan.
_warned_legacy: set = set()


def _warn_legacy(secret_name: str, path: Union[str, Path]) -> None:
    """One-line deprecation notice (stderr + analytics). Never raises (§19:
    the credential VALUE must not appear in any log — only name and path)."""
    if secret_name in _warned_legacy:
        return
    _warned_legacy.add(secret_name)
    try:
        print(
            f"[secrets] DEPRECATED: {secret_name} resolved via legacy path "
            f"{path} — paste the token in the app's Settings window instead "
            "(stored under config/secrets/, see docs/CONTRACT.md §19)",
            file=sys.stderr,
        )
    except Exception:  # noqa: BLE001 - warning must never break resolution
        pass
    try:
        from act.lib import analytics
        analytics.log_event("legacy_secret_path", name=secret_name)
    except Exception:  # noqa: BLE001
        pass


def resolve_credential(
    secret_name: str,
    explicit_path: Union[str, Path, None] = None,
    legacy_default: Union[str, Path, None] = None,
) -> Optional[str]:
    """Return the credential CONTENT per the §19 order (secrets → explicit → legacy).

    ``explicit_path`` / ``legacy_default`` are file PATHS; the return value is
    always the file's stripped content (empty files count as missing). The
    legacy tier still works but is deprecated — see ``_warn_legacy``.
    """
    val = read_secret(secret_name)
    if val:
        return val
    val = _read_path(explicit_path)
    if val:
        return val
    val = _read_path(legacy_default)
    if val:
        _warn_legacy(secret_name, legacy_default)
    return val
