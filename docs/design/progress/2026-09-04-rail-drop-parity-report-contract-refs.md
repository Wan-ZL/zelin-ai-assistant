pr: `fix/rail-drop-parity-report-contract-refs`（#205 review 收尾；docs + parity 报告，无版本 bump，版本由 tag 派生）
phase: P4 余量（D3：web 看板是产品）；D29 / D30 落地后的两处 review finding
law: §54.4 判例行原地改正（陈旧引用）/ §66 报告重铸（truth = `ui/parity/report.json`）

**问题**：#205（`feat/rail-drop-ask-fold-deps`，已合并、随 v1.0.16 发出）的 review 抓到两处不一致。(1) 入库的 `ui/parity/report.json` / `report.md` 还是改动前那份——PRESENT 844，`control:ask:*` 九条、`rail:ask` / `rail:deps` / `screen:ask` 仍判 PRESENT——而 §54.4 追记（「报告 MISSING 0（truth = `ui/parity/report.json`）」）与进度片段 `2026-09-04-rail-drop-ask-fold-deps.md` 都拿它当 truth 说 PRESENT 832；法典指着一份说反话的报告。(2) §54.4 判例行仍写 `tests/test_server_ask.py`（ask / slack manifest / uninstall / maintainer 四组路由）与 `NavRail.test.tsx`（八项、⌘1…⌘8）——前者已随 ask 组退役改名，后者已是六项；`grep -oE 'tests/test_[a-z0-9_]+\.py' docs/CONTRACT.md` 里出现了树上不存在的文件。

**做了**：`PATH=/usr/bin:$PATH python3 scripts/ui/parity_check.py --check` 重铸报告并入库：gated 851 = PRESENT 832 / PENDING 15 / MISSING 0 / STALE 0 / WAIVED 4；not gated 里 retired 33 → 53、informational 423 → 415（ask 整面与两颗 rail 项归 retired；`control:deps:*` 全部仍 PRESENT——它们只是搬到设置面判，没退役）。`pending.txt` / `waivers.txt` 零改动。§54.4 判例行原地改：`tests/test_server_slack_uninstall_maintainer.py`（slack manifest / directory / uninstall / maintainer 三组路由；原 `test_server_ask.py`，ask 组随 §27 墓碑退役）、`NavRail.test.tsx`（六项、⌘1…⌘6，并指向下条 2026-09-04 追记）；§49 追记里对旧文件名的历史引用去掉 `tests/` 前缀，让「CONTRACT 引用的每个 `tests/test_*.py` 都在树上」这条 grep 门自此为空。#205 的 changelog 片段已随 v1.0.16 发出、不改，本 PR 另写 `changelog.d/fix-rail-drop-parity-report-contract-refs.md`。

**门**：web typecheck / build / vitest 过；ruff / compileall / unittest 过；`scripts/qa/run_gates.sh` 六门 OK，跑完 `git status --short ui/parity` 为空。Playwright golden 未动（本 PR 不改任何渲染）。
