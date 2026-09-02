# Tiers 1–5 — the budget axis

Each tier adds layers on top of the previous one. `default_checks(tier)` = every **core** check with `tier ≤ chosen` plus every add-on whose trigger fired (triggers.md). **Extended** checks (catalog.md) appear in the menu with their reason and estimate but are never pre-selected; the AI or the human may add them — and may not silently drop a core one (`core_skipped` in the report; a skip without `skip_reasons` makes the verdict INCOMPLETE).

Per-check timeouts: 档1 300 s · 档2 1800 s · 档3 3600 s · 档4 7200 s · 档5 none (choosing 5 lifts all timeouts). Time estimates in the `menu` are static per check; multiply reruns yourself.

Status vocabulary for every layer: **pass** · **fail** · **na** (project has no such surface) · **unavailable** (tool or input missing, nothing ran) · **substituted** (something else ran; never a pass). Verdict: any fail → red (exit 1); no fail but any unavailable/substituted, or a core check skipped without a reason → incomplete (exit 3); otherwise green (exit 0).

## 档 1 静态门 — seconds (core)

| id | what it catches | pass criterion | tool per stack |
|---|---|---|---|
| `py_compile` | syntax / bytecode errors | `python -m compileall -q <roots>` exit 0 | python |
| `py_lint` | latent bugs, unused names | ruff (`ruff check .`) or flake8 exit 0 | ruff / flake8; unavailable if neither (`uvx ruff check .` works ad hoc) |
| `py_format` | formatting drift | `ruff format --check` / `black --check` | only when pyproject configures one; else na |
| `ts_typecheck` | whole classes of JS bugs | `npx --no-install tsc --noEmit -p .` per package | needs tsconfig + installed tsc |
| `js_lint` | eslint findings | `npx --no-install eslint .` | needs eslint config + install |
| `shellcheck` | shell footguns | `shellcheck <all *.sh>` | shellcheck |
| `swift_parse` | Swift syntax | `swiftc -parse <file>` one file per step | swiftc |
| `deps_direction` | layering violations | project `scripts/qa/depgraph.py --check` or `lint-imports` | na when no rules declared |
| `length_caps` | oversized files / functions | project `scripts/qa/hygiene.py --check` or `complexity_min.py --only lengths` | thresholds from project config |
| `structure` | **tests outside the tests dir · same module basename in two dirs · dir depth · crowded dirs · python import cycles · orphan modules**; mirror ratio reported | zero NEW vs `.test-code/baselines/structure.txt`; caps from `qa/gates.toml [structure]` (defaults depth 6, 40 files/dir) | internal (`structure_check.py`); JS colocated `*.test.*` and Go `_test.go` are conventions, not violations |
| `secret_scan` | key/token-shaped strings in tracked files | zero NEW vs `baselines/secret_scan.txt` | internal (AWS, GitHub, Slack, Anthropic, Google, private keys, generic `token = "…"`) |
| `actions_sha_pin` | GitHub Actions `uses:` not pinned to a 40-hex SHA | zero NEW | internal |

## 档 2 单元 + 尺子 — minutes (core)

| id | what it catches | pass criterion | notes |
|---|---|---|---|
| `py_unit` | regressions | zero NEW failing tests (pre-existing ones listed verbatim from `known_failing`) | unittest discover or pytest; a non-zero exit with no parseable failure is a fail (fail closed) |
| `js_unit` | regressions | vitest / jest exit 0 | per package dir |
| `swift_unit` | regressions | `swift test` (Package.swift) or `xcodebuild test -scheme <first shared scheme>` | |
| `py_coverage` | untested code | project `run_coverage.sh` + `coverage_floor.py`, or `coverage run … && coverage json` + no-drop vs `baselines/coverage_total.txt` | without a baseline the generic path is **substituted** (measured, not gated) |
| `js_coverage` | untested code | `vitest run --coverage`; gated only if the vitest config has `coverage.thresholds` | else substituted |
| `diff_coverage` | untested changed lines | every added Python statement executed (100%) — Google shows exactly this in review | needs `coverage.json` from this run; files outside the coverage source are listed, not failed |
| `complexity` | unmaintainable functions | project `complexity.py --check` or `complexity_min.py --only cc --max-cc <thr>` | Bob-strict 6; skill default 10 |
| `crap` | complex AND untested functions | `CRAP = CC² × (1−cov)³ + CC ≤ threshold`; project `crap.py` or internal fallback with ledger | needs `coverage.json`; Bob-strict 6, classic 30 |

Extended at this tier (catalog.md): `type_coverage` `duplication` `doc_coverage`.

## 档 3 集成 + 契约 — tens of minutes (core)

| id | what it catches | pass criterion |
|---|---|---|
| `py_integration` | cross-component breakage | `tests/integration` suite green |
| `js_e2e` | end-to-end smoke | `npm run test:e2e` / `npx playwright test` exit 0 |
| `golden_contract` | wire / snapshot drift | test files matching golden/contract/snapshot/wire green |
| `migration_roundtrip` | data migrations that do not round-trip | test files matching migrat/round_trip/parity/export/upgrade green |
| `field_add_only` | removed keys in schema-ish files (json/yaml/proto or paths with schema/contract/wire/model/types) | zero removed keys in the diff |

Extended at this tier: `api_breaking` `bundle_size`. **Real-machine execution** belongs here too when the project has an install path: run the installer / launcher once on this machine and probe liveness (the trigger `boundary` add-on and the project's own doctor cover it; a project without such a probe should say so in the blind-spots section).

## 档 4 变异 + 稳定性 — up to hours (core)

| id | what it catches | pass criterion |
|---|---|---|
| `mutation_changed` | tests that assert nothing on changed constitution modules | project `mutate.py --modules <changed ∩ qa/mutation_targets.toml>` (budget `mutation_budget`, default 1800 s): zero survivors after `equivalent_mutants` are excluded; generic: `mutmut run` / `npx stryker run` exit 0 |
| `flaky_detect` | order dependence / nondeterminism | pytest + pytest-randomly ×N green; plain unittest ×N is **substituted** (cannot see order dependence) |
| `test_smells` | no-assert tests, `sleep` in tests, real IO (subprocess/urllib/requests/socket/httpx) in unit tests | zero NEW vs ledger |

Extended at this tier: `property_tests`.

## 档 5 通宵 / 通几天 — no time limit (core)

| id | what it catches | pass criterion |
|---|---|---|
| `mutation_full` | the whole suite's blind spots | project `mutate.py --all --force` with a week-long budget, zero unclassified survivors |
| `security_scan` | vulnerabilities, leaked secrets, bad deps | bandit / gitleaks / pip-audit / npm audit / cargo-audit / govulncheck — every available tool exit 0 (npm audit needs network) |
| `arch_audit` | architecture rule violations | project depgraph + hygiene, `qlty check --all` when configured |

Extended at this tier: `fuzz` `soak_race` `perf_budget` `docs_drift` `dead_code` `license_check` `clean_install` `feedback_channel` — and the clean-VM install is the one to insist on before telling other people to install: fresh VM → install per README → doctor/smoke → upgrade from the previous release → uninstall leaves no residue (recipe in catalog.md).

### Fresh-context verification (agent step, not a check)

Run only at 档 5 and only when the stakes justify spending a second context. Give a fresh agent exactly four things: the task contract (original request + every approved change), the repo at the report's commit, the rerun command, and nothing of your reasoning. Blind phase first: it reproduces the run and records its own numbers, then attacks in this order — the run · the tests (make them pass wrongly; invent mutants you did not choose) · the checkers (feed each a known-bad input) · the mapping. Only then show it the report. It fixes nothing. Findings are graded by the human: behavioural → fix and re-verify in a new context; description → fix and disclose. Cap two rounds; a state no verifier saw is `not performed`. Record the verdict against the exact commit in the report's honest notes.
