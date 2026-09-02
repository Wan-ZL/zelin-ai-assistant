"""看板列目录（lane catalog）——列说明文案的 server-owned 单源。

``GET /api/lanes`` 返回看板各列的「?」说明（原生看板 SectionHeader 的 help
气泡 / hover tooltip 文案）。web 看板只逐字镜像这里的字符串，按当前 UI 语言
取 ``zh`` / ``en`` 键——client 端不再内联第二份列说明（防腐十条 #10：文案进
server-owned catalog，禁第二套双语机制）。

文案来源：``shared/Sources/Lanes.swift`` 的 ``LaneHelp``（backlog / proposals /
running / review / done 的 macOS 分支）与 ``mac/Sources/Cards.swift``
``ArchiveSectionView.helpCopy``（永久性完成条）——原生 app 是行为与文案规格
（D3：退役前冻结）；这里是它们的 server 侧落点，改文案只改这一处。

契约：docs/CONTRACT.md §49（路由表）、§54（web 看板 parity 清单）。
"""
from __future__ import annotations

# slug = dashboard.json 分区名（§2），也是 web 列组件取 help 的 key；顺序 =
# 看板从左到右的显示顺序（潜在任务 | 提案 | 运行中 | 待验收 | 阶段性完成 |
# 永久性完成）。回收站是独立页面不是列，不在此表。
LANES: tuple = (
    {
        "slug": "debt",
        "help": {
            "zh": "真实但不着急的事都先停在这里：雷达低置信度捕获、导入的旧会话、你暂缓的提案。"
                  "不会自动执行、永不过期；再次提起会自动合并计数。点「研究并提议」升级成提案。",
            "en": "Real but not-urgent asks park here — low-confidence radar captures, imported "
                  "sessions, proposals you deferred. Nothing runs on its own and nothing expires; "
                  "restatements merge in automatically. Press \"Research & propose\" to promote one.",
        },
    },
    {
        "slug": "needs_approval",
        "help": {
            "zh": "需要你现在拍板的卡：AI 已附上计划、成本和验收标准。批准=后台开始执行；"
                  "修改=补充方向重提；暂缓=先不做，放进潜在任务。灰色卡是 AI 正在研究的占位。",
            "en": "Cards that need your decision now, each with a plan, cost, and acceptance "
                  "criteria. Approve = start executing; Comment = redo with your input; Later = not "
                  "now, parks it in Backlog. Grey cards are placeholders the AI is still researching.",
        },
    },
    {
        "slug": "running",
        "help": {
            "zh": "已批准的任务由 AI 在后台执行（排队中显示灰卡）。橙色「需输入」= AI 卡住等你回答，排在最前。",
            "en": "Approved tasks the AI is executing in the background (queued ones show grey). "
                  "Orange \"Needs input\" = the AI is blocked on your answer; those sort first.",
        },
    },
    {
        "slug": "review",
        "help": {
            "zh": "AI 认为做完了：看交付摘要或 draft PR。验收=进入「阶段性完成」；打回=带你的反馈继续改。",
            "en": "The AI thinks it's done — check the delivery summary or draft PR. Accept moves it "
                  "to Done for now; Send back continues with your feedback.",
        },
    },
    {
        "slug": "completed",
        "help": {
            "zh": "本轮完成——可能还在等对方反馈，可随时退回待验收；确认彻底结束就点「永久完成」。"
                  "徽章数字是真实总数，列表只显示最近 50 条。",
            "en": "Done for this round — it may still be waiting on someone's reply, and can go back "
                  "to Review any time; when it's truly over, press \"Done for good\". The badge shows "
                  "the true total; the list keeps the latest 50.",
        },
    },
    {
        "slug": "archived",
        "help": {
            "zh": "彻底结束、封存的线程（你点的永久完成 + 自动封存的冷交付）。封存=不再参与匹配，"
                  "后续相关信息会开新卡而不是回锅这张。可随时「放回看板」回到原状态列。",
            "en": "Threads that are truly over — ones you marked done for good, plus auto-sealed cold "
                  "deliveries. Sealed = excluded from matching, so later mentions open a fresh card "
                  "instead of re-raising this one. Press \"Put back\" any time to return one to its "
                  "previous lane.",
        },
    },
)


def catalog() -> dict:
    """``GET /api/lanes`` 的响应体：``{"lanes": [{slug, help:{zh,en}}, …]}``（每次
    返回新 dict，调用方改不到模块常量）。"""
    return {"lanes": [{"slug": lane["slug"], "help": dict(lane["help"])} for lane in LANES]}
