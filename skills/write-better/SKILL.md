---
name: write-better
description: Write and edit the owner's formal English output (emails, papers, articles, docs, reports) in a precision-first style modeled on Munindar P. Singh's editorial standards. Use whenever drafting or revising any email, academic paper, blog article, README, report, or professional message. Also use when the user says "写邮件", "improve this email", "polish this paragraph", "去 AI 味", or asks for writing in "Singh style" / "precise style".
---

# Precise Writing (Singh-standard)

Write formal English the way a career journal editor would: active voice, concrete nouns, zero filler, zero vagueness, punctuation and numbers handled by rule rather than habit. The standard is modeled on Munindar P. Singh (NCSU; former EiC of IEEE Internet Computing), whose published peeves and editing rules are captured verbatim in `references/`.

## Routing — decide before writing

1. **Casual personal messaging** (WeChat/iMessage to friends and family, quick Slack pings): this skill does NOT apply. Defer to the owner's voice profile at `state/voice-profile.md (falling back to config/voice-profile.default.md)`. Precision rules would make those messages sound stiff.
2. **Professional email** (professors, colleagues, recruiters, lawyers, customer service): apply `references/grammar-rules.md` + the email section of `references/genres.md`. Keep the owner's brevity habits (1–5 sentences, no pleasantry openers) — precision and brevity are compatible.
3. **Academic paper / thesis / review**: apply everything, including `references/typography-latex.md`.
4. **Article / blog post / README / report**: apply `references/grammar-rules.md` + the matching section of `references/genres.md`.

When genre is ambiguous, ask nothing; pick the nearest genre and state the assumption in one line.

## Workflow (mandatory, in order)

1. Read `references/grammar-rules.md` and `references/prose-model.md` (short files; read fully). For papers also read `references/typography-latex.md`. Read the matching genre section in `references/genres.md`.
2. Draft the text following the prose model: given→new sentence order, active voice, concrete subjects, numbers under ten spelled out, formal dates (August 17, not 8/17), no contractions in formal registers, serial comma.
3. Write the draft to a temp file and run the linter:
   `python3 skills/write-better/scripts/style_check.py <file>`
   (or pipe via stdin: `... style_check.py -` ). It flags rule violations with line numbers.
4. Fix every ERROR. Fix each WARN or consciously overrule it — an overrule needs a reason you could say out loud (e.g., "8/17 kept because the quoted form itself says 8/17").
5. Re-run the linter until clean, then run the human-read pass in `references/prose-model.md` §Final-read checklist (AI-tell scan: no inflated abstractions, no "systematic confound" register mismatch, no throat-clearing).
6. Deliver. When the user gave their own draft, preserve their sentence order and word choices wherever they already comply; change the minimum.

## Hard rules (memorize; full list in references)

- Active voice almost always; "we" not "I" in papers.
- Never "very", "etc." (formal), "more like", "sort of", "we feel".
- No slashes in prose ("and/or", "w/", numeric dates like 8/17) — slash "indicates a profound carelessness of thought".
- "centered on" and "focused on", never "around".
- Don't start a sentence with a numeral, a citation, a lowercase identifier, or a bare "This ..." whose referent is not obvious.
- Spell out numbers under ten; numerals for 157; commas in 1,000+; years bare.
- No contractions in formal writing (it is, do not, I have).
- Serial comma: "A, B, and C".
- No footnotes: if it is worth saying, say it in the text.
- Introduce new terms in italics, not quotes; use quotes only when essential.
- Minimal pronouns ("this", "it") — repeat the noun or restructure.
- Capitalize "Figure 4", "Section 3"; never "the Figure 3".
- Lowercase discipline names ("computer science") — capitalization makes concepts appear more grand than they are.
