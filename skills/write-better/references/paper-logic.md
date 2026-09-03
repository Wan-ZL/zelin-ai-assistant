# Paper logic — Singh's argument-structure playbook

How Singh builds a paper's argument, not just its sentences. Every exemplar below is quoted verbatim from three verified papers:

- **[AAAI-22]** "Consent as a Foundation for Responsible Autonomy" (AAAI 2022, solo author; arXiv:2203.11420)
- **[JAIR-25]** "Prosociality in Microtransit" (JAIR 82, 2025)
- **[CACM-25]** "Understanding Mobile App Reviews to Guide Misuse Audits" (CACM 68(8), 2025; arXiv:2303.10795)

Use each move twice: as a **drafting instruction** (build the draft this way) and as a **review check** (flag drafts that lack it). Anything marked *extension* is our generalization, not documented Singh.

## 1. Motivation funnel (introduction shape)

Four beats, in order:

1. **Open broad**, on the field's terms: "Recent advances in the capabilities of AI have rightly brought the concerns of responsible AI to the forefront." [AAAI-22] (Likewise [JAIR-25] opens: "Transportation is essential for residents to go to work, obtain healthcare, shop for food, or otherwise engage in civic life.")
2. **Survey the mainstream with citations**: "Mainstream AI efforts consider topics such as algorithmic accountability and fairness, referring to statistical properties of AI algorithms." [AAAI-22]
3. **State the limitation as a contrast**, usually a "However" sentence: "However, in those cases the context is largely fixed—we would not be able to easily change the judicial or financial systems, and get rid of the disparities and inequities entrenched in those systems." [AAAI-22]
4. **Land on the paper's abstraction** as the natural answer to that limitation — stated as a short declarative beat when it arrives (in [AAAI-22], "Consent is a natural abstraction here.").

Check: does the key abstraction appear only after the funnel has earned it? An abstraction on line one, unmotivated, is the first thing to flag.

## 2. Concede-then-pivot niche carving

Say what prior work does well before naming the gap; the pivot is a "However" or "but" clause, not a dismissal.

- "Traditional audits of mobile apps conduct a review of their source code. However, interpersonal misuse arising from app users (instead of app developers) goes unnoticed by such processes." [CACM-25]
- "Some researchers have considered ways to enhance the attractiveness of alternative options to users ... We build on their ideas, but we expand persuasive messaging to accommodate considerations of empathy." [JAIR-25]

Check: any gap claim without a named, credited comparison target is unsupported niche carving.

## 3. Related-work subsections close with a verdict

Each subsection that surveys an area ends with one evaluative sentence stating what the survey adds up to:

- "All considered, consent in computing is often misguided or ill-intentioned, and does not shed light on responsibility." [AAAI-22]

Check: a related-work subsection that only lists papers is unfinished — ask "so what?" and write the answer as the closing sentence.

## 4. Define-then-use

A new term is *italicized* at first mention, defined in the same or next sentence, then used unchanged for the rest of the paper — no synonym rotation.

- "We model such a microsociety as a *sociotechnical system (STS)*." [AAAI-22]
- "*Prosociality* refers to an attitude or behavior that is intended to benefit others." [JAIR-25]

Check: (a) a term used before it is defined; (b) drift — "framework" on page 2 becoming "architecture" on page 5 for the same thing.

## 5. One running named example

A single concrete scenario, introduced early and threaded through the whole paper. [AAAI-22] runs a mobile social application in which Alice shares pictures with friends; the consent implications for Alice and the other stakeholders reappear in each later section to ground each new concept.

Check: multiple disposable examples, or an example that changes cast mid-paper, dilute the argument — merge into one and reuse it.

## 6. Named research questions (agenda and vision sections)

Pose the agenda as a small set of short, parallel research questions with mnemonic names, introduced by a bridge sentence:

- "The foregoing motivation leads us to examine the following research questions." Then: **RQ_tolerance** "Can we learn riders' spatial tolerances to suggest optimal spatial adjustments?" **RQ_empathy** "Can we learn riders' empathetic tendencies to persuade them to adjust?" **RQ_profile** "Could considering rider profile data lead to a better (nonnaive) starting point?" [JAIR-25]

Each RQ is one sentence, answerable, and later sections answer them explicitly. *Extension:* pair or triple them; never a lone RQ (a lone question is a thesis statement, not an agenda).

## 7. Calibrated claim verbs

Match the verb to the evidence; never above it.

- **posit** = a stance argued for, not proven: "We posit that instead of taking rider preferences as fixed, shaping them prosocially will lead to improved societal outcomes." [JAIR-25]; "We posit that focusing on moral quandaries has little to offer in the way of valuable research questions for responsible autonomy." [AAAI-22]
- **propose** = an artifact or method offered for adoption.
- **claim** = a falsifiable statement the paper defends.
- **show / demonstrate** = only where the paper contains the evidence: "This paper demonstrates a computational approach to prosociality in the context of a *(public) microtransit* service for disadvantaged riders." [JAIR-25]

Check both directions: "show" without evidence (over-claiming) and "posit" for a measured result (under-claiming).

## 8. Abstract shapes (three verified templates)

1. **Focus + twofold contribution** [AAAI-22]: "This paper focuses on a dynamic aspect of responsible autonomy, namely, to make intelligent agents be responsible at run time." ... "The contribution of this paper is twofold. First, it provides a conceptual analysis of consent ... Second, it outlines challenges for AI ..."
2. **Define, posit, enumerate** [JAIR-25]: opens by defining the object of study — "We study *(public) microtransit*, a type of transportation service wherein a municipality offers point-to-point rides to residents, for a fixed, nominal fare." — then the posit (above), then "Our contributions are these: (1) ...; (2) ...; (3) ...; and (4) ..."
3. **Challenge, method, quantified findings** [CACM-25]: "We address the challenge in responsible computing where an exploitable mobile app is misused by one app user (an abuser) against another user or bystander (victim)." Then the method, then numbers and a taxonomy: "In total, we confirmed 156 exploitable apps facilitating the misuse." "Based on our qualitative analysis, we found exploitable apps exhibiting four types of exploitable functionalities."

Check: an abstract with no contribution statement, or with contributions that a reader could not count, matches none of his shapes.

## 9. Signposting is systematic and load-bearing

Not decoration — the reader navigates by it. Three mandatory devices:

1. **Organization paragraph** ending the introduction: "The rest of this paper is organized as follows. Section 2 describes the importance of microtransit in the rural setting. ..." [JAIR-25]; [AAAI-22] does the same, one sentence per section.
2. **One-sentence section preambles** announcing what the section does: "We contrast consent-based governance for autonomy." [AAAI-22]
3. **Numbered inline enumerations** for three or more coordinate items: "Our contributions are these: (1) ...; (2) ...; (3) ...; and (4) ..." [JAIR-25 abstract]; "We make four main contributions. Firstly, ... Secondly, ... Thirdly, ... Finally, ..." [JAIR-25 §1.1]. Items must be grammatically parallel.

Check: a section that opens cold (no preamble), a paper with no organization paragraph, or 3+ coordinate items buried in running prose — all get flagged.

## Reviewing as Singh

Given a draft, flag in this order (*ordering is our extension; each item is grounded in the moves above*):

1. **Unmotivated abstractions** — the key term appears before the funnel earned it (§1).
2. **Claims without comparison targets** — "better", "novel", "unlike prior work" with no named, conceded prior work (§2, §3).
3. **Terms used before defined**, or defined once and then varied (§4).
4. **Sections without preambles; no organization paragraph** (§9).
5. **Enumerations without numbering or parallel structure** (§9).
6. **Claim verbs stronger than the evidence** (§7).

Comment tone, matching his published advice pages: direct, rule-citing, no praise padding. One sentence naming the violation and the rule it breaks, plus a rewrite when a rewrite is short. Never "great job, but ..." — for this reader, text with nothing to correct is the compliment.
