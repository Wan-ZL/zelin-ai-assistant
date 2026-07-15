# Demo mode (no real data needed) & recording guide

**Demo 模式(无需真实数据)与录屏指南** —— 不需要 API key、screenpipe 或 Obsidian,
就能跑起完整 UI;这也是贡献者预览界面改动的推荐方式(见 `CONTRIBUTING.md`)。

`scripts/demo_seed.py` 生成一份**完全虚构**的 `state/dashboard.json`（example-bench /
inkweld / alex.doe / sam.rivera……全是编的，绝无真实人物或组织数据），让 app 指着一个
假的 `AIASSISTANT_HOME` 跑，用于 README 截图和 demo 视频。五种卡片类型 + 边缘状态
（queued 灰卡、dispatch 报错、blocked、chat 交付的 final draft、回收站）全部可见。

## 快速开始

```bash
# 1. 生成 demo 数据（目录不存在会自动创建）
python3 scripts/demo_seed.py /tmp/assistant-demo

# 2. 构建并安装 app（如尚未安装）
./mac/build.sh --install

# 3. 退出正在跑的正式实例（避免两个菜单栏图标打架）
pkill -x ZelinAIEngineer || true

# 4. 启动 app 指向 demo 目录
#    ⚠️ 必须直接跑 bundle 里的二进制 —— `open` 不会把环境变量传给 app 进程，
#    AIASSISTANT_HOME 会静默丢失、app 读到的还是 ~/Projects/zelin-ai-assistant。
AIASSISTANT_HOME=/tmp/assistant-demo "/Applications/Zelin's AI Assistant.app/Contents/MacOS/ZelinAIEngineer"
```

app 每 5s 重读 dashboard.json，重跑 seeder 后最多等 5s 界面就换过来。

两个注意点：

- **新鲜度警告**：`generated_at` 超过 90s，看板 header 会出现橙色「actd 可能未运行」。
  每次截图/开拍前重跑一次 seeder；长时间录屏可以开个循环保鲜：
  `while true; do python3 scripts/demo_seed.py /tmp/assistant-demo >/dev/null; sleep 60; done`
- demo 期间点 ✅/❌ 只会往 `/tmp/assistant-demo/state/inbox/` 写文件（没有 actd 在读），
  无害；卡片不会真的动——视频里卡片的流动靠 `--scene` 换数据（见下）。

## 校验模式

```bash
python3 scripts/demo_seed.py /tmp/assistant-demo --check
```

只校验现有文件不写入：counts 与各区数组一致、sources 四字段全为字符串（Swift 侧
`Source` 非可选，一个 null 整列卡片就没了）、epoch 字段是 int、queued 项不带
session_id 等。写入模式跑完也会自动做同一套校验。

## `--scene`：为视频分步生成流水线时刻

主角卡 **R-101**（example-bench 一键导出评测报告）在六个 scene 里走完整个流水线，
其余卡片保持不动，视频里就是"一张卡在动"：

| scene | R-101 所在位置 |
|---|---|
| `captured` | 提案——「AI 研究中」占位卡（会议录音刚被 radar 捕获，完整提案生成前） |
| `initial`（默认） | 提案（T1 卡，满配：sources/plan/DoD/成本/截止） |
| `approved` | 运行中——灰色 queued 卡（已批准、待派发） |
| `running` | 运行中——working，40 秒前启动，带 `claude attach` 命令 |
| `review` | 待验收——delivered_summary 提到 draft PR #42，30 秒前进入 |
| `done` | 已完成——10 秒前验收归档 |

```bash
python3 scripts/demo_seed.py /tmp/assistant-demo --scene approved
```

## 截图 shot list（README 用）

1. **Popover**：点菜单栏图标——提案徽章数、快速捕获输入框、卡片折叠态一屏全有
   （scene `initial`）。
2. **看板主窗口**：菜单栏右键 →「打开主窗口」→ 看板。五列
   提案 / 运行中 / 待验收 / 备选 / 已验收 全部非空（scene `initial`）。
3. **T2 卡展开**：R-102（inkweld demo 环境）点「展开详情 ▸」——$85 成本、
   需文字确认、disagreement、重复×3、🟢 新建 repo 一行全在。
4. **待验收 + final draft**：R-110（周报）——chat 交付，展开可见完整双语周报草稿；
   旁边 R-109 是 repo 交付对照（draft PR 回执 + DoD checklist + 耗时）。
5. **回收站**：展开 trash 区——R-116 被拒建议，「恢复 / 永久保存」按钮 + 搜索框。

## 30 秒 demo 视频 storyboard

镜头始终对着看板主窗口（或 popover，二选一保持不切）。每步先跑命令，等 ≤5s 刷新。

| 时间 | 命令 | 画面 |
|---|---|---|
| 0–6s | `--scene initial` | 全景：五列都有货。镜头推向提案列的 R-101。 |
| 6–11s | 点 R-101 的 ✅，随即 `--scene approved` | 卡片从提案列消失，运行中列顶出现灰色 queued 卡——"批准即入队"。 |
| 11–16s | `--scene running` | 灰卡变 working：转起来的状态、`claude attach` 一键复制。 |
| 16–24s | `--scene review` | 卡片跳到待验收：draft PR #42 回执 + DoD checklist，鼠标划过「验收 ✓」。 |
| 24–30s | 点验收，随即 `--scene done` | 卡片落入完成列（刚刚验收）。拉远回全景，定格。 |

录完记得把 `/tmp/assistant-demo` 删掉，重启正式 app 即可回到真实数据。
