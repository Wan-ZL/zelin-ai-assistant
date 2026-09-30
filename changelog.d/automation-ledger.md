type: added
- **自动行为总账（CONTRACT §81，issue #451，owner 决策 D83）**：「这个软件现在到底有哪些东西在自动跑、开关在哪、动手留没留痕、能不能撤」自此有唯一答案 —— `act/lib/automation.py` 的 `LEDGER`。一条行为一行，带处置（keep / merge / retired）与一句理由。命令行看：`python3 -m act.lib.automation --list`；问某一条现在开着没有：`--enabled <slug>`（出口码 0 = on / 3 = off / 2 = 不认识，与 §48 的源开关 CLI 同款）。
- **动手的自动行为会留一行回执**：免批派发、回收站硬删、冷卡封存、静默并入、worktree 回收、孤儿贴图清理、欠账展开各自在 `state/automation.jsonl` 落一条 `{ts, slug, action, …}`，出生即带 1MB 自压缩帽。与 analytics 无关 —— 关掉用量统计不会让回执消失。
- **五把新开关**（设置页「Feature flags」区，actd 每 pass 现读、不用重启）：`features.merge_silent`（近重复静默并入）、`features.worktree_sweep`（worktree 自动回收，原来只有环境变量）、`features.attachment_gc`（孤儿贴图清理）、`features.raising`（欠账卡自动展开）、`features.ingest`（cron 链里最贵的 headless 笔记加工）。这五条以前**一把开关都没有**。
- **新的 QA 硬门**：`scripts/qa/automation_check.py`（并入 `run_gates.sh`，第七道）把总账的四条不变量钉成可执行判据，存量欠账明账挂 `qa/automation_baseline.txt`（shrink-only）。调度器完备性对 launchd plist、crontab 行、带 `schedule:` 的 GitHub workflow 三个源互为子集校验 —— 以后谁加一条定时任务却不登记，门当场红。
