pr: `qa/contract-s77`（PR #396）
phase: 横切（QA；§58 家族第六件事，owner 决策 **D79**）
law: §77（新增，含 .1–.7 七小节）；D79

2026-09-15 夜 owner 要「今天这一次的 test 能够完整的覆盖所有的功能所有的操作，以防止我在以后使用中出现问题」，并同时要更新 README、产出多模型评审通过的演示视频（不建 VM，boot 卷 ≤1 GB，视频与中间物只落仓库外的媒体目录）。本轮把这套一次性的全覆盖跑法沉淀成 §58 家族的第六件常驻门：不是「某条测试绿」，而是「产品能做的每一件事今天还成不成」——一份机器清点的场景表，每行配一条可执行证据。

**分母（§77.1）**：`coverage_inventory.py` 从五个源清点（`docs/CONTRACT.md` 节 / `ui/parity/native-inventory.json` gated id / `server/app.py` 路由 / `server/settings_catalog.py` 字段 / `shell/Sources` 壳面）铸出 `qa/coverage_inventory.json`（committed、按 id 排序、`--check` 陈旧即红），本轮 1363 场景。**证据 DSL 与跑者（§77.2）**：`unittest:` / `parity:` / `swift:` / `axprobe:` / `http:` / `settings:` / `flow:` / `fixture:` 八种，`full_coverage.sh` 每种重工具整跑一次、写三态报告 R（末三行 `PRESENT=`/`MISSING=`/`WAIVED=`）。**B 档（§77.3）**、**壳探针（§77.4）**、**README 审计（§77.5）**、**视频管线（§77.6）** 各自成节。

**诚实条款的落点**：proof 编不出来就留空并上报（`missing_proof` 是门的第一公民计数），补法只有两条——补真判例或按 `tombstone` / `covered-by-<test>` / `design-not-carried D<nn>` 三种 reason waive。首轮清点出 18 条 no-proof，全部按第一条收口（issue #385–#389：§42 判例指针、`/api/settings/daily-loop` 的 409 分支、六个 web 偏好键的 vitest 往返、九个壳 UserDefaults 键的 BridgeHarness pin、`hasCompletedFirstRun` 的 http 证据），落地 `missing_proof=0`。

**§77.7 事故记名**：覆盖跑者第一次全量跑（临时 HOME）把 owner 正在跑的 live 壳杀了两次、并用一个 ad-hoc 签名的 dev 构建顶替了 `/Applications` 的稳定签名——根因是 install.sh 用 `pgrep -x ZelinAIBoard` 判「要不要重开壳」、uninstall.sh 直接 `pkill` 并从硬编码 `/Applications` 删 bundle，这两条都不看 HOME。修法：`pgrep`/`pkill` 也做成恒「没匹配」的 PATH 假货，`AIASSISTANT_UI_APPS_DIR`（install.sh 既有 seam，uninstall.sh 本轮补齐）把 bundle 安装/删除关进沙箱。ad-hoc cdhash 与 owner 授的 Full Disk Access 对不上，壳因此写不出 `state/shell.heartbeat`——需 owner 在系统设置里对重装后的壳重授 FDA。
