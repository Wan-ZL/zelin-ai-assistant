pr: `ai/self-improve/R-195`（R-195「修红 CI：PR #197 vite 8.2.2」，接替 dependabot PR #197）
phase: 横切（依赖维护；D5 / D12 每日循环「红 CI 是臣子的事」）
law: —（无新 §；§0 第 7 条运行时白名单未动，dev 白名单同名不同版）

dependabot PR #197 把 vite 6.4.3 → 8.2.2，但 `@vitejs/plugin-react@4.7.0` 的 peerDependency 是 `vite ^4.2 || ^5 || ^6 || ^7`，`npm ci` 直接 ERESOLVE，required 的 `Web tests` 与 `QA gates` 加 informational 的 `Web visual` 三个 job 全在 install 那一步红。修法 = 在 main 头上（dependabot 分支落后 main 337 个文件、只有 lockfile 是它的）同时 bump vite `^8.2.2` 与 `@vitejs/plugin-react` `^6.1.1`，重铸 `web/package-lock.json`（-1351 / +469 行，vite 8 换 rolldown 后依赖树瘦了一截）。6.x 的其余 peer（`oxc-transform-react` / `@rolldown/plugin-babel` / `babel-plugin-react-compiler`）全 optional，没装；`vite.config.ts` 零改动，`react()` 默认即可。

本地证据：`npm ci` 干净、`npm run typecheck` 0 错、`npm run build` 857 ms（vite 8 的 reporter 多一条 >500 kB chunk 提示，与 6.x 时同一个 585 kB bundle，非回归）、vitest 81 文件 1399 通过 / 4 skip、playwright 40 通过（board / trash / settings × light / dark 六张 golden 零 diff、未更新）、`tests.test_web_build_self_contained` + `tests.integration.test_web_build_outside_repo` + `tests.test_install_ui_step` 38 条 OK、`scripts/qa/run_gates.sh` 见 PR 描述。
