# LaTeX, BibTeX, and figure rules (papers only)

Condensed from Singh's editing page (https://www.csc2.ncsu.edu/faculty/mpsingh/local/advice/editing.html), verified 2026-08-18; quotes are his. Items not on the page are labeled as extensions.

## LaTeX text

- Quotes: `` and '' (never straight "). "Use a single closing quote (') to mean an apostrophe, e.g., 'Bob's car.' An opening quote does not an apostrophe make."
- En-dash `--` for ranges, no spaces ("11--23" appears as 11–23). Em-dash `---`, no spaces.
- Internal references: `Section~\ref{sec-foo}`, `Figure~\ref{fig-bar}` — tilde before the ref number, never hardcode numbers.
- "Don't use footnotes. Decide if it is worth stating in the main text; if not, don't say it."
- Paragraphs: separate with blank lines; don't end them with `\\` (extension — standard LaTeX practice, not on his editing page).
- Code snippets: `\texttt{}` or `\textsf{}`; URLs and URL-like snippets: `\url{}`.
- Multi-letter identifiers never in math italics: `\emph{FP}` in text, `$Q_{\mathrm{FP}}$` in math. Numbered agents: `$A_1$`, `$A_2$`.
- Emphasis (italics/bold) for a few words only. New terms italicized at first use.
- "Complex formulas are never attractive" — minimize subscripts, don't over-complicate notation, split long formulas somewhere other than at the last parenthesis.

## Citations in text

- Plain space before `\cite`, not a tilde: "Some people use a tilde in Latex before a \cite. Doing so yields a space in the output (good) but prevents a line break (bad). The result is often that the citation overshoots the margin or the word preceding the citation is forcibly hyphenated to start a new line. Thus, those people are wrong — whitespace is what you need."
- "Don't begin a sentence with a citation."
- "By default, use fullname references, readily generated using \usepackage[square]{natbib} \bibliographystyle{plainnat}" — i.e., \citet-style, with author names read as part of the sentence.
- Under a numeric-only style: "When the required style is not compatible with the above, you can still include the names of authors in the main text so that it reads something like 'Singh et al. [11] claim ...'"

## Headings, figures, and tables

- "Capitalize sections, subsections, subsubsections (i.e., make first letter of all big or important words upper case)." "Use sentence case for paragraph headings and end each heading with a period."
- Table captions on top; figure captions at bottom; "Write all captions in sentence case"; all captions end with a period.
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
