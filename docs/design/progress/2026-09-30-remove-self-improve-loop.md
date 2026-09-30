pr: `chore/remove-self-improve-loop`
phase: 横切（做减法；owner 决策 D86）
law: §65 墓碑 / §51 第二条 lane 墓碑 / §70.3 ⑨–⑪ 墓碑 / §75.2 墓碑 / §81.7 墓碑 / §0 第 12 条修宪 / §0 第 4 条③ / §1 / §2 / §4 / §15.3 / §50 / §53.2 / §57 / §78 / §81.2 追记

owner 原话（2026-09-30）：「你把这个自动读 issue 写 PR 的循环功能完整删掉」「我也觉得要做减法」。背景：AI PR 洪水看不过来；普通用户安装的另一台 Mac 上循环照样铸卡；花钱；零件多。

删掉的：§65 self_improve 自动草稿 PR 通道整条——`act/lib/self_improve.py`、`server/self_improve_lane.py` 与 `POST /api/self-improve/resume`、免批准入（`policy.may_auto_dispatch` 与全部天花板、`dispatch.auto_dispatch_pass`，§51 自此零免批 lane）、零 MCP 出网封锁（`llm.NO_MCP_ARGV`）、收割时的 `gh` 交付核验、敏感路径暂停、PR 巡检与跟进卡、拒绝记忆、owner 身份、frozen-in-flight、结算即释放（`worktrees.release`）、`self_improve_enabled` 配置字段与 overrides、设置页「开发者」区两行、web 的 `SelfImproveBanner` 与 PR 核验章；§70 每日循环里读 GitHub issue / PR 评论 / PR 红 CI / 夜间变异报告的三个读取器与 `gh` 注入缝、GitHub 同题去重、分诊标签过滤；actd 的 `_refresh_raw_key` 与总账的 `<块>.<键>` 开关拼法（唯一客户 `autodispatch.enabled` 已无人读）。

留下的：维护半边（去重合成、过时清扫、D74 待验收老化、§75 worktree 回收）、advisory 读取器与维护横幅、素材库提案、PR 评审 workflow、mutation-nightly 作为测量任务（pinned issue 照常更新，只是没人再据此铸卡）、并发上限 / 睡眠闸 / queued 子状态。

兼容（add-only）：`channel=self_improve` 的存量卡按普通 proposed 卡加载，D74 的「隐藏 🤖」旗照发；`needs_mcp` / `execution.self_improve` / `execution.delivery` / `auto_dispatch_block` 照读不写；policy 免批过但没派出的卡（`execution.auto_dispatched`）由 `dispatch_approved` 的 `_withdraw_retired_auto_approval` 一次性退回潜在任务，owner 再点促成运行时 `rearm_dispatch` 清掉痕，护栏每张卡至多触发一次；旧 config.yaml 的 `self_improve:` 块与旧 overrides 键静默忽略；dashboard 顶层 `self_improve` 冻结为常量关闭形（golden 逐字节不变）；store2 白名单行一条不删。

owner 主力机上的可见变化：素材库提案原先挂在 `self_improve.enabled` 下，现在改挂 yaml 专用的 `daily_loop.materials_enabled`（出厂关）——不写这个键，素材库就不再铸卡；要它就在 config.yaml 的 `daily_loop:` 下写 `materials_enabled: true`。判例 `tests/test_retired_self_improve_lane_is_inert.py`。
