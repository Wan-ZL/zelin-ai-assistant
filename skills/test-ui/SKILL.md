---
name: test-ui
description: test ui 测 UI — measure a UI against a REFERENCE through three sensors: STRUCTURE (accessibility-tree inventory — roles, names, topology per screen; source extraction when the app cannot run), TOKENS (design language — colors, type scale, spacing, radius, layout geometry, default theme — from computed styles and/or source constants) and VISUAL (demo-data screenshots, perceptual diff vs goldens). Two modes: `--against <alias | git:ref | dir:path | url:… | app:argv | inventory:file>` (migration parity) and `--against design-system` (project tokens + WCAG). Five tiers, ask ONCE (tier + multi-select), deterministic checkers decide, one honest report (report.md + report.json) with PRESENT / MISSING / CHANGED / WAIVED, the three-way not-run split, fix-first and blind spots. Use for "test the UI", "测 UI", "对照原生", "parity check", "视觉回归", "a11y", "dark mode ok?", before merging a screen/component/token change, or when an agent finishes UI work. It only MEASURES — never designs, never edits UI, never writes a ledger, never blesses a golden.
version: 0.1.0
upstream: test-code (this repo, skills/test-code v0.2.1) — doctrine, ASK, report contract, ledger semantics; anti-gaming lineage robust-code → AmazingAng/old-coder (MIT, NOTICE)
upstream_version: test-code 0.2.1
---

# test ui 测 UI — the measurement ruler for interfaces

<!-- `$SKILL` = this directory. Deterministic scripts decide; this file only decides what to run. Sides: SUBJECT (the UI under test) and REFERENCE (`--against`). Sensors: STRUCTURE · TOKENS · VISUAL. Every artifact carries `producer.mode` (runtime | source | frozen). -->

## The loop — ask ONCE per run, never per check

| Step | Do | Output |
|---|---|---|
| 1 Detect | `python3 $SKILL/scripts/detect_ui.py --repo <repo> [--against <ref>] --out <tmp>/detect.json` | surfaces · tools (node, project playwright, axe, odiff, swiftc) · project adapters (`scripts/ui/*`, `ui/parity/*`, `web/e2e/visual.spec.ts`) · both **sides** resolved with an instrument mode per sensor · seed recipe + demo marker · thresholds{source} · diff vs merge-base → triggers · `recommendation{tier,reason,screens}` · `menu[]{id,circle,kind,sensor,mode,reason,est_seconds,command}` |
| 2 ASK | State the recommended tier (1–5) + one-line reason (screens touched / tokens touched / reversibility) **and the resolved reference** ("against `native` = `ui/parity/native-inventory.json` sha …, mode frozen"). Ask **once**: single-select tier + multi-select checks pre-filled with the tier's core + fired triggers; extended unticked with reason/estimate. The user's pick beats yours. | tier · check ids · `skip_reasons` for any unticked core |
| 3 Run | `python3 $SKILL/scripts/run_ui.py --repo <repo> --detect <tmp>/detect.json --tier N --against <ref> --chosen-by user|headless [--checks …] [--skip …] [--screens …]` (or `--selection FILE`) | `<repo>/.test-ui/reports/<UTC>/{report.md,report.json,selection.json,detect.json,inventory/,tokens/,shots/,proposed/,logs/}` |
| 4 Report | Paste verbatim: verdict line · Sensors table · Items (PRESENT/MISSING/CHANGED/WAIVED per screen) · Layers not run · Core checks skipped · Fix first · Structural blind spots. Absolute path of report.md. Opinions only under `## Opinion (not a measurement)`. | exit 0 green · 1 red · 3 incomplete · 2 usage |

ASK mechanics: Claude Code → `AskUserQuestion` (one single-select tier, one multi-select checks). Other harnesses → numbered menu; wait. Headless (agent finishing UI work) → `--chosen-by headless`, standing default tier 2 `--screens changed`; the report records `recommended, not confirmed`. The reference is a CLI input, never a second question: omitted → detect picks (`[references]` first alias → `git:origin/main` → `design-system`) and the ASK names it; unresolvable → exit 2 with candidates. **You may add checks, never silently drop core ones** (missing `skip_reasons` ⇒ INCOMPLETE).

## Anti-gaming rules (absolute — references/anti-gaming.md; the eight of test-code, with UI enforcement)

1. Never weaken a test to make it pass → never raise `max_changed_pct` / `pixel_tolerance`, widen a token tolerance, grow a mask, or lower a contrast floor: `thresholds_unmoved` diffs thresholds + masks against merge-base; any loosening is FAIL `threshold_raised`.
2. Never edit a test and the implementation in the same step → ledgers/goldens and components moving in one diff are printed side by side (fix-first rank 6).
3. Never mock the unit under test → never re-bless a golden to get green: the skill cannot write goldens; a golden whose sha is not in `manifest.json` with a `reason` is FAIL `unreviewed_golden`; proposals go to `<report>/proposed/`.
4. Never chase the number → "% PRESENT" and "% screens with goldens" are reported, never gated; EXTRA items are never parity.
5. Never report a layer you did not run → `producer.mode` on every artifact; source vs a runtime reference is SUBSTITUTED and can never become pass; UNAVAILABLE is written as such.
6. A failing layer blocks done → exit 1; `seed_guard` / demo-marker refusal aborts all capture; timeouts are FAIL.
7. Never tune thresholds mid-run → read once from the project, printed with their source; ledgers shrink (pending strictly; waivers only with a reason, listed verbatim).
8. Never brief a fresh-context judge → task contract + repo at commit + rerun command only.

UI corollaries the runner enforces: ids come from role + accessible name, never `data-testid`; a `data-parity-id` pin whose role or name does not match its target is CHANGED `spoofed_pin`; hidden (display:none, `hidden`, aria-hidden, 0×0, off-screen) is MISSING, never PRESENT; runtime names not found in the subject's source string set become `{dynamic}` so no user content can enter an inventory even against a hand-given URL; opinion can touch nothing but its own section (unit-pinned).

## Tiers 1–5 — budget axis (per-check timeouts 300 / 1800 / 3600 / 7200 / none; tier 5 lifts all; a timeout is FAIL) — references/tiers.md

| 档 | Budget | Core checks (surface must exist) | Sensor mode |
|---|---|---|---|
| **1 静态门** | seconds, no launch | `surface_detect` `seed_probe` `structure_source` `tokens_source` `pair_structure` `pair_tokens` `theme_default_declared` `off_token_literals` `contrast_pairs` `a11y_static` `ledger_lint` `golden_manifest` `thresholds_unmoved` | source — the real instrument against a source/frozen reference (`native`, `git:`, `dir:`), SUBSTITUTED against a runtime one |
| **2 运行时清单** | minutes | + `structure_runtime` `app_launch` `seed_guard` `pair_runtime` `topology_runtime` `tokens_runtime` `geometry_runtime` `theme_default_observed` `a11y_rules` `screens_capture` | runtime at default theme × default viewport × default language; VISUAL captured, not yet diffed |
| **3 对照 + 矩阵** | tens of minutes | + `visual_diff` `matrix_themes_viewports` `keyboard_reach` `focus_order` `reflow` `i18n_parity` | every theme × viewport × language; VISUAL diffed vs this machine's goldens |
| **4 稳定性** | up to hours | + `inventory_stability` `visual_stability` `states_matrix` `cross_engine` `reference_runtime` | determinism of the instruments — **UNAVAILABLE in 0.1.0** (rows exist, no runner; the report says so) |
| **5 通宵 / 通几天** | **none** | + `matrix_all_routes` `all_references` `clean_machine_ui` `golden_review_sheet` + optional fresh-context adversary (agent step, ≤ 2 rounds) | everything — **UNAVAILABLE in 0.1.0** except the adversary protocol below |

**Extended circle** (menu-visible, never pre-selected — references/catalog.md): `project_parity` (this repo's `scripts/ui/parity_check.py --check`, verbatim) `project_visual` (`web/e2e/visual.spec.ts`, verbatim) `opinion` (advisory only). Table-only in 0.1.0: `lighthouse_a11y` `reduced_motion` `touch_target_size` `dead_tokens` `token_census_floor` `text_overflow_probe` `font_fallback` `rtl_smoke` `perf_budget`, VoiceOver/NVDA scripts, color-blind simulation, Storybook a11y, Percy/Chromatic, macOS AX runtime (`osascript`), iOS `idb`, Flutter semantics dumps.

## Triggers — mandatory add-ons regardless of tier (references/triggers.md)

| Trigger (changed files / added lines in UI files vs merge-base) | Add-on |
|---|---|
| `screen_changed` (`web/src/{components,pages}/**`, `*.tsx/.jsx/.vue/.svelte/.html`, `shell/Sources/*.swift`) | `structure_source` `pair_structure` `topology_runtime` |
| `tokens_changed` (`tokens.css`, `typeScale.ts`, `*Tokens*.swift`, `ui/tokens/**`, `tailwind.config.*`) | `tokens_source` `pair_tokens` `theme_default_declared` `geometry_runtime` `visual_diff` |
| `theme_changed` (`data-theme`, `prefers-color-scheme`, `color-scheme`, theme storage key) | `theme_default_declared` `theme_default_observed` |
| `layout_changed` (`@media`, `grid-template`, `flex-wrap`, width/height literals, `--native-layout-*`) | `topology_runtime` `geometry_runtime` `reflow` |
| `a11y_attr_changed` (`aria-*`, `role=`, `tabIndex`, `:focus`, `outline`, `inert`, `accessibilityLabel`) | `a11y_rules` `focus_order` `keyboard_reach` |
| `names_changed` (i18n catalogs, `L(` / `text(` literals, `server/lanes.py`) | `i18n_parity` |
| `ledger_changed` (`ui/parity/*.txt`, `config.json`, goldens, `qa/gates.toml`) | `ledger_lint` `golden_manifest` `thresholds_unmoved` |
| `demo_changed` (`scripts/demo_seed.py`) · always | `seed_probe` `screens_capture` · `seed_guard` `ledger_lint` |

Docs (`.md/.rst/.txt`), Python and JSON never fire code triggers. An add-on whose instrument is missing is UNAVAILABLE (INCOMPLETE), never silently dropped.

## Reference — `--against` (references/adapters.md)

`design-system` (project tokens + `references/rules/*`; no second inventory — STRUCTURE is measured by `a11y_static`) · `<alias>` from `ui/parity/config.json [references.<alias>]` or the built-in `native` (`ui/parity/native-inventory.json` + `ui/tokens/native-tokens.json`, frozen) · `git:<ref>` (sha resolved at detect; a detached worktree is created under `<repo>/.test-ui/cache/` only when read, removed after; never the live checkout) · `dir:<path>` (another implementation's source) · `url:<http…>` (runtime only; VISUAL refused — the skill did not seed it) · `app:<argv>` · `inventory:<file>` (frozen). Both inventories are written into the report dir so the comparison is reproducible offline. Launch/seed/screens/dims/masks/geometry map are **project data** in `ui/parity/config.json` (this repo gets built-in defaults, named in the report); no launch recipe → runtime checks UNAVAILABLE with the keys to add; tier 1 still runs.

## Items, ledgers, thresholds

Item statuses are exactly four: **PRESENT** · **MISSING** (on reference, absent or hidden on subject) · **CHANGED** (`fields_changed ⊂ {role, name, states, count, gated, unreachable, spoofed_pin, topology:side|parent|order}`) · **WAIVED**; `N-A` marks reference items owned by `shell/os/retired` or runtime-named (`{dynamic}`); subject-only nodes are `extras` (information). Pairing key = `(screen family, role, slug(name))` in the parity contract's grammar (`<kind>:<screen>:<role>:<slug>[#n]`); `aliases.txt` > `data-parity-id` pin > tuple; near-misses (similarity ≥ 0.8) are suggestions only. Ledgers live in the **project**, shrink-only, `<id>  <reason>  [<ref>]`: `ui/parity/pending.txt` (known MISSING; growth vs merge-base = FAIL `pending_grew`; PRESENT while listed = STALE), `ui/parity/waivers.txt` (`<id>` or `<rule>::<id>[::<theme>]`; a line without a reason is invalid; new lines need `selection.waivers_acknowledged`), `ui/parity/aliases.txt` (`<reference id>  <subject id>  <reason>`; dangling = FAIL), `<goldens>/<platform>-<engine>-dpr<n>/manifest.json`. Thresholds: `qa/gates.toml [ui]` → `ui/parity/config.json .thresholds` → skill defaults with the note `strict = WCAG 2.2 AA; visual strict = 0 %`. The skill never writes any of these; `run_ui.py --propose-pending` / `--propose-goldens` write into `<report>/proposed/` and print the copy command.

## Sensor doctrine (kept from test-code's checker doctrine)

| Rule | Implementation |
|---|---|
| Fail closed | unreadable PNG, malformed inventory, driver rc ≠ 0, rc −2 could-not-start, rc −1 timeout, checker crash → FAIL; never pass |
| Negative control per script | `tests/test_skill_test_ui_*.py` + `tests/fixtures/test_ui/{ref,subject}/` plant: missing button, renamed link, `display:none` control, spoofed pin, unlabeled icon button, `#8a8f99` on white, rail moved into `header`, `color-scheme: dark`, lane 400 → 320 with the token still declared, grown pending, reasonless waiver, unreviewed golden, raised threshold — each must go red |
| Mode recorded | `producer.mode` per artifact; SUBSTITUTED only when the reference mode is runtime (or the instrument is a source stand-in for a runtime measurement: topology/geometry at tier 1) |
| Seed guard | the app is seeded by the skill into a temp HOME on a free port; the demo marker must be seen before one screenshot is taken; runtime artifacts carry `seed.seeded_by_skill` |
| Project gate first | `scripts/ui/parity_check.py`, `web/e2e/visual.spec.ts` called verbatim when present; the skill's pairing also runs; disagreement on shared ids = FAIL `parity_disagreement` (both lists, never averaged) |

## Report — report.md + report.json (references/report-template.md)

Header (repo @ commit, dirty, tier chosen/by/recommended + reason, **reference** kind/locator/resolved/mode per sensor, demo marker seen, thresholds source, verdict) · **Sensors** (STRUCTURE / TOKENS / VISUAL × subject mode × reference mode × ran) · **Layers** · **Items** (non-PRESENT rows, capped at 40) · **Rules (hits)** · **Visual** · **Layers not run** (N-A / UNAVAILABLE / SUBSTITUTED) · **Core checks skipped** · **Structural blind spots** · **Fix first** · **Ledger note** · **Triggers fired** · **Tool versions** · **Rerun** · **Notes** · **Opinion (not a measurement)** (only when `opinion` ran; pre-printed "Nothing below changes a status or a rank."). Fix-first: 1 MISSING interactive items and CHANGED **topology** on changed screens → 2 `theme:default`, geometry, tokens → 3 WCAG serious/critical → 4 visual diffs over threshold → 5 MISSING/CHANGED elsewhere → 6 ledger noise (grew, reasonless, dangling alias, unreviewed golden, raised threshold, mask over cap) → 7 other reds. `report.json` is add-only; every `items[].location` is the project id (`control:board:button:approve`) so the daily loop (P5) can turn a MISSING into a proposal card.

## Selection JSON (`--selection FILE`; run_ui writes the one it used next to the report)

`{"tier": 2, "against": "native", "checks": [...], "screens": ["board"], "ask": {"recommended": 2, "reason": "…", "chosen": 2, "chosen_by": "user"}, "skip_reasons": {}, "triggers_waived": {}, "waivers_acknowledged": [], "reruns": 3, "timeout_seconds": null, "opinion": {"text": "…"}}`

## Adapters (references/adapters.md)

Wired: web source (TSX/JSX/HTML/Vue/Svelte tokenizer: implicit roles, `role=`, `aria-label(ledby)`, `<label for|htmlFor>`, `alt`, `title`, `text("zh","en")` bilingual literals, `{flags.x && …}` → gated, `data-parity-id` → pin), web runtime (`probes/driver.cjs` under the project's own `playwright` — never installed; axe-core when resolvable; odiff when on PATH, else the stdlib diff), CSS custom properties per theme scope + W3C design-tokens JSON + `typeScale.ts`, this repo's parity contract (`native` alias; `extract_native_inventory.py` / `extract_native_tokens.py` outputs normalised add-only; `parity_check.py --check` as `project_parity`; `web/e2e/visual.spec.ts` as `project_visual`; launch = `python3 -m server` with `demo_seed.py` on a free `ZAI_PORT` and a temp `AIASSISTANT_HOME`, marker `/api/health .demo == true`). Swift without the project extractor → UNAVAILABLE "needs project adapter" (no Swift heuristics). Table-only: macOS AX runtime, iOS, Flutter, Android.

## Fresh-context verification (档 5 option, ≤ 2 rounds; not a script)

A fresh agent gets exactly the task contract, the repo at the report's commit and the rerun command. Blind phase: reproduce; attack in order — the run · the pairing (rename a control and pin it to the wrong role; hide one behind `display:none`; spoof `data-parity-id`; move a rail into the header keeping every name; shrink a lane keeping the token declared) · the ledgers (grow pending; add a reasonless waiver; swap a golden and update only the sha; raise `max_changed_pct` in a dirty tree; mask 60 %) · the checkers (known-bad inputs) · the mapping (role table, screen map, geometry map). Every attack must be red or disclosed. It fixes nothing; behavioural findings → fix + re-verify in a new context; description findings → fix + disclose. Record `not performed | passed | failed | blocked` against the exact commit in Notes.
