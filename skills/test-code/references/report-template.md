# Report — what `run_ladder.py` writes and how to read it

Location: `<repo>/.test-code/reports/<UTC-timestamp>/` containing `report.md`, `report.json`, `selection.json`, `detect.json`, `logs/<check>.log` (every command with rc, stdout, stderr). Suggest adding `.test-code/reports/` to `.gitignore`; keep `.test-code/baselines/` committed.

## report.md skeleton

```markdown
# test-code report — <repo> @ <commit[:10]> (dirty: N files)
- Generated <UTC> · skill test-code v<version> · **tier <n>/5** (<chosen_by>; recommended <r>/5: <reason>)
- Base `<ref>` · <n> changed file(s) · triggers fired: <ids or none>
- Thresholds from `<source>`: complexity ≤ … · CRAP ≤ … · coverage <floor|no-drop> (<note>)
- **Verdict: GREEN | RED | INCOMPLETE** (exit 0 | 1 | 3)

## Layers                      | check | tier | status | time | summary |   (one row per selected check)
## Layers not run as specified  N-A / UNAVAILABLE / SUBSTITUTED — id + reason each
## Core checks skipped          core = must run at this tier; each skip carries the reason from selection.skip_reasons — a missing reason makes the verdict INCOMPLETE
## Structural blind spots       what this ladder cannot see here (e.g. no feedback channel, no fuzz harness) — informational
## Fix first (rank 1 failing tests → 2 changed-file CRAP → 3 constitution mutants → 4 other mutants → 5 new ledger violations → 6 other red layers)
## Baseline note (zero NEW rule)  pre-existing failing tests verbatim; per-ledger pre-existing counts
## Surviving mutants            `file:line` operator — detail
## Triggers fired               id (hits): evidence…
## Tool versions
## Rerun                        one command with --selection <this run's selection.json>
## Notes                        gitignore hint, and the honest notes you append (below)
```

Long lists are capped at 40 lines in markdown with "… and N more — full list in report.json"; JSON is always complete.

## Why "layers not run" is split three ways

One "skipped" list collapses three claims a reader must tell apart. **N-A**: the project has no such surface (no workflows → no SHA-pin check); it describes the project, not a gap. **UNAVAILABLE**: the surface exists but the tool or an input was missing and nothing ran (no ruff on PATH; no `--declared` set for diff minimality); the verdict becomes INCOMPLETE. **SUBSTITUTED**: something else ran in place of the real instrument (plain reruns instead of shuffled order; coverage measured with no floor to gate against) — it may never be written as pass, and the note says what the substitute cannot detect.

## Fix-first ranking

1. NEW failing tests (from `py_unit`, `js_unit`, `swift_unit`, `py_integration`, trigger subsets) — one line per test id.
2. CRAP offenders whose file is in the diff (`details.new` of `crap` filtered by changed files).
3. Surviving mutants in constitution modules (`qa/mutation_targets.toml`), then 4. other surviving mutants.
5. NEW / WORSE ledger violations, uncovered added lines, files outside the declared set, removed schema keys.
6. Any other red layer with its summary (compile error, timeout, could-not-start, checker crash).

## report.json (schemaVersion 1, add-only)

| field | meaning |
|---|---|
| `skill`, `generated_at`, `repo`, `source_state{commit,dirty,dirty_files}`, `base` | provenance; `base` is the ref, `detect.json → diff.base_commit` is the merge-base actually diffed |
| `tier`, `ask{recommended,reason,chosen,chosen_by}` | the ASK record; `chosen_by` ∈ `user` / `recommended, not confirmed` |
| `thresholds{source,complexity_max,crap_max,crap_tolerance,max_function_lines,max_file_lines,coverage,note}` | where the numbers came from; the skill never writes them |
| `stacks`, `triggers[]{id,evidence,hits}`, `changed_files` | detection summary |
| `checks[]{id,tier,trigger,label,status,tool,command,summary,details,reason,rc,duration_s,timed_out,log,steps_run}` | one row per selected check; `details` carries `new/worse/stale`, `failing/new/pre_existing`, `uncovered`, `outside`, `survivors`, `total`… |
| `not_run{na,unavailable,substituted}` | each a list of `{id, reason}` |
| `core_skipped[]{id,reason}` | applicable core checks left out of the selection; `reason` null ⇒ INCOMPLETE |
| `blind_spots[]` | strings collected from checks' `details.blind_spots` (e.g. `feedback_channel`) |
| `baseline_note{pre_existing_failing_tests,ledger_pre_existing,rule}` | the zero-NEW record |
| `fix_first[]{rank,kind,item,check}` | sorted by rank |
| `surviving_mutants[]{file,line,op,location,detail}` | `location` = `file:line` — the daily loop's input |
| `tool_versions{}` | first version line of every tool that ran + the skill version |
| `rerun`, `verdict`, `exit_code`, `out_dir`, `init_baselines`, `notes[]` | reproduce, judge, follow up |

## Honest notes — what the agent appends after the run

Below `## Notes` in report.md: equivalent-mutant classifications with reasons; results of table-only ecosystems you ran by hand (command + pasted numbers); waivers and why; the fresh-context verification record (`not performed | passed | failed | blocked` against commit X, rounds, attacks tried, findings and their grade); anything that reduces confidence (dirty tree, tool versions differing from CI, a check that ran on a subset). A run you passed first time and a run you fixed your way through are equally fine; the only failure is a run you quietly weakened.
