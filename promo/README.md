# promo/ — 宣传视频制作管线

一条命令重录 README/X 用的宣传片（≤60s，1080p 横版 + 竖版备选）：

```bash
bash promo/make.sh
# 产物: ~/Downloads/zelin-ai-assistant-promo.mp4 (+ -vertical.mp4)
```

分镜与风格说明见 [storyboard.md](storyboard.md)。

## 原理

不录屏、不碰真实数据。`stage/` 是一个用 HTML/CSS 按 `docs/assets/kanban.png`
1:1 还原的深色看板，数据来自 `scripts/demo_seed.py` 的六个虚构 scene
（`captured → initial → approved → running → review → done`，主角卡 R-101 走完
「会议录音 → 提案 → 批准 → agent 开工 → draft PR → 验收」全流程）。
`stage/stage.js` 暴露确定性的 `window.seek(t)`，`render.mjs` 逐帧截图，
`compose.sh` 用 ffmpeg 合成并对齐配乐节奏点。

| 步骤 | 脚本 | 说明 |
|---|---|---|
| 1 | `prepare.sh` | demo_seed 生成 scene JSON → `build/scenes.js`；从 `mac/AppIcon.icns` 提取图标 |
| 2 | `render.mjs` | playwright-core + 本机 Chromium 逐帧截图（30fps；`--vertical` 出竖版） |
| 3 | `compose.sh` | ffmpeg：帧序列 + 配乐（自动下载）→ mp4 到 `~/Downloads` |

依赖：`python3`、`ffmpeg`、`node`（首次会 `npm install playwright-core`）、
本机任一 Chromium/Chrome（自动探测，或设 `PROMO_CHROME`）。

调试：浏览器直接打开 `stage/index.html?play=1` 实时预览；
`node render.mjs --fps 5` 快速低帧率出片验证时间轴。

## 配乐授权

"Voxel Revolution" — Kevin MacLeod (incompetech.com)，
Creative Commons **CC BY 4.0**（https://creativecommons.org/licenses/by/4.0/）。
发布视频时在帖子或简介里保留一行署名：

> Music: "Voxel Revolution" Kevin MacLeod (incompetech.com), CC BY 4.0

`build/` 与 `node_modules/` 为生成物，不入库。
