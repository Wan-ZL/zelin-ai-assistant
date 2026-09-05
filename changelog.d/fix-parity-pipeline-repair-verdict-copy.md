type: fixed
- **管线横幅的一键修复现在报诚实下场**（CONTRACT §68.8 追记；原生 PipelineRepair 的 15 s 轮询回来了）：`POST /api/repair/actd` 成功后每 1 s 问一次 `/api/health`、最多 15 轮——心跳回来 → 绿字「已恢复 ✓ 数据重新更新了」停 6 s 再刷 store（横幅退场）；15 轮没转好 → 「自动修复没成功：后台服务已重启，但数据还没更新——点「让 AI 修」深挖，或查看日志」+ 再试一次 + 让 AI 修（`POST /api/ai-fix {source:"doctor"}`）+ 可复制的手动命令。此前等待句「（最多 15 秒）…」会永远挂着、失败态只在 POST 本身被拒时出现。向导末步「启动后台服务」走同一状态机（`useRepairActd`）。
- **`stalled` 横幅只说已知的**（§47.4 追记）：正文改为「后台服务已 N 分钟没有心跳（最后阶段：…）——卡在原地或已停止，卡片不会动。」——server 只 stat 心跳文件、从不探进程，旧句「actd 进程还活着」断言了谁也没查过的事。
- **横幅的手动命令可以一键复制**：新 `chrome/CopyLine`（原生 CopyPathLine：label + `<code>` + 复制 → 已复制 1.5 s）；`launchctl kickstart -k …` 不再揉进 stalled / stale 的正文句子，三态动作行都带同一条可复制的「手动命令：」。
