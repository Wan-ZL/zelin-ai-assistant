用中文 review 这个 pull request。

- PR 的完整 diff 已放在 `/tmp/pr.diff`；仓库完整代码在当前工作目录（read-only）。
- 这是一个 macOS 个人助理（lite 2.0）：屏幕录制 → ingest 进 Obsidian → 会议纪要。组成：Dock 壳（`shell/Sources/*.swift`）、本机看板 server（`server/`，唯一 HTTP 面）、web 前端（`web/src`）、录制 ingest 脚本（`ingest/`）、纪要与共用库（`act/recap.py`、`act/lib/`）；`docs/CONTRACT.md` 是行为契约的记账。

审查重点（按此顺序）：

1. **正确性**——只报有具体失败场景的真缺陷（给出输入/状态 → 错误结果），不要风格意见、不要假设性担忧。
2. **回归**——diff 是否改变了未声明要改的既有行为；对照上下游调用方。
3. **安全与隐私**——任何把本机路径、密钥、用户数据带出本机的代码路径都是红线级问题；headless claude 必须走 `act/llm.py` 或 `ingest/claude-env.sh` 的隔离常量，任何读写 `~/.claude` 的代码都是红线（宪法第 13 条、§78）。
4. **写者与录制安全**——`state/recap/` 只由 `act.recap` 写（flock 串行），marks 与请求台账只由 `server/` 写；注意 TOCTOU 与半写状态；安装、升级、卸载永不打断正在录制的 screenpipe 引擎（§61.8、§79）。

输出格式：按 P0/P1/P2 分级列出问题，每条含 `file:line`、一句话断言、失败场景；没有问题就明确说「未发现需要修改的问题」。保持简洁，不要复述 diff。
