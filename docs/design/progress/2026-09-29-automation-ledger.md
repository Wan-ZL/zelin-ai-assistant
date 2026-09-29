pr: `ai/self-improve/R-236`（issue #451）
phase: 横切（自动化层重新设计；owner 决策 D83）
law: §81（新增）/ §9 追记（出厂默认）/ §16 追记（五把新 flag + 热开关通则）/ §44 追记（三处共用一闸）/ §75 追记（回收有了配置开关）

owner 原话「当前软件有很多自动的东西。我觉得太多了，有优化的空间。需要整理出来后重新设计。去掉冗余设计」。2026-09-23 的只读审计数出几十条无人值守行为，散在 launchd / crontab / actd 主循环 / `server/` / 两个 Mac 壳 / GitHub Actions 六处，**没有一处**能回答「它们现在哪些开着、开关在哪、动手留没留痕、能不能撤」。

本轮做的是 §48 对三个雷达源做过的那件事，射程推广到全部自动行为：新 `act/lib/automation.py` 的 `LEDGER` 是唯一真源（一条行为一行，带 `verdict` / `why` / `merged_into` 三列回答 issue 第 1 问），`enabled()` 每次现读配置，`audit()` 往带帽的 `state/automation.jsonl` 落回执。四条不变量由新的第七道 QA 硬门 `scripts/qa/automation_check.py` 执法，存量欠账明账挂 `qa/automation_baseline.txt`（shrink-only，`ledger_diff` 按 `qa/*_baseline.txt` 自动接管，零代码改动）。

真改了行为的有五处：(1) `trash.retention_days` 出厂 60 → 0——硬删是整条管线里唯一「自动 + 不可逆 + 动用户数据」的动作，与宪法第 2 条正面冲突，机制不动只翻默认；(2) 六把冷开关转热，名单由 `automation.live_fields()` 派生而不再手抄进 actd 的刷新点；(3) 近重复这一族的三个调度器（入库前折叠判官 / 每 pass 巡检 / 判决落盘）收成一把 `features.merge_silent`——此前关掉「静默并入」只关住了三处里的两处，雷达每轮照样起真判官花钱；(4) 素材库读取器并进 §65.1 那道闸，D57 的「关着时不再产生新的 🤖 卡」自此是真的；(5) 补上五把此前**一把都没有**的开关（静默并入 / worktree 回收 / 孤儿贴图清理 / 欠账展开 / cron 链里最贵的 headless 笔记加工）。

ask 4「写卡 / 删数据 / 花钱默认关」这一轮只真翻了一把（硬删），其余 20 条逐条带理由挂在账本上等 owner 在 PR 上裁——账本只许缩，翻掉一条就是少一行。完备性明写诚实条款：只数得到 committed 的调度器文件（launchd Label / crontab 行变量 / 带 `schedule:` 的 workflow），库内重试循环、后台线程、Swift 壳侧 timer 数不到，门不假装数得到。
