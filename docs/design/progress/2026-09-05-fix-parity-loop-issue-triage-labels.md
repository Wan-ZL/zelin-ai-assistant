pr: `fix/parity-loop-issue-triage-labels`（无版本 bump，版本由 tag 派生）
phase: P5 每日自我改进循环的第二次实战校准（behaviour-parity 批次 `loop-issue-triage-labels`，chain loop-labels #1；PR #213 被按住的根因）
law: §70.3 ⑩ 追记（`EXCLUDED_ISSUE_LABELS` / `issue_parked` 摘要 / `skipped.label_parked`）；§70.6 补一句

**为什么**：2026-09-01 的 tracker hygiene（§5.1 / §5.5）给 17 个 issue 贴了分诊标签——`素材库-idea`「产品想法非缺陷，素材库落地后迁入并关」、`needs-owner`「非 owner 作者只做摘要」、`mac-retire`「随 P4 re-home 后再 scope」、`decision-needed` / `proposal`「等 owner 拍板」。但 §70.3 ⑩ 的 issue 读取器只看作者与「do it」评论，一行都不读 `labels`：#23 是 owner 自己开的，于是 09-04 循环把它铸成卡、通道开出 PR #213（一个 owner 明确说过「先进素材库」的产品想法被当成待办实现了）。owner 的分诊结论写在 tracker 上，循环却要 owner 亲手 close 才安静——这是文档与机器的脱节，不是 owner 决策问题。

**做了什么**：`act/lib/loop_inputs.py` 新模块级元组 `EXCLUDED_ISSUE_LABELS = (素材库-idea, needs-owner, wontfix, invalid, duplicate, decision-needed, proposal, mac-retire)`（§5.5 七枚 label 去掉可铸卡的 `loop-seed` / `owner-decided`，加 GitHub 默认三枚「不做」标签）；`parked_label(issue)` 按 `labels[].name` 逐字、区分大小写匹配（`Wontfix` 不算——不猜 owner 的意思）；`_IssueRouter.route` 先看标签再走 D18：命中 → 一行既有非卡对象 `Summary`（新 kind `issue_parked`，text 带 issue 号 / 标题 / 命中标签，ref = url），**零 Signal**、不花「do it」评论额度、标题仍进 `titles`（issue 还开着，`gh_title` 同题去重不变）。`daily_loop._propose` 给审计行 `skipped` 加 add-only 计数 `label_parked`（= `loop_inputs.parked_count(summaries)`），`select_signals` 与四个既有 skip 一字不动。`gh issue list --json` 早已带 `labels` 字段，零多余 gh 调用。

**没做的**：不改 D18 语义（无标签的非 owner issue 仍走「do it」路径）；不删 §5.1 表里的任何行；不给 `issue_parked` 上横幅（与 D18 摘要行一样只活在审计行与 `last_result.summaries` 计数里——不在本批范围）；不动 gh_title 去重。词表是硬编码不是 config——分诊标签是 owner 在 tracker 上的约定，与 `OWNER_LOGINS` 同一层级的事实，不该有第二个可漂移的真源。

**判例**：`tests/test_daily_loop_issue_labels.py`——有标签零 Signal + 一行摘要；同一张去掉标签成 Signal；逐字 / 大小写 / 前缀 / 尾空格八组真值表；八枚标签逐个参数化；标签压过 owner 作者与「do it」且不调 `gh issue view`；gh 可能返回的坏形状（非 list / 无 name / 裸字符串）；整轮 `daily_loop.run` 的审计行 `skipped.label_parked == 1`、`summaries[].kind == issue_parked`、#23 零卡。
