pr: `ai/self-improve/R-198`（PR #266；self_improve lane 草稿 PR，接管后转 ready）
phase: P5 → P3 闭环（§57 夜间变异存活体 → §70 循环铸卡 → §65 lane 出 PR；R2.3.2「先补测试网」）
law: §44.1 追记（job 记录缺 `id` 的身份回落；清扫边界严格大于）/ §57 靶区映射

**做了什么**：夜间变异报告把 `act/lib/silent_merge.py` 列为存活最多的模块（62 个已跑位点里 38 个存活）。本地全量跑该模块 259 个位点：老映射只带 `tests/test_silent_merge.py`，101 个存活；先把早已存在却从未进靶区映射的 `tests/test_silent_merge_jobs_edge.py` 补进 `qa/mutation_targets.toml`，存活降到 53；再新增 `tests/test_silent_merge_mutation_kills.py`（29 条判例，一条判例钉一个真洞：20 min / 24 h 清扫边界的严格性与常数本身、job 文件 UTF-8 + 2 空格、`SM-` + 8 hex、judge 子进程 detached + stdin 关闭、材料 `display_title` 与 ≤ 6 source、verdict 解析的整段 / 尾缀散文两形、fold note 只在有 brief 时带 brief、计数缺省、crash-retry 三分收敛的四条边、fold 目标搜索跳过关闭卡与越过 linked 命中、CLI / consume 的字面返回值与半消失卡对），存活降到 3——剩下 3 个是等价变异体（`_converge_abort` 的 `and → or` 落在 `mark_note_split` 拒绝的行上；`_bump_counters` 的 `getattr` 缺省值永不被读），理由记在判例文件 docstring。

**唯一的生产改动（3 行）**：补判例时挖出一个真 crash——`state/silent_merge/SM-*.json` 记录若没有 `id` 字段，sweep 的 `_finish` 在 `_write_job` 处 KeyError，整个 actd pass 崩掉（宪法第 11 条）。`_finish` 现以调用方的 job id 补回 `id` 再落盘，缺 id 的记录原地标 failed。接管 review 时逐条把变异体真施加到模块上复核（`>` → `>=`、`[:6]` → `[:7]`、`continue` → `break`、修补行的 `or` → `and`），对应判例全部转红；换回 main 的 `_finish` 跑新判例即 `KeyError: 'id'`。CONTRACT §44.1 追记一段 add-only 法条。同期另一张自提案卡（R-287，PR #267）对同一模块做了同名文件、同一 `_finish` 修补的子集（38 → 2）——本 PR 是其超集，先合并者胜，后者应关闭。

**门**：`compileall`；映射子集 90 条 + 全套件 unittest；ruff 0.15.20；`scripts/qa/hygiene.py` / `depgraph.py` / `ledger_diff.py --base origin/main`；`scripts/ci/changelog_fragments.py check` / `progress_log.py check`；`scripts/qa/mutate.py --modules act/lib/silent_merge.py --force`（结果见 PR body）。
