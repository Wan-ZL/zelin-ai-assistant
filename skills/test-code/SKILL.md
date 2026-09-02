---
name: test-code
description: test code 测试代码 — a five-tier, trigger-aware measurement ladder for ANY repo and ANY AI harness. Detects the stack, asks the human ONCE (tier 1–5 + a multi-select of checks), runs the checks (parallel where safe, per-check timeouts; tier 5 = 通宵/通几天, no limit) and writes one honest report (report.md + report.json) with a three-way "not run" split, a fix-first ranking, core-skip accounting and structural blind spots. Use when asked "test this", "跑测试", "测一下", "测试代码", "test code", "how well is this tested", before a merge, or when an agent finishes work and must attach evidence. It only measures — it never writes specs, code or tests.
version: 0.2.0
upstream: robust-code (Zelin's private skill) ← AmazingAng/old-coder (MIT) — attribution in NOTICE
upstream_version: robust-code SKILL.md snapshot 2026-08-18
---

# test code 测试代码 — the measurement ladder

<!-- Lineage: ASK step, checker doctrine, anti-gaming rules and hardened-layer triggers adapted from robust-code → old-coder (MIT, NOTICE). Ceremony dropped: no SPEC/EVIDENCE artifacts, no build loop — this skill only MEASURES. `$SKILL` = this directory. Deterministic tools decide; this file only decides what to run. -->

## The loop — ask ONCE per run, never per check

| Step | Do | Output |
|---|---|---|
| 1 Detect | `python3 $SKILL/scripts/detect.py --repo <repo> --out <tmp>/detect.json` | stacks · tools · thresholds source · diff vs merge-base · fired triggers · `recommendation` · `menu` (every check: `circle` core/extended, kind na/unavailable/cmd, reason, `est_seconds`) |
| 2 ASK | State the recommended tier (1–5) with its one-line `recommendation.reason` (blast radius / domain / reversibility). Ask **once**: single-select tier + multi-select checks, pre-filled with the tier's **core** checks + fired triggers; extended checks appear unticked with their reason/estimate. The user's pick beats yours. | tier + check ids (+ `skip_reasons` for any core check they untick) |
| 3 Run | `python3 $SKILL/scripts/run_ladder.py --repo <repo> --detect <tmp>/detect.json --tier N --chosen-by user [--checks a,b] [--skip c] [--declared <globs>]` (or `--selection FILE`) | `<repo>/.test-code/reports/<UTC>/report.md`, `report.json`, `logs/*.log`, `selection.json` |
| 4 Report | Paste verbatim: verdict line, layers table, "Layers not run", "Core checks skipped", "Fix first", "Structural blind spots". Give the absolute path of report.md. | exit 0 green · 1 red · 3 incomplete (UNAVAILABLE/SUBSTITUTED, or a core check skipped without a reason) · 2 usage error |

ASK mechanics: Claude Code → `AskUserQuestion` with one single-select (tier) and one multi-select (checks). Other harnesses → numbered text menu; wait. Headless (agent finishing work) → `--chosen-by headless`; the report records `recommended, not confirmed`. **You may add checks, never silently drop core ones**: an unticked applicable core check needs `skip_reasons` in the selection or the verdict is INCOMPLETE.

## Anti-gaming rules (absolute — references/anti-gaming.md)

1. Never weaken a test to make it pass: no broadened assertions, skips, raised tolerances, deleted tests.
2. Never edit a test and the implementation in the same step to reach green. One, run, then the other.
3. Never mock the unit under test; mock boundaries (network, clock, filesystem) only.
4. Never chase the coverage number. Coverage detects untested code; it is not a target.
5. Never report a layer you did not run. UNAVAILABLE and SUBSTITUTED are written as such, never as pass.
6. A failing layer blocks "done". If genuinely blocked, report the failure verbatim as the outcome.
7. Never tune thresholds mid-run. The project's config (`qa/gates.toml` here) is the only truth; ledgers only shrink.
8. Never brief a fresh-context judge with your reasoning; it gets the task, the repo state and the rerun command only.

## Tiers 1–5 — the budget axis (core checks per tier; full definitions: references/tiers.md)

| 档 | Budget | Core checks (always pre-selected when the surface exists) |
|---|---|---|
| **1 静态门** | seconds | `py_compile` `py_lint` `py_format` `ts_typecheck` `js_lint` `shellcheck` `swift_parse` `deps_direction` `length_caps` **`structure`** `secret_scan` `actions_sha_pin` |
| **2 单元 + 尺子** | minutes | `py_unit` `js_unit` `swift_unit` `py_coverage` `js_coverage` `diff_coverage` `complexity` `crap` |
| **3 集成 + 契约** | tens of minutes | `py_integration` `js_e2e` `golden_contract` `migration_roundtrip` `field_add_only` |
| **4 变异 + 稳定性** | up to hours | `mutation_changed` `flaky_detect` `test_smells` |
| **5 通宵 / 通几天** | **no time limit** | `mutation_full` `security_scan` `arch_audit` + optional fresh-context verification (agent step, ≤ 2 rounds) |

Per-check timeouts: 档1 300 s · 档2 1800 s · 档3 3600 s · 档4 7200 s · 档5 none (choosing 5 lifts every timeout). A timeout is a FAIL no post-processing may overturn.

**Extended circle** (menu-visible, never pre-selected; lights up when detection finds the surface or you tick it — references/catalog.md): `type_coverage` `duplication` `doc_coverage` `api_breaking` `bundle_size` `property_tests` `fuzz` `soak_race` `perf_budget` `docs_drift` `dead_code` `license_check` `clean_install` `feedback_channel`, plus table-only rows (cognitive complexity, a11y, test time limits, fuzzing runners, load/soak, SBOM). The catalog is the knowledge; the core is the discipline.

## Triggers — mandatory add-ons regardless of tier (references/triggers.md)

| Trigger (from added lines / file names) | Add-on check | Satisfied by tests named… |
|---|---|---|
| `persisted_state` | `crash_recovery` | crash / truncat / recover / corrupt / partial / atomic |
| `boundary` (network, disk, subprocess) | `fault_injection` | fault / inject / timeout / failure / retry / unreachable |
| `concurrency` | `race_stress` (×`race_reruns`, default 10) | race / concurren / thread / lock / parallel / stress |
| `spawns_processes` / opens fds | `resource_leak` | leak / orphan / fd / descriptor / resource / cleanup |
| `persisted_format` | `corpus_regression` | corpus / fixture / compat / legacy / migrat / schema |
| `documented_behavior` | `contract_drift` + `docs_drift` | contract / drift / help / docs / golden / prompt |
| always | `diff_minimality` | changed files ⊆ `--declared` globs |
| `deps_changed` | `dependency_audit` + `dependency_budget` | auditor on PATH; additions ⊆ `selection.declared_deps` |

A fired trigger with no matching test is a **FAIL** (`missing`), not a skip. Waive only with a written reason: `"triggers_waived": {"persisted_state": "read-only refactor"}`; the reason lands in the report.

## Thresholds, project gates, baselines

- Thresholds are the project's and read-only: `qa/gates.toml` (`[complexity] [crap] [hygiene] [structure]`) → `pyproject.toml` (`max-complexity`, `fail_under`) → skill defaults complexity 10 / CRAP 30 / coverage no-drop / dir depth 6 / 40 files per dir, with the note "Bob-strict = 6". Never create a second source of truth.
- Project gates beat fallbacks: `scripts/qa/{complexity,crap,coverage_floor,depgraph,hygiene,mutate}.py` and `run_coverage.sh` are used when present; otherwise `$SKILL/scripts/complexity_min.py`, `structure_check.py` and the runner's internal CRAP / diff-coverage / no-drop math.
- Old project, day one: `run_ladder.py … --init-baselines` grandfathers today's findings of the skill's own checks (secret_scan, actions_sha_pin, structure, test_smells, docs_drift, complexity/length/CRAP fallbacks, known failing tests, coverage total) into `<repo>/.test-code/baselines/*.txt`. Ledgers are shrink-only: NEW and WORSE fail, STALE is advisory. Commit the baselines; git-ignore `.test-code/reports/`.

## Checker doctrine (kept from robust-code)

| Rule | Implementation |
|---|---|
| Home-grown checks fail CLOSED | unreadable input, checker crash, non-zero rc, timeout → FAIL; no `\|\| true`, no silent skip |
| Every shipped script has a negative control | `tests/test_skill_test_code_*.py` feed known-bad fixtures (planted key, unpinned action, no-assert test, CC-13 function, import cycle, failing test) and assert red |
| Mutation runner must prove it executed each mutant | project `scripts/qa/mutate.py` runs mutants in a temp workspace with hash-keyed state; survivors listed `file:line`; classify equivalents via `selection.equivalent_mutants`, never force kills |
| Kills are attributed to the first failing test | the score validates the suite as a whole, never a single test |

## Report — report.md + report.json (references/report-template.md)

Per-layer status · **Layers not run** split three ways: N-A (project has no such surface) / UNAVAILABLE (tool or input missing, nothing ran) / SUBSTITUTED (something else ran — never a pass) · **Core checks skipped** with the written reason (missing reason ⇒ INCOMPLETE) · **Structural blind spots** (what a single-run ladder cannot see here, e.g. no feedback channel) · baseline note (pre-existing failures verbatim, zero-NEW rule) · fix first: failing tests → changed-file CRAP offenders → surviving mutants in constitution modules → other mutants → new ledger violations → other red layers · ASK record · tool versions · one rerun command. `report.json` is add-only and machine-readable (`surviving_mutants[].location` = `file:line`) for a daily self-improvement loop.

## Selection JSON (optional `--selection FILE`; run_ladder writes the one it used next to the report)

`{"tier": 2, "checks": ["py_unit", …], "ask": {"recommended": 2, "reason": "…", "chosen": 2, "chosen_by": "user"}, "skip_reasons": {"crap": "no coverage tool here"}, "declared_files": ["skills/*"], "declared_deps": [], "known_failing": [], "triggers_waived": {}, "equivalent_mutants": ["act/lib/x.py:10"], "reruns": 3, "race_reruns": 10, "soak_reruns": 20, "mutation_budget": 1800, "timeout_seconds": null}`

## Adapters (references/adapters.md)

Wired: Python (unittest / pytest, coverage.py, ruff / flake8, mypy, project QA scripts or `complexity_min.py` / `structure_check.py`, pylint duplicate-code, interrogate, pip-licenses), JS/TS (vitest / jest, `tsc --noEmit`, eslint, `vitest --coverage`, Stryker, jscpd, size-limit, api-extractor, license-checker, knip), Swift (`swift test` / `xcodebuild test`, `swiftc -parse`), Shell (shellcheck), protobuf (`buf breaking`), generic (secret scan, Actions SHA-pin, docs drift, test smells, structure, clean-VM harness hook, feedback-channel probe). Go / Rust / Java / Scala / SQL: command tables only — run by hand, paste into the report's honest notes.

## Fresh-context verification (档5 option, ≤ 2 rounds; not a script)

Spawn a fresh agent with exactly: the task contract, the repo at the report's commit, the rerun command. It reproduces the run blind, attacks tests / checkers / mapping, fixes nothing; behavioural findings are fixed and re-verified in a new context, description findings are fixed and disclosed. The human grades. Record `not performed | passed | failed | blocked` against the final commit in the report's honest notes.
