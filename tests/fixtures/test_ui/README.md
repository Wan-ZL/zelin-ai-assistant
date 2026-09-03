# tests/fixtures/test_ui — the fixture pair (ref / subject)

Two static HTML boards with the same `tokens.css` shape. `ref/` is the reference; `subject/` plants exactly the defects the skill must catch (each is a negative control in `tests/test_skill_test_ui_*.py` and in `tests/integration/test_skill_test_ui_fixture_pair.py`):

| planted in `subject/` | sensor · check | expected |
|---|---|---|
| left `<nav>` moved into `<header>`, `data-side="top"` | STRUCTURE · pair_structure / topology_runtime (source substitute) | `landmark:board:navigation:rail` CHANGED `topology:side` + `topology:parent` |
| `color-scheme: dark` in `:root` | TOKENS · theme_default_declared | `theme:default` CHANGED light → dark |
| `.lane { width: 320px }` while `--native-layout-lane-width: 400px` stays declared | TOKENS · geometry_runtime (source substitute) | `layout.lane.width` MISSING (token not consumed) — runtime would say CHANGED 400 → 320 |
| Settings `<h2>Materials box</h2>` and the whole Overrides section removed | STRUCTURE · pair_structure | headings / switch MISSING (Overrides lines are in pending.txt → `pending`) |
| `批准` button removed | STRUCTURE · pair_structure | `control:board:button:批准` MISSING (rank 1) |
| rail link renamed Settings → Setting | STRUCTURE · pair_structure | `control:board:link:settings` MISSING with suggestion `control:board:link:setting` (similarity ≥ 0.8; a synonym rename would need aliases.txt) |
| `<button style="display:none">Steer</button>` | STRUCTURE · pair_structure | MISSING `hidden_by display:none` (waiver without reason → invalid) |
| `<span data-parity-id="control:board:button:rework">Rework</span>` | STRUCTURE · pair_structure | CHANGED `spoofed_pin` (role static ≠ button) |
| close button without `aria-label` | STRUCTURE · a11y_static | `wcag.name.interactive` hit |
| `--text-tertiary: #8a8f99` on `--bg #fafbfc` | TOKENS · contrast_pairs (config pairs) | ratio 3.3 < 4.5 |
| `border-radius: 6px`, `color: #1a1c22` literals in board.css | TOKENS · off_token_literals | 2 literals listed |
| `pending.txt` grew vs base; `waivers.txt` line without reason | ledger_lint | `pending_grew`, `reasonless_waiver` |

No binary fixtures: PNGs for the visual tests are generated with `testui_common.encode_png` at test time.
