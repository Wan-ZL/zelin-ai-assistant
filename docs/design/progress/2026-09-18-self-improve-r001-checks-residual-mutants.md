pr: `ai/self-improve/R-001-checks-mutation`（PR #432；R-001「补测试：skills/test-code/scripts/checks.py 变异存活 404 体（杀伤 52%）」；**同一条夜报发现铸出的第六张卡**：#391 R-217 / #401 R-301 / #429 R-8153 / #430 与 #431 R-002 / 本卡 #432）
phase: 横切（测试网；vnext2-plan R2.8 / R2.3.4 每日自我改进循环的 self_improve 通道）
law: —（无新 §、无修法；§57「存活变异体 = 补测试提案、等价体记理由」与 §58 照旧执行）

**先做复测，再写判例。** 卡面数字（run=839 / killed=435 / survived=404）来自 2026-09-17 的夜跑。动手前第一件事是 `gh pr list --state open`：`skills/test-code/scripts/checks.py` 这一个模块已经有**两份**草稿 PR 在做同一件事，且两份的 7 项 required check 全绿。所以本轮的第一产出不是又一套判例，而是**对两份既有判例的独立复测**——两份 PR 的自述（「400 of 404」「404 → 3」）此前谁也没验过。

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
- `int_plus1@556:30#0`（`raw.split("#", 1)[0]` 的 maxsplit 1→2）——**可证等价，永不可杀**。`str.split(sep, k)[i]` 对任何 `i < k` 都与 k 无关：左到右切，第 i 段是真的第 i 段，只有第 k 段（余段）随 k 变，而这里 `[0]` 只读第一段。353,982 条输入（长度 ≤6 的分隔符全排列穷举 55,987 条 + 22 条手造边角 + 含全码位的随机串 297,995 条 + 真 `.gitignore` 72 行）零差异；同一行的 `int_minus1@556:30#0`（1→0，真的会让注释不再被剥）作阳性对照，在 200,533 条输入上报差异、且被两份判例都杀掉——说明这一行本身是被钉住的，存活的不是覆盖漏洞。`mutate.py` 的 `_const_sites` 对整数常量一律铸 ±1，不认识「这个整数是 maxsplit」，属工具已知噪声。**注意方向性**：只有「增大」等价，「减小」是真变异体，任何抑制规则都不能一刀切。
- `return_none@572:12#0`（`_drift_scanner` 内嵌闭包 `dangling` 的 `return False`）——**判等价，故意不杀**。与 438 同形且同样不可观测。差别**不在**「一个是行为一个是结构」，也不在「改了会不会红」——上面那条钉 `_unpinned` 的判例同样耦合实现，把 `_unpinned` 内联掉它一样会红（复核时实测过：内联后公共 API 判例 52 条全绿，只有那份判例 AttributeError）。真正的差别是**测试面有没有先例**：直呼模块级私名是本仓库几十处判例的既有做法，而去翻 `fn.__closure__` 把嵌套函数掏出来没有先例，且钉住的恰恰是「它是个嵌套函数」——提成模块级函数就红。后者不值那一行。#391 独立地作了同一判断。

**杀伤率要分档报，不然是在自夸。** 99.76% 里有相当一部分只是「改动检测」：判例把源里的常数抄成 golden 表，改一个数就红，但仓库里没有第二处载这个数，所以它防的是改动、不是行为。正确的消融是**保留观测通道、只把期望值换成从 `checks.CATALOG` / `checks.TIER_TIMEOUTS` 现算**（删测试会把 oracle 和通道混为一谈，是错的消融——这条是 R-301 自己踩出来的教训）。下表全部在**本分支的 10 份映射判例**上重测（不是 #401 的 9 份，两者不可混用），对象是 404 个原存活体：

| 消融（保留通道，换掉 oracle） | 仍被杀 / 404 |
|---|---|
| 只换四份 markdown 文档 oracle（`ab_docs`） | 360 |
| 只换 `PHASE_GOLDEN`（`ab_phase`） | 336 |
| 只换 `EST_GOLDEN`（`ab_est`） | 314 |
| 换 phase + est = golden 表全失效（`ab_both`） | 248 |
| 全换（`ab_all`，连文档 oracle 一起拿掉） | 206 |

402 个新杀伤的正交分解（`ab_all ⊆ ab_both ⊆ ab_docs`，且 `ab_both − ab_all` 与 `新增 − ab_docs` 同为 42，互斥性成立）：

- **154 个（38%）只靠 golden 表**（`PHASE_GOLDEN` / `EST_GOLDEN`）——`est` 的秒数与 `phase` 的同档内挪动，仓库里确实没有第二处载它们，这一档只值「改动检测」。
- **42 个（10%）靠四份 markdown 文档 oracle**（`_tiers_md_tables` / `_catalog_md_extended` / `_skill_md_tier_rows` / `_documented_timeouts` 真去解析 `references/tiers.md`、`catalog.md`、`SKILL.md`）——这些是**真的第二处真源**，按本节自己的判准**不算**改动检测。
- 其余 206 个是行为锚定。

所以有**两档地板**，别混为一谈：① **golden 表全部失效时 = (435 + 248) / 839 = 81.4%**；② 连文档 oracle 也一并拿掉（这一步其实拿掉了合法的独立 oracle，是个过严的下界）= (435 + 206) / 839 = 76.4%。要报的三个数是：**全口径 99.76% / golden 表地板 81.4% / 最保守地板 76.4%**。本轮新增的那一个杀伤（438）在最强消融下仍然成立（205 → 206），即它不依赖任何 golden 表。

**DoD 逐条对照**：① 「存活体减半」——404 → 2，远超减半，且剩下两个都附了等价证明；② 「覆盖率地板不降」——地板 = `qa/coverage_floor.txt` 的 83.5，量的是 `act/` + `server/` 的总行覆盖（`run_coverage.sh:22` 的 `--source=act,server`），`skills/` 根本不在取值范围内。实测确认而不是推理：在 `trees/r301` 里分别对「原有 5 份 skill 判例」与「那 5 份 + 新增 4 份」跑 `coverage run --source=act,server`，两次都报 `No data was collected`、`act/`+`server/` 覆盖行 0、分母同为 29,124 条语句——新判例对地板的贡献恰好是 0，不可能把它压下去。

**前向兼容实测**（这条比杀伤数更能判判例成色）：把 #401 台账里那条已确诊缺陷的一行修法（三处 JS 分支补 `_tool(ctx, "npm")` 闸门）真打到 workspace 的 `checks.py` 上，再跑本分支的 10 份判例——**修法前后都 `Ran 218 tests … OK`**，即本套判例没有把今天的错误行为钉死。（并行 session 用同一脚本测出 #391 的 `..._builders_t5_ext.py` 在修法后两条 ERROR，这是不采纳 #391 那五份判例的另一条独立理由。）

**本地门**（在 `bd4dff58` 之后的树上）：`python3 -m compileall -q act ingest` exit 0；`ruff check .` All checks passed（本机 0.15.21，CI 钉 0.15.20）；`python3 scripts/ci/changelog_fragments.py check` → `ok`；`python3 scripts/ci/progress_log.py check` → `ok`；`python3 scripts/qa/ledger_diff.py --base origin/main` → `0 finding(s) / OK`；**3.9 腿**（CI 的下限，本机 `/usr/bin/python3` = 3.9.6）跑 10 份映射判例 `Ran 218 tests … OK`。

**全量 `unittest discover -s tests` 照实记：`Ran 8067 tests in 1127.684s`，`FAILED (failures=1, errors=2, skipped=51)`——三条全部与本分支无关，逐条查过：**

1. `test_readme_audit_repo_readme.test_no_stale_claims` —— README 第 13 行写 `v1.0.114`，本机 `scripts/version_stamp.py` 算出 `1.0.116+4`。**本分支一个字节都没碰 README**（`git diff origin/main...HEAD -- README.md` 为空），是本机 tag 状态造成的既有假红。
2/3. `integration/test_auto_deploy_script` 与 `integration/test_auto_deploy_defer_episode` 的 `tearDownModule` —— 断言原文 `took 749s > 600s budget`，是**单文件时间预算**被撞破，不是判例红；本轮自己同时开着复核 agent，机器负载把它拖过了线。

**权威判决取 CI，而且比本机更强**：本 PR 在**当前 head `7b3d17e3`** 上 7 项 required check 全绿（run 35357358640，整体 conclusion `success`），其中 `Tests on ubuntu (Python 3.9)` 7 m 0 s、`Tests on ubuntu (Python 3.x)` 6 m 27 s、`QA gates（含覆盖率地板）` 8 m 6 s——两条 Python 腿都在 ubuntu 上对这棵树跑过全量，覆盖率地板也是在 CI 这个 canonical 环境下判的（`run_gates.sh` 的 crap / coverage-floor 两门在 darwin 上被 `soften_off_canonical` 清零退出码，本就不是权威判决）。

**记两条给下一个 session 的口径修正**：① `_module_score`（`mutate.py:739-745`）把 `timeout` 记在**分子**（killed 侧），所以子集变慢只会让杀伤率**虚高**，不会虚低——每往靶区加一份判例就多一分这个风险；② 本机有 3.9（`/usr/bin/python3` 3.9.6、`~/.local/bin/python3.9` 3.9.25），CI 的 3.9 腿在本地是可跑的，别再写「本机只有 3.14、只能靠眼睛复核」。

**没做**：① 不碰生产代码（`skills/test-code/scripts/checks.py` 一字未改）；② 不把 #391 的五份判例也搬进来——它与 #401 在同一批变异体上高度重叠，两套一起合只是把 1,600 行重复判例塞进 `tests/`，本分支只取它独有的那一刀并自己重写；③ 不给 556 / 572 补判例（理由如上，写在 `tests/test_skill_test_code_checks_residual_mutants.py` 的 docstring 里，夜报再报不必重判）；④ 不改 `mutate.py` 的 `_const_sites` 去抑制 maxsplit 类位点——§57 的跳过名单是成文政策，收窄/放宽都要 owner 拍板，且「增大等价、减小不等价」的方向性规则写错会丢真信号；⑤ 不动 #391 / #401 / #429 / #430 / #431 五张同题 PR（不评论、不关闭——本通道禁止代 owner 发 PR 评论）；建议由 owner 合本张、关其余五张。
