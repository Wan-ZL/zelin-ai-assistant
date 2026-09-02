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
- [ ] **No version, no shared-ledger edits** (CONTRACT §56.1 / §56.7; the required check **Version pins untouched**) — no version pin or `__version__` edit, nothing written into `CHANGELOG.md` `## [Unreleased]`, no new row in `docs/design/vnext2-plan.md` §8
- [ ] **Release note as a fragment** — `changelog.d/<kebab-slug>.md` (first line `type: added|changed|deprecated|removed|fixed|security`, then `- ` bullets; [shape](https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/changelog.d/README.md)), validated by `python3 scripts/ci/changelog_fragments.py check`; released fragments pruned with `python3 scripts/ci/changelog_prune.py` if CI hinted
- [ ] **Progress row as a fragment** (v-next-2 round) — `docs/design/progress/<YYYY-MM-DD>-<slug>.md` with `pr:` / `phase:` / `law:` + body ([shape](https://github.com/Wan-ZL/zelin-ai-assistant/blob/main/docs/design/progress/README.md)), validated by `python3 scripts/ci/progress_log.py check`
- [ ] **Bilingual strings** — every new user-visible string uses `L("中文", "English")`
- [ ] New behavior is covered by a test where practical
- [ ] Commit messages are conventional commits, in English, and explain *why*
