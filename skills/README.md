# Bundled skills

Skills are packaged writing/workflow instructions the assistant can load for a
specific kind of task. Each skill is a directory:

```
skills/<skill-name>/
├── SKILL.md          # entry point: when to use it, routing, workflow, hard rules
├── references/       # detail files the workflow loads on demand (keeps context small)
└── scripts/          # optional deterministic checkers/tools the workflow runs
```

`SKILL.md` starts with YAML frontmatter (`name`, `description`); the
`description` tells the model when the skill applies. Reference files hold the
long material so a skill costs little context until its details are needed.
Scripts must be dependency-free (Python 3.9+ standard library only) so they run
on a stock install.

## Default-off policy

Bundled skills ship **disabled**. A skill changes how the assistant writes or
works, and that is a per-owner preference — nobody should get a new voice or
workflow because they pulled an update. Owners opt in per skill (see the
proposed settings manager in `docs/design/skills-manager.md`; until that
lands, enabling means copying or symlinking the skill directory into
`~/.claude/skills/`).

## Contributing a skill

New skills are welcome via pull request:

1. One directory under `skills/`, following the structure above.
2. No personal data: no real names, private paths, or owner-specific facts.
   Use placeholders (`<your name>`) and repo-relative paths
   (`config/voice-profile.default.md`).
3. Quote external sources with attribution and retrieval date.
4. If the skill includes a script, include a way to test it (a sample input in
   the PR description is enough) and keep it standard-library only.
5. Skills must not send anything anywhere — they instruct and check text; the
   owner sends.

## Current skills

| Skill | What it does | Default |
|---|---|---|
| `precise-writing` | Precision-first formal English (emails, papers, articles, CVs) modeled on Munindar P. Singh's editorial standards, with a deterministic style linter (`scripts/style_check.py`) | off |
