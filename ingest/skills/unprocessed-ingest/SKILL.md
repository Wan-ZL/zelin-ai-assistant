---
name: unprocessed-ingest
description: 处理 `1 - unprocessed/` 中的所有可 ingest 的文件 — screenpipe dump、对话导出（json）、笔记（md/txt）、PDF、图片等。完整的 unprocessed → raw → wiki ingestion pipeline。
---

处理 `1 - unprocessed/` 中的所有有信息价值的文件。**不限文件格式** — 只要 Claude 能读出内容、且对 wiki 有价值，就 ingest。

如果 `1 - unprocessed/` 是空的（或所有文件都是噪音 / 已知应 skip 的类型），直接报告 "1 - unprocessed/ 为空，无需处理" 并结束。

> **Before starting, read `examples/good-raw-excerpt.md`** in this skill's directory. It shows the correct output format for a screenpipe raw file — scene-by-scene with establishing shots, dwell time, deltas, and a separate Conversations Captured section with verbatim text. Use it as your quality bar **for screenpipe-type sources**. Other source types have their own format (see Step 7 below).

## 全自动 — 永不打断 Zelin

This skill is **fully autonomous**. From scan to log, **never** ask Zelin a question, never wait for confirmation, never emit `⚠️ should I...?` prompts. Decide and act on every fork:

- 不确定一个 scene 该怎么 attribute → 自己选最合理的，写进 raw + 在 change-summary 里说明
- 不确定一段内容是 personal life context 还是真 secret → 按 vault `CLAUDE.md` 的 Privacy posture 判断（identifier vs authenticator）
- 不确定一个新 entity 是否值得建 wiki page → 默认建（最小模板也行），后续可由 Zelin lint 时合并/删除
- 不确定 freshness audit 中的 stale page 是否该改 → 直接改，理由进 change-summary
- 唯一例外：raw 文件物理读不出来（corrupted、permission error）→ 在 log.md 记一行错误然后跳过该文件

最终输出永远是：raw 文件 + change-summary + wiki 更新 + log.md 一行 + chat 里给 Zelin 一个简短摘要。**不带问题。**

---

## File type dispatch

按文件名 / 扩展名派发处理方式。**未知类型 → 默认按通用文本读取并 ingest，不要 skip。**

| Pattern | Source type | Reader | Cleaning | Output naming |
|---|---|---|---|---|
| `screenpipe_*.md` | screenpipe dump (audio + OCR) | Read tool | Steps 3-5 (audio + OCR detective) | `2 - raw/YYYY-MM-DD-screenpipe-{slug}.md` |
| `conversations.json` | Claude Desktop / Web 对话导出 | Read + json parse | 按 conversation 拆分（每个一段），保留 user/assistant 全文，去掉无关 metadata | `2 - raw/YYYY-MM-DD-claude-export-{conv-title}.md`（每个对话一个文件，或合成一个 monthly 文件按时间分段）**+ 原件 `2 - raw/YYYY-MM-DD-conversations.json`** |
| `memories.json` / `projects.json` / `users.json` | Claude account dumps | Read + json parse | 提取 schema-relevant fields，删 timestamp/uuid 噪音；按主题分组 | `2 - raw/YYYY-MM-DD-claude-{kind}.md` **+ 原件 `2 - raw/YYYY-MM-DD-claude-{kind}.json`** |
| 其他 `*.json` 数据导出 | 结构化数据 dump | Read + json parse | 识别 schema，提取语义字段；按主题分组 | `2 - raw/YYYY-MM-DD-json-{slug}.md` **+ 原件 `2 - raw/YYYY-MM-DD-{slug}.json`**（结构化数据 markdown summary 是 lossy，rerun 不同 query 需要原 json） |
| `*.md`、`*.txt`（非 screenpipe） | 普通笔记 / 文章 / 粘贴文本 | Read tool | 删除 boilerplate（footer、cookie banner、share button 等），保留正文 | `2 - raw/YYYY-MM-DD-{slug-from-title-or-filename}.md`（单份 — 抽取版 ≈ 原件，无需双份） |
| `*.pdf` | 论文 / 合同 / 收据 | Read tool（pages 参数，>10 页必须分段读） | 抽 text；丢 page header/footer；保留 figures/tables 的描述 | `2 - raw/YYYY-MM-DD-pdf-{slug}.md` **+ 原件 `2 - raw/YYYY-MM-DD-{slug}.pdf`**（PDF 常是法律 / 财务证据 — 签名、印章、Document ID 嵌入图、表单原始排版 markdown 抽不出，必须留原件） |
| `*.docx` / `*.xlsx` / `*.pptx` | Office 文档 | Read tool（必要时先用 `pandoc` / `textutil` 转 markdown） | 抽 body text + tables；公式 / track changes / 嵌入图标注存在但无法完整重建 | `2 - raw/YYYY-MM-DD-office-{slug}.md` **+ 原件 `2 - raw/YYYY-MM-DD-{slug}.{docx,xlsx,pptx}`**（xlsx 公式、docx track changes、pptx 嵌入媒体 markdown 抽不出，必须留原件） |
| `*.png` / `*.jpg` / `*.jpeg` / `*.webp` / `*.gif` / `*.heic` / `*.tiff` | 截图 / 照片 / 图表 | Read tool（vision） | 描述 image 内容；如果有文字（截图、whiteboard、收据）抄出来；保存原图到 `2 - raw/` 并在 raw 里 `![[...]]` 嵌入 | `2 - raw/YYYY-MM-DD-image-{slug}.md` **+ 原图 `2 - raw/YYYY-MM-DD-{slug}.{ext}`** |
| `*.epub` / `*.mobi` | 电子书 | Read tool（or 用 `pandoc` 转 markdown） | 抽正文 + 章节结构；保留 TOC | `2 - raw/YYYY-MM-DD-ebook-{slug}.md` **+ 原件 `2 - raw/YYYY-MM-DD-{slug}.{epub,mobi}`** |
| `*.html` / `*.htm` | 网页保存 | Read tool | 抽正文；删 nav/sidebar/ads | `2 - raw/YYYY-MM-DD-web-{slug}.md`（单份；若是 "Save Page As complete" 带 `_files/` 资源目录，整目录留在 unprocessed，目前不处理） |
| `*.csv` / `*.tsv` | 数据表 | Read tool | < 50 行：保留全部；≥ 50 行：保留 header + 前 N 行 + 总行数 | `2 - raw/YYYY-MM-DD-data-{slug}.md`；**≥ 50 行时额外保留 `2 - raw/YYYY-MM-DD-{slug}.{csv,tsv}`**（小表抽取版 = 原件无需双份；大表抽取丢了 actual data，必须留原件） |
| `*.eml` / 邮件导出 | 邮件 | Read tool | 抽 from/to/date/subject/body；删 quote chain 重复部分 | `2 - raw/YYYY-MM-DD-email-{slug}.md` **+ 原件 `2 - raw/YYYY-MM-DD-{slug}.eml`**（DKIM / Received chain / Message-ID / 附件 markdown 抽不出，反钓鱼 + 法律证据靠原件） |
| `*.srt` / `*.vtt` / 视频字幕 | 字幕 | Read tool | **保留 timestamps**（不要合并掉时间戳）；可合并连续 30s segments，但每段标头注明时间范围 | `2 - raw/YYYY-MM-DD-transcript-{slug}.md`（单份 — 抽取版若保留时间戳即等价原件） |
| `*.mp3` / `*.wav` / `*.m4a` 等纯音频 | 录音 | **Skip** — 当前没有 audio reader。在 log 里记一行 "skipped: no audio reader"，留在 unprocessed | — |
| `*.mp4` / `*.mov` 等视频 | 视频 | **Skip** — 同上 | — |
| `.DS_Store` / `.gitkeep` / 系统文件 | 噪音 | **Skip silently** | — |

**判断"该不该 ingest"的默认 rule**：能用 Read tool 读出有意义内容（不是乱码 / 二进制 / 系统文件） → ingest。宁可多写一个无聊的 raw 文件也别漏掉一份信息。

### 原件保留规则（preserve-original）

上表标注 **"+ 原件 `2 - raw/...`"** 的类型在创建 raw markdown 之外**还要把原件 copy 到 `2 - raw/`**。理由：这些类型的 markdown 抽取是 **lossy**（二进制格式、签名 / formula / DKIM / DOM / 嵌入媒体抽不出，或者数据量大到只能存 summary），关键场景下需要原件。

执行细节：
- 命名：`2 - raw/YYYY-MM-DD-{slug}.{原扩展名}`，与 raw 文件 slug 对齐
- 时序：先 `cp` 到 `2 - raw/`，确认成功，再让 Step 8 从 `1 - unprocessed/` 删除原文件。**绝不能在 2 - raw/ 复制完成前删除原件。**
- raw markdown frontmatter 加一行 `original: 2 - raw/YYYY-MM-DD-{slug}.{ext}`，方便回查
- 不需要保留原件的类型（md / txt / 短 csv / html / srt-vtt / 已粘贴文本）：直接走原本流程，Step 8 删除即可

---

## 核心原则

### 两层架构

| 层 | 文件位置 | 职责 | Dedup 规则 |
|---|---|---|---|
| **Raw** | `2 - raw/` | 每个 session 的独立完整记录。保留所有细节，去除杂质。 | **不做 cross-source dedup。** 即使同一段对话在其他 raw 文件里已有，本文件照样完整保留。 |
| **Change Summary** | `3 - change-summary/` | 二次精炼。标注 overlap、提取 net-new、索引优化。 | **在这里做 cross-source dedup。** 标注 "内容 X 与 [[Source - Y]] 重叠；net-new: Z。" |

### Raw 文件的标准

- **Standalone** — 只读这一个 raw 文件就能理解这个 session 发生了什么
- **Complete** — 所有对话逐字保留、所有有意义的屏幕内容保留
- **Clean** — 去掉了 OCR 重复帧、系统噪音、garbled text
- **Immutable** — 创建后不再修改

---

## Pipeline（10 步）

### 1. Scan

`ls -la "1 - unprocessed/"` 列出**所有**文件（不限扩展名）。记录快照 — 后续步骤只处理这个快照里的文件，不处理扫描后新到的文件。

按 **File type dispatch** 表给每个文件标 source type。看不出类型的（罕见）→ 默认按 `*.md`/`*.txt` 处理。

### 2. Read

按 source type 派发：

- **screenpipe**：Read 整个文件，识别 `## Audio Transcriptions` / `## Screen OCR` sections → 走 Steps 3-5
- **`conversations.json` (Claude 对话导出)**：文件可能上百 MB。先用 `head` / `jq` 查 schema，再分对话提取 user/assistant turns。每个对话独立处理。**不要一次性 Read 整个文件**，会爆 context。可用 Bash + jq 拆分到临时小文件再 Read
- **其他 json**：Read + 识别 schema，提取语义字段
- **md / txt / html**：Read 全文
- **pdf**：Read 时带 `pages` 参数。>10 页必须分段，每段最多 20 页
- **image**：Read 触发 vision，描述内容 + 抄出文字
- **csv / tsv**：Read 全文（如果 < 几 MB），否则 head + 总行数

读完后跳到 Step 6（Security Redaction），跳过 Steps 3-5 — 那些是 screenpipe 专用。

---

> **Steps 3-5 只对 screenpipe 类型执行。其他类型直接跳到 Step 6。**

### 3. Clean Audio (screenpipe-only)

- 按时间排序
- 合并连续 30-second segments 为连贯段落
- 按 topic 分段：时间 gap > 5 min = new segment
- 去转录噪音（garbled text、OCR-in-audio bleed）

### 4. Speaker Attribution (screenpipe-only)

- 能从 context 识别说话人（提到名字、会议角色、声音模式）→ 用 `[[Name]]`
- 不能确定 → `(anon)`
- 有猜测 → `(anon, likely Name)`

### 5. Clean Screen OCR — Detective Notebook Approach (screenpipe-only)

Screenpipe OCR 每隔几秒截一帧。屏幕不动时，连续帧几乎一模一样。动一点点时，变化可能重要也可能不重要。像侦探审查监控录像一样读：

#### Scene Detection
按 (app, window_title) 分组连续帧。一个 scene = 在同一个 app/window 上的不间断停留。

#### First Frame = Establishing Shot
每个 scene 的**第一帧**：提取**完整清洗内容**。保留所有可读文本 — 对话、数据表格、代码输出、UI 状态。

#### Unchanged Frames → Time Range
Scene 内后续无变化的帧，压缩为时间跨度：
```
[14:30:21 – 14:32:30] (still viewing, no content change)
```

#### Small Changes → Flag Delta
帧间有小变化（新消息 badge、scroll 显示新内容、光标移动、popup 出现），明确标注：
```
[14:31:15] Slack notification badge appeared on sidebar
[14:32:10] Scrolled down — new rows now visible: (引用新内容)
```

#### Substantive Text — Always Verbatim
对话（Slack DM、chat）、terminal/CLI 输出、代码、error message、表单内容 → 清理 OCR artifacts 但**保留全文**。**绝不 summarize 对话。**

#### Pure Noise — Discard
Activity Monitor 进程列表、system tray 细节、garbled OCR（无法辨认含义的）、重复的 sidebar/menu chrome。

#### Dwell Time = Behavioral Signal
记录用户在每个 scene 停留的时间。`[14:30 – 14:35] 5 min on Eval Harness page` 和 `[14:33:41] 2 sec glance at Slack DM` 传递的信息完全不同。

### 6. Security Redaction

这是个人电脑、私人 vault — personal life context（财务、租房、健康、人际、家庭、行程等）**保留**，照常写进 raw 和 wiki。

只 redact 真正的 secrets（即使个人 vault 也不该明文存 — vault 可能 sync 到 iCloud / Git / 备份）：
- 密码、API token、API key、access token、refresh token
- 2FA / OTP 验证码
- 1Password / Bitwarden 等密码管理器里 unlock 出来的内容
- 银行卡号、CVV、SSN 全号（末四位 OK）

**保留**（在私人 vault 里有 context 价值）：
- IP 地址、email、电话、住址
- 财务数字、账单、合同细节
- 健康记录、就医笔记
- 人际关系、家庭事务
- 任何其他 personal life context

API key 如需引用，链接到本地 secrets 存放位置（如 `config/secrets/<file>`），不要把 key 本体抄进 wiki。

### 7. Compose Raw File(s)

输出文件名按 **File type dispatch** 表的 "Output naming" 列。所有 raw 文件落在 `2 - raw/` flat 目录下。

#### Screenpipe 格式

- 按 session 拆分：时间 gap > 5 min = 分成多个 raw 文件
- 保存到 `2 - raw/YYYY-MM-DD-screenpipe-{slug}.md`

```markdown
# Screenpipe Session — YYYY-MM-DD (Topic/Meeting Name)

## Meeting Transcript
(cleaned audio, chronological. Omit if no audio.)

## Screen Activity
(scene-by-scene record: first frame full content + dwell time + deltas)

## Conversations Captured
(each Slack DM / chat / terminal session as a clean verbatim block)
```

- `## Screen Activity` = 侦探笔录：时间线 + 每个 scene 的 establishing shot + dwell time + deltas
- `## Conversations Captured` = 把所有对话/terminal/chat 的干净原文从时间线里抽出来单独放，方便搜索和引用。每段对话标注来源（哪个 Slack DM、哪个 terminal session）

#### Claude 对话导出 (`conversations.json`) 格式

每个 conversation 一个 raw 文件：`2 - raw/YYYY-MM-DD-claude-export-{conv-title-slug}.md`（YYYY-MM-DD = conversation 的 created date）。

```markdown
---
source_export: conversations.json
conversation_id: <uuid>
title: <original title>
created: YYYY-MM-DDTHH:MM:SSZ
updated: YYYY-MM-DDTHH:MM:SSZ
---
# <conversation title>

## User
<turn 1 user content>

## Assistant
<turn 1 assistant content, including tool_use / artifacts inline>

## User
<turn 2>

...
```

如果 `conversations.json` 有几百个对话，按 created date 月份分组到多个 raw 文件也可以（`2 - raw/YYYY-MM-claude-export-batch.md`），每段对话用 `---` 分隔。**重要的对话**（长、有引用价值）单独成文件；**短的 / 试探性的**合并。

#### 普通笔记 / 文章 / 网页 格式（不需保留原件）

```markdown
---
source_file: <原文件名>
source_type: note | article | webpage
ingested: YYYY-MM-DD
---
# <Title — 从 H1 / 文件名 / metadata 提取>

<cleaned body, 保留原结构（headings、lists、code blocks、tables）>
```

#### PDF / Office / eml / ebook / 大 csv / 通用 json 格式（必须保留原件）

原件先复制到 `2 - raw/YYYY-MM-DD-{slug}.{ext}`，raw frontmatter 加 `original:` 字段链回：

```markdown
---
source_file: <原文件名>
source_type: pdf | docx | xlsx | pptx | email | ebook | data | json
ingested: YYYY-MM-DD
original: 2 - raw/YYYY-MM-DD-{slug}.{ext}
---
# <Title — 从 H1 / 文件名 / metadata 提取>

<cleaned body / extracted text / structured summary>
```

> **PDF / Office / eml 等必须保留原件 — markdown 抽取版是 lossy。**
> 见上方 "原件保留规则"。Step 8 删除 unprocessed 之前必须确认 `2 - raw/` 已有对应 copy。

#### Image 格式

原图先复制到 `2 - raw/YYYY-MM-DD-{slug}.{ext}`，raw 文件嵌入：

```markdown
---
source_file: <原文件名>
source_type: image
ingested: YYYY-MM-DD
---
# <描述性标题>

![[YYYY-MM-DD-{slug}.{ext}]]

## Description
<从 vision 抽出的描述>

## Text in image
<如果有任何文字，verbatim 抄出>

## Context
<如果能推断出场景：截图自哪个 app、是 whiteboard 照片、是收据、等>
```

#### JSON dumps（memories / projects / users）格式

```markdown
---
source_file: <原文件名>
source_type: claude-account-dump
ingested: YYYY-MM-DD
---
# Claude <Memories | Projects | Users> Export — YYYY-MM-DD

<按主题/项目分组，每组一个 section，列出 fields。删掉 timestamps / uuids 等 noise，除非有引用价值>
```

#### 通用（fallback）格式

任何不在上面列举的类型，至少给出：

```markdown
---
source_file: <原文件名>
source_type: <best guess>
ingested: YYYY-MM-DD
---
# <Title>

<cleaned content>
```

### 8. Delete Processed Files

删除 step 1 快照内的 `1 - unprocessed/` 文件。不删除扫描后新到的文件。

**Preserve-original 类型 (PDF / Office / eml / ebook / 大 csv / 通用 json / image)** — 在删除 unprocessed 副本之前，必须先 verify `2 - raw/YYYY-MM-DD-{slug}.{ext}` 已存在且大小匹配。验证失败 → **不删 unprocessed 原件**，在 log.md 记一行 `original-copy-failed: <filename>` 并跳过删除。这是数据丢失防护：宁可 unprocessed 多留一份等下次跑，也别两边都没了。

### 9. Ingest

#### 9a. Agent 端：端到端处理

Sub-agent 在写完所有 raw file 后，**全权完成**以下工作（Opus 1M context 足以容纳 raw + 相关 wiki 页面）：

1. **对比 raw 和 wiki**
   - 从 raw 中提取关键 topics（人名、项目、系统、决策、状态变化）
   - Grep `4 - wiki/` 找出每个 topic 对应的 wiki 页面（包括 `4 - wiki/people/`）
   - 读相关 wiki 页面，对比 raw 内容：哪些是新的？哪些已有？哪些有矛盾？

2. **更新 wiki 页面**
   - **新人物（有名字 + 角色）** → 创建 person page（即使只有两行，见下方最小模板）
   - **已有页面缺失新事实** → 添加，bump `updated` 日期
   - **已有页面有矛盾** → 更新（保留 `%% ... %%` 用户注释）
   - **个人生活内容（财务、租房、健康、家庭、人际等）** → 照常入 wiki，按主题建独立页面（type 可用 `concept` / `learning` / `decision`，或新增明确的页面如 `Apartment Lease.md`、`Health Log.md`、`Personal Finance.md`）。这是私人 vault，personal life context 是有价值的知识。

3. **Page-internal consistency sweep** — 对 Step 2 直接触及的**每个 wiki 页面**做一次 internal-consistency 检查:
   - 重新整页 Read（或对刚加的 fact 在页内 Grep 关键词）
   - 找: 新加的内容是否跟页面其他 section 矛盾？是否还残留旧工具名、旧 cadence、被新 fact 推翻的 claim、过期的 deadline、已 deprecated 的安装步骤、过时的代码块等 stale 描述？
   - 找到矛盾 → **在同一次 update 里顺手改完**，不要留给主线程 9b QA 兜底。
   - 范围只限本次直接修改过的页，不延伸到全 wiki — 那是 Step 10 Freshness Audit 的事。
   - **为什么必要**: Step 10 Freshness Audit 找的是"7+ 天没动的相关页对比新 source"，**当前正在被改的页**永远不在 stale 列表里。但 sub-agent 倾向于"只加新 section, 不回头审旧 section"——结果就是页面自相矛盾。典型案例: [[Learning - Auto LLM Wiki]] Apr 27 ingest 加了"主 Mac launchd → cron pivot"段, 但页面架构图、文件清单、安装步骤、核心代码块还是 launchd 时代描述, 撕裂被主线程 QA 兜住但 sub-agent 应当自己捕获。

4. **写 change-summary** 到 `3 - change-summary/Source - <name>.md`
   - Frontmatter: type=source-summary, tags, sources, created/updated
   - **变更记录**: 每条说清楚 raw 里有什么 → wiki 怎么处理了 → 为什么。例：
     ```
     - Raw 有 Jordan 新消息 (Apr 24 11:58 AM) → [[Jordan Lee]] 已更新
     - Raw 有 fine-tuning doc 链接 → [[Fine-tuning Notes]] 已更新
     - Raw 有午餐闲聊对话 → wiki 已有，未更新
     - Raw 有租房合同 renewal 细节 → 创建 [[Apartment Lease]]（personal life context, 私人 vault 保留）
     - Raw 有 1Password unlock 出来的 vault token → redact，不入 raw 也不入 wiki
     - Step 3 sweep: 顺手清 [[Some Page]] 旧 section 的 stale 描述 (架构图过期 / 旧工具名 / cadence 错)
     ```
   - **关联**: wikilinks to related pages

5. **更新 `4 - wiki/index.md`** — 如果创建了新页面，加进去；如果只是更新已有页面，不需要改 index

6. **Append to `4 - wiki/log.md`** — 一行 ≤ 200 字符。例：
   ```
   [2026-04-26] unprocessed-ingest | 1 file → raw + 2 wiki updates ([[Jordan Lee]], [[Fine-tuning Notes]])
   ```

#### Person page 最小模板

```yaml
---
type: person
tags: []
sources: [raw/YYYY-MM-DD-xxx.md]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
# Name
Role / context. 首次出现在 [[Source - xxx]]。
```

#### 9b. 主线程：审核

Agent 完成后，主线程做 **QA review**，不重做：

1. **读 change-summary** — 看 agent 做了什么、为什么。这是审核的入口。
2. **抽查 raw 文件** — 检查对话是否逐字保留？scene 是否有 establishing shot？
3. **抽查 wiki 改动** — 对照 change-summary 提到的页面，确认更新合理：
   - 新事实是否真的来自 raw？（不是 agent 推断的）
   - 计划/承诺有没有被错写成事实？
   - `%% ... %%` 用户注释是否保留？
4. **发现问题** → **直接修**。无论大小（措辞、格式、错误事实、漏掉关键信息）都自己修完，不问用户，不需要 `## QA Notes`。

### 10. Freshness Audit

所有 ingest 完成后，做 wiki-wide staleness 检查：

1. **Collect topics**: 从本次 ingest 的所有 source 中提取 key entities 和 topics（项目名、人名、系统、决策）
2. **Reverse lookup**: Grep `4 - wiki/` 中所有非 source-summary 页，找出引用了这些 topics 的页面（"potentially affected"）
3. **Date check**: 对比每个 affected 页的 `updated` 和今天日期。**7+ 天未更新** 且有新相关信息 = candidate
4. **Content check**: 读 candidate 页，与新 source 对比，识别：
   - **矛盾** — 页面说 X，新 source 说 Y
   - **缺失** — 新 source 有重要 context 但页面没有
   - **Stale timeline** — deadline 已过、milestone 变化
   - **Status drift** — 页面说 "planned" 但实际已 "in progress" 或 "done"
5. **Update — always autonomous, never ask**:
   - 改动明确 → 直接更新页面，bump `updated` 日期
   - 改动大或 ambiguous → **仍然自己决定并更新**。理由写进 change-summary，不要在页面里留 `%% %%` 标记（`%% %%` 只属于 Zelin）。
   - 绝不弹问题给用户、绝不输出 `⚠️` 让用户决定。Skill 是全自动的。
6. **Report**: 在本次 ingest 的 change-summary 页里列出：哪些页被更新、为什么、哪些检查后仍 current。log.md 一行总结。**不打断 Zelin。**
