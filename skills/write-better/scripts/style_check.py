#!/usr/bin/env python3
"""Deterministic style linter for the write-better skill (Singh-standard).

Usage: python3 style_check.py [--latex] <file|-> ...   (stdin via "-")
LaTeX mode auto-enables per *.tex file and resets for the next file.
Hard-wrapped lines are joined into sentences per paragraph before matching;
findings report the original physical line/col (0-based) of the match.
Output:  LEVEL file:line:col  [rule-id] message  | snippet
Suppress a rule on a line/sentence with:  lint-ok: <rule-id>
Exit: 3 if any file unreadable; else 2 any ERROR, 1 only WARNs, 0 clean.
"""
import os
import re
import sys
from bisect import bisect_right

S = "\x00"  # sentinel filling stripped LaTeX spans (keeps columns aligned)

CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")
SUPPRESS = re.compile(r"lint-ok:\s*([\w-]+(?:\s*,\s*[\w-]+)*)")
COMMENT = re.compile(r"(?<!\\)%.*$")
SNIP_SENT = re.compile(r"\x00+")

# ---- LaTeX stripping (argument/math spans -> sentinels; columns preserved) --
LATEX_STRIPS = [
    re.compile(r"(\\includegraphics\*?)((?:\[[^\]]*\])?\{[^{}]*\})"),
    re.compile(r"(\\(?:input|include|label|url|texttt|path|href|ref|eqref|"
               r"autoref|[cC]ref|pageref|vref)\*?)(\{[^{}]*\})"),
    re.compile(r"(\\[cC]ite[a-zA-Z]*\*?)((?:\[[^\]]*\]){0,2}\{[^{}]*\})"),
]
VERB = re.compile(r"(\\verb\*?)([^A-Za-z\s])(?:(?!\2).)*\2")
MATH_INLINE = re.compile(r"(?<!\\)\$(?:[^$\\]|\\.)*\$")

# ---- prose rules -------------------------------------------------------------
CONTR = re.compile(
    r"\b(?:\w+n't"
    r"|(?:it|that|there|here|what|who|where|when|how|let|he|she)'s"
    r"|(?:i|you|we|they|he|she|it|that|there|who)'ll"
    r"|(?:i|you|we|they|could|would|should|must|might|who)'ve"
    r"|(?:you|we|they|who)'re"
    r"|(?:i|you|we|they|he|she|it|that|who)'d"
    r"|i'm|y'all)\b", re.I)

BANNED = [
    ("very", "ERROR", r"\bvery\b", "banned word: 'very' is content-free (Twain via Singh)"),
    ("etc", "ERROR", r"\betc\.", "banned word: 'etc.' indicates carelessness of thought"),
    ("we-feel", "ERROR", r"\bwe feel\b", "banned phrase: 'we feel' — use 'we claim/show/argue'"),
    ("more-like", "ERROR", r"\bmore like\b", "vague: 'more like' — assert directly"),
    ("hedge", "ERROR", r"\bsort of\b|\bkind of\b|\bbasically\b", "vague hedge — cut it"),
    ("centered-around", "ERROR", r"\bcent(?:er|re)ed around\b", "diction: 'centered on', not 'around'"),
    ("focused-around", "ERROR", r"\bfocused? around\b", "diction: 'focused on', not 'around'"),
    ("throat-clearing", "ERROR", r"\bit is worth noting that\b|\bit should be noted that\b",
     "throat-clearing — delete"),
    ("opener", "ERROR", r"\bi hope this (?:email )?finds you well\b|\bin today's\b",
     "banned opener — delete"),
    ("marketing", "ERROR", r"\bseamless(?:ly)?\b|\bgame-chang\w+\b|\bcutting-edge\b",
     "marketing adjective — ban"),
    ("amplifier", "WARN", r"\bsignificantly\b|\bgreatly\b", "empty amplifier — give a number or cut"),
    ("vague-ref", "ERROR", r"\bthe following (?:figure|table|section)s?\b", "vague reference — name it"),
    ("the-figure", "ERROR", r"\bthe (?:Figure|Table|Section)(?:\s+\d|\s*~?\\ref\b)",
     "over-specified: 'Figure 3', not 'the Figure 3'"),
    ("slash-banned", "ERROR", r"\bw/(?=\s|$)|\band/or\b|\b24/7\b",
     "slash construction — write the words out"),
]
BANNED_C = [(rid, lvl, re.compile(pat, re.I), msg) for rid, lvl, pat, msg in BANNED]

URLRE = re.compile(
    r"(?:https?://[^\s<>()\"']+|www\.[^\s<>()\"']+"
    r"|\b[A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)*\.(?:com|org|edu|io|net|gov|ai)\b"
    r"(?:/[^\s<>()\"']*)?)")

SLASH_TOKEN = re.compile(
    r"(?<![\w./\\@:–-])([A-Za-z0-9][\w.+-]*(?:/[A-Za-z0-9][\w.+-]*)+)(?![\w./])")
SLASH_TERMS = {"i/o", "a/b", "n/a", "tcp/ip"}
RATIO = re.compile(r"[A-Za-z]+/s(?:ec)?$", re.I)
DATE_TOKEN = re.compile(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?$")

SLASH_DATE = re.compile(r"(?<![\d/.])\d{1,2}/\d{1,2}(?:/\d{2,4})?(?!\d|[./]\d)")
ISO_DATE = re.compile(r"(?<![\d–—-])(?:19|20)\d{2}-\d{1,2}-\d{1,2}(?![\d–—-])")

SMALL_NUM = re.compile(r"(?<![\w.$@#~_/:–—\x00-])([0-9])(?=[.,;:!?)\]]?(?:\s|$))")
FIG_CTX = re.compile(
    r"(?:\bfig(?:ure|s)?\.?|\btab(?:le|s)?\.?|\bsec(?:tion)?s?\.?|\beqn?s?\.?|\bequations?"
    r"|\bpages?|\bpp\.|\bp\.|§|\bchapters?|\bappendix|\bsteps?|\bversions?|\bitems?|\blevels?)"
    r"[~\s]*(?:\d+\s*(?:,|and|or|to|through|–|--)\s*)*$", re.I)

W_ = r"[A-Za-z0-9$%\\][\w$%.\\-]*"
SERIAL = re.compile(r"\b({W}(?: {W}){{0,3}}), ({W}(?: {W}){{0,3}}) (and|or) (?={W})".format(W=W_))
DISCOURSE_LAST = set(
    "however moreover furthermore finally first second third fourth meanwhile nevertheless "
    "nonetheless therefore thus hence consequently additionally similarly specifically "
    "importantly notably overall instead indeed contrast particular fact example instance "
    "addition practice sum short result then also still yet conversely accordingly crucially "
    "unfortunately interestingly surprisingly relatedly".split())

IRREG = ("shown|given|made|done|built|written|used|seen|found|taken|known|sent|kept|chosen|held"
         "|put|set|led|thought|felt|meant|brought|taught|told|paid|said|left|lost|won|read|run"
         "|drawn|grown|driven|hidden|understood|spent|sold|laid|cast|cut|hit|split|spread|begun"
         "|shaken|broken|frozen|proven|borne")
PASSIVE = re.compile(
    r"\b(?:am|is|are|was|were|been|being|be)\s+"
    r"((?:(?:\w+ly|not|also|still|often|never|already|then|thus|now)\s+){0,2})"
    r"([A-Za-z]{2,}ed|" + IRREG + r")\b", re.I)
ED_STOP = set("indeed red hundred unprecedented detailed sacred naked wicked rugged ragged "
              "wretched crooked dogged alleged aged beloved jagged learned need deed seed feed "
              "speed breed creed greed".split())

AUX_VERBS = set(
    "is are was were has have had will would shall should can could may might must does did do "
    "seems seem appears appear remains remain means mean shows show suggests suggest indicates "
    "indicate implies imply allows allow enables enable requires require depends depend leads "
    "lead makes make gives give takes take holds hold matters matter follows follow yields "
    "yield captures capture happens happen occurs occur works work helps help creates create "
    "explains explain reflects reflect provides provide offers offer raises raise poses pose becomes "
    "become turns turn tends tend stems arises arise fails fail differs differ varies "
    "vary".split())
PRON_START = re.compile(r"(This|It|These|Those)\s+([A-Za-z][\w'-]*)")

SPLICE = re.compile(
    r",\s+((?:it|he|she|they|we|you|I|there|this|these|those)|[A-Z][a-z]+)\s+"
    r"(is|are|was|were|has|have|had|does|did|do|will|would|can|could|found|finds|shows?|showed"
    r"|says?|said|argues?|argued|notes?|noted|reports?|reported|thinks?|thought|believes?"
    r"|believed|runs?|ran|writes?|wrote|remains?|remained|seems?|seemed|fails?|failed|works?"
    r"|worked|holds?|held)\b")
SUBORD = set("if when while although though because since as after before unless whereas given "
             "suppose supposing assuming using following considering despite during for with "
             "without under in on at from by to once until unlike like across among between "
             "through over beyond besides according having being however moreover first second "
             "finally here thus".split())
VERBISH = re.compile(
    r"\b(?:is|are|was|were|has|have|had|does|did|do|will|would|can|could|ran|found|showed|made"
    r"|took|went|got|saw|gave|held|kept|set|put|led|meant|felt|built|wrote|read|said|chose|knew"
    r"|met|came|spoke|stood|sat|grew|threw|caught|drew|heard|bought|sought|fought|rose|fell|drove"
    r"|thought|brought|taught|told|paid|left|lost|won|sent|became|began|passed|failed|works?"
    r"|holds?|runs?|fails?|shows?|seems?|remains?|[A-Za-z]{3,}ed)\b")

EMDASH_SPACED = re.compile(r"(?<=\s)(?:---|—)(?=\s)(?!-)")
EMDASH_ANY = re.compile(r"(?<!-)---(?!-)|—")

BOUND = re.compile(r"[.!?][\"')\]]*[ \t]+")
ABBR_WORD = re.compile(r"\b(?:et al|vs|cf|figs?|eqs?|pp|dr|prof|mr|mrs|ms|jr|sr|st|secs?|sect"
                       r"|chs?|vols?|approx|ca|resp|i\.e|e\.g|etc)\.$", re.I)
ABBR_CASED = re.compile(r"\b(?:[A-Z]|Nos?|p)\.$")
ENUM_TAIL = re.compile(r"(?:^|[\s(])\d{1,2}\.$")
ENUM_HEAD = re.compile(r"\d{1,2}\.(?:\s|$)")

SPACE_PUNCT = re.compile(r"(?<=\S)\s+[,.;:!?](?=\s|$)")
DOUBLE_SPACE = re.compile(r"(?<=\S)  +(?=\S)")
QUOTE = re.compile(r"(?<![`\\])\"")
FOOTNOTE = re.compile(r"\\footnote")
HARDREF = re.compile(r"\b(?:Section|Figure|Table)\s+\d")


def _strip_comment(line):
    m = COMMENT.search(line)
    if not m:
        return line, False
    kept = line[: m.start()]
    return kept + " " * (len(line) - len(kept)), kept.strip() == ""


def _strip_display(line, in_disp):
    res, i, n = [], 0, len(line)
    while i < n:
        if in_disp:
            k = line.find("\\]", i)
            if k == -1:
                res.append(S * (n - i))
                i = n
            else:
                res.append(S * (k + 2 - i))
                i, in_disp = k + 2, False
        else:
            k = line.find("\\[", i)
            if k > 0 and line[k - 1] == "\\":  # "\\[2pt]" is a line break, not math
                res.append(line[i: k + 2])
                i = k + 2
                continue
            if k == -1:
                res.append(line[i:])
                i = n
            else:
                res.append(line[i:k] + S * 2)
                i, in_disp = k + 2, True
    return "".join(res), in_disp


def _keep_head(m):
    return m.group(1) + S * (len(m.group(0)) - len(m.group(1)))


def _strip_latex(line, in_disp):
    line, in_disp = _strip_display(line, in_disp)
    line = VERB.sub(lambda m: m.group(1) + S * (len(m.group(0)) - len(m.group(1))), line)
    line = MATH_INLINE.sub(lambda m: S * len(m.group(0)), line)
    for pat in LATEX_STRIPS:
        line = pat.sub(_keep_head, line)
    return line, in_disp


def _check_paragraph(para, out):
    starts, lnos, lens, parts, pos = [], [], [], [], 0
    for ln, txt in para:
        starts.append(pos)
        lnos.append(ln)
        lens.append(len(txt))
        parts.append(txt)
        pos += len(txt) + 1
    joined = " ".join(parts)

    def loc(idx):
        k = bisect_right(starts, idx) - 1
        col = idx - starts[k]
        if col >= lens[k]:
            col = max(lens[k] - 1, 0)
        return lnos[k], col

    url_spans = [(m.start(), m.end()) for m in URLRE.finditer(joined)]

    def in_url(a, b=None):
        b = a + 1 if b is None else b
        return any(a < ue and b > us for us, ue in url_spans)

    # sentence spans (abbreviation-guarded boundaries)
    bounds = [0]
    for m in BOUND.finditer(joined):
        if joined[m.start()] == ".":
            head = joined[max(0, m.start() - 11): m.start() + 1]
            if ABBR_WORD.search(head) or ABBR_CASED.search(head) or ENUM_TAIL.search(head):
                continue
        else:  # "?" / "!" inside a quote or parenthetical, sentence continuing
            nxt = joined[m.end(): m.end() + 1]
            if nxt.isalpha() and nxt.islower():
                continue
        bounds.append(m.end())
    bounds.append(len(joined))
    sents = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if bounds[i] < bounds[i + 1]]
    if not sents:
        sents = [(0, len(joined))]
    sstarts = [s for s, _ in sents]

    def sent_lines(idx):
        k = max(bisect_right(sstarts, idx) - 1, 0)
        s, e = sents[k]
        l1, _ = loc(s)
        l2, _ = loc(max(e - 1, s))
        return set(range(l1, l2 + 1))

    def add(rule, level, idx, msg, snip, prio, end=None):
        if in_url(idx, end):
            return
        ln, col = loc(idx)
        snip = SNIP_SENT.sub("<..>", snip)[:60]
        out.append([level, ln, col, rule, msg, snip, prio, sent_lines(idx)])

    for m in CONTR.finditer(joined):
        add("contraction", "ERROR", m.start(), "no contractions in formal writing",
            m.group(0), 60, m.end())
    for rid, lvl, pat, msg in BANNED_C:
        for m in pat.finditer(joined):
            add(rid, lvl, m.start(), msg, m.group(0), 85 if rid == "slash-banned" else 60, m.end())

    for m in SLASH_TOKEN.finditer(joined):
        tok = m.group(1).rstrip(".")  # sentence-final period is not part of the token
        end, low = m.start(1) + len(tok), tok.lower()
        if low in ("and/or", "24/7"):
            continue  # slash-banned reports these
        if DATE_TOKEN.match(tok):
            continue  # numeric-date reports these
        if low in SLASH_TERMS or RATIO.match(tok):
            add("slash-term", "WARN", m.start(1),
                "established slash term — fine, or write it out", tok, 80, end)
        else:
            add("slash", "ERROR", m.start(1),
                "slash construction — 'profound carelessness of thought'; write the words out",
                tok, 70, end)

    for m in SLASH_DATE.finditer(joined):
        p = m.group(0).split("/")
        if m.group(0) == "24/7":
            continue
        a, b = int(p[0]), int(p[1])
        if len(p) == 3 or (a <= 12 < b) or a > b:
            msg = "numeric date — write it out ('August 17, 2026')"
        else:
            msg = "numeric slash — a fraction? write 'one-third'; a date? write 'January 3'"
        add("numeric-date", "ERROR", m.start(), msg, m.group(0), 90, m.end())
    for m in ISO_DATE.finditer(joined):
        add("numeric-date", "ERROR", m.start(),
            "numeric ISO date — write it out ('July 22, 2026')", m.group(0), 90, m.end())

    for m in SMALL_NUM.finditer(joined):
        if FIG_CTX.search(joined[max(0, m.start() - 24): m.start()]):
            continue
        add("small-numeral", "WARN", m.start(1),
            "number under ten — spell it out unless an identifier/metric", m.group(1), 40)

    for m in SERIAL.finditer(joined):
        if m.group(1).split()[-1].lower() in DISCOURSE_LAST or m.group(1).lower() in (
                "in contrast", "for example", "for instance", "in addition", "in particular",
                "in fact", "in practice", "in sum", "in short", "that is", "by contrast",
                "as a result"):
            continue
        if m.group(2).lower() in ("and", "or"):
            continue
        if m.group(2).split()[-1].lower() in (
                "with", "without", "of", "for", "to", "from", "in", "on", "at", "by",
                "over", "under", "between", "against", "before", "after"):
            continue  # elliptical pair ("reported with and without"), not a list
        add("serial-comma", "WARN", m.start(),
            "possible missing serial comma: 'A, B, and C'", m.group(0), 30, m.end())

    for m in PASSIVE.finditer(joined):
        if m.group(2).lower() in ED_STOP:
            continue
        add("passive", "WARN", m.start(),
            "passive voice — prefer active (almost always)", m.group(0), 30, m.end())

    for m in SPLICE.finditer(joined):
        k = max(bisect_right(sstarts, m.start()) - 1, 0)
        s, e = sents[k]
        pre = joined[s: m.start()]
        toks = pre.split()
        if toks and toks[-1].rstrip(",").lower() in DISCOURSE_LAST:
            continue
        first = joined[s:e].lstrip(S + " \t").split(None, 1)
        if first and first[0].lower().strip(",") in SUBORD and "," not in pre:
            continue  # intro-phrase / subordinate clause before its first comma
        if not VERBISH.search(pre):
            continue
        add("comma-splice", "WARN", m.start(),
            "possible comma splice — two full clauses joined by a comma",
            m.group(0)[:40], 30, m.end())

    for m in EMDASH_SPACED.finditer(joined):
        add("em-dash-spaced", "ERROR", m.start(),
            "spaced em-dash — an em-dash takes no surrounding spaces (word---word)",
            joined[max(0, m.start() - 8): m.end() + 8], 60, m.end())
    n_dash = len(EMDASH_ANY.findall(joined))
    if n_dash > 2:
        out.append(["WARN", lnos[0], 0, "em-dash-many",
                    "%d em-dashes in one paragraph — AI-tell; vary the punctuation" % n_dash,
                    "", 20, set(lnos)])

    for s, e in sents:
        i2 = s
        while i2 < e and joined[i2] in " \t":
            i2 += 1
        if i2 >= e:
            continue
        c = joined[i2]
        if c == S or in_url(i2):
            continue
        seg = joined[i2:e]
        if c.isdigit():
            if not ENUM_HEAD.match(seg):
                add("sent-start-numeral", "WARN", i2,
                    "sentence starts with a numeral — spell it out or restructure", seg[:24], 75)
        elif c.isalpha() and c.islower():
            add("lowercase-start", "ERROR", i2,
                "sentence starts with a lowercase letter/identifier — restructure", seg[:24], 76)
        m = PRON_START.match(seg)
        if m:
            w = m.group(2).lower()
            verbish = (w in AUX_VERBS
                       or (m.group(2)[0].islower() and w.endswith("ed") and w not in ED_STOP)
                       or (m.group(1) in ("This", "It") and m.group(2)[0].islower()
                           and w.endswith("s") and w not in ("its",)))
            if verbish:
                add("pronoun-start", "WARN", i2,
                    "sentence-initial pronoun — is the referent unmistakable? "
                    "Else repeat the noun", m.group(0), 50)


def check_text(text, latex=False):
    text = text.replace("’", "'")
    supp, rows, in_disp = {}, [], False
    for i, raw in enumerate(text.splitlines(), 1):
        m = SUPPRESS.search(raw)
        if m:
            supp[i] = {t for t in re.split(r"[,\s]+", m.group(1)) if t}
        cjk = bool(CJK.search(raw))
        nocom, conly = _strip_comment(raw) if latex else (raw, False)
        stripped = nocom
        if latex and not conly and not cjk:
            stripped, in_disp = _strip_latex(nocom, in_disp)
        rows.append((i, nocom, stripped, cjk, conly, raw.strip() == ""))

    out = []
    # per-line checks
    for i, nocom, stripped, cjk, conly, blank in rows:
        if blank or cjk or conly:
            continue
        for m in SPACE_PUNCT.finditer(stripped):
            out.append(["ERROR", i, m.start(), "space-punct", "space before punctuation",
                        repr(m.group(0)), 30, {i}])
        for m in DOUBLE_SPACE.finditer(stripped):
            out.append(["WARN", i, m.start(), "double-space", "double space",
                        repr(m.group(0)), 20, {i}])
        if latex:
            for m in QUOTE.finditer(nocom):
                out.append(["ERROR", i, m.start(), "straight-quote",
                            "straight quote in LaTeX — use `` and ''", '"', 30, {i}])
            for m in FOOTNOTE.finditer(nocom):
                out.append(["ERROR", i, m.start(), "footnote",
                            "no footnotes — main text or nothing", m.group(0), 30, {i}])
            for m in HARDREF.finditer(nocom):
                out.append(["WARN", i, m.start(), "hardcoded-ref",
                            "hardcoded reference number — use ~\\ref{}", m.group(0), 25, {i}])

    # paragraph assembly (blank/CJK lines break; comment-only lines are invisible)
    para = []
    for i, nocom, stripped, cjk, conly, blank in rows:
        if blank or cjk:
            if para:
                _check_paragraph(para, out)
                para = []
            continue
        if conly:
            continue
        para.append((i, stripped))
    if para:
        _check_paragraph(para, out)

    # suppression (lint-ok: <rule-id> anywhere on the finding's line/sentence)
    kept = [f for f in out if not any(f[3] in supp.get(l, ()) for l in f[7])]

    # dedup same (line, col) span: keep the most specific (highest priority);
    # paragraph-scope findings (em-dash-many) have no point span and bypass dedup
    best, para_scope = {}, []
    for f in kept:
        if f[3] == "em-dash-many":
            para_scope.append(f)
            continue
        key = (f[1], f[2])
        if key not in best or f[6] > best[key][6]:
            best[key] = f
    final = sorted(list(best.values()) + para_scope, key=lambda f: (f[1], f[2], f[0]))
    return [(f[0], f[1], f[2], f[3], f[4], f[5]) for f in final]


def main():
    args = sys.argv[1:]
    latex_flag = "--latex" in args
    files = [a for a in args if a != "--latex"] or ["-"]
    worst, unreadable = 0, False
    for f in files:
        latex = latex_flag or f.endswith(".tex")  # per-file; resets each iteration
        base = os.path.basename(f) or f
        if f == "-":
            text = sys.stdin.read()
        else:
            try:
                with open(f, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError) as e:
                print("%s: cannot read (%s)" % (base, e.__class__.__name__))
                unreadable = True
                continue
        findings = check_text(text, latex=latex)
        if not findings:
            print("%s: clean" % base)
            continue
        errs = warns = 0
        for level, ln, col, rule, msg, snip in findings:
            print("%s %s:%d:%d  [%s] %s  | %s" % (level, base, ln, col, rule, msg, snip))
            if level == "ERROR":
                errs += 1
            else:
                warns += 1
        print("%s: %d error(s), %d warn(s)" % (base, errs, warns))
        worst = max(worst, 2 if errs else 1)
    sys.exit(3 if unreadable else worst)


if __name__ == "__main__":
    main()
