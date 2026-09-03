type: changed
- **CI 在 `dev` 整合分支上每次前进都跑全量（CONTRACT §56.8 追记）**：`ci.yml` 的 `on.push.branches` 从 `[main]` 变为 `[main, dev]`。PR 先合进 `dev` 再提升到 `main` 的那段时间里，两个各自为绿的 PR 合在一起是否还绿此前没人测；现在 push 到 dev 与 push 到 main 同法——per-PR filter 恒 `true`、macOS Apple 套件与 web 套件全跑、fail-closed 三条不变。`concurrency` 语义不动（dev 的 push 不取消在跑的，每个 dev 头留下自己的判决）；`release-on-merge.yml` / `update-pr-branches.yml` 的 push 触发仍只认 main。
