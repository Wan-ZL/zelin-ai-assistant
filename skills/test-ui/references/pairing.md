# Pairing — how a reference item finds its subject item (parity.py)

Order, first hit wins; every decision is recorded in `matched_by` (`alias | pin | tuple | tuple:union | project:parity_check | source-string | none`):

1. **`ui/parity/aliases.txt`** — `<reference id>  <subject id>  <reason>`. A human's statement that the item was renamed or re-homed; the name difference is not a change. A line whose subject id does not exist is `dangling_alias` (FAIL).
2. **`data-parity-id` pin** (web) / `.accessibilityIdentifier("parity:…")` (native, table-only) — the element claims the reference id. A pin whose element has a different role, or a name with similarity < 0.5 to the reference slug, is CHANGED `spoofed_pin`: pins are a rename tool, not a bypass.
3. **Tuple** `(screen family, role, slug(name), ordinal)` — the default. `screen family` = first segment (`board.card → board`); the global chrome family is `window` on both sides (native `window`; web `components/shell/*`, `App.tsx`, `main.tsx`, `chrome`, `layout` — `testui_common.CHROME_SCREENS`), so a sidebar link and a header link can meet; `slug` = the parity contract's `slugify(en or raw)` (for `screen:*` headings the slug comes from the title text — `screen:settings.recording` → `recording`); the ordinal is the `#n` suffix for repeated ids.
4. **Tuple in the union** — only when the reference's screen family does not exist on the subject at all (native-only pages: `about`, `ask`, `deps`, `header…`): the same `(role, slug, ordinal)` anywhere on the subject (§66.2 "web 尚无的页面在全部面的并集里找"); `matched_by: tuple:union`. A family that exists never borrows from another page.
5. **None** → MISSING. Same screen + role candidates with `difflib` similarity ≥ `similarity_floor` (0.8) are listed as **suggestions** — never auto-paired (`Settings → Setting` suggests; `Trash → Bin` does not; that is what aliases are for).

**Second instrument for `shortcut:*` / `setting:*`** — a keyboard glyph and a settings key are not accessibility-tree nodes. When the project gate ran (`project_parity`), its verdict is adopted for those ids (`matched_by: project:parity_check`; PENDING → MISSING [pending], STALE → PRESENT [stale]). Without a gate, the `source-string` probe mirrors §66.2: the glyph (`⌘F`) or the quoted key (`"cardSortOrder"`) present in the subject's code files (tests / fixtures / `ui/parity` excluded) → PRESENT with `detail.evidence`.

## After the match

| Field | CHANGED when |
|---|---|
| `role` | key roles differ (only reachable through alias/pin) |
| `name` | normalised names differ (`{n}` for digit runs, `{}` for interpolations, leading/trailing decorative symbols — emoji, `…`, `:` — stripped, case-folded); skipped for alias matches, for `{dynamic}` names and for landmarks (an unnamed native sidebar vs a `<nav aria-label>` is better a11y, not a rename) |
| `states` | the reference is focusable and the subject is not (both disabled = no change) |
| `count` | the subject has **fewer** same-id items than the reference (the missing `#n` items are MISSING too); more instances than the reference are `extras`, never a change (anti-gaming #4) |
| `gated` | reference ungated, subject only present with all flags on |
| `unreachable` | a runtime focus walk exists and the interactive item is not in it |
| `topology:side / parent / order` | for landmark / navigation / region / list / heading / tablist roles; `side` and `order` compare only when both sides have a value (source references have no bbox); `parent` compares the innermost landmark role. Plain controls do not compare topology — with one exception: an item the reference places inside a **navigation** landmark reports `topology:parent` when it lands elsewhere (a rail link moved into the header = the navigation was restructured, owner (a)) |

Hidden subject (`display:none`, `hidden`, `aria-hidden`, 0×0, off-canvas — outside the document's scrollable box; **below the fold is not hidden**) → MISSING with `detail.hidden_by`, never PRESENT. Reference items owned by `shell / os / retired`, runtime-named (`{dynamic}`), or **not gated by the project inventory** (`project_gated: false` — copy / help text the parity contract lists but does not judge, §66.1) are `N-A`. Subject items nobody matched are `extras` (information; a hidden or dynamic extra is not listed).

## Ledger application (shrink-only)

| Ledger | Effect | Problem when |
|---|---|---|
| `pending.txt` `<id>` | MISSING or CHANGED → `ledger: pending` (not red — a moved item has not landed as specified) | PRESENT while listed → `stale_pending`; a line absent from the merge-base copy → `pending_grew` |
| `waivers.txt` `<id>` / `<rule>::<id>[::<theme>]` / `*`  `<reason>` | MISSING/CHANGED → WAIVED; rule hit → WAIVED | empty reason → `reasonless_waiver` (the row stays red); a new line not in `selection.waivers_acknowledged` → `waiver_grew` |
| `aliases.txt` | pairing step 1 | `dangling_alias` |

The skill reads these and never writes them. `run_ui.py --propose-pending` writes the MISSING ids that are not yet listed into `<report>/proposed/pending.txt` and prints `cat … >> ui/parity/pending.txt` for a human to run.
