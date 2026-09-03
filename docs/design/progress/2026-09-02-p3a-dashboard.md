pr: `refactor/p3a-dashboard`（P3a；无版本 bump，版本由 tag 派生）
phase: P3（R2.3.2 老代码达标；防腐 #9 同名模块）
law: §2（投影 golden 条款）/ §15 / §19 区 / radar health 段落（三处墓碑）/ §58.4（P3a 记录）

**首批 CRAP 清账，零行为变化**：`act/lib/dashboard.py`（`build_dashboard._project` CC 72 / 365 行 → 每 lane 一个行构造器 + `_Session` join 结构体 + `_lane_row` 分派）、`act/lib/silent_merge.py`、`act/merge_review.py`、`act/lib/analytics.py`、`act/lib/sanitize.py`、`act/lib/telemetry_upload.py` 全部函数 CC ≤ 6、CRAP ≤ 6。手法 = 网先于拆：`tests/fixtures/dashboard_golden.json` 逐字节 golden（走遍全部 lane 分支、键序在内，含 #7 capture_id / #11 egress / §63 recaps 顶层键）+ 五个 characterization 判例文件（此前 4–17% 覆盖的路径：roster 读取、merge_review 材料收集/CLI、silent_merge sweep/consume、sanitize 词表、analytics gate 损坏形状）先在重构前的代码上记录，再纯抽取；最后用重构前的 main 重新生成 golden 与重构后逐字节比对，一致。

**结构门清账**：analytics ↔ sanitize import 环——共享件（`MASK` / `SECRET_PATTERNS` / 掩码算法）下沉新叶模块 `act/lib/secret_patterns.py`，analytics 不再 import sanitize（sanitize 侧为 redaction 事件的懒 import 保留：事件被判例钉死、15 处 scrub 调用点含 executor/radar 不在范围，反向不可零行为变化）；两组同名模块改名（`act/lib/health.py` → `radar_health.py`，`act/lib/analytics_sync.py` → `telemetry_upload.py`；入口 `act/analytics_sync.py` 与 cron 行不变；actd / 三个 radar 只改 import 行与 `radar_health.` 前缀）。账本：complexity −35、crap −42、deps −4、hygiene −6（crap 按 CI canonical artifact 为准）；`.test-code/baselines/structure.txt` −3。
