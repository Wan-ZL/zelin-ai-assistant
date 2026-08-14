# LaTeX, BibTeX, and figure rules (papers only)

Condensed from Singh's editing page; quotes are his.

## LaTeX text

- Quotes: `` and '' (never straight "). Apostrophe is a single closing quote: 'Bob's car'.
- En-dash `--` for ranges, no spaces (11--23). Em-dash `---`, no spaces.
- Internal references: `Section~\ref{sec-foo}`, `Figure~\ref{fig-bar}` — tilde before the ref number, never hardcode numbers.
- No footnotes. No `\\` to end paragraphs; blank lines only.
- Code snippets: `\texttt{}` or `\textsf{}`; URLs and URL-like snippets: `\url{}`.
- Multi-letter identifiers never in math italics: `\emph{FP}` in text, `$Q_{\mathrm{FP}}$` in math. Numbered agents: `$A_1$`, `$A_2$`.
- Emphasis (italics/bold) for a few words only. New terms italicized at first use.
- "Complex formulas are never attractive" — minimize subscripts, don't over-complicate notation, split long formulas somewhere other than at the last parenthesis.

## Figures and tables

- Table captions on top; figure captions at bottom; all captions end with a period; sentence case.
- Reference every figure from the text. Widths ~3.25in (column) or ~5.5in (page).
- Same typeface in figures as body; figure text 9–10pt. Check greyscale; do not rely on color.
- Figure files named with hyphen-separated English words matching their labels.

## BibTeX

- One personal bib file (yourname.bib); `\usepackage[square]{natbib}` + `plainnat` by default.
- Braces for string fields; bare numbers for numeric fields; three-letter month macros (jan, feb).
- Titles in title case; protect required capitals with braces: {Internet}, {BERT}. After a colon, capitalize the next word.
- Full author first names: "George B. Shaw", initials end with periods, authors joined by "and".
- Supply pages, DOI (omit URL/ISBN when DOI exists), editor for collections, conference city in address.
- Strip years/locations from booktitles: "Proceedings of the Conference on Foo (Foo)".
- Freeze the bibliography in the final document by inlining the .bbl.

## Submission hygiene

- Clear all compiler warnings you can. Number all pages. Title, author, date on title pages.
- Acknowledge funders and commenters with correctly spelled full names; "Don't thank your coauthors!"
- Comment acknowledgments out for anonymous review.
