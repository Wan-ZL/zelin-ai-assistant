pr: `ai/self-improve/R-8153`（self_improve lane 草稿 PR）
phase: P5 → P3 闭环（§57 夜间变异存活体 → §70 循环铸卡 → §65 lane 出 PR）
law: §57 靶区映射（`qa/mutation_targets.toml` 补两份既有判例 + 四份新判例）/ §58（test-code skill 读项目门，行为无改动）

## 这是同一批存活体的第三张卡，所以第一个决定不是「怎么补测试」

夜报（pinned issue #150）连续三轮把 `skills/test-code/scripts/checks.py` 报成 **839 位点 / 435 杀 / 404 活 / 51.8%**，循环据此铸了三张卡：R-217（草稿 PR #391）、R-301（草稿 PR #401）、本卡 R-8153。开工第一条命令 `gh pr list --state open` 就撞见前两张的 PR 都还开着、CI 全绿、都没合。**夜报数字三轮不变不是因为前两轮白做，是因为变异跑的是 `main`，而那两个 PR 都还是 draft——存活数要等 owner 合并才会动。**

按 owner 在 R-304 那轮定下的规矩（有孪生就只带逐字节 hunk、一条新 case 不加，但仍要开自己的 draft PR），本轮不写第三套实现，逐字采用 PR #401 的产物。四份判例文件与 `qa/mutation_targets.toml` 那一行与 `pr401` 分支逐字节相同（`git rev-parse :<path>` 与 `git rev-parse refs/pull/401/head:<path>` 五个 blob 全等），谁先合都不会打架。

## 为什么选 #401 而不是 #391：五种映射组合的实测

没有照抄任何一方 PR 描述里的自报数。本轮写了一个分片并行验证器（import `scripts/qa/mutate.py` 的 `collect_sites_from_source` / `render_mutant` / `build_workspace` / `run_subset`，把 `--tests` 指定的文件自己拷进工作区，因此绕开了「新判例先 `git add`」和多 agent 抢 `git index.lock` 两个坑；每体 0.04–0.6 s，随机器负载浮动），在**干净工作树**上对同一棵 `checks.py`（blob `669cacb6`，与 `origin/main` 逐字节相同，位点分母 839 没变）全量 `--force` 跑了五种映射组合：

| 映射组合 | 判例文件数 | 杀 | 活 | 杀伤率 |
|---|---|---|---|---|
| `main`（现状三份） | 3 | 435 | **404** | 51.8% |
| 只补靶区映射（+run_ladder +detect） | 5 | 456 | 383 | 54.4% |
| **#401（本卡采用）** | 9 | 836 | **3** | **99.6%** |
| #391 | 8 | 835 | 4 | 99.5% |
| 两者并集 | 14 | 837 | 2 | 99.8% |

五轮 `timeout` 与 `error` 均为 0（并行度不足以把超时误记成杀伤）。`main` 轮**逐位点复现夜报的 435/404**，可以认为口径对齐。单调性自检全过：只补映射的存活集合 ⊂ `main` 的，#401 的 ⊂ 只补映射的。并集轮实测的 2 体恰好等于两个孪生存活集合的交集，内部一致。

**两个孪生的存活集合并非互相包含**，这是 PR 描述里都没写的一点：

- #401 杀掉、#391 留活：`int_plus1@28:16` 与 `int_minus1@28:16`（`sys.path.insert(0, …)` 的下标）。
- #391 杀掉、#401 留活：`return_none@438:8`（`_unpinned` 里的 `return False`）。
- 两者都留活：`int_plus1@556:30`、`return_none@572:12`。

所以「#401 留 3 体、#391 留 4 体」只差 1，**但体数不是选它的唯一理由**：#401 剩下的 3 体经机器对拍全部是等价变异体，真洞为 0；而 #391 留活的 `sys.path.insert` 下标是可观测的（改前插位置会改 import 优先级），最多两个真洞。反过来，#391 多杀的那一体本身是等价变异体（见下），那一杀钉的是私有 helper 的返回值身份，属实现细节而非行为。

## 剩余 3 体的等价性——机器判定，不靠肉眼

1. **`int_plus1@556:30`**：`raw.split("#", 1)[0]` → `split("#", 2)[0]`。取 `[0]` 使 `maxsplit`（≥1）不可观测。用 200,584 条字符串语料（穷举小字母表 + 定种子 fuzz）对拍 `split("#",1)[0]` 与 `split("#",2)[0]`，以及 556 行那句带 `.strip().lstrip("/").rstrip("/")` 的完整表达式，**counterexample 0 条**。可证等价。
2. **`return_none@438:8`（`_unpinned`）与 `return_none@572:12`（`dangling`）**：`return False` → `return None`。全模块唯一消费点分别是 `447: if match and _unpinned(match.group(1)):` 与 `579: if path and dangling(path):`，都是纯布尔语境，`bool(False) == bool(None) == False`。**经唯一调用点不可分**。要杀只能对私有 helper 的返回值做身份断言（`is False`），那钉的是实现细节；本轮按规矩不加新 case，留给 owner 判断值不值。

## 门（本机，干净工作树）

- `python3 -m compileall act ingest` → OK
- 四份采用的判例定向跑 → `Ran 100 tests ... OK`
- 两份新进映射的既有判例 → `Ran 36 tests ... OK`
- `ruff check .` → `All checks passed!`
- `python3 scripts/qa/ledger_diff.py --base origin/main` → `0 finding(s) / OK`
- `qa/coverage_floor.txt`（83.5）与 `qa/gates.toml` 未改动——**卡面 DoD 第 2 条「覆盖率地板不降」按「未动」满足**；只加判例不改生产代码，行覆盖只会涨或平。
- Swift 两道门（`mac/build.sh` / `mac/LogicTests/test.sh`）本轮未跑：改动只含 `tests/**` 与 `qa/mutation_targets.toml`，CI 的 per-PR 路径过滤本就不在这些路径上起 macOS 腿。

## 诚实边界（原样保留 #401 查出的三条，本轮既没推翻也没粉饰）

1. 杀掉的 401 体里有 **154 体只靠照现状抄的 golden 表**（`est` 88 + `phase` 同档间 66），属**改动检测**而非行为防护——仓库里没有任何文档载那些秒数。正确的消融是**保留观测通道、只把期望值换成从源现算**；这么跑那 66 体全存活。折掉整张表的下界是**存活 91 / 杀伤 89.2%**。
   > ⚠️ **这一条的 154 / 88 / 66 / 91 / 89.2% 五个数字是 R-301 在 PR #401 里的消融实测，本轮直接承接、**没有**重新跑过**（本轮跑的是上面那张五组映射对照表）。本轮独立复核的只有「剩余 3 体是等价变异体」那一条。之所以照搬而不重测：判例是逐字节采用的，消融结论随判例一起成立；要推翻它得重跑一次 golden 表消融，那超出本卡「不加新 case」的范围。owner 若要核这条，`docs/design/progress/2026-09-16-self-improve-r301-checks-mutation-kills.md` 是原始出处。
   
   卡面 DoD「存活减半」（≤ 202）**不依赖任何 golden 表**：即便按 #401 报的 89.2% 下界（存活 91）算，也仍然过线。
2. 剩余 3 体是等价变异体，理由与语料规模见上。
3. #401 顺手查出**三个真缺陷只标不修**：`_e2e_for_pkg` / `_b_perf_budget` / `_b_bundle_size` 三处缺「`npm` 在 PATH」的闸门，会把「工具没装」报成「项目坏了」，破 skill 的三分「not run」契约；`docs_drift` 的目录级 `.gitignore` 条目不做前缀匹配导致假红；`_span_cov` 无行数据时返回 `1.0` 是 fail-open，与模块 docstring 写的「一律 fail closed」冲突。判例钉的是**今天的行为**，修任何一条都会故意把相应断言变红，所以留给 owner 拍板，本轮范围仍是「只修测试网」。

## 两条给下一个 session 的操作提醒

- **同题卡被并发扇出**：本轮 `ListAgents` 看到除本 session 外还有一个 `R-8153`、三个 `R-002`、两个 `R-001` 都挂着同一个「补测试：checks.py 变异存活」标题，多个 session 同时在同一棵 worktree 里跑 `mutate.py` 与 `unittest discover`。撞车的表现是**你的未跟踪文件被别人清掉、索引被别人 `git add` 过、`git checkout -B` 出现在 reflog 里**。判据：`git reflog --date=iso` 的时间戳对不上自己的操作。对策是把测量搬进自己 job tmp 下的独立 worktree（`git worktree add --detach`），别和别人抢同一个索引。
- **别人留下的探针会污染生产文件**：撞车期间共享 worktree 的 `skills/test-code/scripts/checks.py` 一度带着三个注入的变异体（`[:10]`→`[:9]`、`[:12]`→`[:13]`、`est 240`→`239`）躺在工作区里未提交。本轮所有决策数字都在干净 worktree 上复跑过一遍（`main` 435/404、#401 836/3，两轮存活集合逐位点相同），确认没被污染；但如果那棵树上有人 `git commit -a`，三个变异体就会进生产代码。**跑完变异实验后 `git status` 看一眼生产文件是不是干净的。**
