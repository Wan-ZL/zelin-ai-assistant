type: changed
- **README 改写为 lite 2.0（中英两版）**：它是什么（录制、Obsidian 导入、会议纪要、一个小的 Dock app）、安装（`git clone` + `bash install.sh`，只支持 macOS）、升级（关于页「立即升级」或 `bash scripts/upgrade.sh`）、隐私（只在本机、零遥测、headless claude 用自己的 `CLAUDE_CONFIG_DIR`、从不读写 `~/.claude`），以及一个围栏的「Removed in 2.0」块。迁移说明写明 1.x 与 2.0 不能并存，最后一个完整的 1.x 版本归档为 git tag 和 branch `full`，回到它：`git fetch --tags && git checkout full && bash install.sh`。
- **README 截图换成 lite 界面的真渲染**：会议纪要（中英各一张）、设置、关于、权限体检，由新的 `scripts/qa/readme_shots.mjs` 从 demo server 拍（固定时钟；拍图机器的路径改写成普通安装上的样子）。
- **README 审计修两处误判（§77.5）**：`removed` 围栏的第一行再写 `Removed in 2.0` 不再被算成第二个块；反引号里以 `~` 或 `/` 开头的整段路径不再按空格切开。
- **docs/ROADMAP.md、docs/design/README.md 改写为 lite 的内容**，docs/SANITIZATION.md 去掉注册表条目那一条，docs/LICENSE-FAQ.md 的竞争用途示例改成会议纪要与录屏助理。
