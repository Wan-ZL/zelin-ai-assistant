# 审批卡片模板（Slack DM）

发送到 `config.owner.slack_user_id`。桌面 Slack 与手机推送是同一条消息，✅/❌ reaction 或 💬 文字回复均可。

> **这一页是历史件**：Slack 审批通道自 v0.21 起退役（审批只在看板里做），随它一起退役的
> `config.approval.poll_interval_minutes` 已于 §81 / D83（issue #451）从配置层删除 —— 它的
> 解析分支是一句字面空操作，属于「说的和做的不一样」的开关。主循环的真间隔是
> `approval.poll_interval_seconds`（不写 = 10 秒）。

```
🔔 *Zelin's AI Assistant · 审批卡片 #{id}*  `{tier} · {tier_hint}`

*需求*: {title 一句话}
*来源*: {who} — {每个源: 渠道+日期+原话摘引}     ← 原话回显 = 防 ASR 误听，点 ✅ 即完成人工核实
*分级*: {硬/软 directive} · {⏰ deadline（若有，含剩余天数）} · {repeated ×N（若 ≥2）}
{*分歧*: 仅当该项与 Zelin 已声明分歧相关时显示，红色提示}
*计划*: {编号步骤，3-5 条}
{*成本*: 仅当 > $5 时显示；> $50 时此卡为 T2，需文字回复确认}
*产出*: {交付物清单}

👉 ✅ = 开工 ｜ ❌ = 不做 ｜ 💬 回复 = 修改方向
```

规则：
- T0 任务不发卡（产物进周一 digest）
- 纯重述需求合并进已有条目不发卡；含增量 → 发"改进卡"，首行标 `↳ 改进 #{父id}` 并列出 delta
- green_sign_required 的条目：卡片附"此产出对外前需 manager green sign"提示，执行只到 draft 为止
- 卡片发出后回填 registry 条目的 `card.slack_ts`，审批轮询按 ts 读 reaction/回复
