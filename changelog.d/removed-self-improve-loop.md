type: removed
- The daily loop no longer reads GitHub issues, PR checks or the nightly mutation report, and no longer mints 🤖 cards from them.
- The automatic draft-PR lane is removed: auto-approval, PR follow-up cards, the needs-owner-eyes pause and Resume banner, the two Developer settings toggles, and the delivery-verified chip.
- Old `self_improve:` config and overrides keys are ignored. Old lane cards become ordinary cards; auto-approved cards that never started go back to Backlog, and lane runs whose session died are moved to Review instead of being resumed. Materials cards still run without MCP servers. Materials proposals now need `daily_loop.materials_enabled: true` (default off).
