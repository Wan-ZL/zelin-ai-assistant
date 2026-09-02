# Ecosystem adapters

Prefer whatever the project already uses; `detect.py` reads project config first. "Wired" = `run_ladder.py` builds and runs the command itself; "table only" = run it yourself and paste the result into the report's honest notes (never into a check row).

## Python — wired

| Layer | Detection | Command the runner builds |
|---|---|---|
| tests | pytest if `pytest.ini`, `conftest.py`, `[tool.pytest…]` or `[pytest]` in tox.ini; else unittest | `python -m pytest -q` / `python -m unittest discover -s <tests_dir>`; subsets: `python -m unittest <files>` / `pytest -q <files>` |
| coverage | project `scripts/qa/run_coverage.sh` (+ `coverage_floor.py`) else coverage.py importable | `bash scripts/qa/run_coverage.sh <out>` / `COVERAGE_FILE=<out>/.coverage coverage run --source=<src roots> -m unittest … && coverage json -o <out>/coverage.json` |
| lint / format | ruff or flake8 on PATH; `[tool.ruff.format]` / `[tool.black]` | `ruff check .`, `ruff format --check .`, `black --check .` |
| complexity / lengths | project `scripts/qa/complexity.py`, `hygiene.py` else skill fallback | `python scripts/qa/complexity.py --check` / `python $SKILL/scripts/complexity_min.py --only cc --root <repo> --max-cc <thr> [--baseline …] <src roots>` |
| CRAP | project `scripts/qa/crap.py` else internal | `python scripts/qa/crap.py --check --coverage-json <out>/coverage.json`; internal fallback maps `coverage.json` line sets onto AST function spans |
| dependency direction | project `scripts/qa/depgraph.py` or import-linter | `python scripts/qa/depgraph.py --check` / `lint-imports` |
| mutation | project `scripts/qa/mutate.py` + `qa/mutation_targets.toml`; else `[tool.mutmut]` + mutmut | `python scripts/qa/mutate.py --modules <changed targets> --json <out>/mutation.json --md <out>/mutation.md --state <out>/mutation_state.json --force --time-budget <s>`; full = `--all`; generic `mutmut run` |
| property tests | test files importing hypothesis; hypothesis importable | the subset command above |
| flaky detection | pytest + pytest-randomly | `pytest -q -p randomly` ×N; unittest ×N is SUBSTITUTED |
| security / deps | bandit, gitleaks, pip-audit (+ a requirements file), vulture | `bandit -q -r <src>`, `gitleaks detect --no-banner --redact -s .`, `pip-audit -r <req>`, `vulture --min-confidence 80 <src>` |

## JavaScript / TypeScript — wired (per `package.json` directory, node_modules must be installed)

| Layer | Detection | Command |
|---|---|---|
| types | `tsconfig.json` + `node_modules/.bin/tsc` | `npx --no-install tsc --noEmit -p .` |
| lint | `.eslintrc*` / `eslint.config.*` + bin | `npx --no-install eslint .` |
| tests | `vitest` or `jest` in dependencies + bin | `npx --no-install vitest run` / `npx --no-install jest --ci` |
| coverage | vitest + `@vitest/coverage-*`; gated only if the vite/vitest config mentions `thresholds` | `npx --no-install vitest run --coverage` (SUBSTITUTED without thresholds) |
| e2e | `test:e2e` / `e2e` script, or `@playwright/test` + bin | `npm run test:e2e` / `npx --no-install playwright test` |
| mutation | `@stryker-mutator/core` + bin | `npx --no-install stryker run` |
| dead code / audit | knip bin; lockfile + npm | `npx --no-install knip`; `npm audit --audit-level=high` (needs network) |

## Swift — wired

| Layer | Detection | Command |
|---|---|---|
| syntax | any `.swift` + swiftc | `swiftc -parse <file>` one file per step (merged calls fail on duplicate basenames and multiple top-level-code files) |
| tests | `Package.swift` → `swift test` in its directory; else the first shared scheme `*.xcodeproj/xcshareddata/xcschemes/*.xcscheme` → `xcodebuild test -scheme <name> -destination platform=macOS` in the project's directory | |

## Shell and generic — wired

| Layer | Command / rule |
|---|---|
| shellcheck | `shellcheck <every tracked *.sh>` |
| secret scan | internal regexes: AWS `AKIA…`, GitHub `ghp_/gho_/github_pat_`, Slack `xox[abprs]-`, `-----BEGIN … PRIVATE KEY-----`, Anthropic `sk-ant-`, Google `AIza…`, generic `api_key/secret/token/password = "<20+ chars>"`; key = `path::rule::sha1(line)[:10]` so line moves do not churn the ledger; report shows only path + rule, never the value |
| Actions SHA-pin | every `uses:` in `.github/workflows/*.yml` must end in `@<40-hex>`; `./local` and `docker://` are exempt |
| docs drift | backticked tokens containing `/` whose first segment is a top-level repo directory must exist as a tracked file or directory; CHANGELOG* exempt; globs, URLs, `./`, `~` ignored |
| test smells | AST scan of `tests/**/test*.py`: `test_*` without any assert/raises/fail/expect/check/verify call, `sleep(` calls, and (unit tests only, `tests/integration` exempt) imports of subprocess / urllib / requests / socket / httpx / http.client |

## Extended circle — wired (menu-visible, never pre-selected; catalog.md has the pass lines)

| id | Detection | Command |
|---|---|---|
| `type_coverage` | `[tool.mypy]` / `mypy.ini` + mypy on PATH | `mypy --txt-report <out>/mypy-report <src roots>` |
| `duplication` | jscpd in node_modules, else pylint on PATH | `npx --no-install jscpd --threshold 3 .` / `pylint --disable=all --enable=duplicate-code <src>` |
| `doc_coverage` | interrogate on PATH | `interrogate -v --fail-under 80 <src roots>` |
| `api_breaking` | `buf.yaml` + buf, or api-extractor in node_modules | `buf breaking --against .git#branch=<base>` / `npx --no-install api-extractor run` |
| `bundle_size` | npm script `size` or size-limit bin | `npm run size` / `npx --no-install size-limit` |
| `license_check` | pip-licenses on PATH / license-checker bin | `pip-licenses --format=markdown` / `npx --no-install license-checker --summary` — SUBSTITUTED (inventory) until an allowlist gates it |
| `clean_install` | `scripts/clean-vm-install.sh` in the project | `bash scripts/clean-vm-install.sh` (档 5, no timeout); recipe in catalog.md (tart / container / clean runner) |
| `feedback_channel` | always | internal probe for telemetry / crash / issue-template files; absence → structural blind spot, never red |
| `structure` (core, 档 1) | always (python-centric graph rules + generic placement rules) | internal `structure_check.py`; caps from `qa/gates.toml [structure]` |

## Table only (no runner code in v0.2) — from old-coder's gauntlet

| Ecosystem | Tests | Types | Lint | Coverage | Mutation | Property |
|---|---|---|---|---|---|---|
| Go | `go test ./... -race` | `go build ./...` | `go vet ./... && staticcheck ./...` | `go test -coverprofile=c.out ./... && go tool cover -func=c.out` | manual procedure | `rapid.Check` |
| Rust | `cargo test` | `cargo check` | `cargo clippy -- -D warnings` | `cargo llvm-cov --branch` | `cargo mutants --file <changed>` | proptest |
| Java | `./mvnw test` / `./gradlew test` | `./mvnw compile` | Checkstyle + Spotless | JaCoCo | PIT (`org.pitest:pitest-maven:mutationCoverage`) | jqwik |
| Scala | `sbt test` | `sbt compile "Test / compile"` | `sbt scalafmtCheckAll "scalafixAll --check"` | scoverage | Stryker4s (`sbt stryker`) | ScalaCheck |
| SQL | project / `dbt test` against a disposable instance | `sqlfluff parse --dialect <d>` + explain each changed statement | `sqlfluff lint` | map every changed statement to an integration test | manual procedure on predicates/joins/constraints | host-language generators |

## Manual mutation procedure (when no tool exists)

Reach for a real tool first: it generates mutants from the syntax tree and cannot report a mutant it never ran. A hand-rolled runner **must prove it executed each mutant** — old-coder's own demo found two same-size mutants written in the same second sharing a bytecode cache, so kills were reported for code that never ran. Script it, persist the script in the repo, pin mtimes or clear `__pycache__`, and abort if the cache check fails. Then: pick the changed code; one at a time introduce 3–5 plausible bugs (flip a comparison, off-by-one a bound, delete a branch or early return, swap and/or, return a constant); run the suite after each; every mutant must fail at least one test; restore and confirm with `git diff` that only intended changes remain; report "manual mutation: N/N killed" in the honest notes and classify any survivor you could not make diverge as equivalent, with the reason.

## Tool notes

`npm audit`, `pip-audit` and `cargo audit` reach the network — expect them at T4 or with an explicit `dependency_audit` selection. `uvx ruff@<version> check .` gives a ruff run without installing anything. Everything the runner spawns goes through one seam (`ladder_common.run_command`, process-group kill on timeout); tests inject a fake and never spawn real tools.
