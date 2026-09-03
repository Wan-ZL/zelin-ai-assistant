# Tiers 1–5 — the budget axis

Each tier adds layers on top of the previous one. `default_checks(tier)` = every **core** check with `tier ≤ chosen` plus every add-on whose trigger fired (triggers.md). **Extended** checks (catalog.md) appear in the menu with their reason and estimate but are never pre-selected; the AI or the human may add them — and may not silently drop a core one (`core_skipped` in the report; a skip without `skip_reasons` makes the verdict INCOMPLETE).

Per-check timeouts: 档1 300 s · 档2 1800 s · 档3 3600 s · 档4 7200 s · 档5 none (choosing 5 lifts all timeouts). A check keeps its own tier's budget when pulled into a lower tier by a trigger. A timeout is a FAIL no post-processing may overturn.

Status vocabulary for every layer: **pass** · **fail** · **na** (project has no such surface) · **unavailable** (tool or input missing, nothing ran) · **substituted** (something else ran; never a pass). Verdict: any fail → red (exit 1); no fail but any unavailable/substituted, or a core check skipped without a reason → incomplete (exit 3); otherwise green (exit 0).

Item vocabulary (inside `pair_*`): **PRESENT** · **MISSING** · **CHANGED** · **WAIVED** (+ `N-A` for items the subject can never own: `owner ∈ {shell, os, retired}` or runtime-named `{dynamic}`).

## 档 1 静态门 — seconds, no launch (core)

| id | sensor | what it measures | pass line | mode |
|---|---|---|---|---|
| `surface_detect` | structure | UI surfaces found, reference resolved, instrument mode per sensor | at least one surface; reference resolvable | — |
| `seed_probe` | ladder | `demo_seed.py <tmp> --scene initial` then `--check` | both exit 0 | cmd |
| `structure_source` | structure | source inventory of the subject (roles, names, topology) | ≥ 1 item, zero unreadable files; a Swift surface without the project extractor is noted as a blind spot | source |
| `tokens_source` | tokens | CSS custom properties per theme scope, design-tokens JSON, `typeScale.ts`, declared default theme, literal census | table found | source |
| `ledger_lint` | ladder | `ui/parity/{pending,waivers,aliases}.txt` well-formed + shrink-only vs merge-base | no reasonless waiver, no dangling alias, pending did not grow, new waivers acknowledged | — |
| `golden_manifest` | visual | `<goldens>/<machine>/manifest.json`: every PNG has its sha and a reason | zero unreviewed / reasonless / dangling | — |
| `thresholds_unmoved` | ladder | thresholds and mask area vs merge-base | nothing loosened (`threshold_raised`) | — |
| `pair_structure` | structure | subject source inventory ⟷ reference inventory | zero MISSING/CHANGED outside pending/waivers, zero ledger problems | source; SUBSTITUTED vs a runtime reference |
| `pair_tokens` | tokens | per theme per path, colors canonical, dimensions ± `geometry_tolerance_px`; MISSING only for `token_required_families` | zero MISSING/CHANGED | source |
| `theme_default_declared` | tokens | `index.html` first-frame script / `tokens.css color-scheme` + prefers block vs reference default | same fallback | source |
| `off_token_literals` | tokens | color / radius / font-size literals in component CSS bypassing `var(--…)` | count ≤ `[ui] max_off_token_literals` when set; advisory otherwise | source |
| `contrast_pairs` | tokens | `config.tokens.contrast_pairs` (default text-primary/secondary vs bg/surface, on-accent vs accent) per theme | ratio ≥ 4.5 (3.0 large) unless waived `wcag.contrast.text::<fg>/<bg>[::theme]` | source |
| `a11y_static` | structure | `wcag.name.interactive`, `wcag.lang`, `wcag.heading.order` on the source inventory | zero serious/critical hits | source |

## 档 2 运行时清单 — minutes (core)

| id | what it measures | pass line |
|---|---|---|
| `structure_runtime` | seed a temp HOME → launch on a free port → wait `ready` → demo marker → `probes/driver.cjs` (default theme × viewport × language) → bundle (nodes, landmarks + side, computed tokens, geometry bboxes, tab walk, overflow, axe, screenshots) | marker seen, driver rc 0, ≥ 1 item; runtime names outside the source string set are rewritten to `{dynamic}` |
| `app_launch` | the launch record (url, seeded, marker seen) | marker seen |
| `seed_guard` | every runtime artifact carries `seed.seeded_by_skill`; without runtime artifacts there is nothing to guard | — |
| `pair_runtime` | runtime inventory ⟷ reference (+ `unreachable` from the tab walk) | as `pair_structure` |
| `topology_runtime` | landmark side / parent / order from bboxes | zero CHANGED topology; at tier 1 the source stand-in is SUBSTITUTED |
| `tokens_runtime` | computed value of every declared custom property vs the declared table | zero color drift |
| `geometry_runtime` | `config.geometry` paths: reference token value vs rendered bbox measure | within `geometry_tolerance_px`; source stand-in (token consumed in CSS) is SUBSTITUTED |
| `theme_default_observed` | first frame under `prefers-color-scheme: light` and `dark` with no stored preference | equals the reference default |
| `a11y_rules` | runtime WCAG subset (name, contrast from computed colors, target size, keyboard) + axe-core when resolvable | zero serious/critical; without axe the pass is SUBSTITUTED |
| `screens_capture` | screenshots exist (not yet diffed) | ≥ 1 |

## 档 3 对照 + 矩阵 — tens of minutes (core)

| id | what it measures | pass line |
|---|---|---|
| `visual_diff` | every shot vs this machine's golden (`<goldens>/<platform>-<engine>-dpr<n>/`), odiff when present else the stdlib diff; masks with `masked_ratio` | `changed_pct ≤ max_changed_pct`, manifest ok; another machine's goldens = UNAVAILABLE |
| `matrix_themes_viewports` | the driver ran themes × viewports × languages | ≥ 2 themes captured (single theme = SUBSTITUTED) |
| `keyboard_reach` | every visible interactive item appears in a Tab walk | zero `wcag.keyboard` hits |
| `focus_order` | Tab walks visit each element once before finishing | no revisit |
| `reflow` | `scrollWidth ≤ clientWidth` on every run (narrow viewport included) | no horizontal overflow |
| `i18n_parity` | bilingual literals have both halves | zero half-translated names |

## 档 4 稳定性 — up to hours (core) — **UNAVAILABLE in 0.1.0**

`inventory_stability` (×`reruns`) · `visual_stability` (×3, self-different shots are `flaky`) · `states_matrix` (hover/focus/active/disabled) · `cross_engine` (webkit/firefox) · `reference_runtime` (`git:` ref launched in a temp worktree). The rows exist so the menu, the ASK and the report tell the truth about what tier 4 would measure; the runner marks them UNAVAILABLE with the reason "not wired in test-ui 0.1.0". Wiring them is the 0.2.0 follow-up (same pattern as `fix/skill-test-code-cross-project`).

## 档 5 通宵 / 通几天 — no time limit (core) — **UNAVAILABLE in 0.1.0** except the adversary

`matrix_all_routes` · `all_references` · `clean_machine_ui` (temp clone → `npm ci` → build → launch → inventory == committed reference) · `golden_review_sheet`. The fresh-context verification protocol (SKILL.md last section) is an agent step, not a script, and is available now.
