<!-- What does this PR do, and WHY? One logical change per PR. -->

Closes #

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
