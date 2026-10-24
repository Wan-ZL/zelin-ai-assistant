type: fixed
- **全新安装的 doctor 不再报 `state dirs` 缺失**：`install.sh` 现在同时建 `state/logs`（doctor 的这一行要求它，1.x 由已退役的 actd 启动时建）。首次 lite fresh-install CI 在 macOS runner 上发现。
- **安装不再把 checkout 弄脏**：`ingest/claude-env.sh` 以 755 入库。它此前是 644，`install.sh` 的 `chmod +x ingest/*.sh` 每次都把它变成一处 tracked 改动，bootstrap 因此报「local edits」不更新，`scripts/upgrade.sh` 也会把这棵 checkout 当成脏树拒绝升级。
