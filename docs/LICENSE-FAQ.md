# License FAQ — FSL-1.1-MIT in plain language

This project is licensed under the **Functional Source License, Version 1.1,
with MIT Future License** (FSL-1.1-MIT). The full text is in
[`LICENSE.md`](../LICENSE.md) — that text is the only thing that is legally
binding. This page is a plain-language summary written by the maintainer, and
**it is not legal advice**; if the answer matters commercially, read the
license (it's short) or ask a lawyer.

## Can I use this at work / inside my company?

**Yes.** Internal use is explicitly a Permitted Purpose — the license names
"internal use and access" as permitted, and that includes commercial companies.
Installing it on your work Mac, running it for your team, or building internal
tooling around it is all fine.

## Can I fork it, modify it, and redistribute my changes?

**Yes.** The license grants the right to use, copy, modify, create derivative
works, and redistribute for any Permitted Purpose. Two conditions from the
Redistribution clause: include a copy of (or link to) the license terms with
what you distribute, and don't remove the copyright notices. Your fork and its
users are bound by the same terms — including the Competing Use restriction and
the same future-MIT dates (the two-year clock runs from when *this project*
released a version, not from when you forked it).

## What can't I do? (What is a "Competing Use"?)

The one restriction: you may not make the Software available **to others** in a
**commercial product or service** that substitutes for this software or offers
substantially the same functionality. Concretely:

- Selling or offering a hosted/paid "AI chief-of-staff for macOS" built from
  this code — not permitted while a version is still under FSL terms.
- Bundling this software into a commercial product that competes with it — not
  permitted.
- Everything short of that — using it yourself or at your company, forking it,
  shipping unrelated commercial products that merely *use* it internally,
  or providing professional services (consulting, setup, customization) to
  someone who uses it — is permitted.

If you're unsure whether your plan counts as competing, open a
[Discussion](https://github.com/Wan-ZL/zelin-ai-assistant/discussions) and ask.

## What happens after two years?

Each released version automatically gains an **irrevocable MIT license** on the
second anniversary of the date that version was made available. After that
date, that version is plain MIT — no Competing Use restriction, OSI-approved,
do what MIT allows. The grant is already in `LICENSE.md` ("Grant of Future
License"); nobody has to do anything for it to take effect.

| Version | Made available | MIT from |
|---|---|---|
| v0.10.3 | July 2026 | July 2028 |
| v0.11.0 | July 2026 | July 2028 |

Each future release starts its own two-year clock from its publication date on
the [Releases page](https://github.com/Wan-ZL/zelin-ai-assistant/releases),
which is the authoritative record.

## Is this "open source"?

Strictly speaking, no — the FSL is not an OSI-approved license, because of the
Competing Use restriction (that's also why GitHub's license detector shows
"Other"). It is **source-available now, and becomes genuinely open source
(MIT) two years per version later**. Background on the license family:
[fsl.software](https://fsl.software).

## Under what terms do I contribute?

By submitting a contribution you agree to the terms in
[CONTRIBUTING.md § License of contributions](../CONTRIBUTING.md#license-of-contributions):
your contribution is licensed under the same FSL-1.1-MIT terms (including the
future MIT grant), and you grant the maintainer the right to relicense it as
part of the project.

**Why the relicense grant?** The FSL's future-MIT promise only works if a
single party holds the right to relicense the entire codebase — with many
copyright holders and no grant, any one contributor could block the MIT
conversion (or any future licensing decision). Keeping that right in one place
is what makes the two-year promise above credible. If you're not comfortable
granting it, open an issue describing your change instead of a PR — suggestions
are just as valuable and carry no license terms.

---

*Again: this FAQ is a good-faith summary, not legal advice, and it does not
modify [`LICENSE.md`](../LICENSE.md). Where they disagree, the license text
wins.*
