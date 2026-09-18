pr: `ai/self-improve/R-001-checks-mutation`（PR #427；R-001「补测试：skills/test-code/scripts/checks.py 变异存活 404 体（杀伤 52%）」；**同一条夜报发现的第三张卡**，前两张是 R-217 / #391 与 R-301 / #401）
phase: 横切（测试网；vnext2-plan R2.8 / R2.3.4 每日自我改进循环的 self_improve 通道）
law: —（无新 §、无修法；§57「存活变异体 = 补测试提案、等价体记理由」与 §58 照旧执行）

**先做复测，再写判例。** 卡面数字（run=839 / killed=435 / survived=404）来自 2026-09-17 的夜跑。动手前第一件事是 `gh pr list --state open`：`skills/test-code/scripts/checks.py` 这一个模块已经有**两份**草稿 PR 在做同一件事，且两份的 7 项 required check 全绿。所以本轮的第一产出不是第三套判例，而是**对两份既有判例的独立复测**——两份 PR 的自述（「400 of 404」「404 → 3」）此前谁也没验过。

复测器：按 `site_id` 逐个施加变异、只跑定向子集、每个变异体独立 workspace、`-f` failfast（对退出码中性），判决语义逐字照抄 `scripts/qa/mutate.py::_execute_mutant`（`pass`→survived / `fail`→killed / `timeout`→timeout）。每体 0.3–0.5 s，12 并发跑满 839 个位点约 1–2.5 min，比整轮 `mutate.py` 快两个数量级。**先自检**：在 `origin/main` 的树上跑满 839 个位点，得 435 killed / 404 survived，**与夜报逐位点一致（不只是计数一致，存活集合逐 site_id 相等）**，复测器才可信。

| 树 | 映射判例 | killed / survived（839 位点） | 杀伤率 |
|---|---|---|---|
| `origin/main`（夜报口径） | 3 份 | 435 / 404 | 51.85% |
| 只补靶区映射（+`run_ladder` +`detect`） | 5 份 | 456 / 383 | 54.35% |
| #391（R-217） | 8 份 | 835 / 4 | 99.52% |
| #401（R-301） | 9 份 | 836 / 3 | 99.64% |
| **本分支** | 10 份 | **837 / 2** | **99.76%** |

**两份既有判例是互补的，不是重复的**——这是复测才看得出来的事：#401 独有地杀掉 `sys.path.insert` 的下标（`int_minus1@28:16#0` / `int_plus1@28:16#0`，靠 `test_skill_scripts_dir_is_prepended_not_inserted_later`），#391 独有地杀掉 `_unpinned` 豁免分支的 `return False`（`return_none@438:8#0`）。两者并集只剩 2 个存活体。本分支因此**原样采纳 #401**（四份判例逐字节相同，`git diff origin/ai/self-improve/R-301 -- <这些路径>` 为空）+ 它的靶区补映射，再补一份 `tests/test_skill_test_code_checks_residual_mutants.py` 收走 #391 那一刀。

**三个残差的判决**（都经复测器逐个复核，不是推断）：

- `return_none@438:8#0`（`_unpinned` 豁免分支）——**杀掉了，但它是类型契约不是行为锚定，docstring 里明说**。豁免分支返 False 还是 None 经 `check_actions_sha_pin` 观测不到：两个值都是假值，唯一消费点是 `checks.py:447` 的 `if match and _unpinned(...)`。钉它的理由是同一函数另一条出口 `not _SHA_RE.match(ref)` 返真布尔，两条出口类型不一致——今天无害，这个值一旦进 details / JSON 报告就立刻可观测。#391 独立地作了同一选择，并同样把它标成「选定不变量」。
- `int_plus1@556:30#0`（`raw.split("#", 1)[0]` 的 maxsplit 1→2）——**可证等价，永不可杀**。`str.split(sep, k)[i]` 对任何 `i < k` 都与 k 无关：左到右切，第 i 段是真的第 i 段，只有第 k 段（余段）随 k 变，而这里 `[0]` 只读第一段。353,982 条输入（长度 ≤6 的分隔符全排列穷举 + 随机含全码位 + 真 `.gitignore` 72 行）零差异；同一行的 `int_minus1@556:30#0`（1→0，真的会让注释不再被剥）作阳性对照，在 200,533 条输入上报差异、且被两份判例都杀掉——说明这一行本身是被钉住的，存活的不是覆盖漏洞。`mutate.py` 的 `_const_sites` 对整数常量一律铸 ±1，不认识「这个整数是 maxsplit」，属工具已知噪声。**注意方向性**：只有「增大」等价，「减小」是真变异体，任何抑制规则都不能一刀切。
- `return_none@572:12#0`（`_drift_scanner` 内嵌闭包 `dangling` 的 `return False`）——**判等价，故意不杀**。与 438 同形且同样不可观测，但宿主只活在 `fn` 的 closure cell 里，判例只能去翻 `fn.__closure__`；那样钉住的是「`dangling` 是个嵌套函数」这一实现结构——把它提成模块级函数或内联掉，行为一字不变而判例会红。这种判例比没有判例更坏（防腐 #7 的反面）。#391 独立地作了同一判断。

**杀伤率要分档报，不然是在自夸。** 99.76% 里有相当一部分只是「改动检测」：判例把源里的常数抄成 golden 表，改一个数就红，但仓库里没有第二处文档载这个数，所以它防的是改动、不是行为。正确的消融是**保留观测通道、只把期望值换成从 `checks.CATALOG` / `checks.TIER_TIMEOUTS` 现算**（删测试会把 oracle 和通道混为一谈，是错的消融——这条是 R-301 自己踩出来的教训）。实测（对 404 个原存活体）：

| 消融（保留通道，换掉 oracle） | 仍被杀 / 404 |
|---|---|
| 只换文档解析 oracle（`ab_docs`） | 359 |
| 只换 `PHASE_GOLDEN`（`ab_phase`） | 335 |
| 只换 `EST_GOLDEN`（`ab_est`） | 313 |
| 换 phase + est（`ab_both`） | 247 |
| **全换（`ab_all`，最强）** | **206** |

所以本分支的**独立 oracle 下界 = (435 + 206) / 839 = 76.4%**；402 个新杀伤里 196 个（49%）靠 golden 表，主要是 `est` 的秒数与 `phase` 的同档内挪动。两个数都要报：**99.76% 是全口径，76.4% 是「就算 golden 表全部失效也还在」的地板**。本轮新增的那一个杀伤（438）在 `ab_all` 下仍然成立（205 → 206），即它不依赖任何 golden 表。

**DoD 逐条对照**：① 「存活体减半」——404 → 2，远超减半，且剩下两个都附了等价证明；② 「覆盖率地板不降」——地板 = `qa/coverage_floor.txt` 的 83.5，量的是 `act/` + `server/` 的总行覆盖（`run_coverage.sh:22` 的 `--source=act,server`），`skills/` 根本不在取值范围内。实测确认而不是推理：在 `trees/r301` 里分别对「原有 5 份 skill 判例」与「那 5 份 + 新增 4 份」跑 `coverage run --source=act,server`，两次都报 `No data was collected`、`act/`+`server/` 覆盖行 0、分母同为 29,124 条语句——新判例对地板的贡献恰好是 0，不可能把它压下去。

**本地门**（在 `bd4dff58` 的树上）：`python3 -m compileall -q act ingest` exit 0；`ruff check .` All checks passed（本机 0.15.21，CI 钉 0.15.20）；`python3 scripts/ci/changelog_fragments.py check` → `ok (127)`；`python3 scripts/qa/ledger_diff.py --base origin/main` → `0 finding(s) / OK`；**3.9 腿**（CI 的下限，本机 `/usr/bin/python3` = 3.9.6）跑 10 份映射判例 `Ran 219 tests … OK`。全量 `unittest discover` 与 `run_gates.sh` 见下节。

**记两条给下一个 session 的口径修正**：① `_module_score`（`mutate.py:739-745`）把 `timeout` 记在**分子**（killed 侧），所以子集变慢只会让杀伤率**虚高**，不会虚低——每往靶区加一份判例就多一分这个风险；② 本机有 3.9（`/usr/bin/python3` 3.9.6、`~/.local/bin/python3.9` 3.9.25），CI 的 3.9 腿在本地是可跑的，别再写「本机只有 3.14、只能靠眼睛复核」。

**没做**：① 不碰生产代码（`skills/test-code/scripts/checks.py` 一字未改）；② 不把 #391 的五份判例也搬进来——它与 #401 在同一批变异体上高度重叠，两套一起合只是把 1,600 行重复判例塞进 `tests/`，本分支只取它独有的那一刀并自己重写；③ 不给 556 / 572 补判例（理由如上，写在 `tests/test_skill_test_code_checks_residual_mutants.py` 的 docstring 里，夜报再报不必重判）；④ 不改 `mutate.py` 的 `_const_sites` 去抑制 maxsplit 类位点——§57 的跳过名单是成文政策，收窄/放宽都要 owner 拍板，且「增大等价、减小不等价」的方向性规则写错会丢真信号；⑤ 不动 #401 / #391 两张 PR（不评论、不关闭——本通道禁止代 owner 发 PR 评论）。
