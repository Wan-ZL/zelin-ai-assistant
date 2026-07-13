// Promo video timeline v4 — ALL timing, captions and camera targets live here.
// Cut times sit on the music's accent grid so every scene change lands on a
// beat. Grid measured by promo/beatgrid.py on "Voxel Revolution" (Kevin
// MacLeod): strong accents every 1.4769 s starting at 0.255 s.
//
// 11 scenes covering the 8 showcase features approved by the owner:
// extraction (mass data → related fragments → one card), transparent proposal,
// quick capture, approve→agent, double-click→live terminal, AI merge-review,
// draft-PR + paste-ready drafts in your voice, accept, phone loop (Slack photo
// → iOS approve), feature grid, end card.
//
// Do NOT speed the film up with ffmpeg setpts afterwards — retime cues here
// so cuts stay on the beat grid.

const OFFSET = 0.255;        // first accent of the track (s)
const PULSE = 1.4769;        // one strong accent of the 81.25 BPM grid (s)
const B = (n) => OFFSET + n * PULSE;

const TL = {
  duration: 56.0,            // total video length (s) — hard cap 60

  // ---- scene boundaries (all on the beat grid)
  title_end: B(2),           //  3.21  title card out, extraction scene in
  extract_end: B(7),         // 10.59  extraction out, board on placeholder
  board_in: B(7) - 1.25,     //  9.34  board fades in under the extraction exit
  captured_end: B(7),        // 10.59  placeholder -> full proposal reveal
  qc_start: B(10),           // 15.02  camera to composer, typing
  qc_end: B(12),             // 17.98  back to hero card
  approve_click: B(13) - 0.30,
  initial_end: B(13),        // 19.45  cut: approved (queued) + fly
  queued_end: B(14),         // 20.93  queued -> working
  dbl_click: B(16) - 0.35,   // 23.53  double-click on the copy-cmd line
  term_in: B(16),            // 23.88  terminal overlay up
  term_end: B(18),           // 26.84  terminal out, merge vignette in
  merge_end: B(21),          // 31.27  vignette out, review lane in
  working_end: B(21),        // (review pane cut — same beat)
  review_pan: B(23),         // 34.22  camera pans to the weekly-report card
  copy_click: B(23) + 1.35,  // 35.57  复制成稿 click
  accept_click: B(25) - 0.30,
  review_end: B(25),         // 37.18  cut: accepted, wide shot + fly
  done_end: B(27),           // 40.13  board out, phone loop in
  phone_switch: B(28.5),     // 42.34  Slack DM -> iOS board
  phone_end: B(30),          // 44.56  phone out, feature grid in
  grid_end: B(33),           // 48.99  grid out, end card in

  fade_out: 55.2,            // video fade to black
};

// which demo_seed scene pane is visible from a given time
const PANE_CUES = [
  { t: TL.board_in, pane: 'captured', fade: 0.5 },
  { t: TL.captured_end, pane: 'initial', fade: 0.35 },
  { t: TL.initial_end, pane: 'approved', fade: 0 },
  { t: TL.queued_end, pane: 'running', fade: 0 },
  { t: TL.merge_end, pane: 'review', fade: 0 },
  { t: TL.review_end, pane: 'done', fade: 0 },
];

// camera keyframes: target = [pane, selector] (or 'window'), pad in board px,
// optional dy shifts the focus center down in board px. cut:true jumps.
const CAM_CUES = [
  { t: TL.board_in, target: ['captured', '.hero'], pad: 260, cut: true },
  { t: TL.captured_end, target: ['captured', '.hero'], pad: 170 },
  { t: TL.captured_end + 0.5, target: ['initial', '.hero'], pad: 90 },
  { t: TL.qc_start, target: ['initial', '.hero'], pad: 55 },
  { t: TL.qc_start + 0.55, target: ['initial', '.qcap'], pad: 210, dy: 170 },
  { t: TL.qc_end, target: ['initial', '.qcap'], pad: 180, dy: 170 },
  { t: TL.qc_end + 0.5, target: ['initial', '.hero'], pad: 70 },
  { t: TL.initial_end, target: ['initial', '.hero'], pad: 60 },
  { t: TL.initial_end, target: ['approved', '.hero'], pad: 300, cut: true },
  { t: TL.queued_end, target: ['approved', '.hero'], pad: 230 },
  { t: TL.queued_end, target: ['running', '.hero'], pad: 170, cut: true },
  { t: TL.term_in, target: ['running', '.hero'], pad: 60 },
  { t: TL.term_end, target: ['running', '.hero'], pad: 60 },
  { t: TL.merge_end, target: ['review', '.hero'], pad: 170, cut: true },
  { t: TL.review_pan, target: ['review', '.hero'], pad: 110 },
  { t: TL.review_pan + 0.6, target: ['review', '.hero2'], pad: 70 },
  { t: TL.review_pan + 1.7, target: ['review', '.hero2 .finaldraft'], pad: 90, dy: 55 },
  { t: TL.review_end, target: ['review', '.hero2 .finaldraft'], pad: 70, dy: 55 },
  { t: TL.review_end, target: ['done', 'window'], pad: -60, cut: true },
  { t: TL.done_end, target: ['done', 'window'], pad: 10 },
];

// lower-third captions
const CAPTIONS = [
  { t0: TL.title_end + 0.4, t1: TL.title_end + 2.0,
    cn: '录音、录屏，全在本地',
    en: 'Meetings and screen, recorded locally' },
  { t0: TL.title_end + 2.1, t1: TL.title_end + 4.1,
    cn: 'AI 从海量数据里找出相关碎片',
    en: 'AI pulls the related fragments from hours of data' },
  { t0: TL.title_end + 4.3, t1: TL.extract_end + 0.9,
    cn: '不同渠道催同一件事，只出一张卡',
    en: 'Same ask, several channels — one card' },
  { t0: TL.extract_end + 1.3, t1: TL.qc_start - 0.3,
    cn: '自动变成提案：计划、验收标准、成本',
    en: 'A full proposal: plan, DoD, cost' },
  { t0: TL.qc_start + 0.3, t1: TL.qc_end + 0.4,
    cn: '或者，一句话扔给它',
    en: 'Or just type one line' },
  { t0: TL.qc_end + 0.7, t1: TL.queued_end + 0.6,
    cn: '一键批准，后台 Claude agent 开工',
    en: 'One click — a background Claude agent starts' },
  { t0: TL.queued_end + 0.9, t1: TL.term_end - 0.3,
    cn: '双击，随时进 live session 微操',
    en: 'Double-click to drop into the live session' },
  { t0: TL.term_end + 0.25, t1: TL.merge_end - 0.3,
    cn: '重复的卡？AI 裁决怎么合',
    en: 'Duplicate cards? AI referees the merge' },
  { t0: TL.merge_end + 0.25, t1: TL.review_pan + 0.3,
    cn: '交付 draft PR + 验收清单，不碰 main',
    en: 'Draft PR + checklist — main untouched' },
  { t0: TL.review_pan + 0.6, t1: TL.review_end - 0.3,
    cn: '写作任务出成稿，用你的语气',
    en: 'Paste-ready drafts, in your voice' },
  { t0: TL.review_end + 0.25, t1: TL.done_end - 0.3,
    cn: '验收，归档',
    en: 'Accept. Done.' },
  { t0: TL.done_end + 0.3, t1: TL.phone_end - 0.3,
    cn: '白板拍一张就是卡片，iOS 直接批准',
    en: 'Snap a whiteboard — approve from your phone' },
];

// cursor waypoints (board space) + clicks
const CURSOR_CUES = {
  show: [
    { t0: TL.qc_end + 0.6, t1: TL.initial_end,
      path: [
        { t: TL.qc_end + 0.6, target: ['initial', '.hero'], ax: 0.9, ay: 0.85 },
        { t: TL.approve_click - 0.2, target: ['initial', '.hero .btn.approve'], ax: 0.55, ay: 0.55 },
        { t: TL.initial_end, target: ['initial', '.hero .btn.approve'], ax: 0.55, ay: 0.55 },
      ] },
    { t0: TL.queued_end + 1.0, t1: TL.term_in,
      path: [
        { t: TL.queued_end + 1.0, target: ['running', '.hero'], ax: 0.85, ay: 0.95 },
        { t: TL.dbl_click - 0.2, target: ['running', '.hero .mono'], ax: 0.4, ay: 0.5 },
        { t: TL.term_in, target: ['running', '.hero .mono'], ax: 0.4, ay: 0.5 },
      ] },
    { t0: TL.review_pan + 0.7, t1: TL.copy_click + 0.45,
      path: [
        { t: TL.review_pan + 0.7, target: ['review', '.hero2'], ax: 0.8, ay: 0.75 },
        { t: TL.copy_click - 0.2, target: ['review', '.hero2 .btn.copyfinal'], ax: 0.5, ay: 0.55 },
        { t: TL.copy_click + 0.45, target: ['review', '.hero2 .btn.copyfinal'], ax: 0.5, ay: 0.55 },
      ] },
  ],
  clicks: [
    { t: TL.approve_click, target: ['initial', '.hero .btn.approve'] },
    { t: TL.dbl_click, target: ['running', '.hero .mono'] },
    { t: TL.dbl_click + 0.16, target: ['running', '.hero .mono'] },
    { t: TL.copy_click, target: ['review', '.hero2 .btn.copyfinal'] },
  ],
};

// ---- extraction scene content (S1) — hero card R-101's real fictional
// sources plus surrounding noise fragments from the same fictional world
const EXTRACT = {
  frags: [
    // [text, kind zh, source-kind, key phrase to highlight, related]
    { who: '周会转写 00:14:03', text: '……预算的事下周再说……', kind: 'audio', rel: false },
    { who: '周会转写 00:17:22', text: '能不能加个按钮，一键把 leaderboard 导出成报告发出去',
      kind: 'audio', hl: '一键把 leaderboard 导出成报告', rel: true },
    { who: '屏幕 OCR · example-bench', text: 'Leaderboard — Run #841 · pass@1 62.4%', kind: 'screen', rel: false },
    { who: '屏幕 OCR · 浏览器', text: 'inkweld-demo · README.md', kind: 'screen', rel: false },
    { who: 'slack · alex.doe', text: '上周说的导出报告那个还做吗？周会又有人问了',
      kind: 'slack', hl: '导出报告', rel: true },
    { who: '周会转写 00:31:47', text: '……demo 环境等 green-sign……', kind: 'audio', rel: false },
  ],
  card_title: 'leaderboard 一键导出评测报告',
  chips: ['meeting', 'slack', '重复 ×2'],
};

// ---- quick capture typing (S3)
const QC = {
  text: '统一 example-bench 和 inkweld 的 lint 配置',
  type_t0: TL.qc_start + 0.7,
  type_t1: TL.qc_start + 2.0,
  enter_t: TL.qc_start + 2.15,   // card pops right after
};

// ---- terminal overlay (S5) — fictional agent session
const TERM = {
  title: 'ghostty — claude attach b1e4d7a2',
  lines: [
    ['$ claude attach b1e4d7a2', 0.0],
    ['✔ attached — agent "export leaderboard report"', 0.35],
    ['● Read src/dashboard/Toolbar.tsx', 0.75],
    ['● Edit Toolbar.tsx  +34 -2  (ExportButton)', 1.25],
    ['● Bash npm test -- --filter export … 12 passed', 1.8],
    ['› opening draft PR #42 — waiting for CI', 2.35],
  ],
};

// ---- merge-review vignette (S6) — mirrors the app's purple verdict card
const MERGE = {
  primary: { title: 'example-bench: leaderboard 一键导出评测报告', tag: '主卡 · R-101' },
  secondary: { title: '评测报告能不能一键导出？', tag: '副卡 · 来自 slack' },
  verdict: 'AI 建议合并：副卡并入主卡',
  confidence: '置信度：高',
  accept: '接受',
  dismiss: '取消',
  select_t: TL.term_end + 0.4,
  verdict_t: TL.term_end + 1.3,
  accept_t: TL.merge_end - 1.2,
};

// ---- phone loop (S9)
const PHONE = {
  slack_self: 'Slack · 发给自己',
  photo_caption: '白板 · inkweld demo 方案',
  card_title: 'inkweld: 搭对外可访问的 demo 环境 + 种子数据',
  processing: 'AI 研究中…',
  ios_header: 'Zelin\'s AI Assistant · iOS（beta）',
  ios_lane: '提案 · proposals',
  approve: '✓ 批准',
  approved: '已批准 ✓',
  photo_t: TL.done_end + 0.5,
  card_t: TL.done_end + 1.5,
  tap_t: TL.phone_switch + 1.1,
};

// feature grid montage — breadth features NOT already staged as scenes
const GRID_ITEMS = [
  ['🖱️', '拖拽文字即捕获', 'drag text onto the menu bar'],
  ['📥', '导入已有 Claude Code 会话', 'import running sessions'],
  ['💬', 'Ask 问答（带引用）', 'ask about your own data'],
  ['🌐', '本地 web 看板', 'local web dashboard'],
  ['🩺', '一键修复', 'one-click fix-it'],
  ['🔁', 'agent 卡住自动恢复', 'auto-resume'],
  ['🔐', 'E2E 云同步（可选）', 'optional E2E sync'],
  ['🐧', 'Linux / Windows（beta）', 'headless pipeline installs'],
  ['🛡️', '质量门', 'quality gate'],
  ['🌿', '隔离 git worktree', 'isolated worktrees'],
  ['📊', '每周 digest', 'weekly digest'],
  ['🌗', '中英一键切换', 'bilingual UI'],
];

const TEXTS = {
  title_big: "Zelin's AI Assistant",
  title_sub_cn: '你只做两件事：批准、验收',
  title_sub_en: 'A personal AI chief-of-staff for macOS',
  grid_cn: 'local-first，数据留在你的 Mac',
  grid_en: 'Local-first — your data stays on your Mac',
  end_url: 'github.com/Wan-ZL/zelin-ai-assistant',
  end_tag: 'macOS 14+ · source available · FSL-1.1-MIT',
};
