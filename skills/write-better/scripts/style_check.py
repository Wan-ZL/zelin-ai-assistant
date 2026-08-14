#!/usr/bin/env python3
"""Deterministic style linter for the write-better skill (Singh-standard).

Usage:
    python3 style_check.py <file> [<file> ...]
    cat draft.txt | python3 style_check.py -
    python3 style_check.py --latex paper.tex      # enable LaTeX-specific checks

Prints findings as  LEVEL line:col  rule  | offending text
Exit code: 2 if any ERROR, 1 if only WARNs, 0 if clean.
"""
import re
import sys

CONTRACTIONS = re.compile(
    r"\b(?:can't|won't|don't|doesn't|didn't|isn't|aren't|wasn't|weren't|"
    r"haven't|hasn't|hadn't|shouldn't|couldn't|wouldn't|it's|that's|there's|"
    r"what's|who's|let's|we'll|we've|we're|we'd|i'll|i've|i'm|i'd|you'll|"
    r"you've|you're|you'd|they'll|they've|they're|they'd|he's|she's)\b",
    re.IGNORECASE,
)

BANNED_PHRASES = [
    (r"\bvery\b", "banned-word: 'very' is content-free (Twain via Singh)"),
    (r"\betc\.", "banned-word: 'etc.' indicates carelessness of thought"),
    (r"\bwe feel\b", "banned-phrase: 'we feel' — who cares about your feelings? Use 'we claim/show/argue'"),
    (r"\bmore like\b", "vague: 'more like' — assert directly"),
    (r"\bsort of\b|\bkind of\b|\bbasically\b", "vague hedge — cut it"),
    (r"\bcentered around\b", "diction: 'centered on', not 'around'"),
    (r"\bfocused? around\b", "diction: 'focused on', not 'around'"),
    (r"\bit is worth noting that\b|\bit should be noted that\b", "throat-clearing — delete"),
    (r"\bi hope this (?:email )?finds you well\b", "banned opener"),
    (r"\bin today's\b", "throat-clearing opener — delete"),
    (r"\bseamless(?:ly)?\b|\bgame-chang\w+\b|\bcutting-edge\b", "marketing adjective — ban"),
    (r"\bsignificantly\b|\bgreatly\b", "empty amplifier — give a number or cut"),
    (r"\bthe following figures?\b", "vague reference — name the figure"),
    (r"\bthe Figure \d+|\bthe Table \d+|\bthe Section \d+", "over-specified: 'Figure 3', not 'the Figure 3'"),
    (r"\bw/(?=\s|$)|\band/or\b|\b24/7\b", "slash construction — write the words out"),
]

SLASH = re.compile(r"(?<![:/\w.])(?:\w+/\w+)(?!\S*(?:\.com|\.org|\.edu|\.io|\.net|\.gov))")
URLISH = re.compile(r"(?:https?://|www\.|\S+\.(?:com|org|edu|io|net|gov|ai)\b|\\url\{|blog\.|github\.|scholar\.)")
NUMERIC_DATE = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
SENT_START_NUMERAL = re.compile(r"(?:^|[.!?]\s+)(\d)")
SMALL_NUMERAL = re.compile(r"(?<![\w.$_\-/:])([2-9])(?![\w.,:%\-/])")
NO_SERIAL_COMMA = re.compile(r"\b\w+, \w+ (?:and|or) \w+")
SPACE_BEFORE_PUNCT = re.compile(r"\s+[,.;:!?](?:\s|$)")
DOUBLE_SPACE = re.compile(r"(?<=\S)  +(?=\S)")
PASSIVE = re.compile(r"\b(?:is|are|was|were|been|being|be)\s+(\w+ed|shown|given|made|done|built|written|used|seen|found|taken|known)\b")
PRONOUN_START = re.compile(r"(?:^|[.!?]\s+)(This|It)\s+(?:is|was|has|means|shows|allows|enables)\b")

def check_text(text, latex=False):
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if latex and stripped.startswith("%"):
            continue
        is_urlish_line = bool(URLISH.search(line))

        for m in CONTRACTIONS.finditer(line):
            findings.append(("ERROR", i, m.start(), "no contractions in formal writing", m.group(0)))
        for pat, msg in BANNED_PHRASES:
            for m in re.finditer(pat, line, re.IGNORECASE):
                findings.append(("ERROR", i, m.start(), msg, m.group(0)))
        for m in NUMERIC_DATE.finditer(line):
            findings.append(("ERROR", i, m.start(), "numeric slash date — write 'August 17'", m.group(0)))
        if not is_urlish_line:
            for m in SLASH.finditer(line):
                findings.append(("ERROR", i, m.start(), "slash construction — 'profound carelessness of thought'", m.group(0)))
        for m in SENT_START_NUMERAL.finditer(line):
            findings.append(("ERROR", i, m.start(1), "sentence starts with a numeral — spell it out or restructure", m.group(1)))
        for m in SPACE_BEFORE_PUNCT.finditer(line):
            findings.append(("ERROR", i, m.start(), "space before punctuation", repr(m.group(0))))

        for m in NO_SERIAL_COMMA.finditer(line):
            findings.append(("WARN", i, m.start(), "possible missing serial comma: 'A, B, and C'", m.group(0)))
        for m in DOUBLE_SPACE.finditer(line):
            findings.append(("WARN", i, m.start(), "double space", repr(m.group(0))))
        for m in PASSIVE.finditer(line):
            findings.append(("WARN", i, m.start(), "passive voice — prefer active (almost always)", m.group(0)))
        for m in PRONOUN_START.finditer(line):
            findings.append(("WARN", i, m.start(1), "sentence-initial pronoun — is the referent unmistakable? Else repeat the noun", m.group(1)))
        if not is_urlish_line and not latex:
            for m in SMALL_NUMERAL.finditer(line):
                findings.append(("WARN", i, m.start(), "number under ten — spell it out unless an identifier/metric", m.group(0)))

        if latex:
            for m in re.finditer(r'(?<!`)"', line):
                findings.append(("ERROR", i, m.start(), "straight quote in LaTeX — use `` and ''", '"'))
            for m in re.finditer(r"\\footnote", line):
                findings.append(("ERROR", i, m.start(), "no footnotes — main text or nothing", m.group(0)))
            for m in re.finditer(r"(?:Section|Figure|Table)\s+\d", line):
                findings.append(("WARN", i, m.start(), "hardcoded reference number — use ~\\ref{}", m.group(0)))
    return findings

def main():
    args = [a for a in sys.argv[1:]]
    latex = "--latex" in args
    files = [a for a in args if a != "--latex"] or ["-"]
    worst = 0
    for f in files:
        text = sys.stdin.read() if f == "-" else open(f, encoding="utf-8").read()
        if f.endswith(".tex"):
            latex = True
        findings = check_text(text, latex=latex)
        if not findings:
            print(f"{f}: clean")
            continue
        for level, ln, col, msg, snippet in sorted(findings, key=lambda x: (x[1], x[2])):
            print(f"{level} {f}:{ln}:{col}  {msg}  | {snippet}")
            worst = max(worst, 2 if level == "ERROR" else 1)
        errs = sum(1 for x in findings if x[0] == "ERROR")
        warns = len(findings) - errs
        print(f"{f}: {errs} error(s), {warns} warn(s)")
    sys.exit(worst)

if __name__ == "__main__":
    main()
