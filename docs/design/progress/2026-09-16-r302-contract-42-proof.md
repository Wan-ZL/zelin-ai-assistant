pr: `ai/self-improve/R-302`（PR #399；R-302「issue #385：coverage: contract:§42 has no proof」；`dev` 上已由 b5bccbbf 修好，本分支是逐字节 backport 到 `main`）
phase: 横切（§58 QA 门与账本 / §77 全量覆盖测试体系——后者只在 `dev` 上）+ §65 自我改进通道
law: —（无修法。只在 `tests/test_radar_triage.py` 的模块 docstring 里加一条指向 §42 的指针；行为零改动，CONTRACT 不动）

**判定**：issue #385 说的是 `dev` 上的覆盖清单——`scripts/qa/coverage_inventory.py`、`qa/coverage_inventory.json`、§77 全量覆盖测试体系三样都只活在 `origin/dev`（`main` 的 CONTRACT 止于 §76）。那边已经修好了：commit b5bccbbf（`test(coverage): give the 9 python/web scenarios a real proof (missing_proof 18 → 9)`，2026-09-16 00:03）给 `tests/test_radar_triage.py` 的 docstring 加了一段 §42 指针，`origin/dev:qa/coverage_inventory.json` 里 `contract:§42` 的 proof 已经是 `unittest:tests.test_radar_triage`；它随 PR #395（`qa/coverage-integrate` → `dev`，2026-09-16 07:53Z 合）落地，那个 PR 正文里就写着 `Closes #385`——但 `dev` 不是默认分支，关键词没触发，所以 issue 至今仍是 OPEN。`main` 这边没有那台机器，所以这一行在 `main` 上仍然是空的、也没人看得见。

**这一轮做了什么**：把 `dev` 那一段**逐字节**搬到 `main`——`git checkout origin/dev -- tests/test_radar_triage.py` 之后 `git diff origin/dev -- tests/test_radar_triage.py` 空，所以 train 开起来时这一处是同一改动的 no-op 而不是冲突。用 `dev` 的生成器（按路径加载、改写 `REPO_ROOT`、只跑 contract 那一趟）对本 worktree 实测：改前 `contract:§42` 的 proof 为空、是这棵树上**唯一**一行 proof 为空的 contract 行；改后 proof = `unittest:tests.test_radar_triage`、空 proof 行数 1 → 0，与 `dev` 已提交的那份 inventory 逐字相同。

**为什么只引一个模块**：`coverage_inventory.py` 的 proof 是 `"unittest:" + ",".join(sorted(modules)[:MAX_MODULES])`（MAX_MODULES = 4）——引用集合一变，proof 字符串就变。`dev` 已经把 `unittest:tests.test_radar_triage` 写进生成的 `qa/coverage_inventory.json`，而 `main` 这一侧再生成不出那个文件；此时在 `main` 上多引一个模块，合车之后 `dev` 的那份 inventory 就陈旧了，`tests/test_coverage_inventory.py::test_file_is_not_stale` 会红。所以本 PR 只此一引，宽度留给 `dev` 上的后续卡。

**issue 正文两处不准，记在这里**：① 它建议的 `tests/test_card_readability.py` 在本仓库不存在；`tests/test_card_model_shapes.py` 钉的是 `card_model.Requirement` 的 round-trip 形状（§1 / §2 / §20），2026-09-03 才建，比 §42 晚七周（那会儿它 import 的 `act/lib/card_model.py` 还没从 registry 里拆出来，所以它根本不可能在 v0.42.0 跑过）；它断的那些形状正是 §42 明文冻结、一个字没动的部分，对 §42 的改动零敏感——引它是假证据。② 「waive as covered-by-\<test file\>」这条退路对 contract 行不存在：`contract_rows` 只会发 `waive_reason=WAIVE_TOMBSTONE`，`merge_rows` 是先到先赢（`setdefault`）且 `build_rows` 把 contract 排在最前，`qa/coverage_fixtures_b.json` 覆盖不了一个 `contract:` id（它里面也只有 `B-xx` 行）。

**为什么 `tests/test_radar_triage.py` 是诚实的证据**：那两条判例是 §42 自己的 commit 37ad1fc8（`feat(ui): 卡面大扫除 …(v0.42.0) (#59)`）加进去的，两条合起来六条断言、其中四条在 37ad1fc8^ 上是红的——旧 prompt 写死 "Zelin"、没有 "asks directed at" 这个说法、正文里真的有 "manager"，`_note_source` 也真的回 `"who": "manager"`。这不是正则碰巧对上，是构造上为真。

**没做、且不许冒认已覆盖**：§42 另外两块——(a) Swift 卡面那一半（D3 退役中的原生 app，python 判例够不着）；(b) §15 语言梯那一半，`tests/test_failures_ui_lang.py` 与 `tests/test_doctor.py` 的 `DoctorLanguageRoutingTestCase`（docstring 已写 `v0.42 (audit #16)`）确实逐条钉着它，但按上面「只引一个模块」的理由不在本 PR 引，留给 `dev` 侧的卡。两处真空白：`act/radar.py:549` 的 `owner_name` 空值回落 `or "Zelin"` 全仓无判例；§42 的「`who` 拼进 quick_capture 的 candidate 描述」（`act/radar.py:1546-1551` 的 `_item_desc(... who=note.stem ...)`）也无判例。另外别去写「同一批笔记可能比旧版提出更多候选卡」的判例——所有 radar 判例都注入 fake runner（绝不 spawn 真 claude），候选产出量是 fake 说了算，可断言的只有 prompt 正文。

**给 owner**：本 PR 与 dev→main 的 train 同批或之前合都安全（同一改动，合车 no-op）；`Closes #385` 只在默认分支触发，所以这一条就是 #385 在 `main` 上的关闭点。
