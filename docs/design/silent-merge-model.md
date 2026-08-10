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

**修复**（`act/lib/silent_merge.py::execute`）：把 `append_fold_note` 的去重返回
值用作天然幂等标记——返回 None ⇔ fold 半程已在前一次（crash 前）落盘 ⇒ 跳过全部
计数增量，只补完 trash 半程（analytics 记 `outcome="ok_retry"`）。判例：
`tests/test_silent_merge.py::test_crash_retry_never_doubles_the_fold`。

## 模型的边界（诚实声明)

- 单 pair 单 job：`MAX_OUTSTANDING` 并发上限没有建模（它是节流不是安全性）。
- 时间被抽象成非确定的 sweep 触发；PENDING_TIMEOUT_MIN 的具体数值不影响安全性。
- §44.2 triage 内联复核与 §44.3 briefing 投递窗不在此模型内——它们各有测试判例；
  下次改这两段协议时值得扩展模型。
