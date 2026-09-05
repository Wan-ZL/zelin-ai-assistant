type: fixed
- **看板弹窗一律按钮提交，Enter 换行（D35 推及弹窗）**：「提建议」弹窗不再把 Enter 当发送——Enter 在 textarea 里就是换行，Shift+Enter / ⌘↵ / Ctrl+↵ 也不提交，「↩ 发送 · ⇧↩ 换行」提示句随之去掉；这同时关掉一处 IME 险情：此前拼音候选上屏的回车会把半截建议直接上传给维护者（勾了公开还会成为公开 GitHub issue），弹窗在 POST 前就关了、无路可回。「强制合并」按钮摘掉没有任何绑定的 `title="↩"` 招牌（Esc 取消照旧）。修改方向 / 打回 / 回答 弹窗本就是按钮提交，不动。CONTRACT §41 追记（含墓碑）/ §54.1 追记。
- **「提建议」弹窗正文换回 §29 的明示条款**：发送后建议全文与所选卡片的标题快照会上传给维护者（即使关闭了匿名统计），请勿包含敏感信息；勾选公开时还会出现在公开 GitHub 仓库的 issue 列表里。此前的「本地先落 state/feedback/，勾选公开才会同步成 GitHub issue」暗示本地闭环，是原生注释点名禁止的说法。
- **ui/parity**：原生弹窗键位提示句 `control:board.dialogs:label:send-newline` 经 `CONTROL_OWNER` 标 retired（理由带 §41 2026-09-05 追记），清单重铸；两本账本零改动。
