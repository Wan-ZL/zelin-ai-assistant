type: fixed
- **设置页 Slack / Gmail 的「启用 … 雷达」开关补回原生的 §48.1 合取写**：翻开时 server 同一笔把 `features.<src>_radar` 也写 true（yaml 里关着的 flag 不再让开关「显示开启、雷达静默」），翻关只写单键；agent 的装 / 卸仍归 install.sh 与「重新安装」（§48.7）。翻开后页面整本目录再拉一次，「Feature flags」那一格随之对上。
- **Gmail 接入区「抓取方式」A / B 以生效的抓取命令为真源**（§14bis）：命令生效着选 A = 真的停用它（PUT 清键）+ 「已切回 A：…」，再选 B 直接写回；命令空着选 B 才出现命令字段 + 「填好下面的抓取命令并点「保存」即生效。」；命令来自 config.yaml 清不掉时如实说。此前单选只是本地显示，选了 A 雷达仍走 B。选 A 的那次 PUT 不再吞掉同区没保存的草稿（走设置区的按键合并）。
- **Gmail 地址的邮箱形状校验回来了**（原生 validateAddress 逐字）：server 目录字段带 add-only `check`，PUT 不合格 400，web 保存前就地拦、「保存」不放行（只看要保存的键——config.yaml 里一个没改过的坏地址不锁住开关）；第 ① 步引导复原两步验证前提与公司 Workspace「The setting you are looking for is not available for your account」提示。
