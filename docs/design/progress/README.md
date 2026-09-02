# docs/design/progress/ — vnext2-plan §8 progress rows, one file per PR (CONTRACT §56.7)

Section 8 of [`docs/design/vnext2-plan.md`](../vnext2-plan.md) is the progress log of the v-next-2 round: one table row per landed PR. Every PR appended its row at the bottom of the same table, so parallel PRs collided on adjacent lines exactly like `CHANGELOG.md` `[Unreleased]` did. The rows already in the plan stay there as frozen history; **new rows are files in this directory** and the CI check **Version pins untouched** rejects a new `| YYYY-MM-DD |` row added to the plan directly.

## Shape

File name: `docs/design/progress/<YYYY-MM-DD>-<kebab-slug>.md` — the date is the day the PR was opened (it becomes the 日期 column), the slug is conventionally the branch name.

```
pr: `ci/changelog-fragments`（PR #NNN）
phase: 横切（流程；§56）
law: §56.1 修订 / §56.7（新增）

做了什么 — the body, free markdown prose, as many paragraphs as needed. Written in the same
register as the existing rows: Chinese narrative, technical terms in English, owner quotes in 「」.
```

The header is `key: value` lines up to the first blank line — `pr:` (branch + PR number), `phase:` (which plan phase / decision this serves), `law:` (CONTRACT §§ touched, or `—` for docs-only). The body follows the blank line. When rendered into the table, paragraphs are joined with spaces and `|` is escaped.

Validate locally: `python3 scripts/ci/progress_log.py check`.

## Reading the full log

`python3 scripts/ci/progress_log.py render` prints the complete §8 table — the frozen rows from the plan followed by every fragment here, oldest first — to stdout. It is rendered **on demand and never written back** into the plan: a generated file in git would recreate the very conflict this directory removes. Pipe it wherever you need it (`| less`, into a review comment, into a report).
