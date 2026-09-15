pr: `feat/worktree-gc`（issue #315）
phase: P6 自动草稿 PR 通道 + P5 每日循环（owner 决策 **D66**）
law: 新增 §75；§2 / §49 / §65.3 / §65.5 / §68.1 / §70.1 六处追记（全部 add-only）

`claude --bg` 每派一个会话就在 `<repo>/.claude/worktrees/<name>/` 隔离出一份完整 checkout，而在这个 PR 之前**没有任何一处代码删过它们**——`git grep "worktree remove|worktree prune"` 在 `act/ server/ scripts/ shell/` 上零命中，`self_improve._accept_merged` / `_reject_closed` 只写 registry，`daily_loop.run` 三段全是 registry-only，`housekeeping` 只扫回收站 / archive / 附件孤儿。owner 的生产 checkout 2026-09-09 报 30 个（最老的是 7/14 的），写这条法时 190+，每个都是一份源码加 `web/node_modules`。

新模块 `act/lib/worktrees.py` 把「谁删得」写成一份判决：硬边界只碰 realpath 之后严格落在 `<repo>/.claude/worktrees/` 之内的路径；守卫是 `main` / `unmanaged` / `missing` / `locked` / `live`（某张 approved·executing·review 卡的会话 cwd）/ `dirty`（`git status --porcelain` 非空，**读不到也算脏**）/ `unpushed`（`git rev-list --count HEAD --not --remotes` > 0，**数不出来一律当有**）；够格删的三个理由是 `merged`（已并进远端默认分支）/ `gone`（分支在任何远端上都没有同名引用）/ `stale`（14 天没动过），三者都先过 2 天的年龄地板——刚建出来、还没提交过任何东西的 worktree 在「分支不在 origin 上」这一条上恒真，没有地板就会误删一个正在起跑的会话。「动过」不只看目录 mtime（它只在顶层增删时才变），而是目录 / 顶层各条目 / `.git` / gitdir 的 `index`·`HEAD`·`logs/HEAD` 里最新的那个。

三个触发点：§65.5 的两条 owner 出口落账之后各调一次 `release`（只删这张卡自己的那一个，best-effort，失败绝不带累「PR 已合并 = 验收」这条落账）；每日循环多一段 `worktree_sweep`（在 `stale_sweep` 之后、`proposals` 之前，计数进 `last_result.worktrees`）；设置页开发者区一行 + 一颗按钮（`GET /api/worktrees` 走与 §72.1 录制磁盘同款的「立刻回缓存、后台线程重算」形制，`POST /api/worktrees/cleanup` 同步跑；两条都经 `server/subproc` 起 `python -m act.lib.worktrees`，server 不 import act）。CLI 另有 `--dry-run`（一个字节都不动）给人手动看。

三处刻意的取舍写进 D66：`git worktree prune` 是仓库全局的，owner 机器上另有 142 个手工 worktree 挂在别的目录树下，所以只在每条登记路径都还在时才 prune；永不 `git branch -D`——`-d` 被拒绝正说明那条分支还有没落地的提交（squash merge 的常态），目录删掉、分支留着；占用对托管根跑一次 `du -sk` 而不是每条一次，量不到报 `null` + `bytes_partial` 而不是 0。顺带修了 §65.3 那句「不核对本地 worktree（agent 的 worktree 归 claude 管）」——它本意是「核验不拿本地目录当证据」（保留），却被当成了「worktree 的生命周期没有主人」，正是 190 个的由来。
