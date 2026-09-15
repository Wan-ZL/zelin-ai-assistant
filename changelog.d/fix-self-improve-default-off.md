type: changed
- **「自动改进本软件」的通道出厂关闭，并在设置页「开发者」区给出开关**（issue #307，owner 决策 D54）：`self_improve.enabled` 默认 `true → false`。关着时每日循环不读 GitHub（`issues` / `prs` / `mutation` 三个读取器零 gh 调用，审计行记 `off`）、§65.5 的 PR 巡检不跑、self_improve lane 不再免批派发，因此不会再有 🤖 开头的「改本软件」卡堆进待验收列；已经在列里的卡原地不动。维护者在 设置 → 开发者 → 「自动改进本软件（每日循环的 GitHub 提案 + 草稿 PR 通道）」打开即可，actd 下一个 pass 生效、无需重启。交付核验与 lane 会话的出网封锁不受这把开关影响。
