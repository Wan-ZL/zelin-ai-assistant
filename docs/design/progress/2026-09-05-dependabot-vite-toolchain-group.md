pr: `ai/self-improve/R-284`（PR #245；R-284「修红 CI：PR #197 vite 6.4.3 → 8.2.2」的 dependabot 配置半边）
phase: 横切（依赖维护；D5 / D12 每日循环「红 CI 是臣子的事」）
law: —（无新 §；dependabot 在 CONTRACT 里只作 §70.3 ⑪ 追记的判例出现，§0 第 7 条运行时白名单未动，web/ dev 白名单只列包名不锁版本）

**病灶**：dependabot 对 `web/` 逐包开 PR。vite 跨大版本时 `@vitejs/plugin-react` 的 peer range 跟着换代（4.x / 5.x 只认 `vite ^4.2 || ^5 || ^6 || ^7`，6.x 只认 `^8`；`vitest` 的 `@vitest/*` peer 更是逐版本精确钉死），单包 bump 的 PR 在 `npm ci` 那一步 ERESOLVE，required 的「Web tests」与「QA gates」在装依赖时就红、测试根本没跑（判例 #197，vite 6→8）。vite 8 本身的升级另走 #244（R-195，接替 #197）。

**改法**：`.github/dependabot.yml` npm 面加 `groups.vite-toolchain`，patterns = `vite` / `@vitejs/*` / `vitest` / `@vitest/*`——以后这几包在同一个 PR 里一起解析、lockfile 一次重铸，peer 才对得上。只加这一个键：schedule / open-pull-requests-limit / labels / commit-message 逐字不变，无 `ignore` / `allow`（分组不关掉任何更新，不匹配的包照旧单独开 PR；不带 `applies-to` 即只管 version updates，security updates 不受影响）。

**门**：`check-jsonschema --builtin-schema vendor.dependabot`（含拼错键的阴性对照会红）、PyYAML 结构断言（对 origin/main 的差只有 `groups`）、`ruff check .`、`compileall`、全量 unittest、`scripts/qa/hygiene.py` / `depgraph.py`、`changelog_fragments.py check` / `progress_log.py check`。不触及 web/ 与 shell/，对应两门按 §56.8 路径 filter 不需跑。
