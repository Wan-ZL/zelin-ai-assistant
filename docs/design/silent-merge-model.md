# SilentMerge.tla — §44 静默并入协议的机器验证

`SilentMerge.tla` 是 CONTRACT §44 两段式协议（detached 只读判官 → actd 单写者执行
可逆 fold+trash）的 TLA+ 模型。2026-07-22 那轮对抗审查是人肉穷举并发交错；这份
模型把同一件事交给 TLC 机器穷举——并且真找到了人肉漏掉的一条。

## 跑法

```bash
# 一次性依赖：JVM + tla2tools.jar（都不进运行时，宪法第 7 条不受影响）
brew install openjdk
curl -sSL -o ~/.local/tla/tla2tools.jar \
  https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar

cd docs/design
# 修复后的协议（当前代码）：全部不变量应通过
java -cp ~/.local/tla/tla2tools.jar tlc2.TLC -deadlock -config SilentMerge.cfg SilentMerge.tla
# 回归演示（FixEnabled=FALSE = 修复前的 execute()）：FoldOnce 应给出 5 步反例
java -cp ~/.local/tla/tla2tools.jar tlc2.TLC -deadlock -config SilentMerge_bug.cfg SilentMerge.tla
```

## 模型覆盖什么

- 判官永不写卡（结构性：模型里 Judge 动作不改卡状态）；verdict 是 LLM 输出，
  建模为非确定的 same/separate；卡壳的 pending 由 sweep 扫成 failed。
- execute() 在写者线程上对双卡 fresh 复检（TOCTOU）；单写者语义下一次 execute
  内部的两笔写对用户动作原子——但 **crash 可以落在 save(primary) 与
  trash(secondary) 之间**，此时 job 文件仍是 `judged`，重启后重跑。
- 用户/生命周期并发动作：副卡升列、被批准（离开 LIGHT=invested）、被用户丢弃；
  主卡派发、交付（离开 open）。
- pair ledger 终生一次（Request 守卫）。

## 验证的不变量

| 不变量 | 含义 | 结果 |
|---|---|---|
| `NoInvestedTrash` | 静默并入永不吞掉已投入（approved+）的卡 | ✅ 两种配置下均成立 |
| `Recoverable` | 副卡被并入时：prev_status 已盖章 且 主卡已带 fold（crash-ordering 主卡先落盘 ⇒ 永不丢信息） | ✅ 两种配置下均成立 |
| `FoldOnce` | fold 效果每 job 至多施加一次 | ❌ 修复前违例（下述）→ ✅ 修复后全空间成立（146 distinct states） |

## TLC 找到的真 bug（已修）

**5 步反例**：`Request → JudgeSame → ExecCrash → ExecAtomic`——actd 死在两笔写
之间，job 文件仍 `judged`，重启后 consume_judged 重跑 execute，fold 的计数效果
（`repeated_mentions`、`silent_merge_count`）被二次施加。性质是**膨胀不是丢失**
（fold note 本身早有 (kind, 文本) 去重；sources 合并幂等），但用户可见的计数会
说谎。

**修复**（`act/lib/silent_merge.py::execute`）：主卡 fold notes 里已有
`静默并入 {副卡id}「` 前缀的 `[radar]` note ⇔ fold 半程已在前一次（crash 前）
落盘 ⇒ 跳过计数增量（`silent_merge_count`、副卡整体 mentions 累加），把 trash
半程补完并收敛到 §44.4 终态（crash 窗口内副卡新吸的 sources 幂等补并、EXECUTING
主卡补 briefing；analytics 记 `outcome="ok_retry"`）。幂等键是**副卡 id**而非
note 全文——第一版实现用 `append_fold_note` 的 (kind, 全文) 去重当标记，但 note
嵌着可变的 `display_title`，重启 pass 里 process_inbox / process_raising 先于
consume_judged 改写标题时全文判重落空、fold 照样翻倍（2026-08-18 review 发现的
model-fidelity gap：模型里的 fold 标记是稳定抽象量，实现必须选一个同样稳定的
键才配得上模型的结论）。判例：
`tests/test_silent_merge.py::test_crash_retry_never_doubles_the_fold`、
`test_crash_retry_survives_title_drift`、
`test_crash_retry_keeps_window_gained_sources`、
`test_crash_retry_briefs_a_freshly_dispatched_primary`。

**同日第二轮收敛**（review 发现，实现侧补漏、模型状态空间不变）：

- **标记探测先于状态复检**：crash 窗口不止改标题，还能挪状态——副卡在重启
  pass 早段被批准派发（dispatch_approved 先于 consume_judged）时，旧序在
  LIGHT 复检处直接 False → job 标 done，主卡带着半程合并记账、副卡活着执行，
  **永久半 fold**。现改为检出标记后按双卡现状三分收敛（§44.4：补观测面 /
  补完 / 按拆出语义中止 + `retry_aborted`），绝不静默 done。判例：
  `test_crash_retry_aborts_when_secondary_got_invested`。
- **trash 之后、log_event/回执之前的 crash 窗口**：数据侧终态已达成但观测面
  全丢（digest 少计、§44.6 回执永不发、留一条说谎的 state_moved）。现由
  三分收敛的情形 1 补齐（事件先查后补，防「死在 log_event 与回执之间」的
  更小窗口造成 digest 双计）。判例：
  `test_crash_retry_after_trash_reemits_observability`。
- **briefing 重放**：第一跑排队的 briefing 可能已被 reconcile（先于
  consume_judged）flush 清队，retry 仅查 pending 的去重失效 → 同文本二次
  投递。现 executor.brief 落 `delivered_briefings` 台账（环形 20 条），
  queue_briefing 双重去重。判例：
  `test_retry_briefing_not_requeued_after_flush`。

## 模型的边界（诚实声明)

- 单 pair 单 job：`MAX_OUTSTANDING` 并发上限没有建模（它是节流不是安全性）。
- fold 标记在模型里是稳定抽象量（每 job 一个布尔），卡片**内容**的并发漂移
  （标题被 analyze/用户改写、副卡在 crash 窗口内再吸新 capture）不在状态空间
  里。实现侧的幂等标记必须锚在不随内容漂移的键上（现为副卡 id 前缀）——
  凡把标记搭在可变文本上，模型的 `FoldOnce` 结论对实现不成立
  （2026-08-18 review 判例）。
- 时间被抽象成非确定的 sweep 触发；PENDING_TIMEOUT_MIN 的具体数值不影响安全性。
- **crash 只建模了进程死亡**（kill/断电——写序中断，job 留在 judged 可重跑）。
  `registry.trash` **抛异常**（磁盘满/权限）是另一种失败形态：consume_judged
  记 `execute_failed` 并把 job 钉成 failed，无重试——save(primary) 已落盘时
  留下半程合并（主卡带记账、副卡活着）。这是本模型之外的既有姿态；execute
  幂等化之后对 execute_failed job 做**有界重试**已经安全（重跑会走同一套
  三分收敛），记为 follow-up，本轮不动。
- §44.2 triage 内联复核与 §44.3 briefing 投递窗不在此模型内——它们各有测试判例；
  下次改这两段协议时值得扩展模型。
