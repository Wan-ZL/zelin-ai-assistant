#!/usr/bin/env bash
# IAEval @ NeurIPS 2026 — pre-submission drift check.
# Purpose: the "no AI/LLM policy anywhere" finding is a SNAPSHOT of 2026-08-28.
# Organizers can still edit the CFP or add a submission-form field (Form_Fields is
# PC-only) right up to close. Run this within ~1h of clicking Submit.
#
# Operative cutoff (OpenReview duedate 1788094740000): 2026-08-30 12:59 UTC
#   = 2026-08-30 05:59 PT.  expdate 1788096540000 -> 13:29 UTC grace (do NOT rely on it).
#   The site advertises "August 29, 2026 (AOE)" = 2026-08-30 11:59 UTC, ~1h EARLIER.
#   Treat Aug 29 AoE as the deadline; the extra hour is slack, not budget.
#
# Exit 0 = nothing moved, act on the 8/28 finding.
# Exit 1 = something moved -> STOP and re-read the CFP / submission form before submitting.

set -uo pipefail

SITE_SHA_BASELINE="67fdd9247b2c09c02568cda3d87c73b2752b6a6b2986cffc24f4685218f18357"
SITE_BYTES_BASELINE=14351
INV_TMDATE_BASELINE=1784592796968          # 2026-07-21T00:13:16Z
INV_KEYS_BASELINE="abstract authors pdf title venue venueid"
REPO_HEAD_BASELINE="5fbbc5ab56"            # 2026-07-27T21:56:34Z, Yao Dou
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FAIL=0
say() { printf '%s\n' "$*"; }
ok()   { say "  PASS  $*"; }
bad()  { say "  FAIL  $*"; FAIL=1; }

say "IAEval pre-submission drift check — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
say "Deadline (OpenReview duedate): 2026-08-30 12:59 UTC / 05:59 PT"
say ""

# (a) workshop site byte-identity
say "[a] workshop site"
if curl -sSL -o "$TMP/site.html" https://eval-interactive-agents-workshop.github.io/; then
  sha=$(shasum -a 256 "$TMP/site.html" | awk '{print $1}')
  bytes=$(wc -c < "$TMP/site.html" | tr -d ' ')
  if [ "$sha" = "$SITE_SHA_BASELINE" ]; then ok "sha256 unchanged ($bytes bytes)"
  else
    bad "sha256 CHANGED: $sha ($bytes bytes, baseline $SITE_BYTES_BASELINE) -> RE-READ THE CFP"
    say "  --- new policy-word hits in the changed page ---"
    LC_ALL=C tr '<' '\n' < "$TMP/site.html" \
      | grep -i -E 'ai-generat|llm|language model|detect|disclos|plagiar|policy|ethic|integrity' \
      | sed 's/^/    /' | head -20
  fi
else bad "site fetch failed"; fi

# (b) submission invitation: form fields + last-modified
say "[b] OpenReview Submission invitation"
if curl -sS -o "$TMP/inv.json" 'https://api2.openreview.net/invitations?id=NeurIPS.cc/2026/Workshop/IAEval/-/Submission'; then
  python3 - "$TMP/inv.json" "$INV_TMDATE_BASELINE" "$INV_KEYS_BASELINE" <<'PY'
import json,sys,datetime
inv=json.load(open(sys.argv[1]))['invitations'][0]
base_tm=int(sys.argv[2]); base_keys=sys.argv[3].split()
keys=sorted(inv['edit']['note']['content'])
iso=lambda ms: datetime.datetime.fromtimestamp(ms/1000,datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
fail=0
if keys==sorted(base_keys): print("  PASS  form fields unchanged: %s"%' '.join(keys))
else:
    print("  FAIL  form fields CHANGED: %s"%' '.join(keys))
    print("        added: %s"%(set(keys)-set(base_keys)))
    print("        -> an AI-disclosure field may now exist. READ THE FORM.")
    fail=1
tm=inv['tmdate']
if tm<=base_tm: print("  PASS  tmdate unmoved (%s)"%iso(tm))
else: print("  FAIL  tmdate MOVED to %s -> invitation edited since sweep"%iso(tm)); fail=1
print("  INFO  duedate %s | expdate %s"%(iso(inv['duedate']),iso(inv['expdate'])))
sys.exit(fail)
PY
  [ $? -ne 0 ] && FAIL=1
else bad "invitation fetch failed"; fi

# (c) venue group instructions must still be empty
say "[c] OpenReview venue group instructions"
if curl -sS -o "$TMP/grp.json" 'https://api2.openreview.net/groups?id=NeurIPS.cc/2026/Workshop/IAEval'; then
  python3 - "$TMP/grp.json" <<'PY'
import json,sys
g=json.load(open(sys.argv[1]))['groups'][0]
ins=g['content'].get('instructions',{}).get('value')
if ins=="": print("  PASS  instructions still empty string")
else: print("  FAIL  instructions NOW SET: %r"%ins); sys.exit(1)
PY
  [ $? -ne 0 ] && FAIL=1
else bad "group fetch failed"; fi

# (d) site repo commits
say "[d] site repo HEAD"
AUTH=(); [ -r "$HOME/Desktop/Keys/personal_github_key.txt" ] && \
  AUTH=(-H "Authorization: Bearer $(tr -d '\n' < "$HOME/Desktop/Keys/personal_github_key.txt")")
if curl -sS "${AUTH[@]}" -H 'Accept: application/vnd.github+json' \
     -o "$TMP/commits.json" \
     'https://api.github.com/repos/eval-interactive-agents-workshop/eval-interactive-agents-workshop.github.io/commits?per_page=5'; then
  python3 - "$TMP/commits.json" "$REPO_HEAD_BASELINE" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); base=sys.argv[2]
if isinstance(d,dict): print("  WARN  github: %s"%d.get('message')); sys.exit(0)
head=d[0]
if head['sha'].startswith(base): print("  PASS  HEAD unchanged %s (%s)"%(base,head['commit']['committer']['date']))
else:
    print("  FAIL  NEW COMMITS since %s:"%base)
    for c in d:
        if c['sha'].startswith(base): break
        print("        %s %s %s"%(c['sha'][:10],c['commit']['committer']['date'],c['commit']['message'].splitlines()[0]))
    sys.exit(1)
PY
  [ $? -ne 0 ] && FAIL=1
else bad "github fetch failed"; fi

say ""
if [ $FAIL -eq 0 ]; then
  say "RESULT: no drift. The 2026-08-28 finding (no AI/LLM/detection policy anywhere in"
  say "the CFP, the OpenReview form, or the venue group) still holds. Submit."
else
  say "RESULT: DRIFT DETECTED. Do not rely on the 2026-08-28 finding — re-read whatever"
  say "changed (CFP text / submission form fields / group instructions) before submitting."
fi
exit $FAIL
