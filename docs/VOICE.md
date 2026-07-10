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
| 2 | `config/voice-profile.default.md` | **The repo default** — a sanitized snapshot of the author's profile (see below). |
| — | neither exists | No injection; agents draft with no voice constraints. |

Both paths are derived from `AIASSISTANT_HOME`, so headless dispatches under
launchd/cron (whose cwd is the *target* repo, not this one) resolve the same
files.

## The default profile

[`config/voice-profile.default.md`](../config/voice-profile.default.md) is the
author's own profile with the privacy stripped out:

- **The rule layer is real** — the global iron rules (short, plain verbs, no
  sign-offs, no em-dashes...), each context bucket's pattern description, the
  negative checklist, and the manager-negotiation paradigm are kept with their
  meaning intact.
- **Every example sentence is fictional** — rewritten into invented scenarios of
  the same shape, rhythm, and length. All names (Sam / Alex / Jordan), systems,
  role strings, and numbers are made up.

That means even out of the box, drafts start from the author's tendencies
(short, direct, sparse thanks-messages, evidence-chain escalations). It is a
reasonable default, but it is still *someone else's* voice. Generate your own.

## Generate your own profile

The goal: 100–200 messages **you actually sent**, distilled into the same
template structure as the default file. With the Slack MCP server connected
(see [SLACK_SETUP.md](SLACK_SETUP.md)), a Claude Code session can do the whole
loop:

1. **Pull your own messages.** Use the Slack search tools with a `from:me`
   query (or `from:@your-handle`), across DMs, group DMs, and channels. Aim for
   variety: requests/asks, manager DMs, channel announcements, technical
   escalations, and casual chat — the buckets only work if each has real
   samples behind it.
2. **Induce the profile.** Ask Claude to read
   `config/voice-profile.default.md` **as a template** and produce your version
   with the same skeleton:
   - global iron rules (message length, sentence shape, punctuation,
     greetings/sign-offs, emoji habits, link/identifier style);
   - one bucket per context you actually write in, each with a one-line pattern
     description plus 4–7 verbatim examples from your corpus;
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
- If you fork this repo publicly, double-check `git status` before pushing —
  the default profile is the only voice file that belongs in git.

### Maintenance

The profile is a living document. Two habits keep it honest:

- When you reject an agent's draft for tone ("too long", "I'd never say
  that"), fold the reason into your negative checklist.
- Re-run the induction every month or two and diff — your registers drift as
  projects and counterparts change.
