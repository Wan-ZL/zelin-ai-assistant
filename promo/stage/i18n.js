// EN translations for the render stage (?lang=en). Two kinds of strings:
// - UI chrome: copied VERBATIM from the app's real L("zh","en") pairs in
//   mac/Sources + shared/Sources/I18n.swift, so the EN board matches what the
//   app actually shows in English mode.
// - Card content: EN renditions of demo_seed's fictional dataset (the data a
//   fictional English-speaking user would have). Keyed by the exact zh string.
// T(s) falls back to the original when a string has no entry.

const I18N_EN = {
  // ---- window / sidebar / board chrome (real app strings)
  "Zelin's AI Assistant — 任务台": "Zelin's AI Assistant — Workbench",
  '任务台': 'Workbench',
  '问问助手': 'Ask',
  '依赖检查': 'Dependencies',
  '录制与 ingest': 'Recording & Ingest',
  '回收站': 'Trash',
  '归档': 'Archive',
  '设置': 'Settings',
  '关于': 'About',
  '数据生成于 刚刚': 'Data generated just now',
  '提案 · proposals': 'Proposals',
  '运行中 · running': 'Running',
  '待验收 · review': 'Review',
  '储备 · backlog': 'Backlog',
  '已验收 · done': 'Done',
  '＋ 一句话，AI 来研究并提案…': '＋ One sentence — AI researches and proposes…',
  '✓ 批准': '✓ Approve',
  '✕ 拒绝': '✕ Reject',
  '💬 修改': '💬 Comment',
  '展开详情 ▸': 'Details ▸',
  '◉ 停止': '◉ Stop',
  '✓ 验收': '✓ Accept',
  '↩ 打回': '↩ Send Back',
  '📋 复制成稿': '📋 Copy final draft',
  '排队中': 'Queued',
  '需输入': 'Input',
  '怎样算办完：': 'Definition of done:',
  '交付了什么：': 'Delivered:',
  '验收清单——逐条对照：': 'Acceptance checklist:',
  '交付：聊天成稿': 'Deliver: chat draft',
  'AI 研究中…（补全上下文、生成提案）':
    'AI researching… (gathering context, drafting proposal)',
  '需 manager green-sign（只出草稿）': 'needs manager green-sign (draft only)',

  // tier hints (registry data shown inside the tier chip)
  'T1 · 一键可批': 'T1 · one-click approve',
  'T2 · 需文字确认': 'T2 · needs written confirmation',
  'T1 · AI 研究中': 'T1 · AI researching',

  // ---- fictional card content (demo_seed dataset)
  'example-bench: leaderboard 一键导出评测报告':
    'example-bench: one-click leaderboard report export',
  'leaderboard 一键导出评测报告': 'One-click leaderboard report export',
  'dashboard 页出现「导出报告」按钮':
    'An "Export report" button appears on the dashboard page',
  '点击后生成 markdown + png 到 exports/':
    'Clicking it renders markdown + png into exports/',
  'draft PR 通过 CI': 'Draft PR passes CI',
  '已开 draft PR example-bench#42：dashboard 加「导出报告」按钮，后端渲染 markdown + png，CI 全绿。':
    'Draft PR example-bench#42 open: "Export report" button on the dashboard, backend renders markdown + png, CI green.',

  'inkweld: 搭对外可访问的 demo 环境 + 种子数据':
    'inkweld: public demo environment + seed data',
  'demo 站可公网访问，演示账号能登录':
    'Demo site publicly reachable, demo account can log in',
  '数据全部合成，无任何真实客户信息':
    'All data synthetic — zero real customer info',
  '一条命令可重置 demo 数据': 'One command resets the demo data',
  'demo 用真实数据还是合成数据，manager 和 alex.doe 意见不一致':
    'real vs synthetic demo data — manager and alex.doe disagree',

  '起草 Q3 planning 的 one-pager（中英双语）':
    'Draft the Q3 planning one-pager (bilingual)',
  '提纲覆盖 3 个 objective': 'Outline covers 3 objectives',
  '中英双语': 'Bilingual (EN + ZH)',
  '一页以内': 'One page max',

  '统一 example-bench 和 inkweld 的 lint 配置':
    'Unify lint config across example-bench and inkweld',

  'example-bench: 修 flaky 的 e2e 测试（retry 逻辑）':
    'example-bench: fix flaky e2e tests (retry logic)',
  'inkweld: README 快速上手一节重写':
    'inkweld: rewrite the README quick-start section',
  'example-bench: 数据集 v2 的 loader 兼容层':
    'example-bench: loader shim for dataset v2',
  '给 inkweld 接 Supabase auth（需要 service key）':
    'Wire Supabase auth into inkweld (needs service key)',

  'example-bench: 评测缓存层（重复 run 提速 10x）':
    'example-bench: eval cache layer (repeat runs 10x faster)',
  '已在 example-bench 开 draft PR #87：runner 加 content-hash 缓存层，重复 run 从 ~12min 降到 ~70s；失效逻辑带 6 个单测，CI 全绿。':
    'Draft PR #87 open in example-bench: content-hash cache layer in the runner; repeat runs down from ~12 min to ~70 s; invalidation covered by 6 unit tests, CI green.',
  '同 config 重复 run 命中缓存': 'Repeat runs with the same config hit the cache',
  '缓存失效逻辑有单测': 'Invalidation logic is unit-tested',
  'CI 全绿': 'CI fully green',

  '写本周 weekly report（发出去前先过目）':
    "Write this week's report (review before sending)",
  '周报初稿完成：两个 project 各一段进展 + 下周计划，中英双语。':
    "Weekly report drafted: a progress section per project + next week's plan.",
  '覆盖本周两个 project 的进展': "Covers both projects' progress this week",
  '不超过一页': 'One page max',

  'example-bench: CI 加 lint gate（ruff + prettier）':
    'example-bench: add a CI lint gate (ruff + prettier)',
  '把周会 action items 自动整理成清单':
    'Auto-collect weekly-meeting action items into a list',

  'example-bench 的 README 安装一节过时了':
    'example-bench README install section is stale',
  'setup 命令已经跑不通，新人第一步就卡住。':
    'The setup command no longer works; newcomers get stuck at step one.',
  'README 里那个 setup 命令已经跑不通了吧？':
    'That setup command in the README is broken, right?',
  '周会纪要没人整理，action items 常丢':
    'Nobody collects meeting action items — they get lost',
  '口头说好的事没人记，下周就忘。':
    'Verbal agreements go unrecorded and are forgotten by next week.',
  '上周说好的两件事这周都没人记得':
    "Nobody remembers the two things we agreed on last week",
  'inkweld 报错日志太吵，真错误被淹没':
    'inkweld logs are too noisy — real errors drown',
  'warning 刷屏，出真错时没人看得见。':
    'Warnings flood the log; real failures go unseen.',
  '日志一分钟滚几百行 warning，真出事根本发现不了':
    'Hundreds of warning lines a minute — a real failure would be invisible',

  // ---- overlays (title / recording / grid / end / captions)
  '你只做两件事：批准、验收': 'You do two things: approve and accept.',
  'manager · 周会录音': 'manager · weekly-sync recording',
  '能不能加个按钮，一键把 leaderboard 导出成报告发出去？':
    'Can we get a button that exports the leaderboard as a report, one click?',
  '会议录音中': 'Recording meeting audio',
  '录屏中': 'Recording screen',
  '📡 radar 已捕获': '📡 picked up by radar',
  '生成提案': 'drafting a proposal',

  '开会录音、录屏，全部本地捕获': 'Meetings and screen, captured locally',
  'radar 检测到需求，AI 研究中': 'Radar picks it up — AI starts researching',
  '自动变成提案：计划、验收标准、成本': 'It becomes a proposal: plan, DoD, cost',
  '一键批准': 'One click to approve',
  '后台 Claude agent 在独立 worktree 开工': 'A background Claude agent gets to work',
  '交付 draft PR + 验收清单，不碰 main': 'Delivered as a draft PR — main stays untouched',
  '验收，归档': 'Accept. Done.',
  'local-first，数据留在你的 Mac': 'Local-first — your data stays on your Mac',

  // grid tiles
  '录屏捕获': 'Screen capture',
  '会议录音': 'Meeting audio',
  '三路 radar': 'Three radars',
  'Obsidian · Slack · Gmail': 'Obsidian · Slack · Gmail',
  '跨渠道去重': 'Dedup across sources',
  '批准前看成本': 'Cost before you approve',
  '一句话快速捕获': 'Quick capture',
  '隔离 git worktree': 'Isolated worktrees',
  '质量门': 'Quality gate',
  '只交 draft PR': 'Draft-PR-only delivery',
  '写作任务出成稿': 'Paste-ready drafts',
  'iOS companion（beta）': 'iOS companion (beta)',
  '手机上批准': 'approve from your phone',
  'Linux / Windows（beta）': 'Linux / Windows (beta)',
  'headless pipeline 可装': 'headless pipeline installs',

  'macOS 14+ · source available · FSL-1.1-MIT':
    'macOS 14+ · source available · FSL-1.1-MIT',
};

// current language, set by stage.js from ?lang=
window.LANG = 'zh';
function T(s) {
  if (window.LANG !== 'en') return s;
  return I18N_EN[s] || s;
}
