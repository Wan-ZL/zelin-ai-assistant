# Catalog — the extended circle (knowledge, not discipline)

The core checks in SKILL.md are what a tier **must** run. This catalog is what a project **can** run: every mainstream hard metric that Google/Meta/GitHub-scale presubmits enforce deterministically, each with the condition under which the ladder lights it up, the pass line, and the tool the runner builds. Extended checks are menu-visible and never pre-selected; the AI reading this file decides what to add for the project in front of it and records the choice (and, for anything it declined that the detector lit up, the reason) in the report's honest notes. Thresholds come from the project's config when it has one; the numbers below are the mainstream defaults.

## Wired extended checks (run_ladder builds the command)

| id | 档 | lights up when | pass line | tool | mainstream precedent |
|---|---|---|---|---|---|
| `type_coverage` | 2 | `[tool.mypy]` / `mypy.ini` present | mypy exit 0; `--txt-report` gives the typed-line % to ratchet | mypy | Meta Pyre / Google typed Python |
| `duplication` | 2 | jscpd in node_modules, or pylint on PATH | ≤ 3% duplicated lines (jscpd `--threshold 3`); pylint `duplicate-code` clean | jscpd / pylint | SonarQube duplication gate |
| `doc_coverage` | 2 | interrogate on PATH | public functions/classes with docstrings ≥ 80% (`--fail-under 80`) | interrogate | Google style guide: public API must be documented |
| `api_breaking` | 3 | `buf.yaml` (protobuf) or api-extractor in node_modules | zero breaking changes vs base (`buf breaking --against .git#branch=<base>`) | buf / api-extractor | Google proto compatibility hard line |
| `bundle_size` | 3 | npm script `size` or size-limit in node_modules | within the declared budget | size-limit | Meta / Google mobile & web size gates |
| `property_tests` | 4 | a test imports hypothesis | N examples, 0 failures | hypothesis | invariants you did not enumerate |
| `fuzz` | 5 | `fuzz/` harness exists | 0 crashes (v0.2: reported UNAVAILABLE with instructions — run by hand) | libFuzzer / atheris / jazzer | Google OSS-Fuzz |
| `soak_race` | 5 | race/concurrency test files exist | ×`soak_reruns` (default 20) all green | project tests | timing bugs that pass once |
| `perf_budget` | 5 | `benchmarks/` + pytest-benchmark, or npm `bench` | no regression vs recorded budget | pytest-benchmark / hyperfine | Lighthouse-style budgets |
| `docs_drift` | 5 (also by trigger) | markdown present | zero NEW backticked repo paths that no longer exist | internal | Google doc tests |
| `dead_code` | 5 | vulture / knip available | zero unused exports / dead functions | vulture / knip | dead-code elimination programs |
| `license_check` | 5 | pip-licenses / license-checker available | inventory (SUBSTITUTED until an allowlist is configured; `--fail-on` / `--failOn` to gate) | pip-licenses / license-checker | legal hard line at every large org |
| `clean_install` | 5 | `scripts/clean-vm-install.sh` exists | fresh VM: install → doctor/smoke → upgrade → uninstall, all exit 0 | tart / container / clean CI runner | "works on 1000 machines, not just mine" |
| `feedback_channel` | 5 | always (informational) | never fails; absence is written into **Structural blind spots** | internal | a single-run ladder cannot replace telemetry / crash reporting / issue triage |

## Table-only rows (no runner in v0.2 — run by hand, paste into honest notes)

| metric | pass line | tool | why it is mainstream |
|---|---|---|---|
| Cognitive complexity | ≤ 15 per function | SonarQube / `flake8-cognitive-complexity` / eslint `sonarjs/cognitive-complexity` | reads closer to "how hard to read" than cyclomatic |
| Test time limits | per-test hard timeout (Google: small ≤ 60 s, medium ≤ 300 s, large ≤ 900 s) | pytest-timeout / vitest `testTimeout` | Google's most famous test discipline |
| Accessibility | axe serious/critical violations = 0 | axe-core / Lighthouse CI | standard in every large web org |
| i18n hardcoded strings | 0 in UI code | eslint-plugin-i18n-json / project lint | when the product ships in >1 language |
| Static security analysis | high-severity findings = 0 | CodeQL / Semgrep (Meta: Infer, Pysa, Zoncolan; Google: Tricorder) | GitHub-native; run nightly |
| Dependency vulnerabilities | critical/high = 0 | OSV-Scanner / pip-audit / npm audit / cargo-audit / govulncheck | supply-chain hard line |
| Fuzzing runner | crashes = 0, coverage ≥ X | libFuzzer / atheris / jazzer / cargo-fuzz | Google OSS-Fuzz |
| Load / soak | p95 latency, error rate, memory growth over N h within budget | k6 / locust / vegeta | services only |
| SBOM | generated per release | syft / cyclonedx | compliance baseline |
| Version matrix | every claimed OS / language version green | tox / nox / CI matrix / tart images | one version green is not evidence for the others |
| Architecture metrics | import cycles 0 · layering violations 0 · distance from main sequence D ≤ 0.3 · propagation cost trending down · decoupling level trending up | dependency-cruiser / import-linter / ArchUnit / JDepend-style calculators / DV8 | Martin's 1994 package metrics; MacCormack propagation cost; Cai's Decoupling Level |
| Commit / PR hygiene | conventional-commit lint passes; PR ≤ ~400 changed lines (advisory) | commitlint / a diff-size script | Google small-CL culture |

## Structure — what `structure` measures and what is deliberately NOT measured

Measured (deterministic, ledgered): tests outside the declared tests dir · duplicate python module basenames across directories · directory depth · files per directory (tests dir root exempt) · python import cycles (Tarjan SCC over the project's own modules) · orphan python modules (no importer, no `__main__` guard, not in `scripts/`/`bin/`/`tools/`). Reported only: mirror ratio (source modules with a same-named test). Not measured, on purpose: whether directory *names* make sense to a newcomer — that is the one structural judgment left to a fresh-context reviewer with a rubric, a negative control and quoted findings (档 5 option), because no number carries it.

## Clean-VM install recipe (档 5, the "other people's machines" check)

1. Provision a fresh image: macOS → `tart clone ghcr.io/cirruslabs/macos-sequoia-base:latest test-code-vm && tart run test-code-vm`; Linux → a plain distro container; Windows → a clean GitHub Actions runner.
2. Inside, follow **only** the README's install steps (no cached toolchains, no dotfiles). Every undocumented prerequisite you hit is a finding.
3. Run the project's doctor / smoke command; then upgrade from the previous published release to the current one; then uninstall and assert nothing is left (launchd/systemd units, cron lines, app bundles, state dirs).
4. Persist the whole thing as `scripts/clean-vm-install.sh` so `clean_install` can run it unattended; record the image identifier and duration in the report.

## Lineage

Ecosystem command tables and the manual mutation procedure adapt AmazingAng/old-coder (MIT, NOTICE). The presubmit-metric inventory reflects public practice at Google (Tricorder, test sizes, mutation testing at scale — Petrović & Ivanković 2018), Meta (Infer, Pysa, size gates) and GitHub (CodeQL, push protection, Dependabot); Martin's package metrics (1994), MacCormack/Baldwin propagation cost, Mo & Cai decoupling level.
