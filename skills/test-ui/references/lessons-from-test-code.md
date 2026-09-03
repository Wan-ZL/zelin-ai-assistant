# Lessons from building test-code — the methodology test-ui inherits

Distilled from the test-code build and review (skills/test-code v0.2.0 → v0.2.1) and cross-checked against what landed. Methodology only: truth for any number = the files named; a sibling skill copies the mechanism, never the number. Binding upstream docs: `docs/design/vnext2-plan.md` (D13 skill store, D14 test skill, R2.7.x, R2.8.x), `skills/README.md`, `skills/test-code/SKILL.md` + `references/*`.

## 1. Doctrine — what must hold for ANY sibling skill

1. **It only MEASURES.** No specs, no code, no tests, no UI edits, no design opinions inside measurements. The owner's word for test-code is the 尺子 (ruler); for test-ui he said the same: "it only MEASURES — never designs, never edits UI". Opinions, if any, live in a clearly separated section and never change a status.
2. **Deterministic tools decide; SKILL.md only decides what to run.** 「能写成代码的规则就别写成文字」— anything mechanically judgeable goes into `scripts/`; the markdown is for the ASK step, the tier menu and the ethics. Agents discount prose; scripts do not.
3. **Ask the human ONCE per run, never per check.** One single-select (tier 1–5) + one multi-select (checks) pre-filled with the tier's core checks + fired triggers; extended checks visible but unticked with reason + estimate. The recommendation comes first with a one-line reason. Headless callers pass `--chosen-by headless` and the report records `recommended, not confirmed` — never pretend the human approved.
4. **Five tiers numbered 1–5, tier 5 unlimited.** The first build shipped T0–T4; the owner asked "T0 是第一档对吧?" and everything was renumbered in the same PR. Per-check timeouts per tier; choosing 5 lifts every timeout; a timeout is a FAIL no post-processing may overturn.
5. **Two axes, not one.** Tiers manage budget; **triggers** manage mandatory add-ons detected from the diff regardless of tier. A fired trigger with no instrument is UNAVAILABLE (or `missing` = FAIL where evidence was expected), never a silent skip.
6. **Three circles.** Core = must run when the surface exists (an unticked applicable core check needs `skip_reasons` or the verdict is INCOMPLETE). Extended = menu-visible, never pre-selected. Deferred = table-only rows. Owner's rule: **「AI 只能多做，不能少做」**.
7. **Thresholds are the project's and read-only.** Project config first (`qa/gates.toml`), then ecosystem config, then skill defaults with a note naming the strict value. Ledgers shrink only; the skill never writes them or the thresholds.
8. **Home-grown checks fail CLOSED.** Unreadable input, checker crash, non-zero rc, could-not-start (rc −2), timeout (rc −1) → FAIL. 「永远绿的检查器比没有检查器更危险。」
9. **Every shipped script has a negative control.** A known-bad fixture must turn it red before its green is believed.
10. **Stack constraints are non-negotiable for the store**: stdlib-only Python, 3.9 floor, zero network, every function CC ≤ 6 and CRAP ≤ 6 (measure with the repo's own ruler before CI does — `scripts/qa/qa_common.cyclomatic_complexity` counts `and`/`or`, ternaries and comprehension `if`s), SKILL.md ≤ 150 lines with ASK + anti-gaming in the first 60, self-contained (no `import act…`; vendor what you need and pin it byte-identical). External tools are invoked only when detected, never installed; absence is UNAVAILABLE with a one-line install hint.
11. **One subprocess seam.** Everything the runner spawns goes through one injectable `runner(argv, cwd, timeout, env) -> RunResult` with `start_new_session=True` and a process-group kill on timeout; unit tests inject a fake; real IO lives only in `tests/integration/` with a per-file time budget.
12. **Structural blind spots are disclosed, never filled.** Owner on user feedback: 「至于用户反馈,我觉得做不到。因为这是单次的 skill」→ a blind-spot line, never a red.
13. **Version discipline.** Frontmatter `version` + `upstream`/`upstream_version`; attribution in `NOTICE`; release note and progress row as fragments (`changelog.d/`, `docs/design/progress/`); never touch the product's version pins in a skills-only PR.

## 2. Self-loop — how test-code ate its own cooking (test-ui repeats every step)

**Step 0 — build with the gates already on.** Self-measure CC after every edit; the first build found 3 functions at CC 7 before the PR and the reviewer found 4 more after features were added. test-ui's first measurement found 76 functions over 6 (mostly `x or {}` defaults and ternaries) and refactored all of them before writing tests.

**Step 1 — a negative control next to every checker.** For test-ui: missing button, renamed link (suggestion only), `display:none` control, spoofed `data-parity-id`, unlabeled icon button, `#8a8f99` on white, rail moved into `header`, `color-scheme: dark`, lane 400 → 320 with the token still declared, grown pending, reasonless waiver, unreviewed golden, raised threshold — all in `tests/fixtures/test_ui/{ref,subject}/` and one behaviour per test file.

**Step 2 — dogfood tier 1 on the host repo, fixing false positives each pass.** test-code's first run produced 110 docs-drift hits and six triggers fired by its own documentation. test-ui's first runs found: per-file `#n` ordinals colliding across files, `{choice.label}` dropped as code so buttons looked unnamed, `<select>` in a TS header comment parsed as an element, `disabled={busy}` treated as literally disabled, a whole-tree reference walk pulling in non-UI HTML, `rgba(…)` tokens classified "other", `<label htmlFor>`, and `git worktree add` as a side effect of detection. Each became a fix + a test. Rule of thumb: > 20 findings in one check is a checker bug until proven otherwise; 1–5 is where the real findings hide (the real ones on the first honest run were the Materials-box items that main had and the branch did not).

**Step 3 — honest tier accounting.** Say per tier what ran, what was UNAVAILABLE and why (this machine has no `playwright` module in `web/` and no `web/dist`; tier 2+ is UNAVAILABLE here until `cd web && npm i -D @playwright/test && npx playwright install chromium && npm run build`).

**Step 4 — tier 4 on itself.** Run `scripts/qa/mutate.py` with an ad-hoc target map (skill script → its test files), pin logic survivors with tests whose docstrings name `file:line op`, add the scripts to `qa/mutation_targets.toml`, report raw and logic-only kill rates side by side.

**Step 5 — a repo it has never seen.** A public Vite + Tailwind starter (utilities defeat source token extraction → SUBSTITUTED note), a plain static site with variables under other prefixes, shadow DOM, `aria-labelledby` indirection — every crash or false red becomes a negative control.

**Step 6 — fresh-context verification (tier 5).** Task contract + repo at commit + rerun command only; blind phase first; two rounds max; record `not performed | passed | failed | blocked`.

**Step 7 — install path proven, not assumed.** Merge on green → autodeploy fast-forwards the live checkout → `ln -s <live>/skills/test-ui ~/.claude/skills/test-ui` → smoke `detect_ui.py` + `run_ui.py --dry-run` from the symlink; zero manual bytes under `~/.claude/skills/`.

## 3. Pitfalls from test-code that shaped test-ui's design

| Pitfall | test-ui consequence |
|---|---|
| Zero-based tier labels | tiers 1–5 from the first line |
| Documentation text fires code triggers | only UI-file added lines feed the line regexes; `.md/.py/.json` never fire |
| Runtime data paths counted as findings | the static-name filter rewrites runtime names outside the source string set to `{dynamic}`; dynamic reference items are `N-A` |
| Design tokens flagged as secrets | ids and names never print user values; report items are project ids |
| Merged tool invocations hiding per-file results | one driver run per screen × dim; per-check logs |
| Diff base = branch tip, not merge-base | thresholds, ledgers and triggers all diff against the merge-base copy |
| Empty diff reported UNAVAILABLE | no changed files → `thresholds_unmoved` N-A, recommendation tier 2 whole tree |
| Integration verdict INCOMPLETE for a hidden reason | every core check the fixture deliberately omits gets a written `skip_reasons` entry |
| Builder agent died at its last step | one writer per branch, commit early, `gh pr checks` polled, never `--watch` |
| "Skipped" hid three different truths | N-A / UNAVAILABLE / SUBSTITUTED, and SUBSTITUTED can never become pass |

## 4. Owner decisions that bind test-ui (paraphrased where not quoted)

- Skill ask-once + multi-select (D14): 「运行这个 skill 之后,它可以问我需要做哪些 test……让我多选之后,再进行大量的测试。」
- Generic, any harness, any project: 「任何一个 AI 或者 harness 使用这个 skill 时,对于任何项目,起码都能点拨到应该考虑什么东西。」
- Five tiers, fifth unlimited: 「第五层就是"通宵模式"或"通几天模式"……第五层是没有限制的。」
- Code, not prose; the code must be tested. Structure as a hard gate; clean-VM install at the top tier; feedback loop is not the skill's job.
- Hard metrics, mainstream only: 「就是这种硬指标……类似于谷歌、Facebook、Meta 这种大企业……用 deterministic code 来作为护城河。」
- Wide menu, narrow default, knowledge in references — 「AI 决定"多做什么",不能决定"少做什么"。」
- test-ui definition (2026-09-02): measure a UI against a REFERENCE through STRUCTURE / TOKENS / VISUAL; two modes with a pluggable reference; PRESENT / MISSING / CHANGED / WAIVED with shrink-only ledgers living in the PROJECT; opinions in a separated section; never screenshot real user data — demo seed required.
- Still undecided by the owner: the default tier for headless agent runs (standing recommendation: tier 2 on changed screens).
