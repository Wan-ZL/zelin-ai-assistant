# Catalog — the extended circle (knowledge, not discipline)

The core checks in SKILL.md are what a tier **must** run. This catalog is what a project **can** run. Extended checks are menu-visible and never pre-selected; the AI reading this file decides what to add for the project in front of it and records the choice (and, for anything it declined that the detector lit up, the reason) in the report's Notes. Thresholds come from the project when it has them (`qa/gates.toml [ui]` → `ui/parity/config.json .thresholds`); the numbers below are the mainstream defaults.

## Wired extended checks (run_ui builds the plan)

| id | lights up when | pass line | tool | precedent |
|---|---|---|---|---|
| `project_parity` | `scripts/ui/parity_check.py` present and `npx` on PATH | its exit code (reads the same `ui/parity/*.txt` ledgers); its `report.json items{id: status}` is compared with the skill's pairing on shared ids → `parity_disagreement` FAIL on any conflict | project script, verbatim | "project gate beats skill fallback" (test-code §58 doctrine) |
| `project_visual` | `web/e2e/visual.spec.ts` + `@playwright/test` installed in `web/` | `npx --no-install playwright test e2e/visual.spec.ts` exit 0 (its goldens are read-only for the skill; no second golden set) | project spec, verbatim | Playwright `toHaveScreenshot` |
| `opinion` | always (advisory) | never fails; prints `selection.opinion.text` under `## Opinion (not a measurement)` with the banner "Nothing below changes a status or a rank."; any key other than `text` is dropped and listed | internal | owner: opinions separated from measurements |

## Table-only rows (no runner in 0.1.0 — run by hand, paste into Notes)

| id / metric | pass line | tool | why it is mainstream |
|---|---|---|---|
| `lighthouse_a11y` | accessibility score ≥ 90, zero serious axe violations | `lighthouse --only-categories=accessibility` | Google's shipped audit |
| `reduced_motion` | with `prefers-reduced-motion: reduce` no element animates (computed `animation-name: none`) | Playwright `emulateMedia` | WCAG 2.3.3 |
| `touch_target_size` | interactive bbox ≥ 44 × 44 on touch viewports | driver bbox + a 44 floor | Apple HIG / WCAG 2.5.5 |
| `dead_tokens` | every declared custom property is consumed by some `var(--…)` | grep over component CSS | design-system hygiene |
| `token_census_floor` | literal census never grows vs a ledger | `off_token_literals` output + a committed count | ratchet, same as coverage floors |
| `text_overflow_probe` | scene `long-strings` renders without clipping/overflow | driver `overflow` per element | i18n QA at every large org |
| `font_fallback` | computed `font-family` resolves to the declared stack on this machine | driver computed fonts | cross-machine rendering |
| `rtl_smoke` | `dir="rtl"` renders without overflow and mirrored landmarks | driver with `dir` set | i18n |
| `perf_budget` | LCP / CLS within budget on the demo seed | Lighthouse CI | web vitals |
| VoiceOver / NVDA announcement scripts | announcements match the inventory names | manual | the tree is measured, the announcement is not heard |
| color-blind simulation | status colors distinguishable under deuteranopia / protanopia | Chrome DevTools rendering emulation | WCAG 1.4.1 |
| Storybook a11y / Percy / Chromatic | project-specific | project | when the project already pays for them |
| macOS AX runtime (`osascript` / Accessibility Inspector), iOS `idb ui describe-all`, Flutter semantics dumps | native inventory at runtime | native tools | D3 freezes this repo's native app to source; the schema and role table already admit these rows |

## Role table (native vocabularies → WAI-ARIA)

`Button/Menu/alert-button → button` · `Toggle → switch` · `Picker(segmented) → radiogroup` · `Picker(menu) → combobox` · `TextField/SecureField/TextEditor → textbox` · `Slider → slider` · `Stepper → spinbutton` · `Link/NavigationLink → link` · headline `Text`/`SectionHeader → heading` · `List/ForEach → list/listitem` · `DisclosureGroup → button[aria-expanded]` · labelled `Image → img` · other `Text`/`label`/`copy`/`help → static`. HTML implicit roles follow the HTML-AAM (`a[href] → link`, `input[type=checkbox] → checkbox`, `h1–h6 → heading`, `nav → navigation`, `header → banner`, `main → main`, `ul/ol → list`, …); `section[aria-label] → region`.

## Lineage

Doctrine and the wired/table-only split adapt skills/test-code (this repo) → robust-code → AmazingAng/old-coder (MIT, NOTICE). WCAG success criteria are W3C's; the hard-metric inventory reflects public practice (Google Lighthouse, Deque axe-core, Playwright visual comparisons, W3C design-tokens community group format).
