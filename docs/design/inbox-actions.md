# inbox 动作 wire 契约（F3 提取稿 — G1 `server/inbox_writer.py` 与 G6 golden 测试的唯一真源）

> **状态（v0.48，2026-08-31）**：本文 §2+§3 目录已被 CONTRACT **§49** 正式引用为 `POST /api/actions` 动词白名单（T-2 终裁）；R2 → CONTRACT §3 v0.48 追记（T-17）、R9 → CONTRACT §10 v0.48 追记（T-18）均已入典。

提取自 live 树（只读，2026-08-30）：`docs/CONTRACT.md` §3/§10/§10bis/§21/§21bis/§22/§24/§29/§34/§34.1/§34bis/§37/§38.2/§39.2 + `mac/Sources/AppDelegate.swift`（`writeInboxFile` 与全部 `submit*`）+ `shared/Sources/InboxAction.swift` + `mac/Sources/ProposalsTriage.swift` + `mac/Sources/SettingsWeeklyDigest.swift` + `mac/Sources/SettingsClaudeImport.swift` + `mac/Sources/Utils.swift`（AppPaths）+ `act/actd.py`（`process_inbox` 读侧）。**Swift 写侧代码即字节真相；CONTRACT 散文与代码冲突处见 §6 风险备注（code wins）。**

## 1. 通用 wire 纪律（所有动词共享）

- **目录**：`$AIASSISTANT_HOME/state/inbox/`（`AppPaths.inboxDir`；写前 `createDirectory(withIntermediateDirectories: true)`）。
- **文件命名**：`<UUID>.json`（Foundation `UUID().uuidString` = 大写连字符 UUID）；唯一例外是 capture 动作 = `capture-<UUID>.json`。actd 用 `glob("*.json")` 消费、**不解析文件名**——stem 只作两个 opaque key：§34.1 幂等键（`execution.inbox_stem`）与 §5.4 ack 台账键。前缀 `capture-` 是 Mac 端 debug 习惯，不是协议；**stem 全局唯一是硬要求**（重放判定按 stem）。server 端用小写 `uuid4` 亦合法，但每个逻辑动作必须铸新 stem。
- **时间戳 `ts`**：`ISO8601DateFormatter().string(from: Date())` = UTC 秒级 `YYYY-MM-DDTHH:MM:SSZ`（与 actd `_iso_now()` 同格式）。今天 actd 对卡片动词不解析 `ts`（provenance-only），但格式照写不减。
- **序列化字节（golden 的判定基准）**：`JSONSerialization(options: [.prettyPrinted, .sortedKeys])` ——① key 升序（全 schema key 均为小写 ASCII，普通字典序即可）；② 2 空格缩进；③ key-value 分隔符为 `" : "`（冒号两侧各一空格）；④ **正斜杠转义为 `\/`**（NSJSONSerialization 特性，Python `json.dumps` 默认不做——G6 逐字节对照的第一雷点）；⑤ 非 ASCII（中文）原样 UTF-8 不转 `\uXXXX`；⑥ 字符串内 `\n` 等控制字符照 JSON 标准转义；⑦ **空数组渲染为 `[` + 空行 + `  ]` 三行**（非 `[]`）；⑧ 非空数组每元素 4 空格缩进独立一行；⑨ **文件末尾无换行符**。
- **原子写**：`Data.write(options: .atomic)` = 同卷临时文件 + rename。Python 复刻时 tmp 文件名**不得匹配 `*.json`**（如 `<uuid>.json.tmp` 或点号前缀），写完 `os.rename` 进 `state/inbox/`——actd 每 ~10s glob 一次，半截 `.json` 会被当 `bad_json` 消费掉。
- **消费顺序与幂等**：actd 按 **mtime 升序**处理，at-least-once（先 apply 后 unlink）；卡片动词靠状态守卫幂等（v0.10.2 公共规则：状态不匹配 = no-op + 日志，连点/迟到/重放安全）；`capture` + `mode:"run"` 是唯一绕开状态守卫的路径，幂等键 = 文件 stem → 建卡落 `execution.inbox_stem`，重放同 stem → ack `running` 跳过（§34.1）。
- **回执**：`state/sync/applied.jsonl`（`{action_id: <stem>, result_status, ts}`）**仅在云同步 ACTIVE 时追加**；本地纯 Mac 安装无 ack——「文件被删 + 投影变化」就是成功信号。result_status 词表：`running`（真实状态变更）| `noop`（守卫幂等）| `unknown`（卡不存在/动词不识）| `bad_json`（文件不可读/非 dict/apply 崩溃）。
- **读侧宽容**：`comment` 非字符串一律 coerce 为 None；毒文件（坏 JSON、非 dict、apply 崩溃）ack `bad_json` 后删除，绝不卡死 inbox。server 端 API 是零容忍 400（BUILD-CONTRACT），但落盘后 actd 侧永远 fail-safe——两层纪律不要混淆。
- **可选 add-only 键（Mac 不写，server PR1 也不写）**：`expected_status`（§32.2 手机端 stale-guard）、`board_seq`（provenance）。列在这里防 G1 把它们当未知字段拒掉 actd 方向的语义——它们只出现在 syncd 落的文件里，不在 web→server 的入站 API 上。
- **ingress 落款 `via`（add-only，T-28）**：HTTP 写入面落盘的每个文件都带——server/inbox_writer 恒 `"web"`（capture/comment 带 `actor:"agent"` 时改 `"agent"`）、act/webui 恒 `"remote"`；Mac 文件**无 via**（缺 via = owner-local 的判据）。`via` 永远是 server 落款：web→server 入站 API 直发 `via` 是 400 UNKNOWN_FIELD；`actor` 是传输面字段（仅 capture/comment 两动词接受、唯一合法值 `"agent"`、不落盘）。actd 读侧按 via 盖捕获源 channel 并裁 comment 的 steer 资格（详见 vnext-amendments.md T-28/T-29——含诚实条款：落款是礼仪 + 取证，不是密码学墙）。golden fixtures 保持 Mac 落盘形（无 via）；server 产物 = golden + 尾键 via。

## 2. 卡片决策类（统一四键形状，`AppDelegate.writeInbox`）

形状恒为 `{"action", "comment", "id", "ts"}`——**`comment` 键永远存在**，无文本时为 JSON `null`（CONTRACT §3 散文只说 comment 动作携带文本，代码是全动词恒带键）。T0/T1/T2 审批的 wire 完全相同：T2 的 typed-confirm（输入「确认」/「go」）是纯客户端闸门（`t2_confirm_pass/fail` 打点），不改 JSON。

### 2.1 approve（批准）
允许 `detected|card_sent` → `approved`（补记 `execution.approved_at`）；其余状态 no-op（白名单防迟到 approve 复活 trashed/merged/raising 卡）。
```json
{
  "action" : "approve",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.2 reject（拒绝）
任意活状态 → 回收站（`trashed`，reason=rejected，可恢复）；先 best-effort 停活 session。
```json
{
  "action" : "reject",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.3 comment（修改方向，携带文本）
文本并入 plan/notes，卡留 `card_sent` 等重新审批；terminal（trashed/merged/rejected）no-op；raising 卡也可 comment（折回 card_sent 是预期行为）。
```json
{
  "action" : "comment",
  "comment" : "改成先出 API 设计再动手，plan 里补一条回滚方案",
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.4 defer（暂缓，提案→备选）
仅 `card_sent` → `detected`（保留全部已扩写内容，继续参与 merge_or_new）。
```json
{
  "action" : "defer",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.5 raise（研究并提议，debt→提案）
`detected` → `raising`（逐轮扩写）→ `card_sent`。
```json
{
  "action" : "raise",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.6 trash（丢弃→回收站）
→ `trashed`（30 天 purge，除非 pin）。
```json
{
  "action" : "trash",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.7 restore（回收站→原状态）
`trashed` → `prev_status`。
```json
{
  "action" : "restore",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.8 pin（回收站项设永久）
回收站项标记永不 purge。注意：shared `InboxVerb` enum 刻意没有此 case，Mac 发裸字符串——动词合法。
```json
{
  "action" : "pin",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.9 accept（验收）
`review` → `delivered`（记 `execution.accepted_at`）。
```json
{
  "action" : "accept",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.10 rework（打回，携带反馈文本）
`review` → `executing`（反馈送回原 session）。**Mac 端空反馈不发空串**：用户留空时 Swift 端替换为固定自查指令（逐字）：`Zelin 打回了这次交付但没有写具体理由。请对照本需求的 definition_of_done 逐条自检：每一条是否真正达成、产出物是否在承诺的位置、质量是否达到可直接使用的程度。找出差距，自行改进后重新交付，并用两三句话说明这次改了什么。`——web 端留空打回必须复刻同一字面量（客户端行为，不是 actd 行为）。
```json
{
  "action" : "rework",
  "comment" : "标题里的数字对不上，请重新核对来源后再交付",
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.11 done_external（已办完·系统外完成）
允许 `card_sent|review|approved|executing` → `delivered`；executing 且有 session 先 harvest 再 stop（均 best-effort）。
```json
{
  "action" : "done_external",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.12 abort_execution（退回提案，丢弃成果）
允许 `approved|executing|review` → `card_sent`；session_id 归档为 `aborted_session_id`。
```json
{
  "action" : "abort_execution",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.13 stop_to_review（去待验收，停 agent 留成果）
允许 `executing|approved|review` → `review`（harvest 后置 `execution.done` + `review_at`）。
```json
{
  "action" : "stop_to_review",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.14 revert_review（退回待验收）
`delivered` → `review`（删 `accepted_at`，记 `reverted_at`）。
```json
{
  "action" : "revert_review",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.15 archive（封存）
仅 `delivered|detected` → `archived`（文件 relocate 进 `act/registry/archive/`）。
```json
{
  "action" : "archive",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.16 unarchive（放回看板）
`archived` → `prev_status`；archived 卡对**其它一切动词**都是中央闸门 no-op（先 unarchive）。
```json
{
  "action" : "unarchive",
  "comment" : null,
  "id" : "R-001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 2.17 merge_apply / merge_dismiss（合并建议卡，id = MS-*）
建议级动作走同一 card 路径（Mac 端 `submit()`），所以 **Mac wire 带 `comment: null`**（shared iOS encoder 不带——见 §6 R4；golden 以 Mac 为准）。`merge_apply` 接受 AI 合并建议并确定性执行；`merge_dismiss` 撤掉建议卡。
```json
{
  "action" : "merge_apply",
  "comment" : null,
  "id" : "MS-0001",
  "ts" : "2026-08-30T12:00:00Z"
}
```
```json
{
  "action" : "merge_dismiss",
  "comment" : null,
  "id" : "MS-0001",
  "ts" : "2026-08-30T12:00:00Z"
}
```

## 3. 特形动作（各自的字段集，无 `comment` 键）

### 3.1 split_note（§38.2 拆成新卡，fold undo）
`note_ts` = 折叠备注行的 ts 标签，逐字回传。
```json
{
  "action" : "split_note",
  "id" : "R-001",
  "note_ts" : "2026-08-30T11:58:03Z",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 3.2 set_title（§37 改显示名）
客户端先归一：所有空白 run（含 U+3000）折成单空格并 trim（Swift `PendingSweep.normalizedTitle` = actd `" ".join(title.split())`），非空且 ≤64 才发；actd fail-closed 复验（Python code points 计数）。同值改名 = 客户端 no-op（不写文件）。
```json
{
  "action" : "set_title",
  "id" : "R-001",
  "title" : "EB-1A 推荐信 3 封定稿",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 3.3 merge_review（§21 多选请求合并建议）
`ids` ≥2 张 R- 卡，**保持用户选择顺序（不排序、不去重——Mac 选区是 Set 天然无重）**；actd 复验。
```json
{
  "action" : "merge_review",
  "ids" : [
    "R-012",
    "R-007"
  ],
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 3.4 merge_force（§21bis 强制合并）
`ids` **去重保序** ≥2、`primary` ∈ ids（客户端守卫 + actd 复验，malformed 整体丢弃）。
```json
{
  "action" : "merge_force",
  "ids" : [
    "R-012",
    "R-007"
  ],
  "primary" : "R-012",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 3.5 feedback（§29 建议上报，无 `id`）
`ids` **升序 sorted**（可空 = 对整体）；`publish` bool 恒在（GitHub 公开跟踪表 opt-in）；可选 `images` = `state/feedback/attachments/` 下的本机 PNG 绝对路径（图片永不上传）。text 与 images 双空 = 客户端不发。
```json
{
  "action" : "feedback",
  "ids" : [
    "R-007",
    "R-012"
  ],
  "publish" : false,
  "text" : "运行中列的排队原因 chip 希望能点开看详情",
  "ts" : "2026-08-30T12:00:00Z"
}
```
空 `ids` 的字节形状（**注意空数组的三行渲染**，golden `feedback-overall`）：
```json
{
  "action" : "feedback",
  "ids" : [

  ],
  "publish" : true,
  "text" : "看板整体加载很快，但暗色模式下对比度偏低",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 3.6 answer_input（§39 回答需输入）
`text` 客户端按 **unicode scalars（= Python code points）** 裁到 4000（`InboxAction.clipAnswer`）；actd 复验 trimmed 1..4000，卡必须 EXECUTING（需输入行只投影 executing 卡），roster 探针防杀 mid-run session。**附图无新键**：尾行 `[附图，用 Read 工具查看] <路径>` 拼进 `text`（前缀常量 = `act/actd.py` `ANSWER_ATTACHMENT_PREFIX`，两侧逐字一致；附图行占 4000 预算，正文让位）。
```json
{
  "action" : "answer_input",
  "id" : "R-001",
  "text" : "用方案 B，先跑通再优化",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 3.7 capture（§10 快速捕获；文件名 `capture-<UUID>.json`）
无 `id`。可选 add-only 键：`mode:"run"`（§34 direct-run，跳过提案闸直落 `approved`，**一律新卡不判重**，幂等键 = 文件 stem，见 §34.1）；`images`（§10bis，`state/attachments/` PNG 绝对路径，actd 边界校验：非 list 整体忽略、仅收非空字符串、去重、上限 4）；`preset`（§34bis，仅 `"proposals_triage"` 且必须同时 `mode:"run"` 才生效，否则 preset 被 fail-safe 忽略、当普通 capture 处理）。
```json
{
  "action" : "capture",
  "text" : "给 OpenReview 提交 rebuttal，提醒我周五前",
  "ts" : "2026-08-30T12:00:00Z"
}
```
direct-run 变体（golden `capture-run`）：
```json
{
  "action" : "capture",
  "mode" : "run",
  "text" : "跑一下 tests 里挂掉的 test_dashboard 修掉",
  "ts" : "2026-08-30T12:00:00Z"
}
```
提案积压清理 preset（golden `capture-preset`；`text` 与 `preset` 是**双端字面量常量**，Swift `ProposalsTriage.captureText/presetKey` = actd `PROPOSALS_TRIAGE_PRESET`，一字不许动）：
```json
{
  "action" : "capture",
  "mode" : "run",
  "preset" : "proposals_triage",
  "text" : "清理提案积压：审阅提案列的积压卡片，给出保留\/丢弃\/合并建议",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 3.8 weekly_digest_now（§24，无 `id`）
```json
{
  "action" : "weekly_digest_now",
  "ts" : "2026-08-30T12:00:00Z"
}
```

### 3.9 import_claude_sessions（§22，无 `id`）
```json
{
  "action" : "import_claude_sessions",
  "session_ids" : [
    "0f9d3a1c-5b7e-4a2d-9c81-2e6f4b8d0a13",
    "7c2e9b40-88ad-4f0e-b1d2-3a5c6e7f8901"
  ],
  "ts" : "2026-08-30T12:00:00Z"
}
```

## 4. G1 复刻配方（Python，已对全部 33 个 golden 逐字节验证通过）

```python
import json

def mac_json_bytes(obj: dict) -> bytes:
    """复刻 Mac JSONSerialization [.prettyPrinted, .sortedKeys] 字节。"""
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                   indent=2, separators=(",", " : "))
    s = s.replace("/", "\\/")            # NSJSONSerialization 转义正斜杠
    s = s.replace("[]", "[\n\n  ]")      # 空数组三行渲染（见下方警告）
    return s.encode("utf-8")             # 末尾无换行
```

- **警告 1（空数组替换的适用域）**：字符串 `replace("[]", …)` 只在「用户文本不含字面 `[]`」时安全——本 schema 里空数组只出现在 `ids` 位，但 `text`/`comment` 是自由文本。G1 生产实现应结构化处理（序列化前检测空 list、逐 key 拼装，或先把字符串值占位再回填），不要照抄这个 fixture 级配方。斜杠转义同理更稳的做法：对**字符串值逐个** `json.dumps` 后做 `\/` 替换再拼装。
- **警告 2（key 排序）**：`.sortedKeys` 与 Python `sort_keys=True` 在本 schema（全小写 ASCII key）下一致；新增 key 时保持小写下划线命名即可维持这一等价。
- **原子写**：`tmp = inbox_dir / (stem + ".json.tmp")` → write → `os.rename(tmp, inbox_dir / (stem + ".json"))`；stem 用 `uuid.uuid4()`（capture 可加 `capture-` 前缀对齐 Mac 习惯，非必须）。
- **动词白名单（server 入站 API）**：本文档 §2 + §3 目录 = 全集，一个不多一个不少；未知动词/未知字段 400（BUILD-CONTRACT zero-tolerance）。`expected_status`/`board_seq` 不在 web 入站面上（§1 末条）。

## 5. golden fixtures（`tests/fixtures/inbox/`）

33 个 `<verb>[-variant].golden.json`，由 `make_golden.swift` 生成（`swift make_golden.swift <outdir>`，序列化调用与 App 逐字一致）：§2 全部 18 个动词 + `split_note` / `set_title` / `merge_review` / `merge_force` / `feedback`(+`-overall`,`-images`) / `answer_input`(+`-attachment`) / `capture`(+`-run`,`-images`,`-preset`) / `weekly_digest_now` / `import_claude_sessions`。

G6 对照规则：固定输入（id/text/ids 用 golden 里的值）+ 把 server 产物的 `ts` 值替换为 `2026-08-30T12:00:00Z` 后**逐字节比较**；`images`/附图路径含 tmpdir 时同样先做值替换（golden 用 `/tmp/zai-demo/...` 占位）。替换只许动 JSON 值、不许 reserialize——reserialize 会洗掉 `\/` 与空数组渲染，测试就失去牙齿。

## 6. 风险备注（CONTRACT 散文 vs Swift 代码；code wins）

- **R1 字节形状**：CONTRACT §3 示例是单行紧凑 JSON、key 序 `id,action,comment,ts`；Mac 实际落盘是 prettyPrinted+sortedKeys（`action,comment,id,ts`）。actd `json.loads` 两者通吃，但「与 Store.swift 产物逐字节等价」的验收以 pretty 形为准（本 golden 集）。
- **R2 动词清单**：§3 散文 `action ∈ approve|reject|comment` 是 v0.1 化石；真全集 = §10 + `set_title`/`split_note` 特形分支（actd `_apply_decision` elif 链即白名单）。BUILD-CONTRACT 2.1 说「白名单 = live CONTRACT §3 现有清单」应读作「App 今天真实会发的动词」= 本文档目录。~~TODO(contract)~~ **已落**：CONTRACT §3 的 v0.48 追记（T-17）把动词清单指向 §10 全集 + golden 字节形。
- **R3 双编码器并存**：Mac `writeInboxFile`（pretty）与 shared `InboxAction.encode`（紧凑、仅 sortedKeys，iOS→syncd 路径）产出两种合法字节形。parity 目标按 BUILD-CONTRACT 指名 Mac 形；不要拿 shared encoder 当 golden 源。
- **R4 merge_apply/merge_dismiss 的 comment 键**：Mac 走 generic card 路径带 `"comment": null`；shared encoder 省略该键。actd 无视。golden 带 null（Mac 形）。
- **R5 `\/` 转义**：NSJSONSerialization 转义正斜杠、Python 默认不转——byte-parity 的最大陷阱，路径类字段（`images`、附图尾行）必踩。配方见 §4。
- **R6 长度单位漂移**：`set_title` Swift 守卫按 Character（grapheme cluster）数 ≤64，actd 复验按 code points——emoji/组合字符标题可能 Swift 放行、actd 拒收（fail-closed no-op，无害但静默）。web 端按 code points 裁（JS `[...str].length`）比 Swift 更贴 actd。`answer_input` 的 4000 上限两侧都已按 code points（Swift 用 unicodeScalars），照抄即可。
- **R7 `ts` 不被校验**：actd 今天不解析 inbox `ts`（provenance-only）。格式仍必须保持 `YYYY-MM-DDTHH:MM:SSZ`——registry/审计侧同格式假设。
- **R8 无 `id` 动作**：`capture`/`feedback`/`weekly_digest_now`/`import_claude_sessions`/`merge_review`/`merge_force` 无卡片级 `id` 键——G1 校验器不得对它们强制 `id`。
- **R9 rework 空反馈替换文案**：是 Mac 客户端行为（§2.10 字面量），actd 不做此替换；web 不复刻则空打回会被 actd 当空 comment 处理，语义走样。~~TODO(contract)~~ **已落**：字面量随 CONTRACT §10 的 v0.48 追记（T-18）冻结入典。
