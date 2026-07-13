// Promo video timeline — ALL timing, captions and camera targets live here.
// Cut times sit on the music's accent grid so every scene change lands on a
// beat. Grid measured by promo/beatgrid.py on "Voxel Revolution" (Kevin
// MacLeod): strong accents every 1.4769 s starting at 0.255 s.
//
// Pacing: scenes run ~1.25x tighter than a first-draft cut would suggest —
// cuts land on EVERY accent (single pulse) rather than every other one, so
// the film keeps its beat sync while feeling faster. Music is untouched.
//
// To retime after swapping the track: rerun beatgrid.py, update PULSE/OFFSET,
// keep cuts on B(n) multiples.

const OFFSET = 0.255;        // first accent of the track (s)
const PULSE = 1.4769;        // one strong accent of the 81.25 BPM grid (s)
const B = (n) => OFFSET + n * PULSE;

const TL = {
  duration: 43.1,            // total video length (s) — hard cap 60

  // scene boundaries (all on the beat grid)
  title_end: B(3),           //  4.69  title card out
  rec_end: B(6),             //  9.12  recording scene out, board in
  captured_end: B(8),        // 12.07  placeholder -> full proposal
  approve_click: B(11) - 0.30,
  initial_end: B(11),        // 16.50  cut: proposal approved
  queued_end: B(12),         // 17.98  queued -> working
  working_end: B(15),        // 22.41  cut to review lane
  accept_click: B(19) - 0.30,
  review_end: B(19),         // 28.32  cut: accepted, wide shot
  done_end: B(21),           // 31.27  board out, feature grid in
  grid_end: B(25),           // 37.18  grid out, end card in

  fade_out: 42.3,            // video fade to black
};

// which demo_seed scene pane is visible from a given time
// fade > 0 crossfades from the previous pane; 0 is a hard cut
const PANE_CUES = [
  { t: TL.rec_end, pane: 'captured', fade: 0.4 },
  { t: TL.captured_end, pane: 'initial', fade: 0.35 },
  { t: TL.initial_end, pane: 'approved', fade: 0 },
  { t: TL.queued_end, pane: 'running', fade: 0 },
  { t: TL.working_end, pane: 'review', fade: 0 },
  { t: TL.review_end, pane: 'done', fade: 0 },
];

// camera keyframes: target = [pane, selector] measured at load, pad in board
// px. cut:true jumps, otherwise eased move from the previous keyframe.
const CAM_CUES = [
  { t: TL.rec_end, target: ['captured', '.hero'], pad: 260, cut: true },
  { t: TL.captured_end, target: ['captured', '.hero'], pad: 150 },       // slow push-in
  { t: TL.captured_end + 0.6, target: ['initial', '.hero'], pad: 90 },   // fit full card
  { t: TL.initial_end, target: ['initial', '.hero'], pad: 45 },          // keep pushing
  { t: TL.initial_end, target: ['approved', '.hero'], pad: 300, cut: true },
  { t: TL.queued_end, target: ['approved', '.hero'], pad: 220 },
  { t: TL.queued_end, target: ['running', '.hero'], pad: 200, cut: true },
  { t: TL.working_end, target: ['running', '.hero'], pad: 120 },
  { t: TL.working_end, target: ['review', '.hero'], pad: 220, cut: true },
  { t: TL.review_end, target: ['review', '.hero'], pad: 110 },
  { t: TL.review_end, target: ['done', 'window'], pad: -60, cut: true }, // wide, slightly over
  { t: TL.done_end, target: ['done', 'window'], pad: 10 },               // settle out
];

// lower-third captions (word-staggered in, dropped out before the next cut)
const CAPTIONS = [
  { t0: TL.title_end + 0.3, t1: TL.rec_end - 0.35,
    cn: '开会录音、录屏，全部本地捕获',
    en: 'Meetings and screen, captured locally' },
  { t0: TL.rec_end + 0.25, t1: TL.captured_end - 0.3,
    cn: 'radar 检测到需求，AI 研究中',
    en: 'Radar picks it up — AI starts researching' },
  { t0: TL.captured_end + 0.35, t1: TL.initial_end - 0.3,
    cn: '自动变成提案：计划、验收标准、成本',
    en: 'It becomes a proposal: plan, DoD, cost' },
  { t0: TL.initial_end + 0.25, t1: TL.queued_end - 0.3,
    cn: '一键批准',
    en: 'One click to approve' },
  { t0: TL.queued_end + 0.25, t1: TL.working_end - 0.3,
    cn: '后台 Claude agent 在独立 worktree 开工',
    en: 'A background Claude agent gets to work' },
  { t0: TL.working_end + 0.25, t1: TL.review_end - 0.3,
    cn: '交付 draft PR + 验收清单，不碰 main',
    en: 'Delivered as a draft PR — main stays untouched' },
  { t0: TL.review_end + 0.25, t1: TL.done_end - 0.3,
    cn: '验收，归档',
    en: 'Accept. Done.' },
];

// cursor: waypoints in [pane, selector] space; clicks trigger ripples
const CURSOR_CUES = {
  show: [
    { t0: TL.initial_end - 1.8, t1: TL.initial_end,
      path: [
        { t: TL.initial_end - 1.8, target: ['initial', '.hero'], ax: 0.95, ay: 0.9 },
        { t: TL.approve_click - 0.25, target: ['initial', '.hero .btn.approve'], ax: 0.55, ay: 0.55 },
        { t: TL.initial_end, target: ['initial', '.hero .btn.approve'], ax: 0.55, ay: 0.55 },
      ] },
    { t0: TL.review_end - 1.8, t1: TL.review_end,
      path: [
        { t: TL.review_end - 1.8, target: ['review', '.hero'], ax: 0.9, ay: 1.05 },
        { t: TL.accept_click - 0.25, target: ['review', '.hero .btn.accept'], ax: 0.5, ay: 0.55 },
        { t: TL.review_end, target: ['review', '.hero .btn.accept'], ax: 0.5, ay: 0.55 },
      ] },
  ],
  clicks: [
    { t: TL.approve_click, target: ['initial', '.hero .btn.approve'] },
    { t: TL.accept_click, target: ['review', '.hero .btn.accept'] },
  ],
};

// recording scene content — the quote is demo_seed R-101's meeting source
const REC = {
  who: 'manager · 周会录音',
  quote: '能不能加个按钮，一键把 leaderboard 导出成报告发出去？',
  type_t0: TL.title_end + 0.5,
  type_t1: TL.title_end + 2.6,
  radar_t: TL.title_end + 3.0,
};

// feature grid montage — every tile is a real, shipped feature (v0.29)
const GRID_ITEMS = [
  ['🖥️', '录屏捕获', 'screen capture'],
  ['🎙️', '会议录音', 'meeting audio'],
  ['📡', '三路 radar', 'Obsidian · Slack · Gmail'],
  ['🔁', '跨渠道去重', 'dedup across sources'],
  ['💰', '批准前看成本', 'cost before you approve'],
  ['⚡', '一句话快速捕获', 'quick capture'],
  ['🌿', '隔离 git worktree', 'isolated worktrees'],
  ['🛡️', '质量门', 'quality gate'],
  ['🔀', '只交 draft PR', 'draft-PR-only delivery'],
  ['📋', '写作任务出成稿', 'paste-ready drafts'],
  ['📱', 'iOS companion（beta）', '手机上批准'],
  ['🐧', 'Linux / Windows（beta）', 'headless pipeline 可装'],
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
