pr: `ai/self-improve/R-002-checks-mutation-twins`（draft PR #430；同一批存活体的第四张卡。卡面指定的 `ai/self-improve/R-002` 已被另一张同号但无关的卡占用，见文末）
phase: P5 → P3 闭环（§57 夜间变异存活体 → §70 循环铸卡 → §65 lane 出 PR）；孪生仲裁，非新判例
law: §57 靶区映射（`qa/mutation_targets.toml` hunk 与 PR #401 逐字节相同）/ §58（行为零改动）/ §65（lane 按分支名验收）

**这张卡是第四张（开工时是第三张），动手第一条命令按 R-304 立的规矩查孪生，当时查到两张。** 夜报（artifact `mutation-report`，
run 35232295853，2026-09-17T15:00Z）把 `skills/test-code/scripts/checks.py` 报成 **839 位点 / 435 杀 /
404 活 / 51.8%**，`executed = 839` —— 全量跑满，不存在 R-292 那种「只跑了半张地图」的问题。同一批 404 体
已经有两个 open draft PR 各自独立做过：

| PR | 分支 | 自报 | 形制 |
|---|---|---|---|
| #401 | `ai/self-improve/R-301` | 404 → 3（51.8% → 99.6%） | 4 份新判例 + 把 2 份**既有**判例补进靶区映射；带 changelog fragment、进度台账、HTML 报告 |
| #391 | `ai/self-improve/R-217` | 404 → 4（52% → 99.5%） | 5 份新判例；无 fragment、无进度台账 |

两个都还是 draft、两个 CI 全绿、两个同一 merge-base（`f5edb5a1c2`，落后 main 59 个 commit）。
**neither 合车 ⇒ main 的靶区映射没变 ⇒ mutate.py 的 state 照旧复用 ⇒ 夜报每晚复述同一批 404 ⇒
daily_loop 继续铸卡。** 这就是第三、第四张卡存在的全部原因（同日 `.claude/worktrees/` 里还有一个
`ai/self-improve/R-8153`「checks-mutants」停在 main HEAD 上 —— 窗内它果然开出了 **#429**，做法与本 PR
完全相同）。所以本轮交付的不是第四套判例，而是**一次实测仲裁 + 把胜者的 hunk 逐字节搬到本分支**。

**收尾时再清点一次（可核验）：同一批 404 个存活体现在有五个 open PR** —— **#391**（`R-217`）、
**#401**（`R-301`）、**#429**（`R-8153`）、**#430**（本 PR）、**#431**
（`ai/self-improve/R-002-checks-mutation` —— 另一个**同样叫 R-002**、同样撞了分支名后加后缀的
session）。后三个都是「逐字节采用 #401 + 自己的仲裁/复核报告」这同一个形状，彼此独立收敛。

## 判决的地基：三件先验事实，逐个核过

1. `skills/test-code/scripts/checks.py` 在 main / R-301 / R-217 上**逐字节相同**（blob `669cacb6c5c1`，
   sha256 `322d706b…` == 夜报 state 的 `content_hash`）。三份原映射判例也逐字节相同。⇒ 404 体的
   site_id 清单在三个分支上直接可比，行号不用重算。
2. 加测试不会让已死的位点复活 ⇒ 只需对那 404 个存活体判决，不必重跑全部 839。
3. 两个分支都没改 `checks.py`、没改任何既有判例 ⇒ 每个分支的杀伤可以只跑它**新增/新映射**的那几份文件
   来判，但本轮为了与夜报口径完全一致，还是按各分支的**完整映射集**跑（R-301 九份 / R-217 八份）。

> 踩到一个 zsh 坑，记一笔：`git show "$r:path"` 里 zsh 会把 `:s…` 当成 history modifier 施加到
> `$r` 上，path 被吞掉，`git show` 转而打印 commit 本体（283 字节），于是三个分支的 checks.py 看起来
> 哈希全不一样 —— 差点据此得出「孪生是在旧版 checks.py 上做的」这个错误结论。正确写法是
> `git show "$r":"path"`（冒号在两个引号之间，落在参数展开之外）。

## 探针与实测

写了 `probe_kills.py`（一次性，留在 job tmp，不进 repo）：import `scripts/qa/mutate.py` 的
`collect_sites_from_source` / `render_mutant` / `build_workspace` / `run_subset`，按 site_id 逐个施加变异体，
**每个 worker 一份独立 workspace**（8 个），所以能并行而不互相覆写目标文件；timeout 180 s 对 baseline
0.7–1.3 s 留了两个数量级余量，全程 0 timeout、0 error。这条余量是刻意留的：`_module_score`
（mutate.py:740-746）的 docstring 明写「**timeout 记 killed 侧**」、公式是 `(killed + timeout) / denominator`，
所以负载下的超时会把分数往上抬 —— 本文的数字因 timeout 全为 0 而不受影响。

**控制组先跑**：current main + 原三份映射判例，404 体 **全部存活、0 杀**。夜报被逐字复现，探针可信。

| 分支 | 映射文件数 | killed（404 体里） | survived | 全模块 score（+ 夜报已杀的 435） |
|---|---|---|---|---|
| main（控制组） | 3 | 0 | **404** | 435/839 = 51.8% |
| **#401 / R-301** | 9 | **401** | **3** | **836/839 = 99.6%** |
| #391 / R-217 | 8 | 400 | 4 | 835/839 = 99.5% |

**两个 PR 的自报数字都被逐字验证**，连留下的存活体身份都对得上：#401 留 `return_none@438`、
`int_plus1@556`、`return_none@572`（正是它点名的三个）；#391 留 `int_minus1@28` / `int_plus1@28`、
`int_plus1@556`、`return_none@572`（正是它点名的四个）。卡面 DoD「存活减半」的门是 ≤ 202，两个都远远过。

交集与分歧：

- **399 体两边都杀。**
- **#401 独杀 2 体** = `int_plus1@28:16` / `int_minus1@28:16`（模块级 `sys.path.insert(0, …)` 的下标）。
- **#391 独杀 1 体** = `return_none@438:8`（`_unpinned` 豁免分支 `return False`）。
- **2 体两边都活** = `int_plus1@556`、`return_none@572`；两个 PR 各自独立判成等价体，判决一致。

## 分歧那三体逐个判读（谁对）

**line 28（#401 独杀）—— #401 对，#391 判错了。** #391 的 PR 正文写 "The scripts dir is already
`sys.path[0]` for every real entry point ... The index only changes shadowing priority against paths that
never carry these module names"，据此归成等价体。#401 的
`tests/test_skill_test_code_plan_plumbing.py::test_skill_scripts_dir_is_prepended_not_inserted_later`
反驳了这个前提：**test-code skill 是跑在任意用户 repo 里的**，那些 repo 完全可能自带叫
`complexity_min` / `ladder_common` / `structure_check` 的模块，所以「插在 0 位」是成文契约而不是巧合。
判例的观测法是先把一个竞争目录塞到 `sys.path[0]`、reload 模块级代码、断言 skill 目录把它顶掉，
期望值取自 `tests/skill_test_code_testkit.py` 里独立算出的 `kit.SKILL_SCRIPTS`（不是从 checks.py 抄的）。
本轮逐文件复核确认杀它的就是这一份判例，不是别处的偶然副作用。

**line 438（#391 独杀）—— 自我更正：这不是两个孪生间的哲学分歧，是 #401 自己文件里的一致性缺口。**

先说我第一版怎么写的（错的）：#401 用机器对拍判它等价（唯一消费点 `checks.py:447
if match and _unpinned(...)`，布尔语境下 `None` 与 `False` 不可分，392 条 `uses:` 语料上结果
IDENTICAL），#391 则**选定**一条更强的不变量用 `assertIs` 钉住，于是我写成「两种立场都站得住，
差别是钉产品行为还是钉声明的返回类型」。

**这个框法不准确。** 采用物自己那份 `tests/test_skill_test_code_internal_math.py` 的
`ActionRefPinTestCase`（:246-254）**已经对 `_unpinned` 用了四次 `assertIs`** ——
`assertIs(checks._unpinned("owner/repo@" + SHA40 + "@v1"), True)` 等等。也就是说这份文件
**已经把 `assertIs` 选定为这个谓词的断言形式**了；只是那四次打的是第二条 return 臂
（`return not _SHA_RE.match(ref)`，而 `not` 恒产出真 bool ⇒ 在「布尔性」上永不会失败，
它们钉的是 pin/unpin 的**判定值**），而豁免臂那句裸 `return False`（:438）是这个形式
**唯一真正会咬到、却没被覆盖的那条臂**。

实测那两行（不改 repo，只在临时 workspace 里加）：

```
site return_none@438:8#0  = checks.py:438  `return False` -> `return None`
  as adopted          baseline=pass  mutant=pass  -> SURVIVED
  +2 assertIs lines   baseline=pass  mutant=fail  -> KILLED
```

补法就是给既有的 `ActionRefPinTestCase` 加两行：

```python
self.assertIs(checks._unpinned("./.github/actions/local"), False)
self.assertIs(checks._unpinned("docker://alpine:3"), False)
```

⇒ 存活 **3 → 2**（836/839 → 837/839），**采用集合成为 #391 的严格超集**。
本轮按「有孪生时一条新 case 都不加」没有加它（往同一个文件里塞会给 #401 / #429 造真冲突），
但它现在不再是「可选的嫁接」，而是**合 #401 之后应当顺手补上的两行**。

**`return_none@572:12`（`dangling`）则仍是真等价体，且理由比 438 硬**：它是**闭包，模块外根本不可达**，
没有任何公共观测面。判这类体看「有没有可达的公共观测面」，不只看「是不是布尔语境」——
438 有（`_unpinned` 是模块级函数、判例已在直接调它），572 没有。

## 决定性的那一刀：前向兼容实测（「修 bug 时谁会挡路」）

判一套变异杀伤判例的成色，最要紧的问题不是杀了多少体，而是**有没有把今天的错误行为钉死** ——
钉死了，以后修 bug 就得先改测试，测试从资产变成负债。这件事可以实测，不必判读：把 #401 台账里
那条一行修法（三处 npm 分支照 `_npm_audit_steps` 的样子加 `_tool(ctx, "npm")` 闸门，不过则
`_unavailable("npm not on PATH")`）真打到 workspace 的 `checks.py` 上，再跑两套判例。

```
### R-301 / #401
  GREEN (survives the fix)  tests/test_skill_test_code_builder_gates.py
  GREEN (survives the fix)  tests/test_skill_test_code_catalog_docs.py
  GREEN (survives the fix)  tests/test_skill_test_code_internal_math.py
  GREEN (survives the fix)  tests/test_skill_test_code_plan_plumbing.py

### R-217 / #391
  GREEN (survives the fix)  ..._plans.py / ..._internal.py / ..._builders_t1_t4.py / ..._catalog.py
  RED  (cements the bug)    tests/test_skill_test_code_checks_mutation_kills_builders_t5_ext.py
                            → ERROR: ApiBundleLicenseTestCase.test_bundle_size_prefers_the_npm_script
                            → ERROR: PerfBudgetDeadCodeTestCase.test_npm_bench_script_is_the_js_path
                            Ran 21 tests, FAILED (errors=2)
```

**#401 全绿，修法零测试改动；#391 两条判例必须手工改。** 根因是两边对同一条断言的写法：

- #401 的 `test_npm_size_script_runs_in_the_package_dir` / `test_npm_bench_script_is_the_js_path`
  **显式把 npm 放进 `tools`**，docstring 里写明「今天这条分支不查 npm 在不在 PATH（不同于
  `_npm_audit_steps`）…补上 npm 闸门后本判例依旧成立」—— 钉的是「声明了脚本才跑」这条真契约。
- #391 的两条同名判例走 `_ctx(...)` → `kit.fake_det(...)`，而 `fake_det` 的默认是
  `"tools": {}`（npm **不在** PATH），于是断言变成了「npm 缺失时仍然返回 `("cmd","npm")` +
  argv `["npm","run","size"]`」—— 把缺陷行为本身钉住了。

#401 是**先发现缺陷、再据此设计断言**；#391 没发现缺陷，断言顺着现状写。这一条比杀伤数差的
1 体重要得多。

**对称检验（免得这个论证是单边的）**：#401 确实钉了 `_span_cov` 无数据返回 `1.0` 这个 fail-open
现状（`internal_math.py:93-101`）。那么反过来问：改**那一条**的时候谁红？同样实测（把
`if not known: return 1.0` 改成 `return 0.0`）：

```
### R-301 / #401 —— RED  tests/test_skill_test_code_internal_math.py（其余三份绿）
### R-217 / #391 —— RED  tests/test_skill_test_code_checks_mutation_kills_internal.py（其余四份绿）
=> 两套都钉了这个 fail-open，各红一份文件。中性，无优劣。
```

（#391 的 `..._internal.py:296-300` 同样 `assertEqual(checks._span_cov(1, 3, set(), set()), 1.0)`。）
所以 #401 的前向兼容优势**完全来自那条已确诊的缺陷**（npm 闸门），在那里是不对称的；在
`_span_cov` 这条**尚未拍板的设计问题**上两边一样。另外 #401 那处是它台账里**明写**的
「判例钉的是今天的行为，修任何一条都会故意把相应断言变红」，披露过、不是藏起来的。

## 两个都合的代价（量过，不是猜的）

`git merge-tree --write-tree origin/ai/self-improve/R-301 origin/ai/self-improve/R-217` ⇒
**`qa/mutation_targets.toml` content CONFLICT**，只此一处（两个 hunk 替换同一行的 `checks.py` 映射数组）。
手工 union 能解。但解完的代价不在 git 上：两套判例会**同时**钉同一张 `CATALOG` 表的 `tier` / `phase` /
`est` 三列 —— 以后动一行 catalog 要改两个地方，而两套的 golden 表形状还不一样。合两个 = 双倍维护，
换来的增量是 1 个位点（438）。

## 顺带：本分支的 CI 正好补上了 #401 缺的那份证据

#401 的 CI 是 **2026-09-16** 跑的，而它的 merge-base 落后 current main **59 个 commit** —— 它的 hunk
从没在今天的 main 上过过 CI。**本分支就是那个实验**：同一批 hunk 逐字节搬到 current main 上、跑一次
全新的 CI。所以即使 owner 决定合 #401 而关掉本 PR，本 PR 的 CI 结果仍是「#401 的 hunk 在今天的 main 上
还能过」的证据。

三个 PR 现在都是 `mergeable=MERGEABLE` / `mergeStateStatus=CLEAN`（逐个查过）⇒ main 没开 strict
up-to-date 要求，#401 不需要先 rebase，`gh pr ready 401` 之后就能合。

## 建议（请 owner 拍板）

**合 #401 的那份 hunk（载体见本节末的落地方案），其余全关。** 理由按权重：

1. **前向兼容**（上一节的实测）：修那条已确诊的 npm 缺陷时，#401 零测试改动，#391 两条判例要手工改
   —— 一套判例把今天的错误行为钉死，价值是负的。这一条权重最高，因为它决定判例是资产还是负债。
2. **靶区映射的结构性收益只有 #401 有**：它把 `tests/test_skill_test_code_run_ladder.py` 与
   `tests/test_skill_test_code_detect.py`（两份早就存在、都 import `checks` 并驱动它的 builder 与菜单）
   补进映射，白得 21 体，而且**永久**扩宽了这个模块的靶区 —— 以后每晚都拿这两份判例一起判。
   #391 没做这一刀。这是与新判例无关的净增益。
3. **文档同源的广度**：#401 把 `tier` / `TIER_TIMEOUTS` / `TRIGGER_CHECKS` 钉在
   `references/tiers.md`、`references/catalog.md`、`SKILL.md`、`references/triggers.md` **四份**文档上、
   三份互为对照，并逐个验过八个「只改文档不改代码」的漂移场景（双向都拦）。#391 钉两份
   （tiers.md / catalog.md）。两边都是真在测试时读 md，不是硬编码 —— 这一点上两个都比 2026-09-02
   那次「整体归成等价体」的判读好得多。
4. **分歧判读 #401 对**（line 28，见上）。
5. **交付形制**：#401 带 `changelog.d/ai-self-improve-r301.md` + 进度台账（含消融实测：93 文档同源 /
   157 行为锚定 / **154 只值改动检测**，以及只删 `EST_GOLDEN` 一张表的下界 = 存活 91 / 杀伤 89.2%）
   + 三个等价体的机器对拍语料规模 + **三处真缺陷的 repro 与一行修法**。#391 两份都没有、缺陷一个没报。
   #401 的进度台账还自我翻案了一次（第一遍把 `phase` 那 66 体的消融做错，保留了 `PHASE_GOLDEN` 当期望值，
   正确消融后 66 体全存活），并据复核意见把 `inline == PHASE_GOLDEN[2] + PHASE_GOLDEN[3]` 这个**有序**
   断言改成比集合 —— 档内次序哪份文档都没定，原写法会让两行互换这种纯装饰改动假红。
6. **归属行**：#391 的 PR 正文尾部留了一行 AI 生成署名（Claude Code 的默认尾注），与 owner 2026-07-29 立的
   「PR / commit 一律不得出现 AI 归属」冲突；#401 没有。（只是一行，但既然要关一个，顺便记上。）

唯一的反向考量本身已被上面「line 438」那节更正掉：那两行不是从 #391 嫁接，而是补 #401 自己文件里的
一致性缺口；补完，采用集合就是 #391 的严格超集。

**落地方案（经审计 Workflow 两个反驳者压过一轮后的版本）**：

1. **合一个基于 current main 的载体**（#429 或本 PR —— 两者逐字节相同、CI 今天新跑），而不是把落后
   59 个 commit、CI 停在 9/16 的 #401 直接合。判例的功劳不变，#401 是作者。
2. 合完顺手补 `ActionRefPinTestCase` 那两行 `assertIs` → 存活 3 → 2（837/839）。
3. **单独开一张卡处理 `est` 那 88 体**：给 `mutate.py` 的 `_skip_subtree` 加数据表子句 + bump
   `RUNNER_VERSION`，把「无文档真源 + 无行为后果」这一列从分母里移走，然后 `EST_GOLDEN` 整张表可以删
   —— 少 88 行 golden，分数反而更诚实。`tier` / `phase` 不动（它们是真信号）。
4. **改生成器**（`act/lib/loop_inputs.py:_mutation_signal`）：按 open PR 抑制同 fingerprint 的信号，
   否则第五张卡今晚还会来。
5. 合车前处理 #401 判例里那三处脆点（档预算阶梯的无 msg 断言、`inspect.getsource` grep 字面量、
   `SKILL.md` 字面量切片）。
6. 关掉 #391、以及 #429 / 本 PR 里没被选作载体的那个。

**#401 带过来的三处待拍板缺陷本轮一个没碰**（判例钉的是今天的行为，修任何一条都会故意把相应断言变红）：
`_e2e_for_pkg` / `_b_perf_budget` / `_b_bundle_size` 缺 npm 时报 RED 而非 UNAVAILABLE（同文件的
`_npm_audit_steps` 查了 PATH，所以是不一致不是取舍）；`docs_drift` 的目录级 `.gitignore` 条目不做前缀匹配；
`_span_cov` 无行数据时返回 `1.0` = fail-open（与模块 docstring 的「一律 fail closed」冲突）。诊断全文在
#401 的 `docs/design/progress/2026-09-16-self-improve-r301-checks-mutation-kills.md`。

## 本分支交付什么

`qa/mutation_targets.toml` 与四份判例**与 #401 逐字节相同**（`git diff origin/ai/self-improve/R-301 --
qa/mutation_targets.toml tests/test_skill_test_code_{catalog_docs,builder_gates,internal_math,plan_plumbing}.py`
= 空）。故意**不**复制 #401 的 changelog fragment / 进度台账 / HTML —— 那三个是独立文件名，两个 PR 都带
就是两份同内容的文件。这么做的后果正是想要的：**#401 先合，本 PR 的 diff 自动变空，直接关掉即可。**
本分支自己也跑了同一套实测（见下），所以卡面 DoD 在本分支上是物理成立的，不是靠引用别人的 PR。

## 门

| 门 | 结果 |
|---|---|
| `python3 -m compileall act ingest` | exit 0 |
| `ruff check .`（miniconda 的 ruff；python3.14 没装） | All checks passed |
| 四份判例 `ast.parse(feature_version=(3,9))` | 全过（CI 有 3.9 job） |
| 本机 `unittest discover -s tests`（含 integration） | **未跑完**：三次都卡在**第一个** integration 测试 `integration.test_auto_deploy_defer_episode` 上被收割（exit 143 / 144）。零输出是 stderr 块缓冲从未攒满，看着像「一个测试都没跑」。当时 load 30–40、swap 27.5-of-28.7 GB，但邻卡在**空载**机器上同样三次 exit 144 ⇒ **内存压力不是根因**，根因在那个 integration 测试本身。如实标未完成，权威证据指向 CI。 |
| 本机顶层 `tests/*.py` 全量（525 模块，排除 integration/） | **Ran 7776 tests, failures=1** —— 唯一那条是 `test_readme_audit_repo_readme::test_no_stale_claims`：README 写 `v1.0.114` 而本机 tag 是 `v1.0.116`（§56.1 规定 tag 才是版本真源）。本 PR 没碰 README，**CI 的 3.x 腿把含这条在内的 8065 个测试全跑绿** ⇒ 本机独有的 tag 态假红。 |
| **CI `Tests on ubuntu (Python 3.x)`（全套，权威）** | **8065 tests, pass** |
| CI `Tests on ubuntu (Python 3.9)`（全套） | 8065 tests，1 error = `AutoDeployScriptTestCase.setUp` 的真 `git clone`（复发 flake，见后文那节） |
| `scripts/qa/hygiene.py --check` / `depgraph.py --check` | OK / OK（列出的违例全是 baseline 既有豁免） |
| `scripts/qa/ledger_diff.py --base origin/main` | **0 finding** |
| `qa/coverage_floor.txt` / `hygiene_baseline.txt` / `deps_baseline.txt` / `gates.toml` | 逐个 `git diff` 为空 = **DoD 2「覆盖率地板不降」**（没降地板、没加 ledger key、没放松阈值） |
| `scripts/ci/changelog_fragments.py check` / `progress_log.py check` | ok / ok |
| 本分支 404 体实测 | 见 PR 正文的对照表 |

## 顺手抓到的第四件事：`Tests on ubuntu (Python 3.9)` 有个真 flake（必需检查）

本 PR 第一轮 CI 的 3.9 job **红**，逐字取日志后确认与本轮改动无关：

```
Ran 8065 tests in 370.818s
FAILED (errors=1, skipped=80)

ERROR: test_install_timeout_counts_as_failure
       (integration.test_auto_deploy_script.AutoDeployScriptTestCase)
Traceback (most recent call last):
  File "tests/integration/test_auto_deploy_script.py", line 489, in setUp
    _git(self.tmp, "clone", "-q", str(self.origin), str(self.live))
  File "tests/integration/test_auto_deploy_script.py", line 414, in _git
    return subprocess.run(
subprocess.CalledProcessError: Command '['git', …, 'clone', '-q', '/tmp/autodeploy-…'
```

8065 个测试只错 1 个，错在 **`setUp` 里那句真 `git clone`**，而 `tests/integration/test_auto_deploy_script.py`
本 PR 一个字没动。三条互证它不是本轮引入的：

1. **#429 带着逐字节相同的四份判例 + 相同 toml hunk，它的 3.9 job 是 `pass`。**
2. 同一个 commit 上的 `Tests on ubuntu (Python 3.x)` 是 `pass`（同样跑这四份文件）。
3. **#391 的 PR 正文 2026-09-16 记录过同一个 job、同一形状的错**（`git clone` exit 128 inside the
   runner's tmp dir，当时命中的是 `test_pre_existing_red_is_not_blamed_on_the_new_version`），
   `gh run rerun --failed` 第二轮即绿。

⇒ **这是一个复发的 flake，两次都落在 `AutoDeployScriptTestCase` 的 `setUp` 的 `git clone` 上，两次都在
3.9 job**（3.x 没见过）。它污染的是一条**必需检查**，所以每次都会把一个本来该合的 PR 拦下来。
本轮没修它：`scripts/auto-deploy.sh` 在 self-improve lane 的受保护路径清单里，而改这条 flake 属于
另一张卡的范围。**建议单开一张卡**，方向是把 `setUp` 里那次 `git clone` 换成不走网络/不依赖 tmp 布局的
本地 `git init` + `git fetch`，或给它加重试与失败诊断（现在的 `CalledProcessError` 连 stderr 都没打出来，
所以两次命中都只能靠形状猜原因）。

`gh run rerun --failed` 在本机那把 PAT 下不可用（`Resource not accessible by personal access token`，
与 2026-09-17 记下的 scope 收窄一致），所以重触发 CI 的办法是再推一个 commit —— 本节这段文字就是那个
commit 的内容。

**✅ 重触发后的结果：全 11 项绿，含 `Tests on ubuntu (Python 3.9)`。** 这把「它是 flake」从推断
变成了实证：同一棵树（只多了文档），同一个 job，一次红一次绿。三次证据链完整 —— #429 同 hunk 绿、
同 commit 的 3.x 绿、本分支重跑绿。

## 独立审计 Workflow 的判决，以及它带来的三条修正

本轮另跑了一个审计 Workflow（13 个 agent / 6 个 lens + 3 判官 + 3 反驳者，58 min，0 error）。
**三个判官全部落在「合 #401 的内容」**（`merge-401-graft-from-391` 2 票 / `merge-401-close-391` 1 票），
oracle lens 独立复核后确认 **R-301 自报的 154 / 93 / 157 三档分法成立**（且误差方向对它自己不利，
真正的改动检测约 157 而非 154）。反驳者还**独立复现了本文那张对照表**（401 / 400 / 399 共杀 /
并集 402 —— 并集恰等于两集合的交集补，可用来自检）。

但**反驳存活只有 1/3**，两个反驳者提出的东西是实质的，逐条自己验过后如下 —— 它们不推翻「#401 的内容
更好」，但**改变该怎么落地**：

**修正一：合车载体也许不该是 #401 本身。** #401 的 CI 是 2026-09-16 跑的，merge-base 落后 current main
**59 个 commit**；而 #429 与本 PR 携带**逐字节相同**的五个路径、基于 current main、CI 是今天新跑的。
「要合这份 hunk」和「要合 #401 这个 artifact」是两件事。（我不替自己的 PR 说话：#429 与本 PR 等价，
owner 挑任一个都行，关键是**载体应当是基于今天 main 的那个**。）

**修正二：那 154 体有个零测试行的、更合法典的解法，四个 PR 一个都没评估过。**
`scripts/qa/mutate.py` 自己就有 `_skip_subtree`（:291-299），docstring 写着「整棵子树不铸 site 的规则
（**等价变异体高发区，§57 明文**）」，现有三条子句 = `__repr__` 函数体 / `__main__` 守卫 / logging 调用
—— **没有数据表子句**。而 `docs/CONTRACT.md:5471`（2026-09-02 P3a 追记）明写「幸存体逐个判定 ——
**等价变异（常数 ±1、日志文案）放过**」。

CLAUDE.md 的必答三问第 3 条（「有没有已存在的机制做类似的事」）要求先搜一遍这个 —— 四个 PR、
六个 lens、三个判官**都没做**。我做了，结论有分寸：

- **`est` 那 88 体该走 skip，不该写 golden 表**。R-301 自己的消融已经证明它「仓库里没有任何文档载这些
  秒数，也没有任何下游行为后果」；那它正是 `_skip_subtree` 的目标形状。加一条子句 + bump
  `RUNNER_VERSION`（旧 state 作废重跑），这 88 个位点**从分母里消失**，零测试行，且与 §57 的成文口径一致。
- **但不能整张 `CATALOG` 一刀切**（反驳者的原话是整张表）：`tier` 那 82 体钉在四份 markdown 上、
  `phase` 有 42 体靠 `run_ladder.run_all` 的真实派发可杀 —— 那 124 体是**真信号**，skip 掉等于自断覆盖。
  真正该 skip 的只有「无文档真源 + 无行为后果」那一列。

所以准确的说法不是「R-301 判错了」也不是「P3a 判错了」，而是：**P3a 那条「常数 ±1 放过」是识别等价体的
启发式，不是对每个整数常量的裁决；对这个模块要按列分开裁 —— `tier`/`phase` 判例化，`est` 走 runner skip。**

**修正三：孪生风暴的根在生成器，不在判例。** `act/lib/loop_inputs.py` 的 `_mutation_signal`
（:535-556）每晚取**存活最多的那一个**模块（`sorted(..., key=lambda t: (-t[4], t[0]))[:1]`，
门槛 `MUTATION_MIN_SURVIVORS`），fingerprint 只是 `mutation:<module>`。**只要 PR 不合、存活数不掉，
它每晚都会再选中同一个模块**；任何进度文档都拦不住第五张卡。要止住得改那一头：让它感知「这个模块的靶区
映射已有 open PR 在改」，或按 open PR 抑制同 fingerprint 的信号。

**另外一条值得记的机制事实**：`_module_score`（mutate.py:740-746）的 docstring 明写
「**timeout 记 killed 侧**」，公式是 `(killed + timeout) / denominator`。所以负载下超时会把分数往上抬。
本文那四轮实测 **timeout 全为 0**，所以这里报的数字没有被这个机制抬高 —— 但夜报在拥挤的 runner 上
未必，读夜报分数时要一起看 `timeout` 计数。

**反驳者还验实了 #401 判例里三处该在合车前处理的脆点**（我复核了它给的位置）：
`catalog_docs.py` 的「档预算阶梯单调」不变量失败时不带 msg，且退役两个 tier-3 core 层就会破
（各档 est 和 = 255 / 990 / 1445 / 2410，退掉两个 600 使档 3 变 245）；一处 `inspect.getsource(e["build"])`
去 grep 字面量 `_internal(`，寄生在 `checks.py:72` 那个两行工厂上；`SKILL.md` 的切片按字面量
`references/catalog.md):` 与 `, plus` 切。这三处都不影响本轮的杀伤判决，但会在将来变成假红。

## 第四条缺陷（安全性）—— 由并跑的第五张卡（#431）提出，我逐条复核成立

#401 报了三条待拍板缺陷（见上）。并跑的另一个 session 又找出第四条，我自己验过，**成立且比那三条更要紧**：

`_scan_files`（`checks.py:376-386`）的 docstring 承诺「**读不到/解析不了记 errors（caller fail closed）**」，
但代码是：

```python
text = lc.read_text(os.path.join(repo, rel))
if text is not None:
    violations.update(fn(rel, text))
```

而 `lc.read_text`（`ladder_common.py:200-203`）对**超过 `TEXT_CAP_BYTES`（:34 = 1 MiB）或二进制**的文件
返回 `None` ⇒ 这类文件被**静默跳过**：既不记违例，也不进 `errors`。与自己的 docstring 直接矛盾，
而且是 fail-**open**。

实测本仓库：`docs/CONTRACT.md` = **1,513,333 字节**，超帽 44%（cap = 1,048,576）⇒
**本仓库自己的 `secret_scan` 对它最大的那份文档完全失明。** 往 CONTRACT.md 里粘一个密钥，
这个检查永远不会报。四个消费者同受影响：`secret_scan`（:426）、`actions_sha_pin`（:457）、
`test_smells`（:530）、`docs_drift`（:591）。

一行修法方向：`read_text` 超帽时不返回 `None` 而是抛/回报原因，或 `_scan_files` 把 `text is None`
的文件记进 `errors`（这样 caller 就按它自己 docstring 说的 fail closed）。

同一个 session 还更正了 #401 的一个范围数：「缺 npm 报 RED」的**实际位点是 10 处，不是 3 处**。
本轮没复核这一条，转述时标明来源。

## 消融下界有三档，三个数都对但不是一回事

报「不依赖 golden 表的下界」时要说清消融了哪几张表 —— 并跑的 session 补齐了另两档：

| 消融 | 存活 | 杀伤 | 过 DoD 门（≤202）？ |
|---|---|---|---|
| 只删 `EST_GOLDEN`（#401 报的） | 91 | 89.2% | ✅ |
| `EST_GOLDEN` + `PHASE_GOLDEN` 都删 | 157 | 81.3% | ✅ |
| 再把 `tier` / `TIER_TIMEOUTS` 的文档表也当抄本折掉 | 265 | 68.4% | ❌ |

最后一档**过苛、不采用**：`tier` 有独立的行为通道（`default_checks` + `run_ladder.timeout_for`），
不是纯抄本。但把它列出来是诚实的：**DoD 成立依赖「文档同源算真 oracle」这条判断**，
这条判断本身请 owner 复核。

## 给下一个 session 的三条

1. **这一族卡的第一条命令是 `gh pr list --state open`，不是读 issue 正文**（R-304 立的规矩，本轮第二次兑现，
   而且这次是**两个**孪生互相冲突）。夜报只要没合车就会永远复述同一批存活体 —— 判「这张卡是不是重复劳动」
   看的是 main 的靶区映射有没有变，不是看存活数变没变。
2. **孪生之争别靠自报数字，跑一次探针就够了**：本轮三个分支 × 404 体，控制组 90 s、两个孪生各 80–115 s，
   总共不到 6 分钟就把两份自报全部逐字验证（连留下的存活体身份都对上）。做法是「每 worker 一份 workspace +
   只判已知存活体」，比全量 `mutate.py --force` 快一个量级。
3. **孪生各自宣布的「等价体」是最值得复核的地方** —— 本轮两边的分歧全落在那里，而且各错各对：
   #391 把一个真契约（`sys.path.insert(0, …)` 防同名模块遮蔽）判成等价体，#401 把一个它自己机器验过
   确实等价的位点（`_unpinned` 的 `False`/`None`）留着不钉。判「等价」之前先问：这个模块会不会跑在
   别人的 repo 里？
