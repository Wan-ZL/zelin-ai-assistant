# Contributing

Issues, suggestions, and pull requests are all welcome — bug reports, feature ideas, and questions included.

## Before you start

1. Read `HANDOFF.md` — architecture map, the reasoning behind every "weird" design, and a list of pitfalls that have each already cost real debugging time.
2. Read `docs/CONTRACT.md` before touching any `dashboard.json` / inbox field. **Contract first**: schema changes land in CONTRACT.md before code. Fields are add-only — never renamed or removed; the Swift side decodes everything with `decodeIfPresent` for backward compatibility.

## Development workflow

- Work in a git worktree, fast-forward merge back to `main` (the main working tree may be a live daemon runtime on the maintainer's machine — see HANDOFF §4).
- Every change batch must pass all three gates before merging:
  1. `python3 -m compileall act ingest`
  2. `AIASSISTANT_HOME=$(mktemp -d) python3 -m unittest discover -s tests`
  3. `bash mac/build.sh`
- Tests must use a tempdir `AIASSISTANT_HOME` — never touch a real `state/` or registry.
- All user-visible strings are bilingual via `L("中文", "English")`.
- Commit messages: conventional commits, English, and say *why*, not just what.

## License of contributions

This project is licensed under the [Functional Source License 1.1, MIT Future License (FSL-1.1-MIT)](LICENSE.md). By submitting a contribution (pull request, patch, or suggestion incorporated into the code), you agree that:

1. Your contribution is licensed to the project under the same FSL-1.1-MIT terms (including the future MIT grant), and
2. You grant the project maintainer (Zelin Wan) a perpetual, worldwide, irrevocable right to use, modify, sublicense, and relicense your contribution as part of this project, including under commercial terms.

This keeps the project's licensing options unified in one place. If you're not comfortable with that, open an issue instead of a PR — suggestions are just as valuable.
