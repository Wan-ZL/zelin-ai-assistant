"""Configuration + canonical runtime paths for Zelin's AI Assistant.

Runtime state lives under ``AIASSISTANT_HOME/state`` (gitignored). The registry
(source of truth) lives under ``AIASSISTANT_HOME/act/registry``; runtime entries
(``R-*.yaml``) are gitignored — they contain real extracted work data.

All paths are derived from the ``AIASSISTANT_HOME`` env var, defaulting to
``~/Projects/zelin-ai-assistant``. Constants are exposed as ``pathlib.Path`` objects so
every component (executor, actd, radar, dashboard) resolves to the same files.

Shell consumers (the ingest scripts) resolve vault paths through the same
layer via ``python3 -m act.lib.config --print-path obsidian_unprocessed``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml  # PyYAML — install if missing (see module docstring)
except ImportError:  # pragma: no cover - surfaced clearly at runtime
    yaml = None  # type: ignore


# --------------------------------------------------------------------------- #
# Canonical paths (env-driven, single source everywhere)
# --------------------------------------------------------------------------- #
def _home() -> Path:
    return Path(os.environ.get("AIASSISTANT_HOME", "~/Projects/zelin-ai-assistant")).expanduser()


HOME: Path = _home()
STATE_DIR: Path = HOME / "state"
REGISTRY_DIR: Path = HOME / "act" / "registry"
INBOX_DIR: Path = STATE_DIR / "inbox"
DASHBOARD_PATH: Path = STATE_DIR / "dashboard.json"
LOG_DIR: Path = STATE_DIR / "logs"

# Auto-memory MEMORY.md injected into every dispatched prompt — Claude Code
# keys its per-project memory dir on the dash-encoded absolute project path.
MEMORY_PATH: Path = (
    Path.home() / ".claude" / "projects"
    / str(Path.home() / "Projects").replace("/", "-")
    / "memory" / "MEMORY.md"
)

# Config files (config.yaml is gitignored; config.example.yaml is the fallback).
CONFIG_PATH: Path = HOME / "config.yaml"
CONFIG_EXAMPLE_PATH: Path = HOME / "config.example.yaml"

# Mac-app settings overrides (§15) — app writes ONLY this file; load_config()
# merges it LAST so it has the highest priority.
SETTINGS_OVERRIDES_PATH: Path = STATE_DIR / "settings_overrides.json"

# Built-in Obsidian vault root fallback — used to derive the pipeline dirs
# when sources.obsidian_raw is not configured (v0.10.3 契约二).
DEFAULT_OBSIDIAN_VAULT: str = "~/Documents/Obsidian Vault"

# Sensitive-app capture exclusion (P1-9) — applied at BOTH ends of the screen
# pipeline: the mac app passes each entry to the engine as --ignored-windows
# (screenpipe skips matching windows BEFORE anything is stored), and
# ingest/screenpipe-export.sh filters already-stored frames with the same list
# (see recording_exclusion_sql). Bare terms match case-insensitive substring
# against app name OR window title; the engine's `App::Title` scoping syntax is
# honoured too. Keep in sync with ScreenpipeRecipe.defaultIgnoredApps in
# mac/Sources/Recording.swift (drift-guarded by tests/test_capture_exclusion.py).
DEFAULT_IGNORED_APPS: list = [
    "1Password",
    "Bitwarden",
    "LastPass",
    "KeePassXC",
    "Keychain Access",
    "Private Browsing",  # Safari private windows (window-title match)
    "Incognito",         # Chrome/Edge incognito windows (window-title match)
]

# Telemetry defaults (docs/TELEMETRY.md) — anonymous usage analytics upload is
# ON by default (like VS Code) and points at the maintainer's Supabase project.
# The publishable key is DESIGNED to be public (RLS allows INSERT only — it can
# never read anyone's data). A key file (CONTRACT §19 / telemetry.key_path)
# still wins when present. Opt out: Settings toggle or `telemetry.enabled:
# false`; setting `supabase_url: ""` disables uploads entirely (forks!).
DEFAULT_TELEMETRY_SUPABASE_URL: str = "https://vlxshwmdjpaxmcwbhutb.supabase.co"
DEFAULT_TELEMETRY_PUBLISHABLE_KEY: str = (
    "sb_publishable_bNWOKJTAH52AfwTao-nHUQ_jdsTUpYi"
)
TELEMETRY_LEVELS: tuple = ("basic", "detailed")

# Feature flags (§16) — default ALL on; config.yaml `features:` then
# settings_overrides.json `features` overlay on top.
DEFAULT_FEATURES: dict = {
    "slack_radar": True,
    "gmail_radar": True,
    "obsidian_radar": True,
    "digest": True,
    "auto_resume": True,
    "analytics": True,
    "manager_pack": True,
}


def ensure_state_dirs() -> None:
    """Create the runtime state directories if they do not yet exist."""
    for d in (STATE_DIR, INBOX_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Config object
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    """Parsed config.yaml (with defaults so callers never KeyError)."""

    raw: dict = field(default_factory=dict)

    # owner
    owner_name: str = "Zelin"
    owner_slack_user_id: Optional[str] = None
    display_name: str = "Zelin's AI Assistant"

    # sources
    obsidian_raw: Optional[str] = None
    # Obsidian 管线另外三目录（v0.10.3 契约二）— 缺省时由 obsidian_raw 的
    # parent（vault 根）+ 标准目录名派生；load_config() 之后永不为 None。
    obsidian_unprocessed: Optional[str] = None
    obsidian_change_summary: Optional[str] = None
    obsidian_wiki: Optional[str] = None
    slack_channels: list = field(default_factory=list)
    slack_dms: list = field(default_factory=list)
    slack_token_path: Optional[str] = None
    # Slack MCP fallback (v0.11) — 没有 xoxp token（卡在管理员审批）时，radar_slack
    # 每 slack_mcp_interval_minutes 用 headless claude + 用户级 Slack MCP 扫一遍
    slack_mcp_fallback: bool = True
    slack_mcp_interval_minutes: int = 30
    watch_people: list = field(default_factory=list)
    # gmail capture (CONTRACT §14) — app password file missing => radar no-ops
    gmail_address: Optional[str] = None
    gmail_app_password_path: str = "~/Desktop/Keys/gmail-app-password.txt"
    gmail_enabled: bool = True

    # approval / cost
    poll_interval_seconds: int = 10
    show_cost_above_usd: float = 5.0
    require_text_confirm_above_usd: float = 50.0

    # execution
    default_target_repo: str = "~/Projects/your-workbench"
    memory_inject: bool = True
    # False by default: approving a card must not silently create GitHub repos
    # / push content that originated from screen/meetings/mail. Explicit
    # config.yaml values (either way) are honored unchanged (PRIVACY.md row 8).
    create_github_repo: bool = False
    auto_resume: bool = True
    # claude --bg with --dangerously-skip-permissions (default, unattended);
    # False = claude's normal permission model, blocked agents -> needs_input
    skip_permissions: bool = True
    self_check: bool = True
    fresh_context_review: bool = True
    system_card_per_ckpt: bool = True

    # trash / recycle bin
    trash_retention_days: int = 60

    # screen-capture sensitive-app exclusion (P1-9) — key absent = defaults;
    # explicit `ignored_apps: []` in config.yaml = deliberate opt-out.
    recording_ignored_apps: list = field(
        default_factory=lambda: list(DEFAULT_IGNORED_APPS)
    )

    # local pre-send redaction (opt-in)
    redaction_enabled: bool = False
    redaction_terms_file: str = "config/redaction_terms.txt"
    redaction_mask_secrets: bool = True

    # telemetry upload (default ON with opt-out; docs/TELEMETRY.md) — level
    # "basic" sends event metadata only; "detailed" (opt-in) may add short
    # instruction/delivery summaries (<=200 chars) to dispatch/delivery events.
    telemetry_enabled: bool = True
    telemetry_level: str = "basic"
    telemetry_supabase_url: str = DEFAULT_TELEMETRY_SUPABASE_URL
    telemetry_key_path: Optional[str] = None

    # phone command channel (§13, channel-pluggable) — which channel carries
    # the notify mirror + the phone command surface. "none"/"slack" keep the
    # legacy Slack behavior (self-gated on features.slack_radar + token);
    # "imessage" switches the mirror to iMessage and arms act/radar_imessage.py
    # (needs imessage_self_handle + Full Disk Access, docs/IMESSAGE_SETUP.md).
    phone_channel: str = "none"
    imessage_self_handle: Optional[str] = None

    # UI language (§15) — stored value only for now ("zh" | "en")
    language: str = "zh"

    # feature flags (§16) — default all on; see DEFAULT_FEATURES
    features: dict = field(default_factory=lambda: dict(DEFAULT_FEATURES))

    @property
    def target_repo_path(self) -> Path:
        return Path(self.default_target_repo).expanduser()

    def feature(self, name: str) -> bool:
        """Feature-flag check (§16). Unknown flags default to on."""
        try:
            return bool(self.features.get(name, True))
        except AttributeError:
            return True

    def requester_display(self) -> str:
        """Best-effort display name for the person whose asks we track."""
        if self.watch_people:
            return self.watch_people[0].split(".")[0].title()
        return self.owner_name


def load_config() -> Config:
    """Load ``config.yaml`` (falling back to ``config.example.yaml``).

    Never raises on a missing file — returns a Config with defaults so the
    daemon keeps running in a fresh checkout.
    """
    path = CONFIG_PATH if CONFIG_PATH.exists() else CONFIG_EXAMPLE_PATH
    data: dict = {}
    if yaml is not None and path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded

    cfg = Config(raw=data)

    owner = data.get("owner", {}) or {}
    cfg.owner_name = owner.get("name", cfg.owner_name)
    cfg.owner_slack_user_id = owner.get("slack_user_id", cfg.owner_slack_user_id)
    cfg.display_name = owner.get("display_name", cfg.display_name)

    sources = data.get("sources", {}) or {}
    cfg.obsidian_raw = sources.get("obsidian_raw", cfg.obsidian_raw)
    cfg.obsidian_unprocessed = sources.get(
        "obsidian_unprocessed", cfg.obsidian_unprocessed
    )
    cfg.obsidian_change_summary = sources.get(
        "obsidian_change_summary", cfg.obsidian_change_summary
    )
    cfg.obsidian_wiki = sources.get("obsidian_wiki", cfg.obsidian_wiki)
    cfg.slack_channels = sources.get("slack_channels", []) or []
    cfg.slack_dms = sources.get("slack_dms", []) or []
    cfg.slack_token_path = sources.get("slack_token_path", cfg.slack_token_path)
    cfg.slack_mcp_fallback = bool(
        sources.get("slack_mcp_fallback", cfg.slack_mcp_fallback)
    )
    cfg.slack_mcp_interval_minutes = int(
        sources.get("slack_mcp_interval_minutes", cfg.slack_mcp_interval_minutes)
    )
    cfg.watch_people = sources.get("watch_people", []) or []

    gmail = sources.get("gmail", {}) or {}
    cfg.gmail_address = gmail.get("address", cfg.gmail_address)
    cfg.gmail_app_password_path = gmail.get(
        "app_password_path", cfg.gmail_app_password_path
    )
    cfg.gmail_enabled = bool(gmail.get("enabled", cfg.gmail_enabled))

    approval = data.get("approval", {}) or {}
    # poll_interval: config.example uses minutes for the approval surface; the
    # daemon loop also accepts an explicit seconds override.
    if "poll_interval_seconds" in approval:
        cfg.poll_interval_seconds = int(approval["poll_interval_seconds"])
    elif "poll_interval_minutes" in approval:
        # Daemon default stays 10s; the minutes value governs the approval
        # surface poll, not the tight local loop. We keep 10s unless an explicit
        # seconds value is provided, to remain responsive to the inbox.
        cfg.poll_interval_seconds = cfg.poll_interval_seconds
    thresholds = approval.get("cost_thresholds", {}) or {}
    cfg.show_cost_above_usd = float(
        thresholds.get("show_cost_above_usd", cfg.show_cost_above_usd)
    )
    cfg.require_text_confirm_above_usd = float(
        thresholds.get("require_text_confirm_above_usd", cfg.require_text_confirm_above_usd)
    )

    execution = data.get("execution", {}) or {}
    cfg.default_target_repo = execution.get("default_target_repo", cfg.default_target_repo)
    cfg.memory_inject = bool(execution.get("memory_inject", cfg.memory_inject))
    cfg.create_github_repo = bool(
        execution.get("create_github_repo", cfg.create_github_repo)
    )
    cfg.auto_resume = bool(execution.get("auto_resume", cfg.auto_resume))
    cfg.skip_permissions = bool(
        execution.get("skip_permissions", cfg.skip_permissions)
    )
    qg = execution.get("quality_gate", {}) or {}
    cfg.self_check = bool(qg.get("self_check", cfg.self_check))
    cfg.fresh_context_review = bool(qg.get("fresh_context_review", cfg.fresh_context_review))
    training = execution.get("training", {}) or {}
    cfg.system_card_per_ckpt = bool(
        training.get("system_card_per_ckpt", cfg.system_card_per_ckpt)
    )

    trash = data.get("trash", {}) or {}
    cfg.trash_retention_days = int(
        trash.get("retention_days", cfg.trash_retention_days)
    )

    recording = data.get("recording", {}) or {}
    apps = recording.get("ignored_apps")
    if isinstance(apps, list):
        cfg.recording_ignored_apps = [
            str(a).strip() for a in apps if a is not None and str(a).strip()
        ]

    tele = data.get("telemetry", {}) or {}
    cfg.telemetry_enabled = bool(tele.get("enabled", cfg.telemetry_enabled))
    _lvl = str(tele.get("level", cfg.telemetry_level) or "").strip().lower()
    cfg.telemetry_level = _lvl if _lvl in TELEMETRY_LEVELS else "basic"
    # An explicit empty/null supabase_url disables uploads entirely (forks:
    # this is the hard off switch); an ABSENT key keeps the default project.
    cfg.telemetry_supabase_url = str(
        tele.get("supabase_url", cfg.telemetry_supabase_url) or ""
    )
    cfg.telemetry_key_path = tele.get("key_path", cfg.telemetry_key_path)

    red = data.get("redaction", {}) or {}
    cfg.redaction_enabled = bool(red.get("enabled", cfg.redaction_enabled))
    cfg.redaction_terms_file = red.get("terms_file", cfg.redaction_terms_file)
    cfg.redaction_mask_secrets = bool(red.get("mask_secrets", cfg.redaction_mask_secrets))
    _tf = cfg.redaction_terms_file
    if _tf and not str(_tf).startswith(("/", "~")):
        cfg.redaction_terms_file = str(HOME / _tf)

    pc = str(data.get("phone_channel") or "").strip().lower()
    if pc in ("none", "slack", "imessage"):
        cfg.phone_channel = pc
    imsg = data.get("imessage", {}) or {}
    if isinstance(imsg, dict):
        cfg.imessage_self_handle = imsg.get("self_handle", cfg.imessage_self_handle)

    if isinstance(data.get("language"), str) and data["language"].strip():
        cfg.language = data["language"].strip()

    feats = data.get("features", {}) or {}
    if isinstance(feats, dict):
        for k, v in feats.items():
            cfg.features[str(k)] = bool(v)

    _apply_settings_overrides(cfg)
    # AFTER the overrides merge, so an overridden obsidian_raw re-points the
    # derived pipeline dirs too (explicitly-set dirs are left untouched).
    _derive_obsidian_dirs(cfg)

    return cfg


# --------------------------------------------------------------------------- #
# Obsidian pipeline dirs (v0.10.3 契约二) — derive the unset ones from the
# vault root (= obsidian_raw's parent, or the built-in default vault).
# --------------------------------------------------------------------------- #
_OBSIDIAN_DIR_NAMES: dict = {
    "obsidian_unprocessed": "1 - unprocessed",
    "obsidian_change_summary": "3 - change-summary",
    "obsidian_wiki": "4 - wiki",
}


def _derive_obsidian_dirs(cfg: Config) -> None:
    """Fill any unset pipeline dir with vault-root + standard folder name."""
    if cfg.obsidian_raw and str(cfg.obsidian_raw).strip():
        vault = Path(str(cfg.obsidian_raw)).expanduser().parent
    else:
        vault = Path(DEFAULT_OBSIDIAN_VAULT).expanduser()
    for attr, name in _OBSIDIAN_DIR_NAMES.items():
        current = getattr(cfg, attr)
        if not (current and str(current).strip()):
            setattr(cfg, attr, str(vault / name))


# --------------------------------------------------------------------------- #
# settings_overrides.json overlay (§15) — Mac app writes it; highest priority.
# --------------------------------------------------------------------------- #
# Scalar cfg fields the app may override, with a coercion for each.
_OVERRIDE_FIELDS: dict = {
    "obsidian_raw": str,
    "obsidian_unprocessed": str,
    "obsidian_change_summary": str,
    "obsidian_wiki": str,
    "slack_token_path": str,
    "gmail_address": str,
    "gmail_app_password_path": str,
    "gmail_enabled": bool,
    "phone_channel": str,
    "imessage_self_handle": str,
    "show_cost_above_usd": float,
    "require_text_confirm_above_usd": float,
    "trash_retention_days": int,
    "language": str,
    "redaction_enabled": bool,
    "redaction_terms_file": str,
    "redaction_mask_secrets": bool,
}


def _apply_settings_overrides(cfg: Config) -> None:
    """Overlay ``STATE_DIR/settings_overrides.json`` onto ``cfg`` (§15).

    Only the fields the file names are touched. Malformed JSON, wrong types, or
    unknown keys are silently ignored — a broken overrides file must never take
    the daemon down.
    """
    try:
        if not SETTINGS_OVERRIDES_PATH.exists():
            return
        data = json.loads(SETTINGS_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — never raise on a malformed overrides file
        return
    if not isinstance(data, dict):
        return

    for key, value in data.items():
        try:
            if key == "features" and isinstance(value, dict):
                for fk, fv in value.items():
                    cfg.features[str(fk)] = bool(fv)
            elif key.startswith("features."):
                # flat form: {"features.digest": false}
                cfg.features[key.split(".", 1)[1]] = bool(value)
            elif key == "gmail" and isinstance(value, dict):
                # nested form mirroring config.yaml sources.gmail
                if value.get("address") is not None:
                    cfg.gmail_address = str(value["address"])
                if value.get("app_password_path") is not None:
                    cfg.gmail_app_password_path = str(value["app_password_path"])
                if value.get("enabled") is not None:
                    cfg.gmail_enabled = bool(value["enabled"])
            elif key == "cost_thresholds" and isinstance(value, dict):
                # nested form mirroring config.yaml approval.cost_thresholds
                if value.get("show_cost_above_usd") is not None:
                    cfg.show_cost_above_usd = float(value["show_cost_above_usd"])
                if value.get("require_text_confirm_above_usd") is not None:
                    cfg.require_text_confirm_above_usd = float(
                        value["require_text_confirm_above_usd"]
                    )
            elif key == "telemetry" and isinstance(value, dict):
                # v0.13 (§15 note): the app's first-run page opts OUT of
                # anonymous usage stats by writing {"telemetry": {"enabled":
                # false}}. App-overridable: enabled + level ONLY —
                # supabase_url / key_path stay config.yaml-only.
                if value.get("enabled") is not None:
                    cfg.telemetry_enabled = bool(value["enabled"])
                if value.get("level") is not None:
                    lvl = str(value["level"]).strip().lower()
                    if lvl in TELEMETRY_LEVELS:
                        cfg.telemetry_level = lvl
            elif key == "telemetry.enabled" and value is not None:
                # flat form, same allowlist (§15 telemetry overrides)
                cfg.telemetry_enabled = bool(value)
            elif key == "telemetry.level" and value is not None:
                lvl = str(value).strip().lower()
                if lvl in TELEMETRY_LEVELS:
                    cfg.telemetry_level = lvl
            elif key.startswith("sources."):
                # dotted form mirroring config.yaml, e.g.
                # {"sources.obsidian_wiki": "/path/to/4 - wiki"}
                sub = key.split(".", 1)[1]
                if sub in _OVERRIDE_FIELDS and value is not None:
                    setattr(cfg, sub, _OVERRIDE_FIELDS[sub](value))
            elif key in _OVERRIDE_FIELDS and value is not None:
                setattr(cfg, key, _OVERRIDE_FIELDS[key](value))
        except Exception:  # noqa: BLE001 — skip just the bad entry
            continue


# --------------------------------------------------------------------------- #
# Capture-exclusion SQL (P1-9) — consumed by ingest/screenpipe-export.sh via a
# one-line python call. One place builds the fragment so quoting/NULL handling
# is testable and the shell never string-munges app names.
# --------------------------------------------------------------------------- #
def recording_exclusion_sql(cfg: Optional[Config] = None) -> str:
    """WHERE-clause fragment excluding frames whose app/window matches
    ``recording.ignored_apps``, mirroring the engine's --ignored-windows
    semantics (screenpipe 0.3.349 window_pattern, source-verified):

    - bare term        → case-insensitive substring against app name OR title
    - ``App::Title``   → app substring AND title substring must both match
    - ``App::`` / ``::Title`` → app-only / title-only substring

    Returns one line of ``AND …`` clauses — empty string when the list is
    empty (explicit opt-out). ``coalesce`` keeps NULL app/window rows
    exported: ``NULL NOT LIKE`` is NULL in SQLite and would silently drop
    them.
    """
    cfg = cfg or load_config()

    def like(column: str, part: str) -> str:
        return (
            f"lower(coalesce(f.{column}, '')) LIKE"
            " '%" + part.lower().replace("'", "''") + "%'"
        )

    clauses = []
    for term in cfg.recording_ignored_apps:
        if "::" in term:
            app_part, title_part = term.split("::", 1)
            conds = []
            if app_part.strip():
                conds.append(like("app_name", app_part.strip()))
            if title_part.strip():
                conds.append(like("window_name", title_part.strip()))
            if conds:
                clauses.append("AND NOT (" + " AND ".join(conds) + ")")
        else:
            clauses.append(
                f"AND NOT ({like('app_name', term)} OR {like('window_name', term)})"
            )
    return " ".join(clauses)


# Module-level singleton for convenience (callers may also call load_config()).
def get_config() -> Config:
    return load_config()


# --------------------------------------------------------------------------- #
# CLI — `python3 -m act.lib.config --print-path <key>` (P1-6). Used by the
# ingest shell scripts to resolve vault paths through the same config layer as
# the daemon. Must never trace on a broken/missing config: cron consumers need
# a usable path on stdout, so any load failure prints the built-in default.
# --------------------------------------------------------------------------- #
_CLI_PATH_KEYS: tuple = (
    "obsidian_raw",
    "obsidian_unprocessed",
    "obsidian_change_summary",
    "obsidian_wiki",
)


def _cli_default_path(key: str) -> str:
    vault = Path(DEFAULT_OBSIDIAN_VAULT).expanduser()
    if key == "obsidian_raw":
        return str(vault / "2 - raw")
    return str(vault / _OBSIDIAN_DIR_NAMES[key])


def main(argv: Optional[list] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m act.lib.config",
        description="Print a resolved config path for shell consumers.",
    )
    parser.add_argument(
        "--print-path",
        required=True,
        choices=_CLI_PATH_KEYS,
        metavar="KEY",
        help="config key to resolve: %s" % ", ".join(_CLI_PATH_KEYS),
    )
    args = parser.parse_args(argv)
    try:
        value = getattr(load_config(), args.print_path)
    except Exception:  # noqa: BLE001 — silent-on-error: print the default
        value = None
    if not (value and str(value).strip()):
        value = _cli_default_path(args.print_path)
    print(Path(str(value)).expanduser())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
