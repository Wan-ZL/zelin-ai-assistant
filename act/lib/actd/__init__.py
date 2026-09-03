"""act.lib.actd — the daemon's pass logic, split by phase (P3b, CONTRACT §58.4).

`act/actd.py` stays the entrypoint (``python -m act.actd``), the owner of the
entry-layer collaborators (executor / analyze / merge_review / radar_claude_sessions)
and the compatibility surface every test and module imports; the work of one
pass lives here, one module per phase of the loop described in its docstring:

  seam          the ``Daemon`` namespace snapshot the facade hands down (§58.3)
  session       live-session plumbing shared by inbox / merge / reconcile (§11 §37 §46)
  triage_guard  §34bis proposals-triage preset + registry snapshot guard
  inbox         (a)  drain state/inbox decision files (§5.4 §10 §22 §29 §38)
  decisions     (a)  the card-level verb whitelist behind ``_apply_decision`` (§10 §32.2)
  merge         merge-review actd side + job housekeeping (§21)
  dispatch      (a') auto-dispatch gate, (b) dispatch, raising expansion (§4 §34bis §51 §65)
  reconcile     auto-resume / harvest / steer flush of executing sessions (§13 §44.3 §46)
  housekeeping  trash purge, auto-archive, attachment GC (§4 §9 §10)
  alerts        transition notifications, auth scan, radar liveness (§40 §48)

Layering (防腐 #2): nothing in this package imports ``act.actd`` or any other
entry module — collaborators arrive through ``seam.Daemon``.
"""
