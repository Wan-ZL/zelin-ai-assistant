"""Skill store — ``skills/index.yaml`` manifest, enable/disable via
``~/.claude/skills/<name>`` symlinks, drift detection (CONTRACT §67; owner
decision D13, requirements R2.7).

The repo is the store: every skill lives at ``skills/<name>/`` and is listed
in ``skills/index.yaml`` (name / version / upstream_version / default_enabled /
description). Claude Code and dispatched agents only read
``~/.claude/skills/<name>``, so **enable = a symlink from there to the repo
directory** (copy fallback on filesystems without symlinks, recorded so the
copy can be told apart from a hand-edited one). Nothing here ever writes into
``skills/`` — git is the only writer of the store (防腐 #8); this module only
touches ``~/.claude/skills`` and ``state/skills.json``.

States of ``~/.claude/skills/<name>`` (wire field ``state``):

- ``disabled``  — nothing there.
- ``enabled``   — symlink resolving to this checkout's ``skills/<name>``;
  ``stale_target: true`` when it points at ``…/skills/<name>`` of another
  checkout (moved repo, other worktree, broken link) — ``sync`` re-points it.
- ``copy``      — a real directory whose content hash equals the repo skill or
  the hash recorded when the store copied it (unmodified older copy →
  ``stale_target: true``, ``relation: behind``, ``distance: N``). Store-owned:
  ``sync`` refreshes it, disable removes it.
- ``custom``    — a real directory whose content differs from every version the
  store knows: the owner's own edit (R2.7.3 自定义). Never overwritten, never
  deleted; toggle is ``locked``; ``installed_version`` + ``relation`` say how
  far it is from the repo version (落后 / 领先 N 版).
- ``foreign``   — a symlink pointing somewhere that is not a ``skills/<name>``
  directory, or a plain file. Not ours; locked.

Per-machine decisions live in ``state/skills.json`` (``decisions``:
name → enabled|disabled). ``sync`` (``scripts/skills_sync.sh``, install.sh
step ``skills``) applies ``default_enabled`` only to skills with **no**
recorded decision, so a skill the owner switched off stays off across
deploys. Version distance = |difference| at the first differing semver
component (``0.2.1`` → ``0.4.0`` is 落后 2 版).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - surfaced as ManifestError at use
    yaml = None  # type: ignore[assignment]

from act.lib import config

MANIFEST_NAME = "index.yaml"
SKILLS_DIRNAME = "skills"
STATE_NAME = "skills.json"
PROJECT_SKILLS_REL = Path(".claude") / "skills"   # tracked in git: project-visible set
MANIFEST_SCHEMA = 1
STATE_SCHEMA = 1

STATES = ("disabled", "enabled", "copy", "custom", "foreign")
VERBS = ("list", "enable", "disable", "sync")
_SKIP_DIRS = frozenset({"__pycache__", ".git"})
_SKIP_FILES = frozenset({".DS_Store"})
_REQUIRED_STR = ("name", "version", "description")


class ManifestError(ValueError):
    """``skills/index.yaml`` is missing, malformed, or disagrees with ``skills/``."""


class SkillError(RuntimeError):
    """A refused enable/disable. ``code`` is the machine token the server maps
    to its envelope: SKILL_UNKNOWN / SKILL_CUSTOM_KEEP / SKILL_FOREIGN_LINK."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# manifest + frontmatter
# --------------------------------------------------------------------------- #
def load_manifest(skills_root: Path) -> list:
    """The validated entry list of ``skills/index.yaml`` (order preserved)."""
    if yaml is None:
        raise ManifestError("PyYAML is required to read skills/index.yaml")
    path = skills_root / MANIFEST_NAME
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError("cannot read %s: %s" % (path, exc))
    except yaml.YAMLError as exc:
        raise ManifestError("%s is not valid YAML: %s" % (path, exc))
    return _validate_manifest(doc, skills_root)


def _manifest_entries(doc) -> list:
    if not isinstance(doc, dict) or doc.get("schema") != MANIFEST_SCHEMA:
        raise ManifestError("index.yaml must be a mapping with schema: %d" % MANIFEST_SCHEMA)
    entries = doc.get("skills")
    if not isinstance(entries, list) or not entries:
        raise ManifestError("index.yaml `skills` must be a non-empty list")
    return entries


def _validate_manifest(doc, skills_root: Path) -> list:
    entries = _manifest_entries(doc)
    seen: set = set()
    for entry in entries:
        _validate_entry(entry, skills_root)
        if entry["name"] in seen:
            raise ManifestError("duplicate skill name %r" % entry["name"])
        seen.add(entry["name"])
    return entries


def _require_strings(entry: dict) -> None:
    for key in _REQUIRED_STR:
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ManifestError("skill entry %r: `%s` must be a non-empty string"
                                % (entry.get("name"), key))


def _validate_entry(entry, skills_root: Path) -> None:
    if not isinstance(entry, dict):
        raise ManifestError("each skills[] entry must be a mapping")
    _require_strings(entry)
    if not isinstance(entry.get("default_enabled"), bool):
        raise ManifestError("skill %r: `default_enabled` must be true/false" % entry["name"])
    if parse_version(entry["version"]) is None:
        raise ManifestError("skill %r: version %r is not dotted integers"
                            % (entry["name"], entry["version"]))
    if not (skills_root / entry["name"] / "SKILL.md").is_file():
        raise ManifestError("skill %r: skills/%s/SKILL.md does not exist"
                            % (entry["name"], entry["name"]))


def _frontmatter_block(text: str) -> Optional[str]:
    """The YAML between the leading ``---`` fence and the closing one; None
    when the file does not open with a fence (Claude Code reads it the same
    way: frontmatter only when ``---`` is the very first line)."""
    if not text.startswith("---\n"):
        return None
    head, sep, _rest = text[4:].partition("\n---")
    return head if sep else None


def _yaml_map(text: str) -> dict:
    if yaml is None:
        return {}
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return doc if isinstance(doc, dict) else {}


def read_frontmatter(skill_dir: Path) -> dict:
    """YAML frontmatter of ``<skill_dir>/SKILL.md``; {} when absent or unparsable."""
    try:
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return {}
    block = _frontmatter_block(text)
    return _yaml_map(block) if block is not None else {}


def frontmatter_version(fm: dict) -> Optional[str]:
    """``version`` at the top level (repo convention) or under ``metadata``
    (Agent Skills spec placement); None when neither is a scalar."""
    value = fm.get("version")
    if value is None and isinstance(fm.get("metadata"), dict):
        value = fm["metadata"].get("version")
    return str(value) if isinstance(value, (str, int, float)) else None


# --------------------------------------------------------------------------- #
# versions + content hash
# --------------------------------------------------------------------------- #
def parse_version(text) -> Optional[tuple]:
    """``"0.2.1"`` → ``(0, 2, 1)``; None for anything that is not dotted ints."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return tuple(int(p) for p in text.strip().split("."))
    except ValueError:
        return None


def _pad(a: tuple, b: tuple) -> tuple:
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def version_relation(installed, repo) -> tuple:
    """``(relation, distance)``: relation ∈ same|behind|ahead|unknown; distance
    = |difference| at the first differing component (落后 N 版 / 领先 N 版)."""
    a, b = parse_version(installed), parse_version(repo)
    if a is None or b is None:
        return "unknown", 0
    a, b = _pad(a, b)
    for x, y in zip(a, b):
        if x != y:
            return ("ahead", "behind")[x < y], abs(x - y)
    return "same", 0


def _keep_file(name: str) -> bool:
    return name not in _SKIP_FILES and not name.endswith(".pyc")


def _iter_tree(root: Path):
    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=True):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in sorted(filenames):
            if _keep_file(fn):
                p = Path(dirpath) / fn
                yield p.relative_to(root).as_posix(), p.read_bytes()


def tree_hash(root: Path) -> str:
    """sha256 over (relative path, bytes) of every file under ``root`` (sorted,
    caches and .DS_Store skipped) — the drift detector's whole input."""
    h = hashlib.sha256()
    for rel, data in _iter_tree(root):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    return h.hexdigest()


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# the store
# --------------------------------------------------------------------------- #
class Store:
    """One repo checkout (the store) + one ``~/.claude/skills`` (where Claude
    Code reads) + one ``state/skills.json`` (this machine's decisions).

    ``symlink`` is the injection seam for the copy fallback (a filesystem that
    refuses symlinks raises OSError; the test suite injects the refusal)."""

    def __init__(self, repo_root: Optional[Path] = None,
                 claude_home: Optional[Path] = None,
                 state_dir: Optional[Path] = None,
                 symlink: Callable[[str, str], None] = os.symlink) -> None:
        self.repo_root = Path(repo_root or config.HOME)
        self.skills_root = self.repo_root / SKILLS_DIRNAME
        home = Path(claude_home) if claude_home else Path.home() / ".claude"
        self.claude_skills = home / SKILLS_DIRNAME
        self.state_path = Path(state_dir or config.STATE_DIR) / STATE_NAME
        self._symlink = symlink

    # ---- state file ------------------------------------------------------- #
    def state(self) -> dict:
        """``{"schema", "decisions": {name: enabled|disabled}, "copies":
        {name: {"version", "hash"}}, "updated_at"}``; unreadable → fresh."""
        try:
            doc = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        decisions = doc.get("decisions")
        copies = doc.get("copies")
        return {"schema": STATE_SCHEMA,
                "decisions": decisions if isinstance(decisions, dict) else {},
                "copies": copies if isinstance(copies, dict) else {},
                "updated_at": doc.get("updated_at")}

    def _save_state(self, doc: dict) -> None:
        doc["updated_at"] = _now_iso()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _record(self, name: str, decision: str, copy: Optional[dict]) -> None:
        doc = self.state()
        doc["decisions"][name] = decision
        if copy is None:
            doc["copies"].pop(name, None)
        else:
            doc["copies"][name] = copy
        self._save_state(doc)

    # ---- inspection ------------------------------------------------------- #
    def manifest(self) -> list:
        return load_manifest(self.skills_root)

    def entry(self, name: str) -> dict:
        for e in self.manifest():
            if e["name"] == name:
                return e
        raise SkillError("SKILL_UNKNOWN", "no skill named %r in skills/index.yaml" % name)

    def _link_kind(self, path: Path) -> str:
        if path.is_symlink():
            return "symlink"
        if path.is_dir():
            return "directory"
        return "file" if path.exists() else "none"

    def _classify_symlink(self, name: str, path: Path) -> tuple:
        """(state, stale_target) for a symlink at ``path``."""
        target = self.skills_root / name
        try:
            real = path.resolve(strict=True)
        except OSError:
            real = None
        if real == target.resolve():
            return "enabled", False
        raw = Path(os.readlink(str(path)))
        if raw.name == name and raw.parent.name == SKILLS_DIRNAME:
            return "enabled", True       # another checkout / moved / broken: ours to re-point
        return "foreign", False

    def _classify_directory(self, name: str, path: Path, copies: dict) -> tuple:
        """(state, stale_target) for a real directory at ``path``: ``copy`` when
        the content is the repo skill or the version the store copied earlier
        (stale when it is not the current repo content), else ``custom``."""
        local = tree_hash(path)
        repo = tree_hash(self.skills_root / name)
        recorded = copies.get(name) if isinstance(copies.get(name), dict) else {}
        if local == repo:
            return "copy", False
        if local == recorded.get("hash"):
            return "copy", True
        return "custom", False

    def inspect(self, entry: dict, state_doc: Optional[dict] = None) -> dict:
        """The wire row for one manifest entry (web ``SkillRow`` mirrors it)."""
        doc = state_doc or self.state()
        name = entry["name"]
        path = self.claude_skills / name
        row = {"name": name, "version": entry["version"],
               "upstream": entry.get("upstream"),
               "upstream_version": entry.get("upstream_version"),
               "default_enabled": entry["default_enabled"],
               "description": entry["description"],
               "path": str(path), "target": str(self.skills_root / name),
               "link": self._link_kind(path), "state": "disabled", "stale_target": False,
               "installed_version": None, "relation": "same", "distance": 0,
               "decision": doc["decisions"].get(name),
               "project_visible": (self.repo_root / PROJECT_SKILLS_REL / name).exists()}
        self._fill_state(row, entry, path, doc)
        row["toggle"] = _toggle_for(row["state"])
        return row

    def _fill_state(self, row: dict, entry: dict, path: Path, doc: dict) -> None:
        kind = row["link"]
        if kind == "symlink":
            row["state"], row["stale_target"] = self._classify_symlink(entry["name"], path)
            row["installed_version"] = entry["version"] if row["state"] == "enabled" else None
        elif kind == "directory":
            row["state"], row["stale_target"] = self._classify_directory(
                entry["name"], path, doc["copies"])
            row["installed_version"] = frontmatter_version(read_frontmatter(path))
            row["relation"], row["distance"] = version_relation(
                row["installed_version"], entry["version"])
        elif kind == "file":
            row["state"] = "foreign"

    def snapshot(self) -> dict:
        """Wire shape of ``GET /api/skills``."""
        doc = self.state()
        rows = [self.inspect(e, doc) for e in self.manifest()]
        return {"skills": rows, "skills_dir": str(self.claude_skills),
                "repo_skills_dir": str(self.skills_root),
                "state_path": str(self.state_path)}

    # ---- mutations -------------------------------------------------------- #
    def _place(self, name: str) -> Optional[dict]:
        """Create the link (or copy); returns the copy record or None."""
        target = self.skills_root / name
        path = self.claude_skills / name
        self.claude_skills.mkdir(parents=True, exist_ok=True)
        try:
            self._symlink(str(target), str(path))
            return None
        except (OSError, NotImplementedError):
            shutil.copytree(str(target), str(path))
            return {"version": self.entry(name)["version"], "hash": tree_hash(target)}

    def _refresh_copy(self, name: str) -> dict:
        target = self.skills_root / name
        path = self.claude_skills / name
        shutil.rmtree(str(path))
        shutil.copytree(str(target), str(path))
        return {"version": self.entry(name)["version"], "hash": tree_hash(target)}

    def _repoint(self, name: str) -> None:
        path = self.claude_skills / name
        path.unlink()
        self._symlink(str(self.skills_root / name), str(path))

    def enable(self, name: str) -> dict:
        """Enable ``name`` on this machine; refuses to touch custom/foreign."""
        entry = self.entry(name)
        row = self.inspect(entry)
        _refuse_locked(row)
        copy = None
        if row["state"] == "disabled":
            copy = self._place(name)
        elif row["state"] == "copy":
            copy = self._refresh_copy(name)
        elif row["stale_target"]:
            self._repoint(name)
        self._record(name, "enabled", copy)
        return self.inspect(entry)

    def disable(self, name: str) -> dict:
        """Disable ``name``: unlink / remove the store-owned copy; custom stays."""
        entry = self.entry(name)
        row = self.inspect(entry)
        _refuse_locked(row)
        path = self.claude_skills / name
        if row["state"] == "enabled":
            path.unlink()
        elif row["state"] == "copy":
            shutil.rmtree(str(path))
        self._record(name, "disabled", None)
        return self.inspect(entry)

    def sync(self, apply_defaults: bool = True) -> dict:
        """Refresh this machine after ``git pull``: re-point stale links,
        refresh unmodified copies, apply ``default_enabled`` where no decision
        was recorded, re-create links the decisions say should exist."""
        actions = []
        doc = self.state()
        for entry in self.manifest():
            action = self._sync_one(self.inspect(entry, doc), apply_defaults)
            if action:
                actions.append({"name": entry["name"], "action": action})
        snap = self.snapshot()
        snap["actions"] = actions
        return snap

    def _sync_one(self, row: dict, apply_defaults: bool) -> Optional[str]:
        name = row["name"]
        if row["state"] in ("enabled", "copy") and row["stale_target"]:
            self.enable(name)   # re-point the link / refresh the copy, keep the decision
            return "repointed" if row["state"] == "enabled" else "copy_refreshed"
        if row["state"] != "disabled":
            return None
        return self._sync_absent(row, apply_defaults)

    def _sync_absent(self, row: dict, apply_defaults: bool) -> Optional[str]:
        if row["decision"] == "enabled":
            self.enable(row["name"])
            return "relinked"
        if row["decision"] is None and row["default_enabled"] and apply_defaults:
            self.enable(row["name"])
            return "enabled_default"
        return None


def _toggle_for(state: str) -> str:
    if state in ("enabled", "copy"):
        return "disable"
    return "enable" if state == "disabled" else "locked"


_LOCKED_MESSAGES = {
    "custom": ("SKILL_CUSTOM_KEEP",
               "%(path)s is a local custom copy (content differs from every store version) — "
               "the store never overwrites or deletes it; move it away first"),
    "foreign": ("SKILL_FOREIGN_LINK",
                "%(path)s is not managed by the store (symlink elsewhere or a plain file) — "
                "remove it by hand first"),
}


def _refuse_locked(row: dict) -> None:
    hit = _LOCKED_MESSAGES.get(row["state"])
    if hit:
        raise SkillError(hit[0], hit[1] % row)


# --------------------------------------------------------------------------- #
# repo-level validation (tests + sync warnings)
# --------------------------------------------------------------------------- #
def validate_repo(store: Store) -> list:
    """Problems between the manifest, ``skills/*/SKILL.md`` and the tracked
    ``.claude/skills`` set — [] when the store is consistent."""
    problems: list = []
    entries = store.manifest()
    for e in entries:
        fm_version = frontmatter_version(read_frontmatter(store.skills_root / e["name"]))
        if fm_version != e["version"]:
            problems.append("%s: SKILL.md frontmatter version %r != manifest %r"
                            % (e["name"], fm_version, e["version"]))
    problems.extend(_unlisted_dirs(store, {e["name"] for e in entries}))
    problems.extend(_validate_project_links(store, entries))
    return problems


def _unlisted_dirs(store: Store, names: set) -> list:
    dirs = sorted(p.name for p in store.skills_root.iterdir() if (p / "SKILL.md").is_file())
    return ["skills/%s has a SKILL.md but is not in index.yaml" % d
            for d in dirs if d not in names]


def _bad_project_link(link: Path) -> bool:
    return (not link.is_symlink() or os.path.isabs(os.readlink(str(link)))
            or not (link / "SKILL.md").is_file())


def _validate_project_links(store: Store, entries: list) -> list:
    """``.claude/skills/<name>`` must exist exactly for the default_enabled set,
    each a relative symlink into ``skills/`` that resolves to a SKILL.md."""
    project = store.repo_root / PROJECT_SKILLS_REL
    expected = {e["name"] for e in entries if e["default_enabled"]}
    present = {p.name for p in project.iterdir()} if project.is_dir() else set()
    problems = [".claude/skills/%s missing (default_enabled skills are project-visible)" % n
                for n in sorted(expected - present)]
    problems += [".claude/skills/%s present but not default_enabled" % n
                 for n in sorted(present - expected)]
    problems += [".claude/skills/%s must be a relative symlink to ../../skills/%s" % (n, n)
                 for n in sorted(expected & present) if _bad_project_link(project / n)]
    return problems


# --------------------------------------------------------------------------- #
# CLI: python3 -m act.lib.skills list|enable NAME|disable NAME|sync [--json] [--no-defaults]
# --------------------------------------------------------------------------- #
def _summary_line(snap: dict) -> str:
    by_state: dict = {}
    for row in snap["skills"]:
        by_state.setdefault(row["state"], []).append(row["name"])
    parts = ["%s %d (%s)" % (state, len(names), ", ".join(names))
             for state, names in sorted(by_state.items())]
    acts = snap.get("actions") or []
    if acts:
        parts.append("actions: " + ", ".join("%s=%s" % (a["name"], a["action"]) for a in acts))
    return " · ".join(parts)


def _run(store: Store, verb: str, name: Optional[str], apply_defaults: bool) -> dict:
    if verb == "list":
        return store.snapshot()
    if verb == "sync":
        return store.sync(apply_defaults=apply_defaults)
    if not name:
        raise SkillError("SKILL_UNKNOWN", "usage: %s <name>" % verb)
    return {"skills": [getattr(store, verb)(name)]}


def _parse_cli(argv: Optional[list]) -> tuple:
    args = list(sys.argv[1:] if argv is None else argv)
    words = [a for a in args if not a.startswith("--")]
    verb = words[0] if words else ""
    name = words[1] if len(words) > 1 else None
    return verb, name, "--json" in args, "--no-defaults" not in args


def _emit(out: dict, as_json: bool) -> None:
    print(json.dumps(out, ensure_ascii=False, indent=2) if as_json else _summary_line(out))


def main(argv: Optional[list] = None) -> int:
    verb, name, as_json, apply_defaults = _parse_cli(argv)
    if verb not in VERBS:
        print("usage: python3 -m act.lib.skills list|enable NAME|disable NAME|sync "
              "[--json] [--no-defaults]", file=sys.stderr)
        return 2
    try:
        out = _run(Store(), verb, name, apply_defaults)
    except ManifestError as exc:
        print("skills: manifest error: %s" % exc, file=sys.stderr)
        return 3
    except SkillError as exc:
        print("skills: %s: %s" % (exc.code, exc), file=sys.stderr)
        return 4
    _emit(out, as_json)
    return 0


if __name__ == "__main__":  # pragma: no cover - entry shim
    sys.exit(main())
