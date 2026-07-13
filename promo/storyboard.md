# Promo video — style notes & storyboard

≤60 秒宣传片（v4 成片 56.0s，11 幕，覆盖 8 个核心功能），**中英各一版**
（`?lang=en` 渲染英文看板——UI chrome 用 app 源码里真实的 `L()` 英文文案，卡片
内容是同一套虚构数据的英文版，见 `stage/i18n.js`）。所有画面数据来自
`scripts/demo_seed.py` 的虚构 scene（example-bench / inkweld / alex.doe /
sam.rivera，零真实信息）。重录一条命令：`bash promo/make.sh`
（详见 [README](README.md)）。

## 参考风格拆解（style notes）

参考：Photon iMessage App API 发布视频（42s，Apple keynote 式极简）。结构：

| 段落 | 手法 |
|---|---|
| 0–3s 标题卡 | 净色背景 + 特粗标题，行内嵌彩色 app 图标，词级入场动画 |
| 3–30s 用例演示 | 设备框内真 UI 走流程；一段一个用例；镜头推近关键操作；转场全部硬切 |
| ~33s 广度展示 | 文字轮播（"your agent can send [icon] games / …"）+ 用例卡片网格拉远 montage |
| 39–42s 收尾 | logo 定格，无 CTA 之外的杂物 |

借鉴要点：**硬切卡在节奏点**、一段只讲一件事、字幕短句、演示画面永远在动
（打字、光标、滚动）、结尾只留 logo + 一行信息。

本片改编：设备框换成 macOS 深色窗口（app 本来就是 dark kanban）；标题/结尾卡
用同款深色 + App 图标；广度段用 12 格功能网格拉远替代文字轮播。

对照参考片补齐的四个手法（v2）：

1. **一屏一件事**：hero 聚焦镜头里非主角卡整体压暗降饱和（`.pane.focus`），
   参考片"大量留白、单一主体"的等价物——在信息密度高的看板上用亮度分层实现。
2. **文字有自己的空间**：字幕加大 + 底部渐变 scrim，不再和卡片文字打架。
3. **切点带视觉冲击**：每个硬切落拍瞬间镜头 3% punch-in 再回弹（0.4s），
   音乐重音和画面冲击同时发生。
4. **物理连续性**：批准/验收两个时刻，hero 卡化作紧凑幽灵卡从原列飞进目标列
   （`.flyghost`），代替瞬移——参考片里 mail 卡"发送入流"的等价物。

## 配乐

"Voxel Revolution" — Kevin MacLeod (incompetech.com)，CC BY 4.0。
`promo/beatgrid.py` 实测：81.25 BPM 网格（与 121.75 呈 3:2，breakbeat 双解），
强拍每 **1.4769s**，首拍 offset **0.255s**。所有镜头切换都落在
`0.255 + n × 1.4769s`（每个强拍）上；见 `stage/timeline.js`。
注意提速不要用 ffmpeg setpts 后期加速——那会让切点脱离节拍网格；
正确做法是改 `timeline.js` 里的 cue（本片即按 1.25x 紧凑度重排）。

发布时注明（CC BY 要求）：
`Music: "Voxel Revolution" Kevin MacLeod (incompetech.com), CC BY 4.0`

## 分镜 v4（timecode 以成片为准；11 幕 × 8 功能）

| # | 时间 | scene 数据 | 画面 | 字幕（CN） |
|---|---|---|---|---|
| S0 | 0:00–0:03.2 | — | 深色标题卡：App 图标弹入 + "Zelin's AI Assistant" + 双语副标题 | — |
| S1 | 0:03.2–0:10.6 | 提取 overlay | 「会议录音中/录屏中」徽章 + 波形 → 6 块碎片飘入（转写×3、屏幕 OCR×2、slack×1）→ 无关碎片退暗、相关碎片点亮关键词 → 紫线连接 → 汇聚成一张卡（meeting/slack chips + 重复×2）→ 切入看板「AI 研究中」占位卡 | 录音、录屏，全在本地 → AI 从海量数据里找出相关碎片 → 不同渠道催同一件事，只出一张卡 |
| S2 | 0:10.6–0:15.0 | `initial` | 占位卡绽放成完整提案：计划/验收标准级联，chips（T1/截止/$12/重复×2）逐拍脉冲 | 自动变成提案：计划、验收标准、成本 |
| S3 | 0:15.0–0:18.0 | `initial` | 镜头上移到输入框：光标打字「统一 example-bench 和 inkweld 的 lint 配置」→ Enter 闪紫 → 灰色研究卡弹出 | 或者，一句话扔给它 |
| S4 | 0:18.0–0:22.4 | `approved`→`running` | 光标点「✓ 批准」→ 幽灵卡飞进运行中（排队中 1.5s）→ 变 working | 一键批准，后台 Claude agent 开工 |
| S5 | 0:22.4–0:26.8 | `running` + 终端 overlay | 推近 working 卡的「双击在终端运行」行 → 双击双涟漪 → ghostty 终端弹出：`claude attach b1e4d7a2` 打字、agent 工具日志逐行滚出 | 双击，随时进 live session 微操 |
| S6 | 0:26.8–0:31.3 | merge overlay | 主卡/副卡两张相似卡多选打勾 → 紫色 AI 裁决卡（建议合并·置信度高）→ 光标点接受 → 副卡飞入主卡融合 | 重复的卡？AI 裁决怎么合 |
| S7 | 0:31.3–0:37.2 | `review` | draft PR #42 回执 + 验收清单三连勾（前半）→ 聚光切到 weekly report 卡，成稿块展开，光标点「复制成稿」→ 已复制 ✓（后半） | 交付 draft PR + 验收清单，不碰 main → 写作任务出成稿，用你的语气 |
| S8 | 0:37.2–0:40.1 | `done` | 硬切全景 + 幽灵卡从待验收飞进已验收 | 验收，归档 |
| S9 | 0:40.1–0:44.6 | 手机 overlay | iPhone 框：Slack 给自己发白板照片（手绘 inkweld demo 草图）→ 「AI 研究中」占位卡 → 切 iOS 看板，拇指点「✓ 批准」→ 已批准 ✓（底部「端到端加密同步」） | 白板拍一张就是卡片，iOS 直接批准 |
| S10 | 0:44.6–0:49.0 | grid overlay | 12 格功能网格拉远（拖拽捕获/导入会话/Ask/web 看板/一键修复/auto-resume/E2E 同步/Linux+Windows/质量门/worktree/每周 digest/双语切换） | local-first，数据留在你的 Mac |
| S11 | 0:49.0–0:56.0 | — | 结尾卡：图标 + 名字 + repo 徽章 + 一行平台/license，音乐淡出渐黑 | — |

8 个批准功能的落位：跨渠道合并→S1｜透明决策 chips→S2｜快速捕获→S3｜
双击微操（+打回回原 session 属同一能力面）→S5｜AI merge-review→S6｜
聊天成稿+voice→S7 后半｜手机闭环→S9｜auto-resume→grid tile
（R-107 卡在 S5 画面里也带着真实的 auto-resume 错误行）。

## 改版指南

- 改文案/时长/镜头：只动 `stage/timeline.js`（所有 cue 集中在此）。
- 换配乐：`promo/beatgrid.py <track>` 重测 BPM/offset，更新 `PULSE`/`OFFSET`。
- 卡片内容变了：什么都不用做——stage 直接吃 `demo_seed.py` 的输出。
- UI 改版了：对照 `docs/assets/kanban.png` 更新 `stage/stage.css` 的还原样式。
