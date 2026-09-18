pr: `ai/self-improve/R-002`（self_improve lane 草稿 PR；同一批存活体的第三张卡）
phase: P5 → P3 闭环（§57 夜间变异存活体 → §70 循环铸卡 → §65 lane 出 PR）；孪生仲裁，非新判例
law: §57 靶区映射（`qa/mutation_targets.toml` hunk 与 PR #401 逐字节相同）/ §58（行为零改动）/ §65（lane 按分支名验收）

**这张卡是第三张，动手第一条命令按 R-304 立的规矩查孪生，查到两张。** 夜报（artifact `mutation-report`，
run 35232295853，2026-09-17T15:00Z）把 `skills/test-code/scripts/checks.py` 报成 **839 位点 / 435 杀 /
404 活 / 51.8%**，`executed = 839` —— 全量跑满，不存在 R-292 那种「只跑了半张地图」的问题。同一批 404 体
已经有两个 open draft PR 各自独立做过：

| PR | 分支 | 自报 | 形制 |
|---|---|---|---|
| #401 | `ai/self-improve/R-301` | 404 → 3（51.8% → 99.6%） | 4 份新判例 + 把 2 份**既有**判例补进靶区映射；带 changelog fragment、进度台账、HTML 报告 |
| #391 | `ai/self-improve/R-217` | 404 → 4（52% → 99.5%） | 5 份新判例；无 fragment、无进度台账 |

两个都还是 draft、两个 CI 全绿、两个同一 merge-base（`f5edb5a1c2`，落后 main 59 个 commit）。
**neither 合车 ⇒ main 的靶区映射没变 ⇒ mutate.py 的 state 照旧复用 ⇒ 夜报每晚复述同一批 404 ⇒
daily_loop 继续铸卡。** 这就是第三张卡存在的全部原因（同日 `.claude/worktrees/` 里还有一个
`ai/self-improve/R-8153`「checks-mutants」在 main HEAD 上，第四张大概在路上）。所以本轮交付的不是
第三套判例，而是**一次实测仲裁 + 把胜者的 hunk 逐字节搬到本分支**。

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
0.7–1.3 s 留了两个数量级余量，全程 0 timeout、0 error（过载把超时记成 kill 会让分数虚高，这条是刻意防的）。

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

**line 438（#391 独杀）—— 两边都站得住，#391 更严，但不值一次合车。** #401 用机器对拍判它等价：
唯一消费点是 `checks.py:447 if match and _unpinned(...)`，布尔语境下 `None` 与 `False` 不可分，
392 条 `uses:` 语料上 `bool()` / `_pin_violations` / 整个 `check_actions_sha_pin` 的结果 IDENTICAL。
#391 不反驳这个事实 —— 它的 docstring 明确承认「豁免分支的返回值只在真假位置被消费」，然后**选定**
一条更强的不变量（「谓词只返真布尔」）用 `assertIs` 钉住。两种立场都成文、都诚实；差别是「钉产品行为」
还是「钉声明的返回类型」。这是唯一值得从败者身上嫁接的东西，量级约两条断言。

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

**合 #401，关 #391，关本 PR。** 理由按权重：

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

唯一的反向考量：#391 的 438 判例。若 owner 想要那条不变量，它是 #401 合车后的两行 follow-up，
不必为它承担一次冲突合车。

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
| `AIASSISTANT_HOME=$(mktemp -d) PYTHON_COLORS=0 python3 -m unittest discover -s tests` | 见 PR 正文（`PYTHON_COLORS=0` 是本机必需，否则 `tests/integration/test_auto_deploy_script.py` 有个与本轮无关的既有假红） |
| `scripts/qa/hygiene.py --check` / `depgraph.py --check` | OK / OK（列出的违例全是 baseline 既有豁免） |
| `scripts/qa/ledger_diff.py --base origin/main` | **0 finding** |
| `qa/coverage_floor.txt` / `hygiene_baseline.txt` / `deps_baseline.txt` / `gates.toml` | 逐个 `git diff` 为空 = **DoD 2「覆盖率地板不降」**（没降地板、没加 ledger key、没放松阈值） |
| `scripts/ci/changelog_fragments.py check` / `progress_log.py check` | ok / ok |
| 本分支 404 体实测 | 见 PR 正文的对照表 |

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
