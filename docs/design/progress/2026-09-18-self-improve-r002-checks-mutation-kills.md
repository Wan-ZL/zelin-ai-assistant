pr: `ai/self-improve/R-002-checks-mutation`（self_improve lane 草稿 PR #431；同一发现的第五个 PR）
phase: P5 → P3 闭环（§57 夜间变异存活体 → §70 循环铸卡 → §65 lane 出 PR）
law: §57 靶区映射（`qa/mutation_targets.toml`）/ §58（test-code skill 读项目门，行为无改动）

**先说结论**：这张卡要的补网**两天前就写完了，躺在没合的草稿 PR 里**。R-002 没有再重写一遍判例，
而是把 #401 的字节原样搬过来，逐字节校验过（`git diff origin/ai/self-improve/R-301 -- <5 个路径>` 空输出）。
owner 要做的不是再审一遍判例，是**从五个 PR 里挑一个合掉、关掉其余四个**。

**为什么会有五个 PR**：夜报（pinned issue #150）对着 `main` 跑。补网只存在于未合的分支上，
所以 `main` 上的 `skills/test-code/scripts/checks.py` 每晚都被重新判成 839 位点 / 435 杀 / 404 活 / 51.8%，
`self_improve` 循环每晚据此铸新卡。到目前为止同一个发现已经产出五个 PR：

| 卡 | PR | 分支 | 开出时间 | 内容 | CI |
|---|---|---|---|---|---|
| R-217 | #391 | `ai/self-improve/R-217` | 2026-09-16 06:24Z | 5 份新判例 + toml 映射（只映射自己那 5 份） | 全绿 |
| R-301 | #401 | `ai/self-improve/R-301` | 2026-09-16 13:09Z | 4 份新判例 + toml 映射（含两份**既有**判例）+ changelog + 进度文档 + 交付物 | 全绿 |
| R-8153 | #429 | `ai/self-improve/R-8153` | 2026-09-18 13:20Z | #401 的字节，零新判例 | 待跑 |
| R-002 | #430 | `…/R-002-checks-mutation-twins` | 2026-09-18 13:24Z | #401 的字节，零新判例（另一个 R-002 session） | 待跑 |
| R-002 | **#431 本 PR** | `…/R-002-checks-mutation` | 2026-09-18 13:45Z | #401 的字节，零新判例 + 本轮独立复核 | 见「门」一节 |

五个 PR 的 toml 改动落在**同一行**（`qa/mutation_targets.toml:93`），所以合掉任意一个，其余四个必然文本冲突。
这不是坏事：它保证不会有人不小心把两份重复的判例一起合进去。合掉一个之后，其余四个直接关掉即可，
下一晚的夜报就不会再铸下一张。

**🔴 两个 lane 缺陷，比补网本身更值得 owner 看一眼**：

1. **同一个夜报发现被守护进程并发扇出到五个并行 session，卡号还互不相同。** 本轮 `ListAgents` 看到
   除本 session 外，还有 `R-002`、`R-001`、`R-8153` ×2 **四个** session 挂着同一个
   「补测试：checks.py 变异存活」标题——连本 session 共 **5 个并行**，再加上已收工的 R-217 / R-301,
   同一个发现一共被处理了 **7 次**。它们各自独立跑同一套判读、各自开 PR，算力与 token 成倍消耗。
   另一个 R-002 session（#430）撞上了同一个分支名冲突，也只能加后缀（`…-twins`）——
   **两个 session 独立地得出同一个结论，这本身就是 lane 缺陷的证据。**
2. **卡号跨发现重复，而 lane 契约按分支名找 PR。** 另一个 session 拿到的卡**也编号 R-002**，
   但内容是完全无关的 web Filters popover Escape 修复；它先占了分支名，于是
   `ai/self-improve/R-002` 现在指向 **PR #427**（`fix(web): close the Filters popover on Escape…`，
   与 #425/R-306 同题）。lane 契约写的是「分支必须叫 `ai/self-improve/R-002`，守护进程按它查 PR」——
   照办只有两条路：非 fast-forward 被拒，或 force-push 覆盖掉 #427 别人的工作。
   **后者是破坏性的、契约本身也禁止（"never force-push"），所以没做。** 本 PR 因此开在
   `ai/self-improve/R-002-checks-mutation` 上，并在此显式记录这个偏离：守护进程按
   `ai/self-improve/R-002` 去查会查到 #427，把一个 web 修复当成本卡的交付物。这需要 owner 修 lane
   （卡号全局唯一，或分支名带发现指纹），不是能在 PR 里绕过去的事。

**为什么选 #401 而不是 #391**（两者都全绿，都对 `main`，都 MERGEABLE）：

- #401 的 toml 映射额外补进两份**早就存在、却从没进靶区**的判例（`tests/test_skill_test_code_run_ladder.py`
  308 行、`tests/test_skill_test_code_detect.py` 252 行，两份都 `import checks` 并驱动它的 builder 与菜单）。
  这一行本身值 21 体，零新代码。#391 只映射自己新写的 5 份。
- #401 带齐了这条 lane 的文书（`changelog.d/` fragment + `docs/design/progress/` fragment + `deliverables/` HTML）；
  #391 只有判例和 toml，缺 §56.7 的两个 fragment。
- #401 的判例 docstring 里**自己写明了哪一档是弱的**（见下「诚实分档」），#391 的 catalog 判例把
  `tier`/`phase`/`est` 三列当同一种东西对着 `references/tiers.md` 钉，没有分档说明。
- 🔴 **决定性的一条：前向兼容实测。** 把下面缺陷 1 的修法（`_npm_audit_steps` 已有的
  `_tool(ctx, "npm")` 闸门）真打到 `_e2e_for_pkg` + `_b_perf_budget` + `_b_bundle_size` 三处，
  再跑两个孪生各自的判例：**#401 `Ran 100 tests OK`（exit 0），#391 `Ran 96 tests FAILED (errors=2)`（exit 1）**。
  #391 有两条判例把**缺陷行为本身**钉死了（它的 `fake_det` 默认 `tools: {}`，于是「缺 npm 仍返 cmd」
  成了断言）：`test_bundle_size_prefers_the_npm_script`（`ApiBundleLicenseTestCase`）与
  `test_npm_bench_script_is_the_js_path`（`PerfBudgetDeadCodeTestCase`）。
  也就是说：**修缺陷 1 时 #401 一行判例都不用动，#391 必须改两条。**
  精度注记：只给 `_e2e_for_pkg` 打闸门时两个孪生都全绿——那两条判例钉的是
  `_b_bundle_size` / `_b_perf_budget`，所以这个判别器必须把三处都打上才显形。
  实验在 job tmp 的两棵 `git worktree add --detach` 里做，没有碰本分支或共享 checkout。

**独立复测（不采信 PR 标题的数字）**：本分支上 `scripts/qa/mutate.py --modules skills/test-code/scripts/checks.py
--force` 全量重跑，839 位点、`checks.py` 的 `content_hash` 与夜报同为 `322d706b…`（生产代码零改动，
位点集合逐字相同）。实测：

```
sites_total=839  executed=839  killed=836  survived=3  timeout=0  error=0
score=0.9964     complete=True  budget_hit=False
```

即**存活 404 → 3，杀伤 51.8% → 99.64%**。两个数不是一回事：杀伤率 836/839 = 99.64%，
存活下降 (404−3)/404 = 99.3%。卡面 DoD 第 1 条「存活减半」的门是 ≤ 202，实测 3。
活下来的三个位点（`int_plus1@556:30` / `return_none@438:8` / `return_none@572:12`）与 #401
标成等价体的那三个**逐字相同**，本轮没有出现 #401 没见过的新存活体。夜报口径与全量口径一致：
这个模块夜报本来就 `executed = sites_total = 839` 跑满，没有「只跑了半张地图」的问题。
映射后的定向子集是 9 份判例、217 条测试、0.23 s。

**诚实分档（沿用 #401 判例 docstring 里的实测，本轮复核同意）**：404 个存活体按「杀它的 oracle 从哪来」分三档，
93 + 157 + 154 = 404。

- **93 体 = 文档同源**：`tier` 82 + `TIER_TIMEOUTS` 11。oracle 是四份独立 markdown
  （`references/tiers.md` / `references/catalog.md` / `SKILL.md` / `references/triggers.md`），
  只改 markdown 不动代码会红，反向也会红。这一档是真 drift detection。
- **157 体 = 行为锚定**：phase 派发完备性 42、builder 闸门 45、internal 算术 34、plan 构造 35、菜单理由回落 1。
  oracle 是派生的或独立来源（`7*24*3600` 用 `timedelta(days=7)` 重算、`_line_hash` 锚 RFC 3174 的
  sha1("abc") 向量、CRAP 按 tiers.md 的公式手算）。
- **154 体 = 只有「照现状抄的 golden 表」能杀，即改动检测，不是行为防护**：`est` 88 + `phase` 同档间改动 66。
  仓库里没有任何文档载这些秒数，`est` 也没有下游行为后果。`phase` 那 66 体的正确消融是**保留线程派发
  这个观测通道、只把期望值换成从 `checks.CATALOG` 现算**；这么跑 66 体全存活。早一轮的自评只删了字段
  回读那段、期望值仍留着 `PHASE_GOLDEN`，因此得出「照样全死」的错误结论，#401 已自我改正。

本轮独立复核（45 个 agent、5 个 lens、35 条经对抗性反驳存活的 finding）把这个 154 逐位点重算，
**与 #401 自报的数字到个位吻合**：`est` 88 体删掉 `EST_GOLDEN` 后 88 全活（derived 断言 0 杀），
`phase` 108 体删掉 `PHASE_GOLDEN` 后 66 活 / 42 由派生断言杀。两档下界要分清是哪种消融：

| 消融 | 存活 | 杀伤 |
|---|---|---|
| 不消融（实测） | 3 | 836/839 = 99.64% |
| 只删 `EST_GOLDEN`（#401 进度文档报的那个） | 91 | 748/839 = 89.2% |
| 删 `EST_GOLDEN` + `PHASE_GOLDEN`（本轮复核报的那个） | 157 | 682/839 = **81.3%** |
| 再把 `tier` / `TIER_TIMEOUTS` 的文档表也当「同一份数据的第二份抄本」折掉 | 265 | 574/839 = 68.4% |

最后一档**过苛、本轮不采用**：`tier` 不只是一张抄来的表，它有独立的行为通道
（`checks.default_checks` 与 `run_ladder.timeout_for`，两者都被判例逐档穿过），所以把 `tier` 归成
「同一份数据的第二份抄本」是错的。

**卡面 DoD 第 1 条（存活减半，门 ≤ 202）在实测值和前两档消融下都满足**：3 / 91 / 157 全部 ≤ 202。
只有最苛那一档（存活 265）不满足，而那一档的前提已被复核否掉。诚实地说清这层依赖关系：
这道门成立依赖「`tier` / `TIER_TIMEOUTS` 的文档同源算真 oracle」这条判断——本轮认定它成立，
理由是上面那两个行为通道，但它不是零假设，写在这里供 owner 复核。

**剩下 3 个是等价变异体**，#401 用机器对拍复核过（施加变异后与原模块在一大片可观测面上逐字相同），
本轮未改动、未重判：① `int_plus1@556:30`（`raw.split("#", 1)[0]` → `maxsplit=2`，只有下标 `[0]` 被消费，
任何 `maxsplit ≥ 1` 的 `[0]` 都是第一个分隔符之前那段）；② `return_none@438:8`（`_unpinned` 早退
`return False → None`，唯一消费点 `checks.py:447` 是布尔语境）；③ `return_none@572:12`（`dangling` 闭包同款，
唯一消费点 `checks.py:579`）。为这三个写判例等于去钉 Python 的返回类型而不是产品行为。

**四条仍在等 owner 拍板的真缺陷**（前三条 #401 查出、第四条本轮新查出；按「只修测试网」的范围一条都没改，
判例钉的是**今天的行为**，所以修任何一条都会故意把相应断言变红，要一起改）。**前三条随 #401 一起被搁了两天，
本文重述一遍，免得 owner 挑了本 PR、关掉 #401 之后它们跟着丢掉**：

1. **缺工具被报成项目失败。本轮复核把范围从三处扩到十处**（#401 只点了三处）：`checks.py` 里有
   **十个** `kind: "cmd"` / `"substituted"` 的 plan 直接 exec `npm` / `npx` 而从不问
   `_tool(ctx, "npm")` / `_tool(ctx, "npx")`，尽管 `detect.py:25` 明明把两者都探进了 `det["tools"]`
   （`detect.py:72-73`）。#401 点名的五处：`_e2e_for_pkg` :998（npm）与 :1000（npx/playwright）、
   `_b_perf_budget` :1174（npm）、`_b_bundle_size` :1323（npm）与 :1325（npx/size-limit）；
   另有五处同形（走 npx 的 knip / jscpd / api-extractor / license-checker / stryker 只确认了
   本地 `node_modules/.bin` 里有那个 bin，没过 `_b_ts_typecheck` / `_b_js_lint` 用的 `_js_ready(...)` 闸门）。
   同文件的 `_npm_audit_steps`（:1128）查了（`if not _tool(ctx, "npm"): return []`），所以这是不一致而不是取舍。
   后果：没装 node/npm 的机器上这十层把必然 ENOENT 的命令交给 runner，记成 **RED（项目坏了）
   而不是 UNAVAILABLE（工具没装）**，破的是 skill 的头号契约——报告里那个三分「not run」划分
   （`SKILL.md` / `references/report-template.md`）。一行修法：照 `_npm_audit_steps` 在这些分支前加
   `_tool(ctx, "npm")` / `_js_ready(...)` 闸门，不过则 `_unavailable("npm not on PATH")`。
2. **`docs_drift` 的目录级 `.gitignore` 条目不豁免目录内文件**：`_ignored_literals`（:552-558）特意
   `.rstrip("/")` 把 `build/` 收成字面量 `"build"`，但 `dangling`（:570-573）只比整条路径和 basename
   （`if path in ignored or os.path.basename(path) in ignored`），**从不比路径前缀**，那个 rstrip 白做。
   后果：gitignore 掉构建输出目录、又在文档里提到里面某个文件的仓库，`docs_drift` 假红
   （经 `TRIGGER_CHECKS`，`documented_behavior` 触发器上也假红）。本轮复核补两点精度：
   触发还需另四个条件同时成立（token 的首段在 `top_dirs` 里、扩展名在 `_DOC_EXT` 内
   ——`checks.py:537-540` 已排除 `.json` / `.txt`、路径未被 tracked、该顶层目录至少有一个 tracked 文件），
   所以命中面比听起来窄；另外它**只读仓库根的 `.gitignore`**，子目录里的 `.gitignore` 一概不看。
3. **设计问题，不算缺陷，但和成文规则冲突**：`_span_cov`（:711-712）在函数区间内一条 coverage 数据都
   没有时返回 `1.0`，于是完全没被测到的函数拿到最好的 CRAP 分。这是 fail-**open**，而模块 docstring
   写的是「自制检查一律 fail closed（读不到 = fail，不是 pass）」。请 owner 定：「没有行数据」算
   `cov=0.0`、进 `errors`、还是保持现状并给 docstring 那句话加个例外。

4. 🔴 **本轮新查出的第四条（#401 没有，安全相关）：超过 1 MiB 的文件被 `_scan_files` 静默跳过，
   `secret_scan` 因此对本仓库最大的那份文档完全失明。** `lc.read_text`（`ladder_common.py:200-203`）
   对超帽文件返回 `None`（帽 = `TEXT_CAP_BYTES`，`ladder_common.py:34`，1 MiB），
   `_scan_files`（`checks.py:376-386`）在 `if text is not None:`（:382）把它丢掉——
   **既不记违例，也不进 `errors`**，而 `_finish_scan`（:389-393）只看 `errors`，所以这次跳过
   从不出现在报告里。这与 `_scan_files` 自己的 docstring 直接矛盾：那行写的是
   「读不到/解析不了记 errors（caller fail closed）」，实际走的是 fail-**open** 的静默路径。
   实测：`docs/CONTRACT.md` = **1,513,333 字节 > 1,048,576**，所以本仓库跑第 1 档
   `secret_scan` 时它被整份跳过、零提示。四个消费者同受影响：`secret_scan`（:426）、
   `actions_sha_pin`（:457）、`test_smells`（:530）、`docs_drift`（:591）。
   一行修法：`_scan_files` 里把 `text is None` 归进 `errors`（`"%s: over TEXT_CAP_BYTES" % rel`），
   于是 `_finish_scan` 按既有的 fail-closed 语义报出来；或者提高帽子。请 owner 定哪个。

另有一条轻的：`_ctx` 在五份判例里有 5 个定义、4 种签名（`skills/` 下没有 `def _ctx`，
生产侧的工厂是 `checks.py:48` 的 `make_ctx`，五个 helper 都在包它）。这是判例侧的重复，不是生产缺陷。

**门**（本分支，2026-09-18）：

- `python3 -m compileall act ingest` → OK
- `ruff check`（四份判例）→ All checks passed
- 定向子集 `python3 -m unittest`（9 份映射判例）→ **Ran 217 tests, OK**，0.23 s
- **单元/行为层全套**（525 个 `tests/test_*.py` 模块，不含 `tests/integration/`）→
  **`Ran 7776 tests in 349.791s`，唯一一条失败且为既有假红**：
  `tests.test_readme_audit_repo_readme.RepoReadmeIsCurrentTest.test_no_stale_claims`
  —— README 第 13 行的版本字面量 `v1.0.114` 落后于当前 tag `v1.0.116`（§56.1：tag 是版本唯一真源）。
  **与本轮无关**：本 PR 不碰 `README.md`。证明法是在 job tmp 里开一棵指向 `origin/main` 的
  detached worktree 跑同一个模块——**逐字同样的失败**（同一行、同一对版本号），
  而本轮那八个文件一个都不在那棵树上。
- **integration 层**（22 个 `tests/integration/*.py`）→ ⚠️ **本机未能跑完，判决交给 CI**。
  三次尝试都在第 1 / 第 13 个测试处 **exit 144** 被系统收割：本轮五个 session 并发处理同题卡、
  十几个 agent 同时在跑，load 13–51（18 核）、swap 接近耗尽。
  **这是资源问题不是判例问题**，证明法同上：在安静机器上那棵 pristine main 的树里跑
  `integration/test_auto_deploy_defer_episode`，得 `Ran 5 tests in 131.089s OK exit 0`
  （它本身就慢——5 个测试 131 秒，所以它是负载下最先被杀的那个）。
  **不谎报绿**：这一层看 PR 上的 `Tests on ubuntu (Python 3.9 / 3.x)` 两条腿。
  （`PYTHON_COLORS=0` 是本机必需：不加另有一个**与本轮无关的既有**假红
  `tests/integration/test_auto_deploy_script.py`，Python 3.14 的彩色 traceback 把它 grep 的字符串切开了）
- 定向子集另在**高负载**下复跑一次以排除超时污染：`Ran 217 tests in 139.432s / OK`，
  而 `per_mutant_timeout_seconds = 60`。这条曾被复核者提为「超时被 `mutate.py` 记成杀伤 ⇒ 分数虚高」，
  **已被反驳并由实测否掉**：本轮 report.json 的 `timeout = 0`、`error = 0`，836 个杀伤里没有一个来自超时；
  安静机器上子集 0.23 s，对 60 s 有 260 倍余量，夜报又跑在专用 ubuntu runner 上。
- `python3 scripts/qa/ledger_diff.py --base origin/main` → **0 finding**，即卡面 DoD 第 2 条
  「覆盖率地板不降」：没动任何 baseline、没加 ledger key、没降地板、没放松 `qa/gates.toml`
- `scripts/ci/changelog_fragments.py check` / `progress_log.py check` → ok
- `scripts/qa/mutate.py --modules skills/test-code/scripts/checks.py --force` →
  `sites_total=839 executed=839 killed=836 survived=3 timeout=0 error=0 score=0.9964 complete=True`
  （报告与 state 全放本轮 job tmp，没碰 `.qa/`）

**给下一个 session 的三条**：

1. 接「补测试：X 变异存活 N 体」卡的第一条命令是 `gh pr list --state open`。这张卡是同一个夜报发现的
   **第四次**铸卡；前三次的成品都在且 CI 全绿，重写第四份判例是纯浪费，还会造出第四套互相冲突的文件。
   **夜报对 `main` 跑，所以存活数只会在 PR 合进 main 之后才掉**；只要没合，它每晚都会再铸一张。
   见到「同一模块同一数字第 N 次铸卡」不要当成判例写坏了。
2. 开工第二条命令是 `ListAgents` + `gh pr view <自己的分支名>`：本轮同题卡被并发扇出到六个 session，
   而卡号会跨发现重复，分支名可能已被一个**完全无关**的发现占用（本轮就是，见上面 lane 缺陷第 2 条）。
