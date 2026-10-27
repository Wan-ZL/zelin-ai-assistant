# Demo mode (no real data needed)

**Demo 模式（无需真实数据）** —— 不需要 API key、screenpipe 或 Obsidian，就能把会议纪要页、设置页、
关于页完整跑起来；这也是贡献者预览界面改动的推荐方式（见 `CONTRIBUTING.md`）。

`scripts/demo_seed.py` 往一个临时的 `AIASSISTANT_HOME` 写一份**完全虚构**的 `state/recap/`
（inkweld / example-bench / alex.doe / sam.rivera / jordan.lee……全是编的，绝无真实人物或组织数据）。
看板 server（`python3 -m server`）照常经 `GET /api/recaps` 读它，所以页面上看到的就是真 app 的渲染。
1.x 的 seeder 写的是退役看板的 `state/dashboard.json` 卡片数据，全文在 git tag `full`。

## 一条命令

```bash
bash scripts/dev-preview.sh              # 场景 initial；缺 web/dist 会先 npm install && npm run build（需 Node LTS）
bash scripts/dev-preview.sh generating   # 另一个场景
bash scripts/dev-preview.sh --real       # 不种 demo，读 home 指针指向的真实数据
```

demo 模式永远用临时目录（默认 `/tmp/zai-demo`，`ZAI_DEMO_HOME` 可改），写上「向导已完成」标记，然后起
server 并打开浏览器。**绝不触碰真实的 `state/`。**

## 场景

| `--scene` | 页面上有什么 |
|---|---|
| `initial`（缺省） | 8 份已出稿的纪要：五行形与可发送长版（分节、逐条标签）两种形状；质量 ok / 需复核（带结构化问题）/ 转写太短 / 没录到音频；一份重新生成过（有上一版、有转写原版）；一场进行中的会；已归档、已忽略各一份 |
| `generating` | 同 `initial`，外加一个正在进行的「重新生成」请求（§63.8 回执 = 生成中） |
| `empty` | 什么都没有：第一次装好时的空页面 |

## 直接用 seeder

```bash
python3 scripts/demo_seed.py /tmp/assistant-demo                          # 写 initial
python3 scripts/demo_seed.py /tmp/assistant-demo --scene empty
python3 scripts/demo_seed.py /tmp/assistant-demo --now 2026-09-02T12:00:00Z   # 固定时钟，逐字节可复现
python3 scripts/demo_seed.py /tmp/assistant-demo --check                  # 校验形状 + 走一遍 GET /api/recaps 的投影
AIASSISTANT_HOME=/tmp/assistant-demo HOME=/tmp/assistant-demo ZAI_PORT=47821 python3 -m server
```

`--check` 退出码非 0 = 有一份纪要形状不对，或它没出现在 `GET /api/recaps` 里。每份纪要都同时带中英两种语言，
界面语言是看的人自己的选择，所以 seeder 没有语言开关。可粘贴的 `copy_*` 正文与「需复核」那份的问题列表由
`act/lib/recap_text` 真渲染、真校验得出，数据不会和 daemon 的产出漂移（判例 `tests/test_demo_seed.py`、
`tests/test_demo_seed_golden.py`）。

## 谁在用它

- web 视觉基线与 e2e（`web/e2e/demoServer.ts`，场景 `initial`）；
- `scripts/qa/readme_audit.py --render`（经 `scripts/qa/readme_render.mjs`）；
- README 截图（`scripts/qa/readme_shots.mjs`：固定时钟 `--now`，写 `docs/images/*.png`）；
- `scripts/dev-preview.sh`；
- server 测试的公共夹具（`tests/test_server_common.seed_scene`）。

README 截图只许真渲染（CONTRACT §77.5）：从 demo server 拍，绝不手绘、绝不用生成模型画界面。重拍：

```bash
(cd web && npm ci && npx playwright install chromium && npm run build)
node scripts/qa/readme_shots.mjs          # 覆盖 docs/images/ 下 README 引用的几张 PNG
```

拍之前脚本只做一件改写：把拍图机器上的路径（本 checkout、临时 home、运行者的 `~`）换成一台普通安装上的
`~/Projects/zelin-ai-assistant` 与 `~`，免得 README 带上拍图那台机器的用户名与临时目录；界面本身不动。
