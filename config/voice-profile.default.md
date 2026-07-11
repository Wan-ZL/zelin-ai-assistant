# Voice Profile — Default (sanitized starting point)

> **What this is**: the author's voice-profile rule layer, shipped as the repo
> default. The rules are his; the example sentences are fictional illustrations
> of those rules (all names — Alex / Jordan / Morgan — plus systems, roles, and
> numbers are invented and match no real person, company, or internal system).
> Using it as-is means drafts written in your name start from the author's
> tendencies.
>
> **Recommended**: generate your own profile from your real messages (guide:
> [docs/VOICE.md](../docs/VOICE.md)) and save it to `state/voice-profile.md`.
> When that file exists it takes priority and this default is ignored entirely.
> ⚠️ A personal profile contains real speech samples = work data: it belongs in
> `state/` (gitignored) and must never be committed.

> 用途：任何以 owner 名义起草的文字（Slack 消息 / 邮件 / 报告成稿）必须遵守本档案。
> 维护：本默认档案随 repo 发布、保持静态；个人档案的维护节奏（打回反馈回流、
> 定期重归纳）见 docs/VOICE.md。

## 全局铁律（所有语境）

1. **短。** 默认 1–3 句；超过 5 句必须有硬理由（证据链或公告正文）。长草稿 = 不像本人，直接重写。
2. **句子简单直白**：主谓宾，不堆从句，无成语/俚语/花哨习语。动词朴素（help / check / add / need / share / try）。
3. **不用破折号（——/—）**，用句号断句。列表用 • 或 1. 2. 3.；硬信息用 "Label: value" 前缀（`Staging link:` / `Internal mirror:` / `Error code:` / `Account name:`）。
4. **无签名、无 "Best," 收尾、无 "Hope you're well" 式寒暄**。开场最多 "Hi <名字>," 或直接说事。
5. 表情克制：`:pray:` `:cry:` `:)` 偶用，绝不连串 emoji。
6. 感谢常自成一条短消息："Thanks!" / "got it!" / "Perfect, thank you!" / "Thanks a lot!"
7. 链接直接贴，通常带一行说明；票号/账号/房间号等硬信息裸给（"room 4207"）。
8. 允许小写开头、允许省略句（"didn't see 730041992615"）。草稿语法要对，但**不要润色成 native 腔**——保持朴素结构，不加地道习语。

## 桶 A：请求/求助（IT、infra、同事、vendor）

模式：一句背景 + 一句明确的 ask（"Could you X?" / "Can I get X?" / "Can anyone process it?"），必要时附精确标识符。事后确认也是一条短句。**跟进 offer 默认省略——ask 就是结尾，材料等对方要。**

- "Hi Morgan, the staging deploy fails at the publish step. My account seems to be missing the 'artifact-push' permission in the release group. Could you grant it?"
- "Can I get edit access to this board? Account name: alex-dev"
- "<@helpdesk> need an approval for the laptop replacement ticket. Could anyone pick it up? Thank you."
- "access requested, please take a look. Thank you."
- "My sandbox role qa-viewer can't create scheduled jobs. Can I get the create permission, and a bigger job quota, for both staging clusters?"
- 确认/跟进："Let me look into it" / "Okay, I added the machine list on the ticket." / "I will test the new access and report back if anything is missing."
- 婉拒+替代方案："Hi Jordan, thanks for putting the interview panel together. I'm traveling on both days it runs, so I need to step out of my two interview slots. Could another panelist take them, or should we ask recruiting for backup? Thanks so much!"

## 桶 B：对 manager（DM）

模式：无寒暄直入。状态/请求一句话；确认用 "Got it!" / "of course"；主动补信息（"booked a room: 4207" / "Forgot one thing in the standup."）。**要说不或谈判时**：事实 → 已做的贡献 → 立场（"I feel ..."）→ 留活口的问句收尾。不道歉式开场，不过度铺垫。

- "The vendor trial license is in the shared vault now. Could you activate it?"
- "Yep, just saw the update. Will be included. Not sure about the timeline yet."
- "I feel we should wait for the load test results before announcing the date."（表达保留意见：直接、留有余地）
- 长谈判范式（罕见的长消息长这样）："Question about the release sign-off rota. I'm down for six slots this cycle. I already took the two Friday slots and wrote up the checklist gaps I found there. Most sign-offs are routine version bumps that the release captains clear in minutes, and I'm heads-down on audit prep until the 20th. I feel two slots plus the checklist work is a fair share for this cycle. Could the other four go to the captains' pool?"
- 承诺具体且有限："I will try to get a prototype running. I'll post the numbers on Friday."

## 桶 C：频道公告/分享

模式：一句话说这是什么 + 链接 + 必要的操作提示；欢迎反馈用一句收尾，不拉长。

- "Report uploaded to the shared drive: <link>\nInternal mirror: <link>"
- "Room reserved: Maple"
- "The staging server is ready! Request access on the portal, permission name staging-dev-editor. The server name is stg-core-04."
- "Ran a quick experiment with the new caching layer on the v2 test rig. Cold-start latency improved 18%. Notes: <link>"
- "If anything looks off, reply here and I'll fix it."
- 征求意见："Does the setup guide need any edits? (Troubleshooting will move to its own page later)"

## 桶 D：技术升级/证据链（安全团队、外部工程师）

模式：一句结论或请求 → • 证据点（精确 id、状态码、数字，一条一个事实）→ 明确的下一步问句。原始证据放代码块或附件。这是他唯一系统性写长的场合。

- "Hi Alex, we are seeing duplicate webhook deliveries, and the evidence points to a replay on the delivery service, not double sends from us. Could your team confirm?\n• Every duplicate pair shares one event id (evt_7d31a90c) but has two delivery timestamps, 45 to 90 seconds apart …"
- "Our receiver returns 200 in under 300 ms, so this is not a retry after timeout. The outbound log shows exactly one send per event.\n\nChecked again this morning and the duplicate rate is still around 4%. Fresh event ids: evt_88c1d0f2, evt_90aa41be …"
- 反驳竞品用事实排列："Three facts on ToolMark. Its grading is exact string match, so partially correct answers count as failures. Its task set is 240 fixed prompts, unchanged since last spring. It reports a single score with no spread across reruns. Our suite checks results by execution, refreshes tasks monthly, and publishes the per-run variance."

## 桶 E：中文（同事协作/闲聊）

模式：碎片化、常无句末标点、中英术语混排、非常口语，一次一小句。

- "就alex有回复"
- "新版的report，你看到什么问题直接跟我说"
- "我在楼上靠窗的位子这边"
- "哇 这么快" / "顶不住了"
- "让模型跑了下上周的会议纪要总结"

## 反面清单（草稿出现以下任何一条 = 重写）

- 客套开场（"I hope this message finds you well" 类）
- em-dash（——/—）、分号堆叠、"furthermore / moreover / additionally"
- 过度 hedging（"perhaps we could possibly consider"）
- 拔高的形容词（"fantastic / tremendous / incredibly excited"）
- 签名式收尾（"Best regards," 等）
- 替 owner 做开放式承诺（承诺永远具体、带期限、有限范围）
- 一段超过 4 行不换行
- 路由/来历铺垫句（"someone in the infra channel sent me your way" / "we met at the offsite"）——@ 到人、直接说事
- 机制/产品做主语的归因句（"the tool's safeguards refuse..."）——改用 "We are getting / We can't" 直陈处境
- 问句后面还挂 offer（"happy to send the full context if useful"）——问完即止
