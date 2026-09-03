# Report — what `run_ui.py` writes and how to read it

Location: `<repo>/.test-ui/reports/<UTC>/` with `report.md`, `report.json`, `selection.json`, `detect.json`, `inventory/{subject-source,subject-runtime,reference}.json`, `tokens/{subject,reference}.json`, `runtime/` (driver config + output), `shots/`, `proposed/` (only with `--propose-*`), `logs/<check>.log`. Add `.test-ui/reports/` and `.test-ui/cache/` to `.gitignore`; ledgers stay in `ui/parity/` (the project's, committed).

## report.md skeleton

```markdown
# test-ui report — <repo> @ <commit[:10]> (dirty)
- Generated <UTC> · skill test-ui v<version> · **tier <n>/5** (<chosen_by>; recommended <r>/5: <reason>)
- **Reference** `<locator>` (<kind>) resolved <sha|path> · modes {"structure": …, "tokens": …, "visual": …}
- Base `<ref>` · <n> changed file(s) · screens <list|all> · triggers fired: <ids|none> · demo marker seen: <bool|None>
- Thresholds from `<source>`: visual ≤ x% · contrast ≥ 4.5 · target ≥ 24px (<note>)
- **Verdict: GREEN | RED | INCOMPLETE** (exit 0 | 1 | 3)

## Sensors                     | sensor | subject mode | reference mode | ran |
## Layers                      | check | sensor | tier | status | time | summary |
## Items (counts · pending · extras)   one line per non-PRESENT row: STATUS `id` fields [ledger]  (capped at 40)
## Rules (hits)                rule `id` measured vs threshold (severity)
## Visual                      `shot id` status changed% (threshold, masked%)
## Layers not run as specified  N-A / UNAVAILABLE / SUBSTITUTED — id + reason each
## Core checks skipped          each skip carries selection.skip_reasons; a missing reason makes the verdict INCOMPLETE
## Fix first                    r1 … r7 (see below)
## Ledger note                  dir · pending n · waivers n · aliases n · "ledgers live in the project and only shrink; the skill writes nothing"
## Structural blind spots       the fixed list (never filled, never red)
## Triggers fired               id (hits): evidence…
## Tool versions
## Rerun                        one command with --selection <this run's selection.json>
## Notes                        gitignore hint, runtime hint, config source, and the honest notes you append
## Opinion (not a measurement)  only when `opinion` ran; first line "Nothing below changes a status or a rank."
```

## Why "layers not run" is split three ways

**N-A** describes the project (no goldens dir, no launch recipe declared, design-system mode has no reference inventory). **UNAVAILABLE** describes the machine (no `playwright` module, no `web/dist`, another machine's goldens) — nothing ran and the verdict is INCOMPLETE. **SUBSTITUTED** describes a weaker instrument that did run (source topology instead of bboxes, "token consumed in CSS" instead of a rendered width, the stdlib WCAG subset without axe, a single theme instead of the matrix) — it may never be written as pass and the note says what it cannot see.

## Fix-first ranking

1. MISSING interactive items and CHANGED **topology** on the changed screens (`--screens`; all screens when none given).
2. `theme:default`, geometry, tokens (`theme_default_*`, `geometry_runtime`, `pair_tokens`, `tokens_runtime`).
3. WCAG serious/critical rule hits (`a11y_static`, `a11y_rules`, `contrast_pairs`, `keyboard_reach`).
4. Visual diffs over threshold (`visual_diff`).
5. MISSING/CHANGED elsewhere (non-interactive, other screens).
6. Ledger noise: `pending_grew`, `reasonless_waiver`, `waiver_grew`, `dangling_alias`, `stale_pending`, unreviewed / reasonless goldens, `threshold_raised`, mask over cap.
7. Any other red layer with its summary (seed refusal, launch failure, driver crash, timeout, checker crash).

## report.json (schemaVersion 1, add-only)

| field | meaning |
|---|---|
| `skill`, `generated_at`, `repo`, `source_state{commit,dirty}`, `base` | provenance |
| `tier`, `ask{recommended,reason,chosen,chosen_by}`, `against`, `reference`, `subject`, `demo_marker_seen` | the ASK record and both sides with their instrument modes |
| `thresholds{source,…}` | where the numbers came from; the skill never writes them |
| `sensors[]{sensor,subject_mode,reference_mode,ran}` | the Sensors table |
| `triggers[]`, `changed_files`, `screens` | detection summary |
| `checks[]{id,sensor,tier,trigger,label,status,tool,command,summary,details,reason,rc,duration_s,timed_out,log}` | one row per selected check (+ `parity_disagreement` when the project gate also ran) |
| `items{rows[],counts,total,pending,extras}` | the non-PRESENT rows; `rows[].location` is the project id — the daily loop's input |
| `rules[]`, `visual[]` | rule hits with `check`; per-shot diff rows |
| `not_run{na,unavailable,substituted}`, `core_skipped[]`, `blind_spots[]`, `fix_first[]{rank,kind,item,check}`, `ledger_note`, `tool_versions`, `rerun`, `verdict`, `exit_code`, `out_dir`, `notes[]`, `proposals{}`, `opinion` | as in report.md |

## Honest notes — what the agent appends after the run

Below `## Notes`: table-only checks you ran by hand (command + pasted numbers); waivers you acknowledged and why; the fresh-context verification record (`not performed | passed | failed | blocked` against commit X); anything that reduces confidence (dirty tree, tool versions differing from CI, a single theme captured, a reference resolved to a stale sha). A run you passed first time and a run you fixed your way through are equally fine; the only failure is a run you quietly weakened.
