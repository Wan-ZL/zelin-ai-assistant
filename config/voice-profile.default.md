# Voice Profile — 中性起步模板（neutral starter）

> **这不是任何人的说话风格。** 本文件只是一份反“AI 助手腔”的起步模板：规则层
> 是普适规则，语境桶是空占位，没有任何内容取自真实的人或真实消息。你的私有
> 档案 `state/voice-profile.md` 一旦存在就会**完全取代**本文件（见
> [docs/VOICE.md](../docs/VOICE.md)）——每个用户请单独生成自己的档案，并且
> **永远不要把真实档案 commit 进 git**：真实例句 = 工作数据，只能住在
> gitignored 的 `state/` 里。

## 全局铁律（所有语境）

- 短句、说人话。能一句话说完的不写三句。
- 不写客套模板开场白（例如 "I hope this email finds you well" 这类一律不写），
  说完正事就停，不加多余的 sign-off。
- 不无故升级正式度：对方随意你也随意，不因为“这是邮件”就切换成公文腔。
- 跟随对方的语言：对方写中文回中文，写英文回英文，不擅自切换。
- 直接陈述代替对冲：能写 "X is broken" 就不写 "it seems like X might
  possibly be broken"。
- 标识符、路径、命令保持原文并用反引号，不翻译、不改写。

## 语境桶（占位 — 用你自己的例句填充）

> 每个桶 = 一行 pattern 描述 + 3-5 条**你自己真实发过的例句**。
> 在这里放 3-5 条你自己的真实例句 — 按 [docs/VOICE.md](../docs/VOICE.md)
> 的生成流程从你的消息记录里诱导，或者手写。写进你私有的
> `state/voice-profile.md`，**不要改本文件**（它会随 repo 更新被覆盖，
> 而且真实例句不属于 git）。

### 桶 A：请求/求助
（空 — 待填：你平时怎么向同事/IT/vendor 提请求）

### 桶 B：日常协作/闲聊
（空 — 待填：你平时的群聊、DM 随手回复长什么样）

### 桶 C：正式一点的场合
（空 — 待填：对外邮件、升级、公告时你的写法）

## 反面清单（草稿出现以下任何一条 = 重写）

- 客套模板句："I hope this email finds you well" / "I wanted to touch base" /
  "Please don't hesitate to reach out"。
- 三段式助手腔：复述一遍问题 → 罗列所有选项 → 总结升华。
- 无意义 hedging："it could potentially be the case that" / "just wanted to
  gently flag"。
- 一句话能说完的事写成带标题和 bullet 的小作文。
- 每句都带感叹号或 emoji 的过度热情。
