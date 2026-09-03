pr: `chore/issue-sweep`（PR-6；无版本 bump）
phase: §5.1 LOOP 行清账（每日循环 seed 首批；每 issue 一个 commit）
law: §7 / §10 / §15 / §18 / §49（dev 白名单）/ §54.1 第 11 项

**8 个 loop-seed issue 落地**：#15 CI 钉 Xcode（`.github/xcode-version` + `scripts/ci/select_xcode.sh` 双 workflow 共用，缺版本 fail-loud）；#16 ingest 链 smoke test（`tests/integration/test_ingest_smoke.py`，桩 claude；inbox 不可读 exit 1 不再伪装空）；#18 `demo_seed --english`（词表逐值替换、零 CJK 判例；外部 PR #136 评审后由本实现取代）；#19 `docs/assets/social-preview.png` + `promo/social-preview.sh`（上传是 owner 手动步骤，无 API）；#11 §7 `egress[]` 建 repo 出机披露；#7 §10 `capture_id` 贯通 inbox → sources → dashboard；#37 consent 标记视口可见才写 + 失败事件只传 `failure_id`（AST privacy lint）；#8 web `CardSurface` 键盘路径 / 状态词 aria-label / 复制播报 / axe-core 扫描。

附带：`build_dashboard._project` 与 `actd.py` 行数账本双双缩水（`_proposal_extras` / `registry.capture_source` 抽出，零行为变化）。

**剩余 open issue 处置**：#90 needs-owner（D18 摘要制）；#29 / #28 / #27 mac-retire——等 P4 web 设置页 re-home（本轮无 PR 覆盖）；#23 素材库 → P5；#20 pinned 输入源不动；#127 已随 #135 关；#128 → P6 附注；#129 → P5b（PR-5）。
