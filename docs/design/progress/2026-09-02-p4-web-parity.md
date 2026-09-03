pr: `feat/p4-web-parity`（P4 余量；无版本 bump，版本由 tag 派生）
phase: P4（D3 / R2.2.2 / R2.2.3；s4 顺序第 3、5、6、7 步 + Tier-0 0.1–0.3、0.7）
law: **§68（新增）** / §15 追记 / §26 追记 / §28 追记 / §49 路由表 / §54 追记 + §54.1 第 11 项

**web / shell 补齐原生 app 剩余全部用户功能，旧 app 可按 R2.2.4 退居 "(old)"**：server-owned 设置目录（`GET/PUT /api/settings/{section}`，10 个通用区目录驱动、diff-write 与 nested 拼法判例钉在 config.py 上）+ 凭证面（0600、值 write-only、探针注入缝、Slack 自动填 owner id）+ 权限体检（壳桥探 TCC 三项 + server 列 FDA 可执行文件的可复制真实路径与 doctor TCC 行）+ 诊断（doctor 缓存 / health / deploy_state / install_report / 源健康 / 日志尾巴 size-cap）+ 首次运行向导（config 从模板、FDA、可选凭证、完成标记）+ 关于 / 更新（§26 CLI 落点；Sparkle 不进壳，D17 替代）+ MCP 只读（Skills 商店 = §67）+ 导入 Claude 会话；看板：合并建议卡 / 多选操作条（批量批准 T2 跳过）/ 提建议 / 清理积压 / 改名 / 拆分 / 捕获历史与斜杠命令 / 在终端接管 / 横幅一键修复 / 永久性完成整页；壳：NotifyRelay 搬入、TCC 探针与深链、登录自启、Dock 徽章、⌃⌥Space 全局快速捕获、vault-sync-helper + framegrab 进壳 bundle。server 路由表驱动化（`_route_get` 账本划掉）。

**未做（§68.14 诚实例外）**：粘贴图片上传通道、Apple Events 终端偏好、会话索引搜索、看板动画、同步 / 配对区——P5 seed。**待 owner**：新机器上 Documents / 屏幕录制 / 通知授权按壳 id 各做一次（权限体检页有步骤）；一周日用无需打开旧 app 即 P4 完成判据。
