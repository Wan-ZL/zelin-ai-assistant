# Pairing — how a reference item finds its subject item (parity.py)

Order, first hit wins; every decision is recorded in `matched_by` (`alias | pin | tuple | none`):

1. **`ui/parity/aliases.txt`** — `<reference id>  <subject id>  <reason>`. A human's statement that the item was renamed or re-homed; the name difference is not a change. A line whose subject id does not exist is `dangling_alias` (FAIL).
2. **`data-parity-id` pin** (web) / `.accessibilityIdentifier("parity:…")` (native, table-only) — the element claims the reference id. A pin whose element has a different role, or a name with similarity < 0.5 to the reference slug, is CHANGED `spoofed_pin`: pins are a rename tool, not a bypass.
3. **Tuple** `(screen family, role, slug(name), ordinal)` — the default. `screen family` = first segment (`board.card → board`); `slug` = the parity contract's `slugify(en or raw)`; the ordinal is the `#n` suffix for repeated ids.
4. **None** → MISSING. Same screen + role candidates with `difflib` similarity ≥ `similarity_floor` (0.8) are listed as **suggestions** — never auto-paired (`Settings → Setting` suggests; `Trash → Bin` does not; that is what aliases are for).

## After the match

| Field | CHANGED when |
|---|---|
| `role` | key roles differ (only reachable through alias/pin) |
| `name` | normalised names differ (`{n}` for digit runs, `{}` for interpolations, case-folded); skipped for alias matches and for `{dynamic}` names |
| `states` | the reference is focusable and the subject is not (both disabled = no change) |
| `count` | number of same-id items differs; the surplus `#n` items are MISSING |
| `gated` | reference ungated, subject only present with all flags on |
| `unreachable` | a runtime focus walk exists and the interactive item is not in it |
| `topology:side / parent / order` | only for landmark / navigation / region / list / heading / tablist roles; `side` and `order` compare only when both sides have a value (source references have no bbox); `parent` compares the innermost landmark role |

Hidden subject (`display:none`, `hidden`, `aria-hidden`, 0×0, off-screen) → MISSING with `detail.hidden_by`, never PRESENT. Reference items owned by `shell / os / retired`, or runtime-named (`{dynamic}`), are `N-A`. Subject items nobody matched are `extras` (information; a hidden or dynamic extra is not listed).

## Ledger application (shrink-only)

| Ledger | Effect | Problem when |
|---|---|---|
| `pending.txt` `<id>` | MISSING → `ledger: pending` (not red) | PRESENT/CHANGED while listed → `stale_pending`; a line absent from the merge-base copy → `pending_grew` |
| `waivers.txt` `<id>` / `<rule>::<id>[::<theme>]` / `*`  `<reason>` | MISSING/CHANGED → WAIVED; rule hit → WAIVED | empty reason → `reasonless_waiver` (the row stays red); a new line not in `selection.waivers_acknowledged` → `waiver_grew` |
| `aliases.txt` | pairing step 1 | `dangling_alias` |

The skill reads these and never writes them. `run_ui.py --propose-pending` writes the MISSING ids that are not yet listed into `<report>/proposed/pending.txt` and prints `cat … >> ui/parity/pending.txt` for a human to run.
