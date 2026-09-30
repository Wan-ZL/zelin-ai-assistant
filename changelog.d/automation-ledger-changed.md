type: changed
- **回收站不再自动硬删（出厂默认 `trash.retention_days` 60 → 0）**：硬删是整条管线里唯一同时满足「自动、不可逆、动的是用户数据」的动作，与设计宪法第 2 条「绝无不可恢复的自动删除」正面冲突。机制一字不改，改的只是出厂默认 —— 回收站的卡一直躺着，什么时候清由人点。config.yaml 或设置页里写过数字的安装（含写着 60 的）行为不变。
- **六把「设置页翻了却要重启守护进程才生效」的开关自此下一 pass 生效**：`trash.retention_days`、`card_summary.enabled`、`updates.check_enabled`、`features.feedback_sync`、`autodispatch.enabled`、`archive.after_days`。哪些开关必须现读，名单由总账派生（`automation.live_fields()`），不再靠人手抄进刷新点。
- **近重复这一族从三个互不知情的调度器收成一把闸**：入库前折叠判官（每张候选新卡一次真 LLM 调用）、每 pass 近重复巡检、判决落盘，此前只有中间那个勉强算有闸。自此三处共用 `features.merge_silent` —— 关掉它，雷达每轮就真的不再花那笔钱。
- **素材库提案并进「自动改进本软件」的通道开关**：素材库铸的同样是 self_improve 卡（target 是本仓库、plan 写着「实现成草稿 PR」），却从不跟着那把出厂关着的总开关关。D57 说的「关着时不再产生新的 🤖 卡」自此是真的。
