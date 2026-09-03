# Prose model — how the writing should read

Derived from Singh's published prose (AAAI 2022 solo paper arXiv:2203.11420, JAIR 2025 and CACM 2025 abstracts, his resume) plus his stated rules. This file is about sound and rhythm; argument structure lives in `paper-logic.md`.

## The target sound

- **Declarative and confident.** "This paper focuses on a dynamic aspect of responsible autonomy." No hedging unless the uncertainty is real, in which case state it precisely ("we did not evaluate X").
- **Given → new.** Each sentence starts from established ground and ends with the payload. Paragraphs chain: the "new" of one sentence becomes the "given" of the next.
- **Concrete subjects, strong verbs.** "The form arrived by email" beats "There is a form that was sent". Prefer verbs over nominalizations: "we measured" not "measurement was performed".
- **Signposting is systematic, not sparing.** Singh signposts every paper the same way: an organization paragraph, one-sentence section preambles ("We contrast consent-based governance for autonomy."), and numbered inline enumerations for 3+ coordinate items ("The contribution of this paper is twofold. First, ... Second, ..."). See `paper-logic.md` §9; do not trim these as redundancy — they are load-bearing.
- **Restate dense points with discourse markers.** After a dense sentence, Singh restates in plain terms: "That is, it considers settings where decision making by agents impinges upon the outcomes perceived by other agents." / "Simply put, we place agents in a social setting." / "In essence, ..." / "All considered, ..." (sentence-initial "In essence," is a restatement marker, distinct from the content-free adverb "essentially" that grammar-rules.md flags) (the last to close a survey with a verdict). Sentence-initial "But" and "And" are permitted contrastive moves. "Zero filler" bans throat-clearing, NOT this metadiscourse — restatement that adds understanding is not filler.
- **Sentence rhythm: long build-up, short resolution.** Median around 20 words, but roughly a quarter of his sentences legitimately exceed 30 (enumerations, definitions). The real rule is not a cap; it is resolving a long build-up with a short declarative beat: "Consent is a natural abstraction here." / "Simply put, we place agents in a social setting." Never stack three long sentences without a short one.

## AI-tell scan (run on every draft)

These patterns read as machine-generated; owners consistently flag them. Remove on sight:

1. **Register-inflating a single observation**: "a systematic confound in agent evaluation" → "refusals were masking models' true capability". One data point does not get a taxonomy name. But a real, populated taxonomy is a signature Singh move and stays: "we found exploitable apps exhibiting four types of exploitable functionalities" (CACM 2025) — four types, each then described; grouped criteria tables likewise. The test: does each category have members?
2. **Throat-clearing openers**: "It is worth noting that", "In today's fast-paced world", "I hope this finds you well" (banned outright).
3. **Symmetrical triads everywhere** ("clear, concise, and compelling") — real writers use triads occasionally, not per paragraph.
4. **Empty amplifiers**: "significantly", "greatly", "seamlessly", "robust", "comprehensive" without a number behind them.
5. **Stacked hedges**: "could potentially help", "might possibly improve" — cut to one modal. A single calibrated modal is Singh-idiomatic, not a tell: his 8-page AAAI paper uses "would" 10 times, "may" 9, "potentially" 4 ("they would lead to unethical outcomes", "the agent may have previously obtained consent"). Never strengthen a modal beyond the evidence — "may help" becomes "helps" only when the draft contains the showing.
6. **Uniform paragraph lengths and bullet symmetry** — vary structure to match content.
7. **Em-dash chains and arrow chains.** A single em-dash attaching a consequence or reformulation is Singh-idiomatic and stays: "economic signals (e.g., surge pricing) are not applicable—they would lead to unethical outcomes by effectively coercing poor people" (JAIR 2025). The tell is chains — two or more em-dashes per sentence — and arrow notation (A → B → C) in prose. Write sentences.

## Final-read checklist (after the linter passes)

1. Read the draft aloud mentally. Any sentence you would not say to the recipient's face — rewrite it.
2. Every number, date, name, and title verified against a source. Titles of talks and papers must match the official listing verbatim (a referee will check).
3. The first sentence carries the point. Delete any sentence whose removal loses nothing.
4. For claims about the owner's own work: never inflate (task counts, model counts, seniority). Understating is recoverable; inflating is not.
5. If the recipient has known style preferences (e.g., Singh himself), check the draft against THEIR documented peeves — the highest compliment to a careful reader is text with nothing to correct.
