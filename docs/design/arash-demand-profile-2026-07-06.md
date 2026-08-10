# Arash 需求画像（2026-05-20 → 2026-07-06）

> 生成：2026-07-06，由 vault 全量扫描子代理产出（wiki 人物页 1296 行 + 1on1 wiki ×3 + ~25 份 raw screenpipe/Slack 记录），另经主会话以近 6 周 Slack 全量检索交叉校验。
> 校正（主会话补充）：§五-1 中 "Arash 承诺的 doc 无交付证据" 已过时——该 doc 已于 7/4 以 Confluence 草稿《A benchmark for API-agent trustworthiness in enterprise system (Draft)》（VALUES-DESTROY）在 #llm-training 兑现（vault ingest 只到 7/2 所以子代理没看到）。球现在完全在 Zelin 侧，July 14 倒计时。

**数据源**：`4 - wiki/people/Arash Nourian.md`（1296 行人物页，覆盖至 7/1）、`4 - wiki/1on1/`（Jun 3 / Jun 10 / Jul 1）、`4 - wiki/Agent Harm & API Safety Benchmark.md`、约 25 个 `2 - raw/` screenpipe/Slack 文件。vault ingest 最新到 7/2，7/3–7/6 无新记录。**可信度分级**：Slack DM / #llm-training / Confluence = 逐字可引；1:1 会议音频 = 单麦低保真重构，方向可靠、措辞不可逐字引用（下文标 ⚠️audio）。

---

## 一、派活清单（时间线 + 状态推断）

### 5/20 strategy 1:1（12:31–13:18 PT）⚠️audio
1. **Bench 外享姿态**："give it like four tasks… less than ten… walk both Anthropic and fireworks through those ten. Without showing them dashboard or anything" → done（5/27 建 Anthropic/Fireworks 分开的 Drive 文件夹；6/11 机制化成 sync call）
2. **"we have to close the benchmark like literally probably next week"** + "in three days we should have this"（bench + model）→ 实际 7/1 才 1.0 launch，但 Arash 6/24 主动放宽（见下）
3. 真实 Postman 数据源：10 年 collection history + Kamal 数据；**legal check 前置** → 演化成 synthetic-from-pattern 路线
4. 灾难性遗忘 6 技术清单（LoRA rank / Thinking Machines / **generative replay 首推** / layer freezing / EWC / SI）→ done（Phase G 做了 EWC）
5. 调研中国 OSS reasoning 模型（GLM / Kimi / Qwen）→ done
6. 查 Piyush（Crusoe）outreach → done（6/11、6/22、7/1 三次 call）
7. "**lets sync before you talk to anthropic and fireworks to make sure we are aligned**"（5/20 11:01 DM，verbatim）→ done

### 5/27–5/29
8. **⭐ 7-task SFT taxonomy**（5/27 13:16 DM，verbatim）："Build an SFT dataset from real API lifecycle tasks: OpenAPI editing, endpoint naming, collection generation, test generation, doc generation, change requests, and governance actions." → done（数据集在 /Users/zelin/Projects/data，Phase H 执行）
9. 数据合法路径（5/27 15:38 音频 verbatim）："**I get a real user data, analyze the pattern, then I generate a fake one.**" = synthetic-from-pattern 是唯一合法构造法；enterprise 数据禁训 → 已内化
10. 虚拟卡（5/27 17:20）：费用型资源缺就申请 virtual card → 半落地（Crusoe 加卡被 6/1 "先过 vendor 评估" 否了）
11. **催进展**（5/28 10:08）：数据质量 + 数据集进展，目标"快速 fine-tune 一个很便宜/低延迟的模型"

### 6/1–6/3
12. **Vendor 评估流程**（6/1 ⚠️audio）："I evaluated these four vendors… prepare a doc, justify the reason. And then I review it, and then I'll give you the green signal to go" + "You're gonna be responsible" → 进行中（6/10 认可 stay-Fireworks）
13. 成本姿态（6/1）："we don't have a hard budget for tokens… but try to save"；点名 Fireworks "too expensive"；自己盯 Redash spend dashboard
14. 发表战略（6/1）：white paper → arXiv（"just put it there"）→ conference workshop → 部分 done（blog 7/1 发；arXiv 未见记录）
15. 会议政策（6/1）：每年 ≥1 industry + 1 academic，US 优先 → done（Zelin 6/5 交了 shortlist）
16. **⭐ 6/2 11:45 书面反馈**（#llm-training，verbatim）：① "ship a narrow v0.1 first, then quickly expand… v1.0"；② 确认失败模式 = "execution behavior… not knowledge"，**批准转 RL**；③ "we should optimize against a single canonical evaluator: APIFlow v0.1"（Agent Mode 不当 north star）；④ "Freeze the smallest useful tool set now"、axes "Keep this below 6-8"、"pass rate as the headline metric, with confidence intervals"、首版不搞 judge panels；⑤ 重定义 "done" = 5 条（frozen contract / stable signal / statistically meaningful lift over current SFT ckpt / gains in the right failure modes / private set 不回归）→ done（Phase I 按此走）
17. AI-testing self-maintenance use case（6/2 12:27）："Perhaps this should be one of the use cases to focus in the first batch?" → **疑似被落下**（"first batch" 指什么从未确认，无下文）
18. 6/3 月度 1:1（⚠️audio）：vendor 打分框架（cost 权重最高、deprecation cadence、managed ≈40% margin、"close the vendor thing quickly"）；**blessed 8b-first**；多版本迭代法（v0/v1… 每版一组 eval 维度）；"know vs know-how-to-search" 的 split "You guys need to define that split" → split 定义未见成文

### 6/4–6/11（bench 实时 coaching 周）
19. 6/4 #llm-training：研究 **arena.ai agent-arena 方法论**，给 APIFlow 发布配同等详细度 blog → done（blog 7/1 上线，过两轮外部 review）
20. 6/9 12:29 rubric 升级（verbatim）："I'd not ask Claude to assign a single 'difficulty' label first; I'd ask it to assign orthogonal tags like category, step count bucket, ambiguity level, dependency depth, and likely failure mode. Then engineers can do the final calibration pass… Primary axis: Easy, Long-horizon, Hard, Hard + long-horizon."（他还自己写成 Confluence 设计页发布）→ done
21. **⭐ 6/10 12:37 DM（verbatim）**："You should maintain a training log page as he is saying and document all of those things. **We should also create a system card for every model we fine tune/train**" → 部分（training log 有 RUN_JOURNAL；**system card 模板 "现在就 publish + share with Abhijit" 未见完成证据**）
22. 6/10 1:1（15:41–16:41 ⚠️audio）：1:1 改 bi-weekly；governance "verify with the latest disclosures"；model card 每 iteration 一张（理由：compliance audit + R&D tax credit）；bench 定位 = "you are a funnel that gets all these evals"；踢皮球处理套路（Slack channel + deadline + 公开 pause）；"**Go and fully execute it. Don't wait for me.**"；deadline coaching（"keeping proof, you're not shipping"）；质量 bar = Anthropic "don't come back and say the way you're evaluating doesn't make sense"；validator 别在 chain 上只评 last task（"the model can take the shortcut"）；当面评价 "**really good design, actually. Honestly.**"
23. 6/11 Anthropic thread（verbatim）："walk them through a version of AIBench... **without sharing data or APIBench directly with them**. Treat this as a sanity check with a partner where you set up a regular sync call." → done
24. 6/11 11:39 技术长评（verbatim 核心句）："**you're currently validating value equality, not semantic correctness**" → 强制 JSON schema 结构化输出 + 逐字段确定性校验，LLM verifier 只作 advisory → 大半在 v4.8+ 双验证器实现

### 6/17 working session（⚠️audio，attribution 推断）
25. **小模型新方向**：2B–4B low-cost tool-calling base，候选 VibeThinker-3B，"very quick analysis of this as a baseline"；serve via Fabric Gateway → **疑似被落下**（主线走 27B RL，无 VibeThinker 分析交付记录）
26. **每周 R&D page**（与 WER doc 分开的独立 template）→ **状态不明，需查证**
27. Versioning 纪律：release 时下一版 "already cooking"；**"每个 benchmark 以及 model 发布前必须有 design document — otherwise we're not going to release it"**
28. **API Bible**：API 领域知识库作 post-training external knowledge + "quantifying way" 证明 lift；找 content owner（Shamasis? 名字 ASR 糊）→ **疑似被落下**（外联对象未 confirm，无下文）
29. Engineer dedication："I want him to dedicate, work with you directly" + "I'll craft a [hand-off] task" → 悬空（Arash 侧的球）
30. 数据机制：product dump 进 warehouse → data team schema transformation → 你拿想要的 shape

### 6/23–6/26
31. **⭐ 6/23 ~13:42 对抗式 bench review**（#llm-training 文字，≈11 点）：pass@10≥1 太弱 → **pass@10≥3，跨 seed variance = 一等信号**；canary 要 crypto 强度 + decoy；二元评分 → axis-level 部分分 + step-completion + failure-stage（"对训练尤其重要"）；long-horizon 20–50 步；规模 300–1000 任务 + hidden test set；prompt injection in API response；**缺 5 维 = reliability / efficiency / robustness / generalization / calibration**；internal review 先于 public + 版本号
32. 6/24 10:28 收敛成 **5 层架构**：L1 Core → L2 Reliability → L3 Efficiency → L4 Robustness → L5 Generalization + calibration 横切
33. 6/24 1:1（⚠️audio）：**"Get to the model fast" → AWS managed / EC2，don't self-host**（"hardware choices are not a differentiator"）；"be very careful what data leaves Postman territory"；**benchmark-as-pipeline**（自更新，每季度新版，"the time is now to put stakes in the ground"）；multi-grader ensemble（"distribute the trust"）；solvability oracle 3–4×；"use weaker/smaller models to find the holes"；**"Build the training engine in AWS — it's what I do at AWS"**；先 create demand 再 justify compute；**"If it takes two quarters, it's fine… it'll excite people"**（SWE-bench 类比，质量>速度）；coverage-balance 任务采样（cluster → categorize → 补 gap，"coverage, not quantity"）；**training-owner 新 hire**（"Raja"? 名字未确认）— Zelin 保持 benchmark owner；blog green-light gate
34. 6/26 AWS 定调："**I don't think it's a good idea to have access to aws prod**"（verbatim）→ 单一共享 postman.ai 账号挂现有 OU → done（CLOUDINFRA-4307 已批，账号 7/1 landed）

### 7/1–7/2
35. Leaderboard **meaningful threshold**（~70%）要 justify、必要时提高 → 进行中（写进 1:1 doc "Upcoming"）
36. 发布前**全量最新模型**上榜：缺 Sonnet 5 + GPT-5.6（走 Azure OpenAI，可 request access）；单模型 ≈$112 → 部分：Sonnet 5 ✓ Fable 5 ✓；**GPT-5.6/Azure 无进展**
37. 7/1 第二段 6 组（⚠️audio）：eval-signal 先用 proxy（thumbs up/down + conversation-completion）别造标注；**data schema 定好交 data team populate**；**⭐⭐ CONVERGENCE：eval-dataset 与 benchmark "converge 成 ONE piece, sooner than later" 且要落成 action item**；stakeholder-funnel（成为架构变更第一个被 informed 的人，"limit tools to ~10"，"keep me posted"，"**document everything we discuss**"）；领先架构一个 version；**AGM 2.0 shadow-copy agent**（跨团队来回改 = "a no-no"）
38. **⭐ AgentHarm/API-safety proposal**：围绕 harmfulness/honesty for APIs，"不用 plain-vanilla AgentHarm，改一改；**我明天给你 doc**"，**July 14 出结果** → nascent。（主会话校正：doc 已于 7/4 以 VALUES-DESTROY Confluence 草稿兑现，球在 Zelin 侧）
39. 7/1 17:28 DM（verbatim）："**Make sure you do not share any private data or plans on what we do, roadmap etc without me reviewing first with any partner**" → standing rule
40. 7/1 17:56/17:59 DM（verbatim）："Fable is now available so could you add that to the benchmark as well as Sonnet 5 as discussed. assuming the cost is less than $120? **Let me know the cost**" / "As long as it is reasonable as Sonnet 5, we should include it" → done
41. 7/2 三连（verbatim）：10:00 "check with security to make sure you have access first and not blocked form our side and see how you can unblock"；11:39 "**make sure Fable issue is resolved before you put it in the benchmark**"；11:59 "You do not need to share all the things you know about Fable… Just go with one of the tasks that you get refusal and ask them why this is happening. **I'd advise to not share with them that this is relevant to benchmark.**" → security check 闭环；联系 Anthropic 进行中（张力：Fable 已在榜上，与 11:39 顺序相反）

### Growth 1:1（7/1 13:16–13:28 ⚠️audio）— 标准表述
- Staff 定义："not only what you build, but what others build [on it]…Build things, show impact, across the stack, for a consistent period of time"
- **"Impact has to be real impact… company income / company revenue is the biggie"**（反例 "faking pad"）；无直接 revenue 的工作要找 output metric to track
- Recognition 先 → promotion 后
- **10x AI-leverage**：宁付 $150k 给能用 AI 做 10x 的人，也不付 $100k 给只 utilize ~25% 的人；期望 "a ten-times-better version of yourself with AI"
- **"We are not even at Anthropic level yet"** — Anthropic expectation = shipped，feature request → production "less than a few [days]"
- 派活：看 "Code with Claude" workshop（YouTube）→ Zelin "let me try it"，未确认完成

---

## 二、需求类型分布

| 类型 | 占比 | 典型例子 |
|---|---|---|
| Benchmark/评测设计 | ~40% | 6/23 11 点 review、6/24 5 层架构、pass@10≥3、threshold justify、模型覆盖 |
| 训练/模型 | ~20% | 7-task taxonomy、批准转 RL、8b-first、小模型/VibeThinker、多版本迭代法 |
| 治理/数据/安全 | ~15% | synthetic-from-pattern、enterprise 禁训、system card、training log、AgentHarm、数据出境边界 |
| 对外姿态/信息管控 | ~10% | "Not yet" 外享、"without me reviewing first"、Fable "别暴露 benchmark" |
| Vendor/Infra | ~10% | vendor 评估流程、AWS managed 定调、postman.ai 账号 |
| Career/工作方式 | 贯穿 | 10x-with-AI、Anthropic velocity bar、insight→decision、staff-path |

特征：**benchmark 是他 hands-on 最深的线**（亲自写设计页、发千字 brief、给 4 步 validator 方案）；训练线给方向和验收标准但不下场；治理线是价值观底色，每次主动加码。

## 三、隐含 SLA

1. 他自己：重大决策开会中 80 秒回；低优先级攒 28h–3 天批量回 → 默认别人也分级
2. **催办阈值 ≈1 天**（5/27 下达 → 5/28 10:08 就催）→ 下达后 24h 内要有可见动作或 ack
3. **Ack 即时、交付 1–3 天**；口头 deadline（"3 days"、"next week"）是 urgency 锚点不是硬日期，但**到点要有 tangible progress**，否则触发 "keeping proof, you're not shipping"
4. 绝对标尺 = Anthropic velocity（"less than a few days"）
5. **质量可以换时间**（"two quarters is fine"）— 前提是过程持续可见（版本号、design doc、delta）
6. 他会主动查收 + 要 delta（"share the delta"、"Let me know the cost"）→ 交付物自带 delta 和成本数字，别等他问

## 四、质量标准（什么会让他不满意）

- **"Quality is your signature"**；bench 会受"最 intense"的内外 scrutiny
- **Truly representative**：leaderboard top-2 = 竞品免费营销，数字不实 = 替对手背书
- **Validator-first / 语义正确**："validating value equality, not semantic correctness" 是他最尖锐的技术批评；确定性校验 > LLM judge
- **Variance/reliability 一等公民**：pass@10≥3；缺 5 维会被点名
- **Insight→decision 闭环**："不是为了有 benchmark 而做 benchmark"；已成文进 1:1 doc："Level up on data-driven storytelling: every insight attributes back to a decision"
- **文档化纪律**：design doc = release gate；system card per fine-tune；training log；"document everything we discuss"
- **信息边界零容忍**：不 review 不外发（唯一骂过人的领域）；对 partner 不暴露内部意图
- 不 overclaim；首版别 overcomplicate（≤6-8 axes、no judge panels）；design choice 要自带 risk+mitigation

## 五、"被落下的球"风险清单（按风险排序）

1. **🔴 AgentHarm/API-safety proposal（July 14）**— proposal 未写；（校正：Arash 的 doc 已于 7/4 兑现 = VALUES-DESTROY 草稿）今天 7/6 距 deadline 仅 8 天，球全在 Zelin 侧，最优先
2. **🔴 GPT-5.6 via Azure OpenAI 上榜** — launch 覆盖缺口，需 request access，无进展
3. **🟠 Convergence action item** — Arash 明确要求"落成 action item"，未见成文
4. **🟠 System card 模板 "现在就 publish + share with Abhijit"**（6/10）— 未见发布证据；他给了两个会回头查的理由（compliance audit、R&D tax credit）
5. **🟠 每周 R&D page**（6/17）— 状态不明
6. **🟡 VibeThinker-3B quick analysis**（6/17）— 无交付记录
7. **🟡 API Bible + content-owner 外联**（6/17）— 对象名字都没 confirm
8. **🟡 "first batch" AI-testing use case**（6/2）— 连指什么都没确认，thread 未回
9. **🟡 "Code with Claude" workshop** — 承诺未确认完成
10. **⚪ Arash 侧悬空（要帮他记的球）**：training-owner hire（"Raja"?）、dedicated engineer hand-off task、Fastino 免费 credits、data-science org change 确认 — 影响 Zelin scope，值得 1:1 主动问

## 六、他的工作风格（pipeline 输入面）

- **四条下达通道**：① Slack DM — 短促 directive，常 3 连发、会议间隙成簇（5/11、7/1 傍晚、7/2 各一组三连）；② #llm-training — 千字级书面 brief（6/2、6/23、6/24），实为需求文档，Amazon response-doc 风格 + AI polish（"delegate the writing, own the thinking"）；③ 1:1 口头 — 密度最高但音频最不可靠，常有 "as we discussed today" 指向未录到的段落；④ Confluence — inline comments + 他自己发布的设计页
- **给参考不给答案**：Terminal-Bench paper、Braintrust、Beads、VibeThinker、Fastino、train-llm-repo1.zip、"Code with Claude" — 每个 reference 隐含"去消化并回报 take"的期待
- **Green-light 合同**："evaluate → prepare doc + justify → I review → green signal → you're accountable"；对准备充分的 draft trust-default 秒批
- **他会二次检查的事**（= 他在意的）：数据集进展、delta、cost、模型覆盖、governance 文档、外发边界
- **满意的交付形态**：quantified + delta-first + insight 挂 decision + 版本化；postmortem 式坦诚复盘他明确欣赏

## 对 pipeline 设计的具体启示

1. **捕获层覆盖全部四通道**，1:1 音频 directive 走"**次日书面确认**"环节（recap 发 Arash 或写进 1:1 doc）— 既消歧又制造他喜欢的 documentation
2. **每条 directive 结构化落库**：日期、verbatim、显式/推断 deadline、类型、状态、他给的 reference、Zelin 承诺原话（"Will be added" 这类承诺就是下一个被验收项）
3. **双向承诺追踪**：也追 **Arash 承诺的输入**（"我明天给你 doc"、"I'll craft a task"、hire）— 超时自动生成礼貌 nudge 草稿；AgentHarm doc 就是现成事故
4. **硬 gate 建模**：任何外发/leadership-facing/partner-facing 动作必须过 Arash-review 状态位才能执行
5. **Deadline 语义**：他的日期 = "届时要有 tangible progress 展示"；pipeline 在日期前 1–2 天自动生成 progress delta 报告
6. **24h ack SLA + 1–3 天首迭代**；thread 静默 >5 工作日自动告警
7. **汇报模板内建他的标准**：delta vs 上一版、cost、insight→decision 一栏、版本号、风险+mitigation 一栏
8. **重复主题探测**：他提两次以上的话题（数据进展、variance、模型覆盖、governance 文档、convergence）自动升优先级
9. **常驻工件自动维护**：training log、system card per fine-tune、weekly R&D page、1:1 doc — 他点名的 standing artifacts，每次实验后自动更新应为默认后处理

---
---

# 版本 B：workflow 并行扫描版画像（12 代理 / 33 份 raw 文件独立产出，与版本 A 交叉验证）

# Arash Nourian 需求画像
（数据窗口：2026-05-20 → 2026-07-06；来源 = screenpipe 会议记录 + Slack 全量消息，已跨源去重）

---

## 1. 派活清单时间线（按周，跨源合并）

**图例**：✅ done ｜ 🔄 进行中 ｜ ⚠️ 疑似被落下 ｜ ❓ 状态未知 ｜ 📌 长期约束（非一次性任务）

### Week 5/18–5/24（爆发周：一次 huddle 派出 11 条）
- [5/20] 对 Anthropic/Fireworks 受控分享：4-10 任务 walk through、不给 dashboard、删已发 Slack 消息 — ✅（5/21 打包 zip，5/27 分文件夹上传）〔会议+Slack 双源〕
- [5/20] 对外沟通前必须先与他 sync — ✅（当天 huddle 即 sync）
- [5/20] 3 天内 benchmark v1 + "very very good fine-tuned model"，且下一版储备好、"two steps ahead" — 🔄（bench v0.2 赶上了；模型侧 3 天 deadline 事实上 miss，他未追责但 5/21 即催 "How is ft going? need to have the initial model soon and test in prod"）
- [5/20] "very quick" 拿真实 Postman 数据（10 年 collection 历史）— 🔄（5/27 才联系 Melvin，draft 未发；7/1 演变为 traces/schema 线）
- [5/20] 问 Kamal 的 context-graph 数据可否借用 — ⚠️ 无下文
- [5/20] 当天下午找 legal 确认匿名化数据可训 — ⚠️ 无下文（部分被 6/10 privacy-disclosure 指令覆盖，但那条也没闭环）
- [5/20] 灾难性遗忘多路技术方案（LoRA rank/replay/EWC/freezing）— ✅（Phase G 全套 + 5/27 数字）
- [5/20] 评估下一代基座（GLM-5.1/Kimi 2.6/Qwen）— ✅（全部进 v0.2 leaderboard）
- [5/20] size-vs-performance tradeoff 曲线（lower/upper bound × 参数量）— ⚠️ 未见成型交付物
- [5/20] 查 Piyush outreach（5 年免费额度，替代 Fireworks）— ⚠️ 无下文（Crusoe 线方向类似，Piyush 本人 6/8 反而以 Crusoe PM 身份出现）
- [5/20] 小模型打通 mechanics → 蒸馏压缩大模型能力 — 🔄（Phase G/H 打通）
- [5/21] bench v0.1→v0.2 delta 对比 — ✅（当晚回 8 模型 delta 表）
- [5/21] Slack 四连催：ft 进展 / data pipeline 状态（考虑 legal）/ parallel runs / "consume as much as you can"（credits）+ 让 Ben 向上 raise — ❓ 部分回复未捕获

### Week 5/25–5/31
- [5/26] 汇报 training 进展 + 解释 Serverless 用量激增（Ben 的问题转达）— ❓
- [5/27] **7 类 SFT 数据集 directive**（OpenAPI editing / endpoint naming / collection gen / test gen / doc gen / change requests / governance）— 🔄（102K 条 7 类全覆盖，但首个 ckpt 暴走失败）〔Slack DM 13:16 + 会议音频双源，DM 为 directive of record〕
- [5/27] 数据合成方法论 "I get a real user data, analyze the pattern, then I generate a fake one" — 🔄
- [5/27] 梳理会产生费用的资源报给他（无虚拟卡可申请）— ⚠️ 无 Zelin 回复痕迹
- [5/27] 对 training Slack channel 提议表态（"What do you think?"）— ⚠️ 待办挂着未回（后 #llm-training 实际建立，视为自然闭环）
- [5/28] 催 SFT 数据（"any update on ☝️?"）+ 预期 "very cheap/low latency model fine-tuned quickly" — 🔄
- [5/28] 6/1 前 consolidate 所有散落 markdown/work threads（"don't wanna skim on the potential"）— ⚠️ 无跟进记录
- [5/28] 本周锁定 4-5 个 demo use cases；客户向 comparative benchmarks；3 团队 productivity 指标 + blog — ❓（团队向/产品向，Zelin 仅部分相关）
- [5/29] eval/grader 6 条设计规范（canvas：语义匹配 grader、工具镜像生产、警惕 proxy metrics/reward hacking、看 pass/partial pass）— 🔄 长期约束
- [5/29] Crusoe 信用卡卡点：IT + Crusoe **双线并行**解决 — 🔄（6/4 再催 "their free credits are only available this month"；6/10 仍 blocked）
- [5/29] FYI Shamasis/Uday 资源对接 — ✅

### Week 6/1–6/7（战略定调周：6/1 三连会 + 6/2 书面 spec）
- [6/1→6/2] **APIFlow v0.1 = 唯一 canonical evaluator**：freeze narrow tool contract、deterministic validator-first、6-8 高信号 axes、30-50 public + 20-30 private regression、pass rate + CI 做 headline、批准 SFT→RL pivot、给出 "done" 五条件定义 — 🔄（Phase I 主线）〔6/1 口头 + 6/2 Slack 长消息固化，书面版为 spec〕
- [6/1] release gate：超 SFT baseline 8-12 绝对点 + private holdout 零回归 — 📌
- [6/1] ensemble/orchestrator 架构探索，目标 90%+ — 🔄
- [6/1] 3 层 eval 架构（public bench → Agent Mode compatibility suite → translation layer）；不等 Kamal，先建 compatibility layer — 🔄/❓
- [6/1] 请教 OpenAI Applied AI + Cursor（Composer 经验），grain of salt — ⚠️ 无下文（Composer 报告阅读 ✅）
- [6/1] Connect Sumit（周四 demo、拿文档、guide 他）— 🔄（DM 开了未发）
- [6/1] 对 Anthropic 统一口径要 pre-release access（比照 Cursor）— 🔄
- [6/1] 看 Redash Agent Mode spend dashboard — ❓
- [6/1] **vendor evaluation doc**（4 家横评→他 review→green signal；"You're gonna be responsible"）— 🔄→6/10 实际收敛为 "stay on Fireworks"，doc 疑似不了了之
- [6/1] vendor onboarding POC ~$20K sub-processor 流程 — ⚠️
- [6/1] benchmark 写 paper（arXiv + workshop + 开源）— ⚠️（部分合流到 6/23 blog 线）
- [6/1] 每年 1 行业 + 1 学术会议；Databricks Summit — 🔄（已注册）
- [6/1] 跟进 AI-team AWS 账号 — ✅（7/1 落地，CLOUDINFRA-4307，历时 ~7 周）
- [6/3] vendor 框架加权重（cost 最高）+ 补 deprecation/上新速度维度 + AWS 纳入；"close the vendor thing quickly" — 🔄→降级
- [6/3] 多版本迭代方法论（每版多维 eval、盯 failing 项）— 🔄
- [6/3] 定义 model knowledge vs harness/search 分界线 — ⚠️ 无产出
- [6/3] priorities 清单 + 硬 deadline + ready/not ready 回报 — 🔄（doc 有 priorities 缺硬日期）
- [6/3] 绿卡 employment letter（本周 close，他主导）— 🔄→6/10 发 Kathy — ❓
- [6/3→6/4] RFT vs GRPO 方法指引 + arena.ai methodology："come up with our own methodology... need similar blog post ready with same amount of details" — 🔄

### Week 6/8–6/14（治理 + validator 纠偏周）
- [6/9] Confluence "APIFlow Benchmark Design Thoughts"：Difficulty×Horizon 两轴、每 task 必须有 reference solution、AI pre-label + 工程师 review — 🔄（v4 按此推进）
- [6/10] **training log page + system card per model**（数据来源全留痕，供 IT/Sec/Legal；audit + R&D tax credit）— 🔄〔6/10 DM + 6/11 #brainwave **重复要求 ×2**〕
- [6/10] 核实最新 privacy disclosures（enterprise 数据默认禁训；"you need to verify this"）— ⚠️ Zelin 答 "We can check that again" 后无下文
- [6/10] model card 模板先 publish 一版 + share Abhijit — 🔄
- [6/10] Nihar 的 eval：给 guidelines 不代写，"you are actually perceived as a funnel that gets all these evals" — 🔄
- [6/10] path parameterization 踢皮球：拉 channel + 限期 + 否则公开 pause — ⚠️ "Gotcha, cool idea" 后未见 channel
- [6/10] vendor 关系明确化：POC criteria 或 temporary vendor 二选一，避免 lock-in — ⚠️ 路径未选
- [6/10] 试用 Crusoe free GPUs（free 期到 July）— 🔄
- [6/10] category-guided generation "go and fully execute, don't wait for me" + 用他 doc 里的 rubric — 🔄
- [6/10] bench 打磨到 Anthropic 愿意内部跑 → early access 杠杆 — 🔄
- [6/10] long-horizon 用链式定义；verbosity 不作 metric；unit of task 与 unit of economics 分开 — 🔄
- [6/10] domain engineer one-shot 综合问题清单（"you can't keep going back"）— 🔄 清单未成形
- [6/10] delegate 数据分析给 Marc 的 data team + 翻 #proj-evals-agent-mode 历史 — 🔄（channel 加了；找 Marc 要材料未见）
- [6/10] "next few days" 逼成具体日期写进 1:1 doc；hard requirement 先于 hard date — 🔄
- [6/10] 对照 literature 确保 bench 质量 bar（外部挑不出毛病）— 🔄
- [6/10] 链式 task 合并后 validator 只评最后一步的 shortcut 风险 — ⚠️ 6/11 gap 分析未覆盖
- [6/10] 1:1 改 bi-weekly — ✅
- [6/11] **canary/validator 四步整改**："you're currently validating value equality, not semantic correctness" →(1) strict output schema (2) field-level deterministic validator (3) LLM verifier 仅 advisory (4) 追踪 disagreement cases — 部分 ✅；分歧聚合未建；structured-output 强制 vs Zelin 的 format-neutral 路线是**真分歧悬而未决**
- [6/11] Anthropic regular sync：walk through 一个 version、不共享 data/APIBench、问 research team 是否想参与 — 🔄

### Week 6/15–6/21（小模型转向周）
- [6/17] VibeThinker-3B 快速分析作 baseline，或提 2B-4B 更轻替代 — ❓
- [6/17] **战略转向：2B-4B 小 tool-calling 模型**，bypass general phases 直进 production phase；接入 Fabric gateway（Portkey 一两月内被替换）；目标指标 = auto-mode round-robin 流量占比 — 🔄（6/22 action items 即执行分解）
- [6/17] 复用 VibeThinker RL recipe，"there's no point for you to start recreating this" — 🔄
- [6/17] 向数据源负责人提结构化需求（"this many records, this is the schema... that's what I do"）— ❓
- [6/17] **R&D weekly page**（data/model/bench/infra 四线，"By end of the day" 定模板）— ⚠️ 未见每周执行痕迹
- [6/17] serving workstream（AI gateway 进 Fabric；target = Akshay，可找 Farah）— ❓
- [6/17] Portkey 替换列为工作项 — ❓
- [6/17] model-training × benchmark cycle 耦合成**架构级方案**（"don't go solve a point solution"），可发 blog/paper — 🔄
- [6/17] 给出 timeline；API 知识 pre-train 建议（or 咨询 Kamal）— ❓
- [6/17] bench 持续演进覆盖 Agent-Mode-like（公开 + 私有双轨）— 🔄
- [6/17] 探索现有数据、缺口自造小数据；product dump→warehouse→data team transform 机制 — 🔄/❓
- [6/17] API Bible 调研（必须回答 "what extra things do I want to bring to the models" + "a very quantifying way of showing it"）；**当天**找 Shamasis 并回报 — 🔄/部分完成
- [6/17] Udit/Parth review bench（他承诺找 manager 要 dedicated 人）— 🔄（Parth OOO；dedicated engineer 承诺未落地）
- [6/17] Jira↔GitHub commit 可追溯 — ❓
- [6/17] bench versioning 纪律：**下一版无 design doc 不许发** — 🔄

### Week 6/22–6/28（12 条反馈 + AWS 落地周）
- [6/23] **12 条/5 层 bench 系统反馈**（Slack 书面，"Good work! Here are my initial feedback"）：效率/成本二级指标（tool-call count/latency/error rate/token cost，防 "A 300-step brute-force agent and a clean 6-step agent score the same"）｜solvability oracle pass@10≥3 + 方差一等信号｜多 tool interface 跨接口一致性｜cryptographic canary + decoy｜checkpoint 升为 axis 级 partial credit — 🔄（v2 design 吸收中；多条 ❓）
- [6/23] "we are not going to publish till we complete our internal review/checks. Assign a version number + structure" — ✅/🔄
- [6/24] **AWS managed services，放弃自建**（"self-managing is going to delay you"）；access request "do it today" — ✅（方向接受，账号 7/1 落地）
- [6/24] Fastino/Pioneer 免费 credits 试用 + 研究做法 — ⚠️ 会中 browse 3 分钟后无下文
- [6/24] 数据边界：协议外数据不出 Postman，走 Fabric Gateway 先例 — 📌
- [6/24] 新分工：Raja end-to-end own 训练、Zelin own benchmark — 🔄（**Zelin 当场表异议**"the goal needs to be changed"，分歧待对齐，Raja 未到岗）
- [6/24] bench → 可持续 pipeline（每 1-2 季度自动生成新版；"the time is now"）— 🔄
- [6/24] bench+model pipeline 搬 AWS，"first create that demand and generate that traffic and then we come back" — 🔄
- [6/24] 12 条反馈按 impact-effort 排序、首版吸收几条 — 🔄
- [6/24] failure analysis 展示 failed cases（"give them a big shock"）— 🔄
- [6/24] 任务扩展按 coverage：clustering 找 bucket 不平衡再定向生成（SWE-bench 式）— 🔄 已采纳
- [6/24] ship v1 now 收反馈（定位 "not a wow moment but... be a reference"）；blog 先过他 **green sign** — 🔄
- [6/24] 训练策略书面对齐：每人统一 template 的 system doc / game plan — 🔄
- [6/25] "you and xiaoxiao discuss a plan on Benchmark and Models and let me know what you are planning on each" — ❓
- [6/26] AWS 单账号路线（不建独立 org，mirror postman-data）；message lance.johnson **压周四 deadline** — ✅（7/1 落地；Lumos/Okta 等 Arash 审批）

### Week 6/29–7/6（launch + safety 周；他 7 月上旬 out）
- [6/30] "zip file corrupted? Was this reviewed internally with you, xiaoxiao, and others?" — review 纪律复查
- [7/1] Fable + Sonnet 5 加 benchmark，"assuming the cost is less than $120? Let me know the cost" — 🔄（7/2 Sonnet 5 已报）
- [7/1] 📌 "Make sure you do not share any private data or plans, roadmap etc without me reviewing first with any partner"
- [7/1] **Safety：July 14 出结果**；AgentHarm → API-Harm proposal（"别用 plain-vanilla 的"）— 🔄
- [7/1] 70% leaderboard threshold 要 justification — ⚠️ 无后续
- [7/1] launch 前补齐所有 published models（gpt-5.6 走 Azure partnership）；leaderboard "truly representative"（= 给厂商的 free marketing）— 🔄
- [7/1] 早拿 external/third-party feedback — 🔄（#apiflow-bench 内部发布 + 7/2 weekly demo）
- [7/1] insight→decision 闭环成 guideline（"你得回来说 we used this insight to make this decision"）— ❓
- [7/1] thumbs up/down + default traces：验证 sanitized + size；**schema first** 交 data team populate；找 proxy metrics — 🔄/❓
- [7/1] 与 product team 建数据通道（做 consumer；Zelin 承认从没 share 过东西）— ⚠️
- [7/1] eval-dataset ↔ benchmark **converge 成 ONE piece + 专门讨论落 action item** — ⚠️ 讨论未排日程
- [7/1] Agent Mode 下一版架构 key stakeholder / funnel，keep him posted；AGM 2.0 agent design（shadow copy，assume a switch）— 🔄
- [7/1] "10x with AI"（Code with Claude workshop）；Staff 路径（others build on it + output metric）— 🔄 长期
- [7/2] Fable refusal：先 "check with security to make sure... not blocked from our side"；问 Anthropic 但 "I'd advise to not share that this is relevant to benchmark"；"make sure Fable issue is resolved before you put it in the benchmark" — 🔄
- [7/4] 分享 VALUES-DESTROY safety benchmark 草稿，"so you can work and keep things moving/done while I'm out" — 🔄（**他休假期间的预载任务**）

---

## 2. 需求类型分布（~95 条去重后 directive 的估算）

| 类型 | 占比 | 典型例子 | 趋势 |
|---|---|---|---|
| **Benchmark/评测** | ~33% | v0.1 freeze、6/11 validator 四步、6/23 12 条反馈、coverage clustering、solvability oracle、leaderboard | 全程最大类且持续加码；他有自己的 design doc 和 scoring equation，视为 "your signature" 和 "your baby" |
| **模型训练** | ~14% | 7 类 SFT、RL pivot、2B-4B 小模型、VibeThinker recipe、蒸馏 | 5 月最热，6/24 起部分移交 Raja（有分歧） |
| **数据** | ~12% | 10 年 collection 历史、real→analyze→fake、traces sanitized+size、schema first、API Bible | 反复重启（5/20→5/27→6/17→7/1），是他最不满进度的一类（"should have been done by now"） |
| **流程合规** | ~11% | system card ×2、training log、privacy disclosures、数据边界、version+design-doc 纪律、internal review gate | 说一次就算数且会复查；6 月起密度陡增 |
| **基建/账号/vendor** | ~10% | AWS 账号、SageMaker、Crusoe 信用卡、vendor eval doc、Fireworks lock-in、serving workstream | 他亲自下场清障最多的一类 |
| **汇报/展示/发布** | ~9% | delta 表、blog + green sign、arena.ai 对标 methodology、failure analysis "big shock"、priorities+deadline 清单、R&D weekly page | 格式要求最具体的一类 |
| **协作协调** | ~8% | Sumit、Marc data team、Nihar guidelines、Shamasis、Lance+周四 deadline、path-param channel | 他给 who+what+deadline 三件套，期待 Zelin 接住自己 drive |
| **Safety** | ~4% → 急升 | AgentHarm→API-Harm、July 14、VALUES-DESTROY、Fable refusal | 7 月起成为新主线（他休假前专门预载） |

---

## 3. 隐含 SLA（从实际催促行为反推）

- **Slack DM 提问 → 当天回**。他自己秒回单字（"yes"、"Monday"），期待对等节奏。10:00 发指令期待当天动起来（7/2 security check）。
- **directive → 次日上午有进展信号**。5/27 13:16 发 7 类 SFT 构想 → 5/28 10:08 "any update on ☝️?"。这是最硬的一条实测 SLA：**新 directive 的首次回音窗口 < 24h**。
- **"today / this afternoon" 类小事 → 字面当天**。legal（5/20 当天下午）、AWS access request（"do it today"）、找 Shamasis（"morning or afternoon — then let me know"）、R&D page 模板（"By end of the day"）。
- **交付类 → 天级不是周级**。"in three days"、"Monday"、"Thursday we want this"、"July 14th, put the results"。他明说时间尺度依据："the expectation is we can move much faster with the AI"。
- **模糊时间词会被当场逼具体**。"'Next few days' means how long?" → 必须换算成日期写进 doc。
- **telemetry 触发式追问 → 零延迟**。看到 Serverless 用量激增当天就问 "any candidates based on your evals?"；Fireworks 账单 $18-19K 他主动追。**大额消耗后 24h 内最好主动先报产出**。
- **有效期资源 → 他替你记着**。6/4 "were you able to use crusoe? their free credits are only available this month"。
- **例外：签名级作品给长跑道**。bench publish "如果要两个季度也没关系"——他区分"流程性事务要快"与"签名级作品要精"，但即便长跑道也要"ship it now to get some feedback"（快迭代 + 高终局并存）。
- **他 out ≠ 暂停**。休假前发草稿 "so you can work and keep things moving/done while I'm out"——他回来第一件事会 check 这段时间的产出。

---

## 4. 质量标准清单（原话为证）

### 会让他满意 ✅
1. **自带 delta 的交付**："Could you share the delta here vs the previous one?" — 只给绝对数不够。
2. **数据支撑的诊断**：对 PAN log-prob 证据链 "That's a good idea — I really like it"（还要拿去和同事讨论）。
3. **确定性、可决策的指标**："some deterministic validator needs to be there; that's better than ambiguous scoring across different layers"；threshold 必须能 justify。
4. **coverage 而非数量**："We're not optimizing for quantity... What are you missing?"
5. **failure analysis**："if you do the failure analysis, give them a big shock in terms of how this is gonna become valuable."
6. **复用现成不重造**："there's no point for you to start recreating this"（VibeThinker recipe）；"mirror the policies of the postman data account"。
7. **under-promise**："people actually appreciate it more when they're not over-promised" — 宁可晚几天并如此定位。
8. **可复述的一句话 proof-point**："bug triage time 从一周降到几分钟 — that's all I want to have"；"Vedan just showed"。
9. **insight→decision 闭环**："你得回来说 by the way 我们用了这个 insight 做了这个 decision — 这样才是 data-driven"。
10. **带方案来而非带问题来**：vendor 比价、category 分布分析都获即时认可；"I evaluated these four vendors, we want this one."
11. **ownership/pride**："你要是对这份工作最 proud 的那个人"；"quality — that's your signature"。
12. **诚实对待不确定性**："If the two harnesses are not identical, we should not pretend they are."

### 会让他不满意 ❌
1. **无法解释的结果**："We can't explain it because we have no record of why BERT classified the extras that way" → 不能上线。
2. **可被 game 的 metric**："A 300-step brute-force agent and a clean 6-step agent score the same — that's a problem"；"verbosity is not a metric"。
3. **单点信任**："Right now you only use one grader... you're essentially trusting whatever comes out of it" → distribute the trust。
4. **过拟合式刷分**：teacher 输出回训再同集涨分 — "go ahead, but that's fake."
5. **无权重的平列框架**：三标准 "That's a reasonable framework" 但立刻补 "assign a weight — cost is the most important thing"。
6. **单次实验就下结论**：IFT "收益 cancel out" 被纠为多版本+多维 eval 迭代。
7. **overclaim**：绿卡信 "第一块砖" 比喻 — 在建，但不能声称 in charge of this building。
8. **未对齐的对外动作**：删已发 Slack 消息；"lets sync before you talk to anthropic and fireworks"；"do not share any private data or plans... without me reviewing first"。
9. **无 justification 的花钱**："不能就这样花一大笔钱，现在又没有 model 产出"；加卡到未审核平台 "it's not a good idea"。
10. **信息孤岛/该 delegate 自己做**："Marc 的 team 不是在做这类分析吗？"；"如果你没到那（schema）我们做不了任何事"。
11. **进度低于心理预期**："我本来以为这个 should have been done by now"。
12. **低效路径**："20 轮才做完的活应该 2 轮做完"；"we are not even at Anthropic level yet"（feature→production 以天计）。
13. **讨论不落 action item**："有时我们 discuss 一些 topic 看起来 aligned，但没落成 action item" — 他明确视为问题。
14. ⚠️ **语气≠严重性**：6/23 反馈开头 "Good work!" 但 5 大点全是必须整改项；底线词是 "needs to be fixed"、"that's a problem" vs 软建议 "you may want to consider"。

---

## 5. "被落下的球"风险清单（按风险降序）

**高危（触碰他的复查习惯 / 钱 / 合规 / 他明确点名的痛点）**
1. **eval-dataset ↔ benchmark convergence 专门讨论**（7/1）— 他当场抱怨 "看起来 aligned 但没落成 action item"，这正是他最不满的模式；未排日程 = 自证他的批评。
2. **70% threshold justification**（7/1）— gate 在 launch 路径上，他有 callback 习惯（"I told you this, right?"），launch review 时必被再问。
3. **privacy disclosures 核实**（6/10）— 合规红线 + "you need to verify this" 明确指令 + "this may change later" 要求持续核实；training log/system card 线他已重复催过 2 次，同类。
4. **费用资源梳理报备**（5/27）— 成本是他最高权重敏感项，$18-19K 账单他在追，此事无回复痕迹已 5+ 周。
5. **vendor 关系明确化**（6/10 二选一：POC criteria vs temporary vendor）— "we need to be clear with Fireworks... avoid lock-in"，路径至今未选；stay-on-Fireworks 只是暂态。
6. **R&D weekly page**（6/17，"By end of the day"）— 流程性规矩他说一次就算数且会复查（system card ×2 先例）；每周节点都在制造新的"欠账"。
7. **product team 数据通道**（7/1）— 他用绩效语言施压过（"that's gonna impact your performance, everything"）。

**中危（技术债/他给过具体指引）**
8. **validator 链式 shortcut 漏洞**（6/10）— 他说 "that's the hardest part"，6/11 gap 分析未覆盖；v2 design 若不含此项会被点名。
9. **structured output 真分歧**（6/11）— 与 Zelin format-neutral 路线冲突，悬而未决的分歧比落下的任务更危险（他 6/24 处理分歧的方式是留待后续对齐，不等于遗忘）。
10. **model knowledge vs harness/search 分界**（6/3）— "you guys need to define it"，与小模型战略直接相关，迟早回来。
11. **size-vs-performance tradeoff 曲线**（5/20）— 与 2B-4B 选型论证天然合流，可低成本补上。
12. **insight→decision guideline 落地**（7/1）— 他说 "这必须成为 guideline"。

**低危（外部 leverage / 一次性，但他记性好）**
13. Kamal 数据（5/20）/ Piyush outreach（5/20）/ OpenAI Applied AI + Cursor 请教（6/1）/ Fastino-Pioneer（6/24）— 他主动搭的桥没人走过去；单条成本低，累积会形成"给的资源不用"印象。
14. path-param escalation channel（6/10）— 他教的是可复用模板，项目本身或已自然 deprioritize。
15. paper/arXiv（6/1）— 部分被 blog 线吸收，但 "arXiv — just put it there" 未兑现。
16. Jira↔GitHub、serving workstream（Akshay）、Portkey 工作项、timeline 回答（均 6/17）— 小模型线重启时会一并被问。
17. 6/1 consolidation（5/28）、Redash spend dashboard（6/1）、Braintrust/codesota 链接（6/2）— 时效已过或低优。

**另注意——他欠 Zelin 的承诺也要追踪**（他期待被 remind）：dedicated engineer（找 Udit/Parth 的 manager）、"我明天把我在写的 doc share 给你"（safety doc，7/4 已兑现）、"I get people pooling in from AWS to help you"、绿卡信后续、会议预算。

---

## 6. 对 pipeline 设计的启示（具体到功能）

### 6.1 捕获层（信号源分级）
- **P0 = Slack DM**：directive of record。一条消息常含完整 spec（5/27 一条列全 7 项）。
- **P0 = #llm-training 长消息**：canonical spec，**必须按编号拆分成 5-8 个子需求**（6/2、6/11、6/23 均是"一条消息 = 一批任务"）——整条当一个任务处理必然漏项。
- **P1 = 他发布的 Confluence doc**：定框架用（Design Thoughts、VALUES-DESTROY 草稿），期待 Zelin 在其上扩展（"not exhaustive, you can expand"）。
- **P2 = 会议口头**：directive 密度最高、最易散失（June 1 consolidation、R&D page、tradeoff 曲线全部丢在这里）——**这是 pipeline 的核心增量价值**。口头 deadline（"today"、"this week"、"morning or afternoon"）几乎从不落书面。
- 特殊模式：**休假前预载**（7/4）——他 out 前发的材料 = 回来第一天要 check 的清单，需打高优标签。

### 6.2 解析层（每条 directive 的抽取字段）
- **who + what + deadline 三件套**（他自己派跨团队活就用这个格式："put him a deadline — Thursday we want this"）。
- **硬/软分级词典**：硬 = "needs to be fixed" / "that's a problem" / "we have to" / "make sure"；软 = "you may want to consider" / "I'd look into" / "that's also a good idea"。**不能按语气判级**（"Good work!" 后全是整改项）。
- **成本字段**：金额上限（<$120、~$20K）+ 是否要求回报成本（"Let me know the cost"）。
- **review gate 字段**：是否需要他 green sign（blog、vendor doc、对外分享一律 yes）。
- **模糊 deadline 强制具体化**：捕到 "next few days / soon / quickly" 时，卡片必须提示"向 Arash 或自行换算成日期并写进 1:1 doc"——这是他亲自教的规矩。
- **他的管理学词汇做索引 tag**：decision-grade threshold、one-way door、frozen benchmark、compatibility layer、system balance、funnel、distribute the trust。

### 6.3 去重与关联层
- **跨源合并**：同一需求普遍会议+Slack 双现（7 类 SFT、受控分享、validator 整改），以书面版为 spec、口头版补 deadline 和语境。
- **repeated-mention 检测**（关键功能）：同一要求第二次出现 = 第一次没闭环 = 不满前兆（system card 6/10+6/11 ×2；"quality is your signature — I told you this, right?"）。重复即自动升级优先级。
- **反馈时效校验**：他的反馈可能基于旧 artifact（6/11 基于 v4.5，Zelin 已在 v4.6+ 实现大半）——收到批评先 diff 当前版本，**主动同步最新进展能直接消掉整改项**。
- **双向承诺账本**：Arash 的承诺（dedicated engineer、doc share、AWS people）也入库，供 Zelin 适时 remind。

### 6.4 Approve 卡片必须显示
1. 原话 quote（英文原文，他的措辞就是验收标准）
2. 来源 + 日期 + 渠道（DM / channel / 会议 / Confluence）
3. 硬/软分级 + 推断 deadline（含"需具体化"标记）
4. 类型标签（8 类之一）+ 关联历史 directive（是否 repeated mention、是否属于某条长消息的第 N 点）
5. 涉及成本？→ 金额 + 是否需回报
6. 需要 green sign？（对外/花钱/partner 分享 = 强制 yes）
7. 是否存在与 Zelin 的**已声明分歧**（structured output、Raja 分工）——分歧项单独颜色，防止被当普通任务静默处理
8. 当前状态 + 上次回音时间（>24h 无回音的新 directive 标红——对应他的实测催促窗口）

### 6.5 汇报格式模板（按他实际奖励的形态）
- **必带 delta**：vs 上一版数字（5/21 事件）。
- **必带成本**：跑了什么、花了多少、对应产出（Serverless 事件；"$112/模型"是他要的粒度）。
- **insight→decision 一行**："用这个 insight 做了什么决定"。
- **ready / not ready against priorities 清单**："define your priorities in a list... come back and say this is ready, this is not ready. Typically we work from that."
- **失败也报**：postmortem 化（training channel 提议原文点名 "lessons learned from failed trials"）。
- **收到批量反馈的标准回应** = impact-effort 排序 + "首版吸收 N 条" + 立刻做的立刻做（6/24 他亲自示范此流程）。
- 措辞：短、结构化、有编号——他自己的书面反馈就是这个格式，期待对等。

### 6.6 自动 vs 必须人工
**可自动**：捕获/拆分/去重；状态回填（从 Slack/commit/文档活动推断）；>24h 无回音提醒；repeated-mention 升级；有效期资源倒计时（Crusoe credits 类）；成本聚合看板；他 out 期间的进度日志（回来后一键汇报）；1:1 前自动生成 ready/not-ready 清单和"欠账"清单。

**必须人工（Zelin 亲自）**：
- 一切对外动作（partner 分享、发布、blog）——双重 gate：先 Arash green sign，再执行；pipeline 只能 draft 不能 send。
- 花钱动作——他对未审核平台加卡明确 "not a good idea"，任何新支出走"报备→批准"卡。
- 向他承诺 deadline——承诺即被 track，自动系统不能替 Zelin 许诺。
- 分歧裁决——他区分"我拍板"（long-horizon 定义）与"你拍板"（8b vs 27b："it's your tool, try it"），卡片需标注哪类；"你拍板"类自动执行 OK，"待他拍板"类只能催不能替。
- 合规敏感操作（动真实用户数据、privacy 相关）——留痕自动化，决策人工。

**Overkill 杠杆点**（超额满足的最短路径）：① 把他最痛的"讨论不落 action item"变成每次会后 2 小时内自动产出的 action-item 清单发他确认——直击 7/1 抱怨；② 新 directive 24h 内主动回音（哪怕只是 "captured, plan by X"）——他所有催促都发生在这个窗口；③ 大额消耗当天主动附产出说明——先于他的 telemetry 追问；④ 每周自动维护 R&D page + training log + system card 草稿——把他"不接受'太耗精力'"的三件流程债一次性自动化清零。
