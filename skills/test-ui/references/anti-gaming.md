# Anti-gaming rules — why each exists and what the runner enforces mechanically

Lineage: skills/test-code → robust-code → AmazingAng/old-coder (MIT). The ladder only creates trust if it cannot be gamed; a report you quietly weakened is the only real failure. The eight rules are test-code's; the third column is what test-ui's scripts enforce, and `tests/test_skill_test_ui_anti_gaming.py` proves each enforcement can go red.

| # | Rule | Why | Runner enforcement |
|---|---|---|---|
| 1 | Never weaken a test to make it pass | Raising `max_changed_pct`, widening `pixel_tolerance`, growing a mask or lowering a contrast floor redefines correctness to match the bug | `thresholds_unmoved` diffs every threshold key with a direction and the total mask area against the merge-base copy of `qa/gates.toml` / `ui/parity/config.json`; any loosening is FAIL `threshold_raised` |
| 2 | Never edit a test and the implementation in the same step | Ledgers/goldens and components moving together redefine expectation and behaviour at once | ledger noise is fix-first rank 6 in the same report as the component findings; `ledger_changed` fires `ledger_lint` + `golden_manifest` + `thresholds_unmoved` |
| 3 | Never mock the unit under test | Re-blessing a golden to get green proves nothing | the skill has no code path that writes a golden or a ledger; `--propose-*` writes into `<report>/proposed/`; a golden whose sha is not in `manifest.json` with a `reason` is FAIL `unreviewed_golden` |
| 4 | Never chase the number | "% PRESENT" is a detector, not a target | percentages are printed, never gated; subject-only items are `extras`, never parity |
| 5 | Never report a layer you did not run | An invented result destroys the whole scheme | five statuses only; `producer.mode` on every artifact; source vs a runtime reference, topology/geometry stand-ins and the axe-less a11y subset are SUBSTITUTED and a post hook cannot turn them into pass |
| 6 | A failing layer blocks done | "Almost green" is red | exit 1 on any fail; seed refusal and a missing demo marker abort capture before one screenshot; timeouts are fail |
| 7 | Never tune thresholds mid-run | Moving the goalposts is rule 1 in a costume | thresholds are read once per run and printed with their source; pending only shrinks (`pending_grew`), waivers need a reason and an acknowledgement, aliases must resolve |
| 8 | Never brief a fresh-context judge | A judge that read your reasoning inherits your blind spots | the 档 5 verifier gets task + commit + rerun command only (SKILL.md last section) |

## UI corollaries (each is a negative-control test)

- Identity is role + accessible name in the parity contract's id grammar; `data-testid` never forms an id.
- A `data-parity-id` pin that points at a different role, or at a name with similarity < 0.5, is CHANGED `spoofed_pin` (fixture: a `<span>` claiming `control:board:button:rework`).
- Hidden is MISSING, never PRESENT: `display:none`, `visibility:hidden`, the `hidden` attribute, `aria-hidden` on any ancestor, a 0×0 or off-screen box at runtime.
- Runtime names not present in the subject's source string set become `{dynamic}` — no user content enters an inventory or a report, even against a hand-given URL.
- The demo marker (`/api/health .demo == true` by default) must be seen on the seeded server before capture; a URL the skill did not seed gets no VISUAL.
- `opinion` can only write `report.opinion` and its own section; keys that try to touch `checks`, `items`, `fix_first`, `status` or `verdict` are dropped and listed.
- An unticked applicable core check needs `skip_reasons` or the verdict is INCOMPLETE (「AI 只能多做，不能少做」).
