type: changed
- Per-card delivery reports under `deliverables/` are now gitignored. Untracked, they made every card worktree look dirty to the worktree sweeper (CONTRACT §75), which then kept it forever — 20 worktrees were pinned this way. Committing a report on purpose now takes `git add -f`.
