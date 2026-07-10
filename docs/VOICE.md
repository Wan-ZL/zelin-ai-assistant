# Voice Profile — drafts that sound like you

Background agents routinely draft text that goes out under your name: a Slack
reply delivered as a chat-mode `FINAL DRAFT:`, an email body, the prose in a
report. Left to itself, an LLM drafts in "polished assistant" register — long,
hedged, boilerplate greetings and sign-offs — which reads nothing like a real
person. The voice profile fixes that: the executor injects a **VOICE PROFILE**
block into every dispatched prompt, telling the agent to read the profile file
and match it before delivering any text written in the owner's name.

## Resolution order (two-level fallback)

`act/executor.py` (`resolve_voice_profile()`) picks the profile file at dispatch
time:

| Priority | File | What it is |
|---|---|---|
| 1 | `state/voice-profile.md` | **Your private profile** — induced from your real messages. Work data: gitignored, never committed. |
| 2 | `config/voice-profile.default.md` | **The author's sanitized default** that ships with the repo (see below). |
| — | neither exists | No injection; agents draft with no voice constraints. |

Both paths are derived from `AIASSISTANT_HOME`, so headless dispatches under
launchd/cron (whose cwd is the *target* repo, not this one) resolve the same
files.

## Master switch

Voice injection is on by default and controllable in two equivalent places:

- **Settings → 语气档案 · Voice profile → 启用语气注入** — the app diff-writes
  the `voice_enabled` override to `state/settings_overrides.json` (highest
  priority, same mechanism as the other Settings toggles);
- `config.yaml`:

  ```yaml
  voice:
    enabled: false
  ```

When off, background tasks run unchanged — drafted text just stops imitating
the profile (the executor skips the VOICE PROFILE block entirely). The same
Settings group also shows which file is currently in effect and opens it.

## The shipped default is the author's (sanitized) voice

This project is named after its author, and it ships with his writing
tendencies as the starting point — that is part of the product's identity.
[`config/voice-profile.default.md`](../config/voice-profile.default.md) is a
**sanitized snapshot of the author's profile**:

- The **rule layer** (iron rules, context-bucket patterns, negative checklist,
  the negotiation arc) is his, verbatim in meaning: short and plain, no
  boilerplate, hard facts as bare `Label: value`, asks that end at the ask.
- Every **example sentence is a fictional illustration** of those rules —
  invented names, systems, and numbers. The file went through repeated
  adversarial audits; a test guards that it stays free of real-world
  identifiers (real people, companies, or greppable message fingerprints).

Out of the box your drafts therefore start from the author's tendencies. To
make them sound like *you*, generate a private profile — it fully replaces
the default the moment it exists.

## Generate your own profile

The goal: 100–200 messages **you actually sent**, distilled into the same
template structure as the default file. The Settings page's one-click
**Generate voice profile** button runs exactly this flow headlessly
(`python -m act.voice_gen`): read-only Slack MCP tools collect your sent
messages, the profile is induced against the default template's skeleton and
validated, any existing `state/voice-profile.md` is backed up to
`.bak-<timestamp>` first, and a failed run never overwrites the old profile.
Alternatively, with the Slack MCP server connected (see
[SLACK_SETUP.md](SLACK_SETUP.md)), a Claude Code session can do the whole
loop by hand:

1. **Pull your own messages.** Use the Slack search tools with a `from:me`
   query (or `from:@your-handle`), across DMs, group DMs, and channels. Aim for
   variety: requests/asks, manager DMs, channel announcements, technical
   escalations, and casual chat — the buckets only work if each has real
   samples behind it.
2. **Induce the profile.** Ask Claude to read
   `config/voice-profile.default.md` **as a structural template** and produce
   your version with the same skeleton:
   - global iron rules (message length, sentence shape, punctuation,
     greetings/sign-offs, emoji habits, link/identifier style) — keep the
     universal ones, correct any that don't match how you actually write;
   - one bucket per context you actually write in, each with a one-line
     pattern description plus 4–7 verbatim examples from your corpus;
   - a negative checklist: things that would instantly mark a draft as "not
     me".
3. **Save it to `state/voice-profile.md`.** From then on it overrides the
   default automatically — no config needed.

A prompt that works:

```
Read config/voice-profile.default.md as a structural template. Then search my
Slack messages (from:me, last 6 months, DMs + channels, aim for 150+) and
induce MY voice profile in the same structure: global rules, context buckets
with my real example messages, negative checklist. Write it to
state/voice-profile.md. Use my real messages verbatim as the examples.
```

### Keep it private

Your profile quotes your real messages — treat it like the work data it is:

- `state/` is gitignored; **never** move the file into a tracked directory or
  paste its contents into commits, issues, or PRs.
- The shipped default is the only voice file that belongs in git. Never
  commit a real profile — yours or anyone else's — no matter how "sanitized"
  it looks: rewritten examples still leak phrasing habits.
- If you fork this repo publicly, double-check `git status` before pushing.

### Maintenance

The profile is a living document. Two habits keep it honest:

- When you reject an agent's draft for tone ("too long", "I'd never say
  that"), fold the reason into your negative checklist.
- Re-run the induction every month or two and diff — your registers drift as
  projects and counterparts change.
