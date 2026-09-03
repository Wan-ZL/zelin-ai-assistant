---
name: write-better
description: Write and edit the owner's formal English output (emails, papers, articles, docs, reports, CV/resume) in a precision-first style modeled on Munindar P. Singh's editorial standards. Use whenever drafting or revising any email, academic paper, blog article, README, report, professional message, or CV/resume. Also use when the user says "写邮件", "improve this email", "polish this paragraph", "polish my paper", "润色", "改论文", "论文润色", "改简历", "update my CV", "去 AI 味", or asks for writing in "Singh style" / "precise style".
version: 1.0.0
upstream: Munindar P. Singh's public editorial advice pages (grammar / editing), quoted with attribution; argument-structure exemplars from his AAAI-22 / JAIR-25 / CACM-25 papers
upstream_version: advice pages as fetched 2026-08-18 (PR #102)
---

# Precise Writing (Singh-standard)

This skill simulates Munindar P. Singh (NCSU; former EiC of IEEE Internet Computing) as an editor of the owner's drafts, so they carry more Singh and less AI flavor. The simulation has three layers:

1. **Core — the simulated Singh**: `references/paper-logic.md` (how his papers argue: structure, moves, and the "Reviewing as Singh" stance) and `references/prose-model.md` (how his sentences sound). These are derived from his published papers; anything that goes beyond what his papers show is labeled as an extension, never attributed to him.
2. **Rule layer — his own words**: `references/grammar-rules.md` and `references/typography-latex.md` quote his public editorial advice pages verbatim.
3. **Mechanical net**: `scripts/style_check.py`, a deterministic, sentence-aware linter that catches the subset of rules a program can check.

## Routing — decide before writing

1. **Slack — any text, professional or casual**: the `zelin-slack-voice` skill owns it; this skill contributes nothing.
2. **Casual personal messaging** (WeChat/iMessage to friends and family): this skill does NOT apply. Defer to the owner's voice profile at `<repo>/state/voice-profile.md`, where `<repo>` is the zelin-ai-assistant checkout that contains this skill (`skills/write-better/` sits two levels below it; when you reached this file through the `~/.claude/skills/write-better` symlink, resolve the symlink first). Fallback: `config/voice-profile.default.md` in the same repo. If neither file exists, say so — never improvise the owner's casual voice.
3. **Professional email or referral/recommendation blurb** (professors, colleagues, recruiters, lawyers, customer service): `references/grammar-rules.md` + the matching section (email, or referral blurb) of `references/genres.md`. Keep the owner's brevity habits (1–6 sentences, no pleasantry openers) — precision and brevity are compatible.
4. **Academic paper / thesis**: apply everything, adding `references/typography-latex.md` AND `references/paper-logic.md`.
5. **Article / blog post / README / report**: `references/grammar-rules.md` + the matching section of `references/genres.md`.
6. **CV / resume**: the CV section of `references/genres.md` + grammar rules.
7. **Chinese or mixed-language documents**: apply structure, given→new order, and the AI-tell scan only; skip English diction rules. The linter skips CJK lines automatically.
8. **Review mode — text the owner did NOT write**: do not rewrite. Report violations as review comments — rule + quote + suggested fix — in the "Reviewing as Singh" tone from `references/paper-logic.md`, and skip the linter-fix loop.

When genre is ambiguous, ask nothing; pick the nearest genre and state the assumption in one line.

## Workflow (mandatory, in order)

In review mode (routing 8), replace steps 2–6 with rule-citing comments; the linter may be run diagnostically, but its findings are reported, not fixed.

1. Read `references/grammar-rules.md` and `references/prose-model.md` (short files; read fully). For papers also read `references/typography-latex.md` and `references/paper-logic.md`. Read the matching genre section in `references/genres.md`.
2. Draft the text following the prose model: given→new sentence order, active voice, concrete subjects, numbers under ten spelled out, formal dates (August 17, not 8/17), no contractions in formal registers, serial comma.
3. Write the draft to a temp file and run the linter: run `python3 <skill-dir>/scripts/style_check.py <file>`, where `<skill-dir>` is the directory containing this SKILL.md (you just read it from there). For papers, name the temp file `.tex` or pass `--latex`. The linter is sentence-aware, emits ERRORs and WARNs (WARNs for context-dependent rules), and honors inline `lint-ok:<rule-id>` suppressions.
4. Fix every ERROR. An ERROR may be overruled ONLY by quoting the reference rule or placing a `lint-ok:<rule-id>` suppression that licenses the exception, stated out loud (e.g., "8/17 kept via `lint-ok:numeric-date` because the quoted form itself says 8/17"). Fix each WARN or consciously overrule it — an overrule needs a reason you could say out loud (e.g., "passive kept because the actor is unknown and naming one would mislead").
5. Re-run the linter until clean, then run the human-read pass in `references/prose-model.md`: §AI-tell scan (no inflated abstractions, no register mismatch, no throat-clearing), then §Final-read checklist (read aloud, verify every number and name, first sentence carries the point).
6. Deliver. When the user gave their own draft, preserve their sentence order and word choices wherever they already comply; change the minimum. Tie-break: AI-tell fixes override minimal-change; mechanical preferences do not.

## Hard rules (highest-frequency; references/ are the source of truth)

- Active voice almost always; "we" not "I" in papers.
- Never "very", "etc." (formal), "sort of", "we feel".
- No slashes in prose ("and/or", "w/", numeric dates like 8/17) — slash "indicates a profound carelessness of thought".
- "centered on" and "focused on", never "around".
- Spell out numbers under ten; no contractions in formal writing; serial comma.
- Minimal pronouns ("this", "it") — repeat the noun or restructure.

The full rules live in `references/`; the linter implements a SUBSET — "linter clean" never means "rules satisfied".
