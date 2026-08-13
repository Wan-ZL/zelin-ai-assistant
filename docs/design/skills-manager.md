# Design proposal: skills manager in Settings

Status: proposal (shipped alongside the first bundled skill, `skills/precise-writing`).

## Problem

The repo now bundles skills — packaged instructions plus optional checker
scripts that change how the assistant writes or works. Two gaps:

1. **No enable/disable surface.** Enabling a skill today means manually copying
   its directory into `~/.claude/skills/`. Disabling means deleting it. Neither
   is discoverable, and updates to a bundled skill do not propagate to copies.
2. **No safe default.** Skills alter the assistant's voice and behavior;
   silently activating them on update would surprise owners.

## Proposal

A "Skills" section in the settings UI (menu-bar app and webui):

- **List** every directory under `skills/` with the `description` from its
  SKILL.md frontmatter and an on/off toggle.
- **Default off.** A fresh install or update never activates a skill. State
  lives in the runtime config, e.g.:

  ```yaml
  skills:
    precise-writing: true   # everything absent from this map is off
  ```

- **Enable = symlink** `skills/<name>` into `~/.claude/skills/<name>`
  (copy on filesystems without symlinks). Disable removes the link. Symlinks
  keep enabled skills current when the bundle updates; a copied skill shows an
  "update available" hint when the bundled version changes.
- **Local skills stay untouched.** Anything in `~/.claude/skills/` that the
  manager did not create is listed read-only as "local" — the manager never
  edits or removes it.
- **Conflict rule.** If a local skill and a bundled skill share a name, the
  local one wins and the toggle is disabled with an explanatory tooltip.

## Contribution flow

New skills arrive as pull requests adding one directory under `skills/`
(format and rules in `skills/README.md`). The manager needs no changes to pick
up a new skill — it lists whatever the directory contains.

## Out of scope

- A skill marketplace or remote fetching — skills ship with the repo, reviewed
  as code.
- Per-skill configuration UIs — a skill needing options should read a file
  under `config/` and document it in its SKILL.md.

## Testing

- Unit: toggle writes/removes the symlink and the config entry; local-skill
  collision leaves the local directory untouched.
- The existing suite is unaffected — the manager touches only `skills/`,
  `~/.claude/skills/`, and its own config key.
