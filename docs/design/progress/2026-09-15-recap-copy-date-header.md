pr: `ai/self-improve/R-300`（issue #299；PR #351）
phase: §63 会议 recap 的第三轮打磨（#296 备注预检、#297 生成态之后；self-improve 通道 §65）
law: §63.5 追记（新增「复制时的抬头一行」+ 本节「正文 5 行纯文本」就地修订 + Settings 三把→四把）/ §49 追加（`GET/PUT /api/settings/recap` 的 `copy_header?`，add-only）

**病历**：复制键只写五行正文。时间活在详情面板的 `<h3>`（`rowLabel`），日期活在左列表的按日分组表头（`dayKey`），两样都不进剪贴板——粘到 Slack / 邮件 / 文档里，收件人看不出这是哪天哪场会；同一天两场会的两份粘贴逐字同形。issue #299 自己指出了第二半：`rowLabel()` 连日期都没有，所以「把标题也复制上」并不够。

**修法在同一个 PR 里**：§63.5 原文立过「复制正文 = 该语言 5 行、不加任何别的东西（issue #129 §4）」，所以这不是「修 bug」而是改规格。本 PR 在 §63.5 新增一条「复制时的抬头一行」，并把本节正文那句就地改读为「抬头一行 + 5 行」；同时给 `copy_header` 开关（**默认开**）——实用上这是缺陷，默认就该是修好的状态，但明文立过的东西不留回头路地改掉不合规矩。

**三条裁决**（issue 把它们列为开放问题）：(1) **时间是采集到的原值，不向整点取整**——session 由屏幕与音频活动判出，`14:07` 是录制注意到这场会的时刻，不是它被安排的时刻；粘出去的是记录，取整会让它读起来像日程（宪法第 4 条「记录 ≠ 立案」的同一个立场）。(2) **抬头随详情页的语言切换**，与正文同一把开关；EN 半角括号、中文全角，不另立第二套文案机制。(3) **星期表在代码里写死两张**（`WEEKDAYS_EN` / `WEEKDAYS_ZH`，周日起、与 `Date.getDay()` 同序），**禁 `Intl` / `toLocaleDateString`**——Node 与浏览器的 ICU 数据不一致，判例会随运行环境漂。

**实现**：`web/src/components/recaps/recapText.ts` 三个纯函数 `dayLabel` / `recapHeader` / `recapClipboardText`（零请求、零 React）；日期复用既有 `dayKey`、时段 / 应用 / 时长复用既有 `rowLabel` 逐字，**星期是本轮唯一新增的显示物**。`RecapDetail` 的 `<h3>` 从 `rowLabel` 换成同一个 `recapHeader`——所见即所粘，且日期不再只活在列表侧；左列表行照旧 `rowLabel`。退化两条：`start` 解析不出 → 抬头退回 `rowLabel`（它自己已是 `--:--`），绝不多出一个 `?`；没有正文 = 空串，光一行抬头不是纪要。

**开关的落点是个例外，写进了 §63.5**：`recap_copy_header` 是唯一一把 **Python 管线不读**的 recap 旋钮（抬头在页面拼，唯一出口仍是剪贴板）。它住 `act/lib/config.py` 只为让 §15 的 override 白名单盖住页面的写入（`maintainer_repo_path` / `maintainer_session_id` 两把的先例），并**故意不进** `recap_store.settings()` 的管线视图——`tests/test_recap_store.py` 钉了 `assertNotIn("copy_header", st)`，免得下一个 session 以为管线该读它。其余一切同前三把旋钮：config.yaml `recap:` 块 → `_OVERRIDE_FIELDS` 扁平键 → `GET/PUT /api/settings/recap` diff-write。

**一层没动的**：`state/recap/recaps/<key>.json` 仍存五行（抬头不落盘）；**§63.4 的 Slack 草稿 payload 仍是五行**（另一条通路、另一段 payload，issue 问的是「复制出来的正文」，跟不跟进等 owner 一句话）；`recap_generate` / `recap_slack_draft` 的 wire 字段、inbox golden、`recaps[]` 投影零改动；五层无发送路径一层没松。视觉 golden 零变化——新 checkbox 在设置页「会议纪要」区里，而各区是默认折着的 `Fold`，1440×900 的 `settings-*` 只拍到折着的区头；会议纪要页本身不在三张 golden 页里。

**自审改掉的一处真错**：抬头例句原本写成 `14:07–14:48 · Zoom · 48 min`，但 `duration_min` 恒等于 `int(round((end-start)/60))`（`recap_store.py` / `act/recap.py` 两个写者），14:07→14:48 永远是 41 min——真机产不出那一行，而它当时散在六处（设置页中英两句、`config.example.yaml`、changelog fragment、CONTRACT 本条、`recapText.ts` docstring），两个新判例的 fixture 还照着它写（`duration_min: 48` 配 41 分钟的窗口），等于把一个产不出来的形状钉成了判例。六处全改 `41 min`、fixture 改 41。运行时渲染一直是对的，坏的是文案与 fixture——这条记在这里，因为「例句要能被真机产出」是本仓文档纪律里没写明但该有的一条。同轮顺手改正了三处本 PR 造成的失真：页面顶部「5 行纯文本，复制即用」、保存回执「下一轮 cron 生效」（复制抬头立即生效，其余三把才等 cron）、以及五处「三把旋钮」注释。

**判例**：`recapText.test.ts`（抬头两语言、**中英各七天**的星期序、原值不取整、同日两场不同形、坏 `start` 退化、剪贴板 6 行与关掉后 5 行）、`RecapsPage.test.tsx`（粘贴内容 = 面板标题逐字 + 五行、语言切换、`copy_header: false` 回到五行、设置快照缺席时按开算）、`RecapSection.test.tsx`（四把旋钮水合 + 一次 PUT 四键零多余字段 + 关掉抬头）、`tests/test_server_recaps.py`（默认 `copy_header: true` + `source` 四键 / config.yaml 层 / diff-write 两向）、`tests/test_recap_store.py`（override 白名单 + 坏值保默认 + 不进管线视图）、`tests/test_server_paths_mirror.py`（`DEFAULTS` 与 `Config` 四把逐字对账）。判例与时区无关：用本地时间构造 ISO、再按本地时间格式化，两头同一个时区抵消掉（2026-03-04 是周三，离任何 DST 跳变都远）。

**同 PR 顺手修的一件无关事（独立 commit）**：`tests/test_recap_generate_request.py` 的「`requested_at` 在起子进程之前取」把戳写成字面量 `2026-09-14T12:00:00Z`，而 `recap_requests.record` 写时按 `TTL_S`（24 h）剪旧条——这条判例在 **2026-09-15T12:00Z 一到就开始 `KeyError`**，此后每个 PR 的 CI 都会红、与改了什么无关（本轮 11:10Z 那遍全绿、12:05Z 那遍这一条红，正好撞在门上）。戳改成按同一格式现取真 now。教训与防腐 #5 同源：**判例里的时间字面量遇上带 TTL 的台账就是定时炸弹**，要么现取、要么把时钟做成缝。
