pr: `ci/visual-goldens-job`（PR #TBD；无版本 bump，版本由 tag 派生）
phase: 横切（流程；§56 / §66 QA 仪表）
law: §66.4 追记（golden 由 runner 渲染；truth = `.github/workflows/visual-goldens.yml`）

**做了什么**：「Web visual (playwright)」自 2026-09-04 起在 main 上连红，六张全红、每张 1–3 % 像素——下载 `web-visual` artifact 看 diff：布局逐像素一致，亮起来的是每一个 CJK 字形和按钮描边，典型的文字光栅化漂移（capture 机器的 PingFang / Chromium 构建 / hinting 与 `macos-latest` 镜像不同），背后零 UI 改动。多位 reviewer 的结论一致：golden 必须出自将来判它的那台 runner。注：任务简报说 CI 在 ubuntu 上比对，实际 ci.yml `web-visual` 一直是 `macos-latest`（job log 路径 `/Users/runner/…`）——漂移是「Mac 对 Mac 镜像」，不是「Mac 对 ubuntu」，结论不变。

新 `workflow_dispatch` 工作流 `.github/workflows/visual-goldens.yml`（「Refresh visual goldens」，`ref` 输入默认 main）：`runs-on` 与 setup 步逐字镜像 ci.yml `web-visual`（同一 pinned checkout / setup-node / upload-artifact SHA、node 22、`npm ci` + `npx playwright install chromium`、PyYAML、`npm run build`），跑 `playwright test --update-snapshots=all` 重截全部六张，然后**再原样比对一次**——同一 runner 紧接着复现不了自己的输出 = runner 自身不确定，job 红并上传 diff 三件套，那套 golden 不许提交。产物 = artifact `visual-goldens`（`web/e2e/__screenshots__/**`）+ step summary 里每张 png 的 sha256 与实际 checkout sha（`GITHUB_SHA` 在 dispatch 上是工作流所在 ref，不是 `inputs.ref`）。job 自己**不 commit 不开 PR**：`GITHUB_TOKEN` 开的 PR 在本仓不触发 CI，人 / agent 下载、覆盖、开 PR 才是通道。`permissions: contents: read`，`concurrency` 不 cancel（每次 capture 都是独立证据）。

文档：CONTRIBUTING「Visual baselines」改写——本地 `npm run visual:update` 只用来看自己改了什么，**待提交的 golden 永不在 Mac 上生成**；流程 = `gh workflow run "Refresh visual goldens" --ref main -f ref=<branch>` → `gh run download <id> -n visual-goldens` → 覆盖 `web/e2e/__screenshots__/visual.spec.ts/` → PR 写明哪几张为何变并引用 run URL；`macos-latest` 标签迁大版本时两个 job 一起迁、刷一次 golden 是唯一合法的「六张全变零 UI diff」PR。CONTRACT §66.4 追记一条 add-only 同义法条；ci.yml `web-visual` 的注释改成指向新工作流（runs-on / setup 改了那边要逐字跟）。

**门**：actionlint（两份工作流）、`scripts/ci/changelog_fragments.py check`、`scripts/ci/progress_log.py check`、`scripts/qa/hygiene.py`。后续同日第二个 PR：在 main 上跑一次该工作流，把 runner 出的六张 png 提交（`chore(visual): re-capture goldens on the CI runner`），使「Web visual (playwright)」在 main 上转绿。
