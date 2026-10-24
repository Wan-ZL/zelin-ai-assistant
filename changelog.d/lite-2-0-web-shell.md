type: removed
- **壳（Zelin's AI Assistant.app）随 lite 2.0 收窄（PR-B）**：实时字幕引擎与悬浮窗（LiveCaptions / CaptionCore / CaptionOverlay）、终端接管队列消费与壳心跳（TerminalLauncher / TerminalRelay）、全局快速捕获快捷键 ⌃⌥Space 与菜单「聚焦捕获框 ⌘L」、Dock 徽章、framegrab 抽帧工具、从旧菜单栏 app 一次性迁移偏好（LegacyPrefs）全部删除；壳桥不再提供 `setCaptions` / `setCaptionPrefs` / `setBadge` / `probeCaptionKey`，快照里不再有 `captions` 块与 `hotkey` 键；`Info.plist` 不再申请 Apple Events 授权；壳不再写匿名使用统计（`Analytics` 只剩空实现）。
- **火山引擎两把字幕 key 从凭证页与 `GET /api/secrets` 移除**（`volcano-speech-key.txt` / `volcano-ark-key.txt`），PUT 回执里的 `legacy_pair` 一并删除。
- **安装器不再扫除「孤儿」录制引擎**：`install.sh` 的 `ui` 步与重启壳的 SIGKILL 回落都不再 `pkill` screenpipe。1.x 的完整壳代码留在 git tag / branch `full`。
