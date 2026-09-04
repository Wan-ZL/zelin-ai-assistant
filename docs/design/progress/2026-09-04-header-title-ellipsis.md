pr: `fix/header-title-ellipsis`（无版本 bump，版本由 tag 派生；#206 / #208 responsive-header 的收尾——#208 的真浏览器复核 + 最后一条 minor finding）
phase: P4 余量（D3：web 看板是产品；D31「顶栏永远一行」的自身验收——无新 owner 决策，plan 决策表不加行）
law: §49 追记（tight 极窄时标题省略号 + tooltip、标识不缩；判例清单）

**先复核 #208**（合并时没跑过 adversarial pass）：按 reviewer 的配方在真浏览器里跑——`page.route('**/api/actions*')` 改成 200 `{ok}` / 500 `{error:{message:≈1300px 原文}}`，en + 壳桥 @720、zh / en + 壳 @1440、en + 壳 @1200 五景，开「提建议」→ 填 textarea → 发送，回执 `.chrome-feedback-note` 可见期间量：顶栏 `scrollWidth == clientWidth`、标题右沿 ≤ 左翼右沿、右翼右沿 ≤ `innerWidth`、`documentElement.scrollWidth == innerWidth`、顶栏 52px、回执 `closest('.chrome-filterbar')` 为空——**全部通过，#208 无需改动**。spec 里原缺 en + 壳 @1200 一景，本 PR 补上；另加一景 @720 导航栏 320（顶栏 400，标题已在省略号）——修复前这景红，红在标题本身被裁 56px，与回执无关。

**修最后一条 minor finding**：tight 档左翼只剩标识 + 标题，但 `.shell` 下限 720 − 导航栏最宽 320（NavRail `WIDTH_MAX`）= 顶栏 400；tight + 壳桥的槽位 96 + 右翼 149 + padding / gap 52 之后左翼只剩 103px，标识 20 + gap 10 + 标题（zh 100 / en 129）放不下——`.shell-header-left { overflow: hidden }` + `.shell-title { white-space: nowrap }` 没有 `min-width: 0 / text-overflow`，h1 被硬裁（顶栏 400 时 zh 27px、en 56px；zh < 427、en < 456 起裁），没有省略号也没有 tooltip。修法两行：`.shell-header[data-density="tight"] .shell-title { min-width: 0; overflow: hidden; text-overflow: ellipsis }`，`HeaderBar` 在 tight 给 h1 挂 `title={appName}`。**只在 tight 生效**是刻意的：full / compact 里标题一旦 `min-width: 0` 就会和三段小字按 flex-basis 比例一起缩，破坏 §49「左翼三段小字先让、标题不缩」的次序；tight 里左翼只剩它一个可缩项，所以省略号只在标题真放不下时出现。标识 `flex: none` 照旧。

**golden 条件逐像素比对**：本地用临时 probe（不入库）在修复前把 zh @1440 导航栏 200 无壳的 `.shell-header` 截图 + `outerHTML` 存成 snapshot，修复后 `toMatchSnapshot({ maxDiffPixels: 0, threshold: 0 })` 与 HTML snapshot 双双通过——顶栏 DOM 与像素一字不变（full 不挂 `title`、不加样式）。入库的判例改成量得出来的等价条件：golden 条件下标题 `scrollWidth == clientWidth`、computed `text-overflow` 仍 clip、无 `title`。

**判例**：真浏览器 `headerLayout.spec.ts` +13（13 → 26）：`sidebarWidth` 320 / 300 / 280 @720 + 壳 × zh / en → 顶栏 400 / 420 / 440（标题矩形在左翼内、标识 20px、顶栏 52px、省了字则 `text-overflow` = ellipsis 且 `title` = 全名、槽位控件一个不裁、展开搜索框后标题省的字不增——实测放大镜换成 basis 0 的输入框还回 6px）；导航栏 320 时 zh / en `scrollWidth > clientWidth`（省略号真出现过，不是空判）；导航栏 260（顶栏 460）两种语言全文可见；golden 条件如上；「提建议」回执 +2 景。修复前 9 条红（7 条标题 + 2 条对照——460 与 golden 两条修前修后都绿，钉的是「≥460 不许省」与「golden 不变」）。jsdom `HeaderBar.test.tsx` +1：tight 挂 `title` 全名（双语）、compact / full 不挂。**提醒**：e2e 跑的是 `web/dist`，改源码后先 `npm run build`。

**不变的**：wire 零变化；full / compact 零变化；6 张视觉 golden 在 main 上自 #204 / #205 起已全红（侧栏少两项 + 设置页多一区），本 PR 不动它们；§66.2 判卷面 `report.*` 由 `run_gates.sh` 重生成（结果见 PR 描述）；`vnext2-plan.md` 决策表不加行。
