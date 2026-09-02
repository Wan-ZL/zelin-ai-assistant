pr: `feat/material-box`（无版本 bump，版本由 tag 派生）
phase: P5 前置（D11；R2.5.1–R2.5.4 的存储 / 设置入口 / 弹窗 / 抓取器；循环消费留 P5 主体）
law: **§62（新增）** / §49 路由表追加 / §45 范围追记

**素材库**：设置页 section「素材库」= 一行表单（链接 + 一句备注）+「查看待处理（N）」弹窗（原生 `<dialog>` 可滚动，**只列尚未开 PR / 完成 / 放弃的**，每行可放弃；client 不做第二套过滤，防腐 #10）；台账 `state/materials/materials.jsonl`（append-only 全记录行、按 id 折叠、1 MiB 自压缩只丢最老终态、开放条目 500 封顶拒绝、`flock` 串行）；状态机 `new → picked_up → proposal_created → pr_opened → done` + 任何非终态 → `dismissed` + `dismissed → new` 回程票（宪法 2），`transition()` 唯一改状态、`links{proposal_id, pr_url}` 只增不删、判例逐格穷举；**不铸卡不写 registry 不走 Slack**（owner 原话三条一一对应）。

`materials.fetch(url)` 给循环：yt-dlp 字幕（装了才用，argv 钉死，部分失败有字幕不算错）→ oEmbed 标题退路，网页 stdlib html→text（charset 三级、2 MiB / 20k 字双上限），永不抛；`prompt_block()` 唯一进 prompt 形态，owner 备注与抓取内容各自 `fence_untrusted`；server 三端点四闸 + 错误映射（404 / 409 `CONFLICT {reason}` / 501）；GET 表路由 handler `(ctx, query)`。新代码 CC ≤ 5、覆盖 100%、qa 账本零新增。
