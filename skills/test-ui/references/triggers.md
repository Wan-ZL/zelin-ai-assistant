# Triggers — the second axis (mandatory add-ons regardless of tier)

`detect_ui.py` scans the changed file names and the **added lines of UI files** (`.tsx .jsx .vue .svelte .html .htm .css .swift .ts .js`, tests excluded) in the diff versus the merge-base, plus untracked files. Docs (`.md/.rst/.txt`), Python and JSON never fire code triggers — the first test-code dogfood lit six triggers from its own documentation, and the lesson is kept. Every fired trigger appends its add-on checks to the default selection whatever tier was chosen; an add-on whose instrument is missing is UNAVAILABLE (verdict INCOMPLETE), never silently dropped. Waive only with a written reason in `selection.triggers_waived`; the report records it.

| Trigger | Fires on | Add-on checks | What red means |
|---|---|---|---|
| `screen_changed` | `web/src/components/**`, `web/src/pages/**`, any `*.tsx/.jsx/.vue/.svelte/.html`, `shell/Sources/*.swift` | `structure_source` `pair_structure` `topology_runtime` | MISSING/CHANGED on a changed screen not in a ledger; a landmark moved |
| `tokens_changed` | `*tokens.css`, `*typeScale.ts`, `*Tokens*.swift`, `ui/tokens/**`, `tailwind.config.*` | `tokens_source` `pair_tokens` `theme_default_declared` `geometry_runtime` `visual_diff` | any CHANGED token / geometry not waived; a shot over threshold |
| `theme_changed` | added lines matching `data-theme`, `prefers-color-scheme`, `color-scheme`, `zai.theme` | `theme_default_declared` `theme_default_observed` | declared ≠ reference, or first frame ≠ declared |
| `layout_changed` | `@media`, `grid-template`, `flex-wrap`, `--native-layout-`, `width|height: <number>` | `topology_runtime` `geometry_runtime` `reflow` | side/order/geometry CHANGED; horizontal overflow |
| `a11y_attr_changed` | `aria-`, `role=`, `tabIndex`/`tabindex`, `:focus`, `outline`, `inert`, `accessibilityLabel` | `a11y_rules` `focus_order` `keyboard_reach` | a new rule hit |
| `names_changed` | `*i18n*`, `server/lanes.py`; added `L("` / `text("` literals | `i18n_parity` | a half-translated name |
| `ledger_changed` | `ui/parity/*.txt`, `ui/parity/config.json`, `ui/parity/goldens/**`, `qa/gates.toml` | `ledger_lint` `golden_manifest` `thresholds_unmoved` | reasonless line; pending grew; threshold loosened; unreviewed golden |
| `demo_changed` | `scripts/demo_seed.py` | `seed_probe` `screens_capture` | seed `--check` fails |
| always | — | `seed_guard` `ledger_lint` | a runtime artifact without the skill's own seed |

## Tier recommendation (`recommendation{tier,reason,screens}`)

No diff → 档 2 on the whole tree · docs/ledger-only → 档 1 · `tokens_changed` and > 3 mapped screens touched → 档 4 · `tokens_changed` or shared shell/chrome components → 档 3 · `screen_changed` → 档 2 on the touched screens · anything else → 档 1. `screens` = the `config.screens[].source` globs matched by the diff; `run_ui.py --screens` narrows fix-first rank 1 to them.
