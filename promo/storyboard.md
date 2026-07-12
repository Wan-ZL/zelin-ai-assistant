# Promo video — style notes & storyboard

≤60 秒宣传片（成片 56.5s）。所有画面数据来自 `scripts/demo_seed.py` 的虚构
scene（example-bench / inkweld / alex.doe / sam.rivera，零真实信息）。重录一条
命令：`bash promo/make.sh`（详见 [README](README.md)）。

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

## 配乐

"Voxel Revolution" — Kevin MacLeod (incompetech.com)，CC BY 4.0。
`promo/beatgrid.py` 实测：81.25 BPM 网格（与 121.75 呈 3:2，breakbeat 双解），
强拍每 **1.4769s**，首拍 offset **0.255s**。所有镜头切换都落在
`0.255 + n × 2.9538s`（双强拍）上；见 `stage/timeline.js`。

发布时注明（CC BY 要求）：
`Music: "Voxel Revolution" Kevin MacLeod (incompetech.com), CC BY 4.0`

## 分镜（timecode 以成片为准）

| # | 时间 | scene 数据 | 画面 | 字幕（CN / EN） |
|---|---|---|---|---|
| S0 | 0:00–0:06.2 | — | 深色标题卡：App 图标弹入 + "Zelin's AI Assistant"，副标题两行 | 你只做两件事：批准、验收 / A personal AI chief-of-staff for macOS |
| S1 | 0:06.2–0:12.1 | — | 录音场景：「会议录音中/录屏中」徽章 + 波形律动 + manager 语录逐字打出「能不能加个按钮，一键把 leaderboard 导出成报告发出去？」→ 「radar 已捕获 → 生成提案」 | 开会录音、录屏，全部本地捕获 / Meetings and screen, captured locally |
| S2 | 0:12.1–0:15.0 | `captured` | 切入看板，推近待审批列：R-101「AI 研究中」占位卡，spinner 转动，紫色高亮脉冲 | radar 检测到需求，AI 研究中 / Radar picks it up — AI starts researching |
| S3 | 0:15.0–0:20.9 | `initial` | 占位卡绽放成完整提案卡：chips（T1·一键可批 / 截止 / $12 / 重复×2）、计划、验收标准逐条级联入场；镜头缓推 | 自动变成提案：计划、验收标准、成本 / It becomes a proposal: plan, DoD, cost |
| S4 | 0:20.9–0:23.9 | `approved` | 光标滑向「✓ 批准」点击（白圈涟漪，切点前 0.3s）→ 硬切：卡片已到运行中列，灰色「排队中」 | 一键批准 / One click to approve |
| S5 | 0:23.9–0:29.8 | `running` | 硬切：queued 变 working——蓝色徽章、`claude attach b1e4d7a2`、agent 名；镜头推近 | 后台 Claude agent 在独立 worktree 开工 / A background Claude agent gets to work |
| S6 | 0:29.8–0:37.2 | `review` | 硬切到待验收：「交付了什么: 已开 draft PR example-bench#42…」+ 验收清单三项逐条打勾；光标滑向「✓ 验收」点击 | 交付 draft PR + 验收清单，不碰 main / Delivered as a draft PR — main stays untouched |
| S7 | 0:37.2–0:41.6 | `done` | 硬切全景：卡片落入已验收列（✓ 已验收 · 刚刚），五列满员定格微退 | 验收，归档 / Accept. Done. |
| S8 | 0:41.6–0:49.0 | — | 12 格功能网格拉远 montage（录屏/会议录音/三路 radar/去重/成本/快速捕获/worktree/质量门/draft PR/成稿/回收站/wiki），大字幕压底 | 全程 local-first，数据不出你的 Mac / Local-first. Nothing leaves your Mac. |
| S9 | 0:49.0–0:56.5 | — | 结尾卡：图标 + 名字 + `github.com/Wan-ZL/zelin-ai-assistant` 徽章弹入 + 「macOS 14+ · source available · FSL-1.1-MIT」，音乐淡出，画面渐黑 | — |

## 改版指南

- 改文案/时长/镜头：只动 `stage/timeline.js`（所有 cue 集中在此）。
- 换配乐：`promo/beatgrid.py <track>` 重测 BPM/offset，更新 `PULSE`/`OFFSET`。
- 卡片内容变了：什么都不用做——stage 直接吃 `demo_seed.py` 的输出。
- UI 改版了：对照 `docs/assets/kanban.png` 更新 `stage/stage.css` 的还原样式。
