# changelog.d/ — one release-notes fragment per PR (CONTRACT §56.7)

`CHANGELOG.md` `[Unreleased]` was the last place where every parallel PR inserted a line at the same spot and merged into a conflict. From now on a PR never edits `[Unreleased]` (the CI check **Version pins untouched** rejects added bullets and `### ` headings there); it adds **one file here** instead. Files never touch each other, so `main` can move freely under an open PR.

## Shape

File name: `changelog.d/<kebab-slug>.md` — lowercase letters, digits and hyphens, conventionally your branch name (`ci-changelog-fragments.md`). One `type` per file; a PR that both adds and fixes writes two files (`<slug>.md` + `<slug>-fixed.md`).

```
type: added
- **What changed, in one bold clause**: the rest of the entry, as long as it needs to be.
  Indented continuation lines and sub-bullets belong to the bullet above.
- A second entry.
```

The first non-blank line is `type: <kind>` with kind one of `added` `changed` `deprecated` `removed` `fixed` `security` (Keep a Changelog's groups; case-insensitive). Everything after it is top-level `- ` (or `* `) bullets; loose prose is rejected. Chinese-first, technical terms in English, same register as the entries in `CHANGELOG.md`.

Validate locally: `python3 scripts/ci/changelog_fragments.py check` · preview the assembled section: `python3 scripts/ci/changelog_fragments.py render`.

## Lifecycle

1. **PR**: add your fragment. CI validates its shape (a malformed fragment would otherwise silently vanish from the release notes).
2. **Merge = release** (§56.2): `release.yml` builds the GitHub Release body from every fragment present at the tag plus the legacy `[Unreleased]` text, minus what the previous tag already contained (`scripts/ci/changelog_release_notes.py`). Nothing is rewritten or deleted by CI — CI never commits to `main`.
3. **Prune**: once a tag has shipped a fragment, the next PR that touches `changelog.d/` (or any chore PR) removes it with `python3 scripts/ci/changelog_prune.py` (deletes only fragments that are byte-identical to the copy in the latest tag; `--dry-run` lists). CI prints a `::notice::` on every PR while released fragments are still lying around. Late or skipped pruning never duplicates a note: the release body is a delta.

Do not edit a fragment that has already shipped — the edited entry would re-appear in the next release. Add a new fragment instead.
