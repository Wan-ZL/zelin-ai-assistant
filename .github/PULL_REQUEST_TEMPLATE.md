<!-- What does this PR do, and WHY? One logical change per PR. -->

Closes #

## The three questions (constitution check — answer in the description above)

Read [`CLAUDE.md`](https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/CLAUDE.md) and [`docs/CONTRACT.md` §0](https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/docs/CONTRACT.md) first.

1. Which CONTRACT §§ does this change touch? (New behavior ⇒ which section is added/amended in this same PR — or state "none, because…")
2. Which §0 constitution articles does it relate to? Does it break one? (Breaking one ⇒ amend the constitution explicitly in this PR, or change the approach.)
3. Does an existing mechanism already do something similar? (Search the contract before inventing a parallel one.)

## The three gates

CI runs the same three on every PR; please run them locally first.

- [ ] `python3 -m compileall act ingest` passes
- [ ] `AIASSISTANT_HOME=$(mktemp -d) python3 -m unittest discover -s tests` passes (tests use a tempdir HOME — never a real `state/` or registry)
- [ ] `bash mac/build.sh` builds cleanly

## Project rules

See [CONTRIBUTING.md](https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/CONTRIBUTING.md). Tick what applies; strike through what doesn't (e.g. docs-only).

- [ ] **Contract first** — this PR touches a `dashboard.json` / inbox field only if `docs/CONTRACT.md` is updated in the same PR, the field is add-only (nothing renamed or removed), and the Swift side decodes it with `decodeIfPresent`
- [ ] **Bilingual strings** — every new user-visible string uses `L("中文", "English")`
- [ ] New behavior is covered by a test where practical
- [ ] Commit messages are conventional commits, in English, and explain *why*
