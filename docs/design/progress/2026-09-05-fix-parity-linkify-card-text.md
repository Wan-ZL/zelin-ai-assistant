pr: `fix/parity-linkify-card-text`（行为对齐批次 `linkify-card-text`，链 cards-face 第 2 批）
phase: P4 收尾（D3：web 看板继承原生看板行为规格；§66 审计「原生有、web 丢」清账）
law: §54.1 追记（无新 §）

对照退役原生 app 的行为审计找出一条卡面 / 详情丢失项（gap `board-cards-linkify`，user_impact medium），一批修回。原生 `Utils.swift:877-889` 的 `linkified(_:)` 用 NSDataDetector 把纯文本里的 URL 标成 `.link` + 下划线，SwiftUI `Text` 直接可点；应用在四处——提案卡摘要（`Cards.swift:1073`）、潜在任务卡摘要（`:2028`）、「💬 需求来自」引文（`:508` / `:1311`，原生注释「Slack quotes often carry links — make them clickable」）、「📋 要做什么」步骤（`:529` / `:1329`）。运行中 / 待验收行的标题与正文原生**明确不** linkify（`:1829`：链接点击与整卡复制手势冲突，放弃并记入完成报告），AI 研究中占位、「怎样算办完」、交付正文也不。web 这四处此前全是纯字符串，只有 markdown 正文（`MarkdownDocument`）出链接。

落点 = 展示积木 `web/src/components/board/Linkified.tsx`：`linkifyParts(text)` 纯函数按 `https?://` 切段，`<Linkified text>` 把 URL 段渲染成 `<a class="linkified" target="_blank" rel="noreferrer">`（壳的 `WKUIDelegate` 自 #223 起把 `target=_blank` 交系统浏览器），其余仍是裸文本节点；href 过 `detail/markdown.ts` 既有的 `sanitizeUrl` 白名单（正则只认 `https?://`，`javascript:` / `data:` 本就不匹配，白名单是双保险）。URL 边界照中文引文习惯：空白、`<>`、`「」()（）` 不算进去，尾随标点（`。，、；：！？.,;:!?` 与右引号）不算进去（NSDataDetector 同判，「见 https://x.dev/a。」链接是 a 不是 a。）。**没有 URL 时原样返回字符串**——DOM 与此前逐节点相同，既有判例与视觉 golden 一个不动（demo 数据无 URL）。

接线与原生边界一比一：`CardHead` 加 add-only `linkify` 开关（缺省 false），只有提案卡与潜在任务卡的摘要优先面打开——`aria-label` / T2 与拒绝弹窗正文仍是纯字串，链接只在可见标题里；运行中 / 待验收 / 已完成行标题与占位不开。`DetailFields`（D34 唯一详情面）的摘要段（含「交付了什么」下的灰色审批时摘要——同一字段）、每条引文、每个步骤（「[修改方向]」行仍橙）走 `Linkified`；交付正文、「怎样算办完」、产出、备注不走。样式一条 `.linkified`（`--accent` 色 + 下划线 + `overflow-wrap: anywhere`，字级字重继承所在文本）。不动 `cardMarkdown.ts` / `MarkdownDocument`。无新文案（不涉 i18n）、无 wire 变化、不读 store。

三个新判例文件各钉一个行为（防腐 #7）：`Linkified.test.tsx`（纯函数切段 + 渲染：无 URL 原样、`」` `）` `。` 在链接外、`javascript:` / `data:` / 裸域名 / `https://.` 不成链、两个 URL 各自成链）、`CardHead.linkify.test.tsx`（提案 / 潜在任务摘要出 `<a target=_blank rel=noreferrer>`、`aria-label` 纯字串、无 URL 时标题 DOM 单文本节点、占位与运行中行不出链）、`DetailFields.linkify.test.tsx`（摘要 / 引文 / 步骤出链且文本逐字不变、「怎样算办完」与交付正文不出链、无 URL 时整面零 `<a>`）。ui-parity 门 MISSING 0 / STALE 0，`report.json` 未变。
