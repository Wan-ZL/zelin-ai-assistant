# Structural blind spots — fixed lines in every report (never filled, never red)

A single-run measurement skill cannot see these. They are printed under `## Structural blind spots` in every report so nobody mistakes a green report for "the UI is good":

- **Design quality / hierarchy / taste** — only ever under `## Opinion (not a measurement)`; never a status, never a rank.
- **Assistive-technology output** — the accessibility tree is measured; what VoiceOver/NVDA actually announce is not heard.
- **Behavioural correctness of controls** — clicking Approve must approve; that is test-code's e2e layer.
- **Native macOS runtime** (AX tree, AppKit computed styles) — under D3 the native app is frozen source; only `extract_native_inventory.py` speaks for it.
- **Motion, easing, scroll physics, haptics, sound** — clocks are frozen and animations disabled by design.
- **Undeclared interaction states and undeclared feature flags** — the driver captures rest state and the declared flag set.
- **Real-data layouts** — never shot; the static-name filter hides dynamic text on purpose.
- **Cross-machine rendering** — goldens are machine-bound (`<platform>-<engine>-dpr<n>`), fingerprint recorded; another machine's goldens are UNAVAILABLE, not compared.
- **Color perception beyond contrast arithmetic** — ratios are computed, palettes are not judged.
- **Translation quality beyond zh/en pair presence**.
- **Performance** unless Lighthouse is present (table-only).
- **No feedback channel** — a single-run skill cannot replace telemetry or user reports (P5's job).
