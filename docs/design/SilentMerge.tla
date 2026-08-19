------------------------------ MODULE SilentMerge ------------------------------
(***************************************************************************)
(* Formal model of the CONTRACT §44 silent-merge two-phase protocol        *)
(* (act/lib/silent_merge.py): a detached READ-ONLY judge writes a verdict  *)
(* into a job file; actd's single writer thread consumes judged jobs and   *)
(* performs the reversible fold+trash. This spec model-checks the safety   *)
(* claims the 2026-07-22 adversarial review verified by hand, plus the     *)
(* crash window between execute()'s two registry writes.                   *)
(*                                                                         *)
(* Modeled faithfully:                                                     *)
(*   - the judge never mutates cards (structural: no card writes here);    *)
(*   - execute() re-checks BOTH card states on the writer thread (TOCTOU); *)
(*   - registry writes inside one execute() are atomic w.r.t. user actions *)
(*     (single-writer thread) — but a CRASH can land between save(primary) *)
(*     and trash(secondary); the job file then still reads "judged", so a  *)
(*     restarted actd re-runs execute();                                   *)
(*   - the pair ledger admits one judge request per pair, ever;            *)
(*   - trash is reversible: prev_status is stamped on every trash.         *)
(*                                                                         *)
(* Checked invariants:                                                     *)
(*   NoInvestedTrash — silent-merge only ever trashes a card that was in   *)
(*                     LIGHT_STATES at execute time;                       *)
(*   Recoverable     — a merge-trashed secondary always has prev_status    *)
(*                     stamped AND the primary already carries the fold    *)
(*                     (crash-ordering primary-first: no information loss);*)
(*   FoldOnce        — the fold lands on the primary at most once per job. *)
(*                                                                         *)
(* Result (TLC, 2026-07-26): with FixEnabled = FALSE, FoldOnce is          *)
(* VIOLATED via Request -> JudgeSame -> ExecCrash -> ExecAtomic: the       *)
(* crash-retry re-applies the fold (duplicate note, repeated_mentions and  *)
(* silent_merge_count inflated). With FixEnabled = TRUE (execute() skips   *)
(* the fold half when the primary already carries this job's fold marker   *)
(* — the code fix shipped alongside this spec), all three invariants hold  *)
(* over the full state space. No information is ever lost either way; the  *)
(* bug class is duplication, not loss.                                     *)
(***************************************************************************)
EXTENDS Naturals

CONSTANT FixEnabled            \* TRUE = model the idempotence guard in execute()

VARIABLES
  pStatus,     \* primary card:   "card_sent" | "executing" | "delivered"
  sStatus,     \* secondary card: "detected" | "card_sent" | "approved" | "trashed"
  sPrev,       \* secondary's prev_status stamp ("none" until first trash)
  trashedBy,   \* "none" | "user" | "merge" — who trashed the secondary
  foldCount,   \* times the fold landed on the primary (mentions/sources/note)
  job,         \* "none" | "pending" | "judged" | "judged_sep" | "done" | "failed"
  ledger       \* pair judged-once ledger: 0 | 1

vars == <<pStatus, sStatus, sPrev, trashedBy, foldCount, job, ledger>>

Light  == {"detected", "card_sent"}          \* LIGHT_STATES (uninvested)
OpenP  == {"card_sent", "executing"}         \* primary must still be open

TypeOK ==
  /\ pStatus \in {"card_sent", "executing", "delivered"}
  /\ sStatus \in {"detected", "card_sent", "approved", "trashed"}
  /\ sPrev \in {"none", "detected", "card_sent", "approved"}
  /\ trashedBy \in {"none", "user", "merge"}
  /\ foldCount \in 0..2
  /\ job \in {"none", "pending", "judged", "judged_sep", "done", "failed"}
  /\ ledger \in 0..1

Init ==
  /\ pStatus = "card_sent"
  /\ sStatus \in Light
  /\ sPrev = "none"
  /\ trashedBy = "none"
  /\ foldCount = 0
  /\ job = "none"
  /\ ledger = 0

(* -- auto_merge: rule hit files a detached check (pair-once ledger) ------- *)
Request ==
  /\ job = "none" /\ ledger = 0
  /\ sStatus \in Light /\ pStatus \in OpenP
  /\ job' = "pending" /\ ledger' = 1
  /\ UNCHANGED <<pStatus, sStatus, sPrev, trashedBy, foldCount>>

(* -- the detached judge: READ-ONLY on cards; verdict is an LLM output, so  *)
(*    nondeterministic same/separate. A stuck judge is swept to failed.     *)
JudgeSame     == /\ job = "pending" /\ job' = "judged"
                 /\ UNCHANGED <<pStatus, sStatus, sPrev, trashedBy, foldCount, ledger>>
JudgeSeparate == /\ job = "pending" /\ job' = "judged_sep"
                 /\ UNCHANGED <<pStatus, sStatus, sPrev, trashedBy, foldCount, ledger>>
SweepTimeout  == /\ job = "pending" /\ job' = "failed"
                 /\ UNCHANGED <<pStatus, sStatus, sPrev, trashedBy, foldCount, ledger>>

(* -- actd consume_judged, single writer ----------------------------------- *)
ConsumeSeparate ==
  /\ job = "judged_sep" /\ job' = "done"
  /\ UNCHANGED <<pStatus, sStatus, sPrev, trashedBy, foldCount, ledger>>

ExecGuard == sStatus \in Light /\ pStatus \in OpenP      \* execute() re-check

\* fold half runs unless the fix detects this job's marker already on the card
FoldWanted == IF FixEnabled THEN foldCount = 0 ELSE TRUE

ExecAtomic ==                    \* the normal path: fold + trash, one thread
  /\ job = "judged" /\ ExecGuard
  /\ foldCount' = IF FoldWanted THEN foldCount + 1 ELSE foldCount
  /\ sPrev' = sStatus /\ sStatus' = "trashed" /\ trashedBy' = "merge"
  /\ job' = "done"
  /\ UNCHANGED <<pStatus, ledger>>

ExecCrash ==                     \* actd dies between save(primary) and trash:
  /\ job = "judged" /\ ExecGuard \* fold landed, job file still says "judged"
  /\ FoldWanted                  \* (with the fix, a marked card skips the fold
  /\ foldCount' = foldCount + 1  \*  half, so this crash window closes too)
  /\ job' = "judged"
  /\ UNCHANGED <<pStatus, sStatus, sPrev, trashedBy, ledger>>

ExecSkip ==                      \* states moved since the check was filed
  /\ job = "judged" /\ ~ExecGuard
  /\ job' = "done"
  /\ UNCHANGED <<pStatus, sStatus, sPrev, trashedBy, foldCount, ledger>>

(* -- concurrent user / lifecycle moves (inbox actions, same writer thread, *)
(*    but freely interleaved BETWEEN protocol steps)                        *)
SPromote  == /\ sStatus = "detected" /\ sStatus' = "card_sent"
             /\ UNCHANGED <<pStatus, sPrev, trashedBy, foldCount, job, ledger>>
SApprove  == /\ sStatus = "card_sent" /\ sStatus' = "approved"   \* invested!
             /\ UNCHANGED <<pStatus, sPrev, trashedBy, foldCount, job, ledger>>
SUserTrash == /\ sStatus \in Light \/ sStatus = "approved"
              /\ sStatus # "trashed"
              /\ sPrev' = sStatus /\ sStatus' = "trashed" /\ trashedBy' = "user"
              /\ UNCHANGED <<pStatus, foldCount, job, ledger>>
PDispatch == /\ pStatus = "card_sent" /\ pStatus' = "executing"
             /\ UNCHANGED <<sStatus, sPrev, trashedBy, foldCount, job, ledger>>
PDeliver  == /\ pStatus = "executing" /\ pStatus' = "delivered"
             /\ UNCHANGED <<sStatus, sPrev, trashedBy, foldCount, job, ledger>>

Next ==
  \/ Request \/ JudgeSame \/ JudgeSeparate \/ SweepTimeout
  \/ ConsumeSeparate \/ ExecAtomic \/ ExecCrash \/ ExecSkip
  \/ SPromote \/ SApprove \/ SUserTrash \/ PDispatch \/ PDeliver

Spec == Init /\ [][Next]_vars

(* ------------------------------ invariants ------------------------------ *)
\* silent-merge never claims an invested card: at trash time it was LIGHT
NoInvestedTrash == (trashedBy = "merge") => sPrev \in Light

\* reversibility + crash-ordering: a merge-trash implies the fold already
\* landed on the primary and the return ticket (prev_status) is stamped
Recoverable == (trashedBy = "merge") => (foldCount >= 1 /\ sPrev # "none")

\* the fold lands at most once per job (violated without the fix: crash-retry)
FoldOnce == foldCount <= 1

=============================================================================
