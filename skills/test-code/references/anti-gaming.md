# Anti-gaming rules — why each exists and what the runner enforces mechanically

Lineage: robust-code → AmazingAng/old-coder (MIT). The ladder only creates trust if it cannot be gamed; a report you quietly weakened is the only real failure.

| # | Rule | Why | Runner enforcement |
|---|---|---|---|
| 1 | Never weaken a test to make it pass | A broadened assertion, an added skip or a raised tolerance redefines correctness to match the bug | none possible — behavioural; `test_smells` flags no-assert tests after the fact |
| 2 | Never edit a test and the implementation in the same step to reach green | Simultaneous edits let you redefine the expectation and the behaviour together | none — behavioural; `diff_minimality` shows the changed-file set so a reviewer can see both moved |
| 3 | Never mock the unit under test | A test that exercises only mocks proves the mocks | `test_smells` real-io rule pushes IO to boundaries; mutation exposes vacuous tests |
| 4 | Never chase the coverage number | A test that touches lines without asserting is gaming; mutation exists to catch it | CRAP uses coverage as a detector; `flaky_detect`/`mutation_*` catch vacuity |
| 5 | Never report a layer you did not run | An invented result destroys the whole scheme | statuses are only pass / fail / na / unavailable / substituted; `substituted` can never become `pass`, even if a post hook says so |
| 6 | A failing layer blocks done | "Almost green" is red | exit code 1 on any fail; a fired trigger with no test is `missing` = fail; timeouts are fail and post hooks may not overturn them |
| 7 | Never tune thresholds / rubric mid-run | Moving the goalposts is rule 1 in a costume | thresholds are read from the project config every run; the skill never writes them; baselines only shrink (NEW/WORSE fail) |
| 8 | Never brief a fresh-context judge | A judge that read your reasoning inherits your blind spots | fresh verifier gets task + commit + rerun command only (SKILL.md T4 section) |

Corollaries kept from the source: every home-grown check fails closed (unreadable input = failure, never pass); every shipped script has a negative control that proves it can go red; equivalent mutants are classified, not killed with meaningless tests; pre-existing failures are recorded verbatim and held at zero NEW, never "improved" silently.

Corollary added in v0.2 (owner rule 「AI 只能多做，不能少做」): the AI may ADD extended checks freely and must record why it declined any the detector lit up; it may not drop an applicable **core** check without a written `skip_reasons` entry — the runner lists such skips under *Core checks skipped* and turns the verdict INCOMPLETE when a reason is missing.
