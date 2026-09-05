#!/usr/bin/env python3
"""原生 Mac app 的用户可见面清单 —— 机器提取，写进 ui/parity/native-inventory.json。

法典：docs/CONTRACT.md §66（UI 对齐契约）。mac/Sources 在 D3 下冻结、只读，
所以这份 JSON 是退役中 app 的**终版规格**：web 看板补齐的不是「看着差不多」，
是清单里每一条 id（owner 2026-09-02：「要通过一些硬指标、硬代码、硬文档来
进行保证」）。提取范围：
  - rail        —— 主窗口左侧图标栏（MainSection enum：顺序 / 双语标题 / SF 图标 / ⌘n）
  - screens     —— 页面与面板：rail 页、Settings 各 section（SettingsSectionDescriptor
                   注册表）、独立窗口 / sheet（权限体检、初始设置向导、诊断条…）
  - controls    —— 每个 L("zh","en") 双语字面量按调用链归类（toggle / button /
                   picker option / textfield / menu-item / alert-button / label / copy / help）
                   + 所属 screen + Swift file:line
  - settings_keys —— settings_overrides.json 键（含 features.* / telemetry.* 嵌套）
                   与 UserDefaults 纯界面偏好键
  - lanes       —— 看板列顺序 / 双语列名 / 左右两根可折叠书立条 / 卡面按钮
  - shortcuts   —— .keyboardShortcut + NSMenuItem keyEquivalent
  - notifications —— 通知 kind 词表
  - theme / layout —— 默认主题（owner 拍板 light）与列宽等布局常量的指针
                   （数值真源 = scripts/ui/extract_native_tokens.py → ui/tokens/）

输出确定性：键排序 + 条目按 (screen, source) 排序 + 同 id 递增 #n 后缀——
重跑零 diff 由 tests/test_ui_native_inventory_fresh.py 钉死。唯一手写的部分是
`FILE_SCREEN` / `TYPE_SCREEN` / `MEMBER_SCREEN` / `VIA_SCREEN` / `FUNCTION_SCREEN` 五张归属表、
`SCREEN_OWNER`（谁负责补齐：web / shell / os / retired）、prefs 键的 `PREF_OWNER`
（shell / server / retired + 理由）、单条 control 的 `CONTROL_OWNER`（retired + 理由：
非界面文案 / 新架构无落点的句子）与 rail 项的 `RAIL_OWNER`（owner 决策拿掉的侧栏项：
retired + 理由；`rail:order` 只数剩下的）——表本身也进 JSON（`attribution`）。owner=shell 的
条目原则上只列不判；例外是带 `probe` 的条目（通知句 / kind → notify_catalog，
壳持有的偏好键 → shell_source，搬到 server 的偏好键 → server_source），§66.2 追记。

用法：
    python3 scripts/ui/extract_native_inventory.py --write    # 重铸 JSON
    python3 scripts/ui/extract_native_inventory.py --check    # 与已提交版本比对（0 = 无 diff）
    python3 scripts/ui/extract_native_inventory.py --stats    # 各类计数
"""

import argparse
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui_common as uc  # noqa: E402

# --------------------------------------------------------------------------- #
# 归属表（唯一手写部分；进 JSON 的 attribution 节，改表 = 改规格，PR 可见）
# --------------------------------------------------------------------------- #

# 文件 → 默认 screen
FILE_SCREEN = {
    "AppDelegate.swift": "app",
    "Ask.swift": "ask",
    "BoardMotion.swift": "board",
    "CaptionOverlay.swift": "captions",
    "Cards.swift": "board.card",
    "Composer.swift": "board.composer",
    "DashboardView.swift": "header.recording",
    "Diagnostics.swift": "board.diagnostics",
    "Doctor.swift": "doctor",
    "Freshness.swift": "header.freshness",
    "Kanban.swift": "board",
    "LiveCaptions.swift": "captions",
    "MainWindow.swift": "window",
    "NotifyRelay.swift": "notifications",
    "Onboarding.swift": "onboarding",
    "Pages.swift": "pages",
    "PastedImages.swift": "board.composer",
    "Permissions.swift": "permissions",
    "Recording.swift": "header.recording",
    "Settings.swift": "settings",
    "SetupWizard.swift": "setup_wizard",
    "Store.swift": "board.notices",
    "Utils.swift": "shared",
}

# 顶层类型 → screen（覆盖文件默认）
TYPE_SCREEN = {
    "MainSection": "rail",
    "TrashPageView": "trash",
    "ArchivePageView": "archive",
    "SectionHeader": "board.lane",
    "ApprovalCardView": "board.needs_approval",
    "PendingEchoRow": "board.needs_approval",
    "RunCapturePendingRow": "board.running",
    "TaskRow": "board.running",
    "ReviewRow": "board.review",
    "DebtRow": "board.debt",
    "MergeSuggestionCard": "board.merge",
    "ForceMergeSheet": "board.merge",
    "TrashSectionView": "trash",
    "TrashRow": "trash",
    "ArchiveSectionView": "board.archived",
    "ArchiveRow": "board.archived",
    "ArchiveLaneContent": "board.archived",
    "ProposalsTriageButton": "board.needs_approval",
    "NoticeRow": "board.notices",
    "DepsView": "deps",
    "DepsModel": "deps",
    "DepAction": "deps",
    "IngestView": "ingest",
    "IngestModel": "ingest",
    "EngineDiagnosisRow": "ingest",
    "AboutView": "about",
    "UpdateCheckModel": "about",
    "HelloBubbleView": "onboarding.hello_bubble",   # 「我在这里 👆」指向菜单栏图标——D3 退役
    "CredentialRowView": "settings.credentials",
}

# (类型, 成员) → screen（AppDelegate 一个 class 装了菜单、对话框、首启气泡）
MEMBER_SCREEN = {
    ("AppDelegate", "installMainMenu"): "menu.main",
    ("AppDelegate", "showStatusMenu"): "menu.status",
    ("AppDelegate", "makeMainStatusItem"): "menu.status",
    ("AppDelegate", "updateStatusTitle"): "menu.status",
    ("AppDelegate", "showHelloBubble"): "onboarding",
    ("AppDelegate", "confirmT2"): "board.dialogs",
    ("AppDelegate", "promptFeedback"): "board.dialogs",
    ("AppDelegate", "promptComment"): "board.dialogs",
    ("AppDelegate", "promptRework"): "board.dialogs",
    ("AppDelegate", "promptText"): "board.dialogs",
    ("AppDelegate", "promptAnswer"): "board.dialogs",
    ("AppDelegate", "submitAnswer"): "board.dialogs",
    ("AppDelegate", "alertImagesNotSaved"): "board.dialogs",
    ("AppDelegate", "pastedImagesAccessory"): "board.dialogs",
    ("AppDelegate", "copyCommand"): "board.card",
    ("AppDelegate", "applicationShouldTerminate"): "app",
    # 录制模式回滚句（RecordingController.rollbackNote + 它的 label(forMode:) 词表）：壳 Recording.swift
    # 组句、经 postSystemNotice 直发 + 经桥 `recording.note` 原文推给页面——web 只显示不组句，
    # 归 notifications（owner shell、探针 notify_catalog：server/notify_catalog.py 的 slots 词表 + 壳 L()）。
    ("RecordingController", "rollbackNote"): "notifications",
    ("RecordingController", "label"): "notifications",
    # rollbackNote 体内的 `let cause: String` + switch 被成员扫描器认成最内层成员（mac/ 冻结 → 稳定），
    # 三句 cause 片段由它归属
    ("RecordingController", "cause"): "notifications",
}

# (文件, 顶层自由函数) → screen（第六张归属表）：Cards.swift 的 fileprivate 词表函数默认
# 归文件 screen board.card，但 trashReasonLabel（你拒绝的 / 你删除的）只被 TrashRow 调用——
# 它是回收站页的词，web 也只在回收站页渲染它（§68.11 / TrashPage）。
FUNCTION_SCREEN = {
    ("Cards.swift", "trashReasonLabel"): "trash",
}

# 单条 control id → 归属（第七张归属表；改表 = 改规格，PR 可见）。同一 screen 里个别 L()
# 不是界面文案、或其机制在新架构里没有落点时，在这里点名 retired 并写一行理由（进 JSON
# attribution.control_owner；只列不判）。§66.2 末句「新的不搬判断走归属表」的单条版——
# 不进 waivers.txt（那本账只许缩）。
CONTROL_OWNER = {
    "control:header.freshness:button:board-health-banner-background-service-down-one": {
        "owner": "retired",
        "reason": "AIFix.launch(context:) 的 prompt 上下文字串，从不渲染；web 的「让 AI 修」上下文由 server 从 doctor 报告推导（§68.4 / §54.4）",
    },
    "control:doctor:label:failed-to-write-dest": {
        "owner": "retired",
        "reason": "原生 app 自己渲 plist 写 ~/Library/LaunchAgents 的失败句；server 永不写 plist（§68.8：修复 = launchctl kickstart，未加载 → 409 指向 install.sh）",
    },
    "control:doctor:label:launchctl-load-failed": {
        "owner": "retired",
        "reason": "原生 app 自己 launchctl load 的失败句；server 永不 load plist（§68.8 同上，install.sh --reinstall-agent 是唯一装载路径 §48.7）",
    },
    "control:setup_wizard:label:failed-to-write-dest": {
        "owner": "retired",
        "reason": "向导末步原生自渲 plist 的失败句；web 向导「启动后台服务」= POST /api/repair/actd（§68.5 ⑦），server 不写 plist",
    },
    # fix/parity-r2-settings-header（settings 面）：原生 Gmail IMAP 探针是壳起 runtime python 子进程；web 的探针在
    # server 进程内跑（§68.3 secrets_store._probe_gmail），没有「找不到解释器」这一失败态。
    "control:settings:label:no-usable-python": {
        "owner": "retired",
        "reason": "Gmail IMAP 探针在 server 进程内执行，无 runtime python 子进程可失败（§68.3）",
    },
    # fix/parity-secret-row-save-path（settings.credentials 面）：CredentialRowView 的状态章 `kind == .plain ?
    # 「已保存（App 内管理）」: 「已保存（未验证）」`——原生四个实例（anthropic / gmail / volcanoSpeech / volcanoArk）
    # 没有一个是 .plain，这句从不渲染；web 五行都有 server 探针或壳的「检测」，同样恒走「已保存（未验证）」。
    # 此前 web 把它错渲在字幕两把 key 上（parity 审计 gap recording-captions-volcano-row-badge-and-save-note）。
    "control:settings.credentials:label:saved-managed-in-app": {
        "owner": "retired",
        "reason": "原生 Kind.plain 分支无任何行实例化、从不渲染；web 五行皆可验证 / 可检测 → 恒「已保存（未验证）」（§68.3 2026-09-05 追记）",
    },
    # 原生「新建 skill」表单往 ~/.claude/skills/<name>/SKILL.md 写文件；§67 立法后仓库 = 商店、`skills/` 只有 git 写
    # （防腐 #8）、§67.5 明文「不做编辑器」——新 skill 是一次进 skills/ 的 PR（§65 草稿 PR 通道），不是设置页表单。
    "control:settings.skills:button:new-skill": {
        "owner": "retired",
        "reason": "仓库 = skill 商店，skills/ 只有 git 写；新 skill 走 PR，设置页不做编辑器（§67.1 / §67.5）",
    },
    "control:settings.skills:button:hide-form": {
        "owner": "retired", "reason": "同 new-skill：新建表单不存在（§67.5）",
    },
    "control:settings.skills:textfield:name-kebab-case-e-g-my-skill": {
        "owner": "retired", "reason": "同 new-skill：新建表单不存在（§67.5）",
    },
    "control:settings.skills:textfield:one-line-description-claude-uses-it-to-decide-wh": {
        "owner": "retired", "reason": "同 new-skill：新建表单不存在（§67.5）",
    },
    "control:settings.skills:label:write-failed": {
        "owner": "retired", "reason": "同 new-skill：web 不写 SKILL.md，无写入失败态（§67.5）",
    },
    # D35（owner 2026-09-04 原话「这个我回车我不希望是直接跑而是下一行，要跑是需要点击按钮。」）：原生 Composer.swift 的
    # 键位提示句「↩ 发送 · ⇧↩ 换行 · Esc 退出 · ⌘V 可贴图」描述的是 Return=发送 / Shift+Return=换行——web 列顶输入框自
    # D35 起 Enter=换行、只有按钮提交，这句话在 web 里没有对应机制（copy 本就只列不判；这里点名是把判断落成判例）。
    "control:board.composer:copy:send-newline-esc-dismiss-v-pastes-images": {
        "owner": "retired",
        "reason": "D35 owner 要 Enter=换行、只按钮提交（§41 2026-09-04 追记）；原生「↩ 发送 · ⇧↩ 换行」的键位与提示句不搬",
    },
    # D34（owner 2026-09-04，issue #217）：卡片详情只留一面——「展开详情 ▸」打开右侧详情侧栏，原生 CardSurface 的
    # 就地展开详情槽在 web 退役，卡面永远收起态。「收起 ▾」是就地展开的对偶动词，侧栏的关闭是 × / ⎋ / 背板；
    # 详情槽里的积木（💬 需求来自 / 📋 要做什么 / 怎样算办完 / 日志 / 指令 / 会话 ID …）照判——渲染面换成侧栏。
    "control:board.card:button:collapse": {
        "owner": "retired",
        "reason": "D34 卡片详情只留侧栏一面（§49 追记 2026-09-04 / §54.1 第 2 项 tombstone）：就地展开退役，无「收起 ▾」；侧栏关闭 = × / ⎋",
    },
    "control:board.needs_approval:button:collapse": {
        "owner": "retired",
        "reason": "同 board.card:button:collapse（D34，§49 追记 2026-09-04）：提案卡的就地展开退役，详情在侧栏",
    },
}

# D29（owner 2026-09-04 原话「这个问问助手我希望去掉。」）：问问助手 web 页整页退役——Ask.swift 的 17 条 L() 全部
# retired、只列不判（screen `ask` 同时在 SCREEN_OWNER 标 retired，screen:ask 随之不判）。`act/ask.py` 引擎与
# `state/ask_history.json` 不动：旧 app 仍 shell out 到它，等 P8 一起删（CONTRACT §27 tombstone）。
_ASK_RETIRED_REASON = "D29 owner 去掉问问助手 web 页（§27 tombstone 2026-09-04；act.ask 引擎留给旧 app 到 P8）"
_ASK_CONTROL_IDS = (
    "control:ask:copy:couldn-t-start-the-q-a-helper-launcherror-run-a",
    "control:ask:copy:no-answer-came-back-hit-retry",
    "control:ask:copy:the-ai-didn-t-answer-within-60-s-hit-retry",
    "control:ask:label:ask-the-assistant",
    "control:ask:copy:ask-anything-about-this-product-why-there-are-no",
    "control:ask:textfield:type-a-question-press-return",
    "control:ask:button:ask",
    "control:ask:label:thinking-model-elapsed-s-elapsed-60s-max",
    "control:ask:button:cancel",
    "control:ask:help:helpful-logs-an-anonymous-event-that-uploads-wit",
    "control:ask:help:not-helpful-logs-an-anonymous-event-that-uploads",
    "control:ask:button:retry",
    "control:ask:copy:ask-is-disabled-in-config-yaml-ask-enabled-false",
    "control:ask:copy:the-ai-engine-is-not-connected-connect-it-first",
    "control:ask:button:connect-setup-wizard",
    "control:ask:button:re-detect",
    "control:ask:label:recent-questions",
)
CONTROL_OWNER.update({cid: {"owner": "retired", "reason": _ASK_RETIRED_REASON} for cid in _ASK_CONTROL_IDS})

# rail 项（MainSection 的 case）→ 归属（第八张归属表）：原生八页里被 owner 决策从左侧导航栏拿掉的项。owner=retired、
# 不判、理由进 JSON attribution.rail_owner；`rail:order` 的期望顺序只数仍 gated 的项（parity_check._rail_order_ok）。
#   ask  —— 整页退役（D29），screen:ask 随 SCREEN_OWNER 一起不判；
#   deps —— 页面并入设置页的「依赖检查」区（D30，owner 原话「这个依赖检查我希望合并到 setting里面」）：只有 rail 项退役，
#           screen:deps 与 control:deps.* / doctor.* 照判（渲染面 = 设置页，web/src/parity.test.tsx SCREEN_SURFACE）。
RAIL_OWNER = {
    "ask": {"owner": "retired", "reason": _ASK_RETIRED_REASON},
    "deps": {"owner": "retired",
             "reason": "D30 依赖检查并入设置页一区（§49 / §54.4 追记 2026-09-04）；页面内容仍判，只有侧栏项退役"},
}

# screen 前缀 → 负责补齐的一方。web = 看板必须补（进门）；shell = 原生残留
# （R2.2.3：字幕悬浮窗、系统通知、TCC 引导）；os = macOS 应用菜单惯例；
# retired = 计划明文退役（D3 菜单栏图标；D29 问问助手页）。非 web 的条目只列不判——例外是
# PROBED_SHELL_SCREENS：壳直发的系统通知句按 server-owned 目录判（§66.2 追记）。
SCREEN_OWNER = {
    "captions": "shell",
    "notifications": "shell",
    "menu.main": "shell",
    "menu.status": "retired",
    "app": "shell",
    "settings.menuBar": "retired",
    "onboarding.hello_bubble": "retired",
    "ask": "retired",
}

# 调用者标识 → screen（第四张归属表）：`Self.postSystemNotice(title: L(...))` 是壳
# （RecordingController，Recording.swift 逐字节搬进 shell/）直发的系统通知，不是
# header.recording 的界面文案——归 notifications（owner shell），与 NotifyRelay 的
# 汇总句同一探针（server/notify_catalog.py + shell/Sources 的 L() 对）。
VIA_SCREEN = {
    "Self.postSystemNotice": "notifications",
}

# owner=shell 却要判的 screen：探针 = notify_catalog（§66.2 追记）。
PROBED_SHELL_SCREENS = frozenset({"notifications"})

# UserDefaults 纯界面偏好键 → 归属（第五张归属表；表外的键按前缀规则：字幕 / 录制
# 引擎的键随逐字节搬入的引擎文件归 shell，其余归 web）。
#   shell   —— 壳自己持有同名 UserDefaults 键（探针：shell/Sources 出现 "<key>"）
#   server  —— 概念搬到了 server 侧（launcher / 首启标记都归 server）：探针 = `landing`
#              字面量出现在 server/*.py
#   retired —— 新架构里没有对应概念（D3 退役 / 并入别的键），只列不判，reason 进 JSON
PREF_OWNER = {
    "screenPermissionRequested": {"owner": "shell",
                                  "reason": "TCC 引导归壳（R2.2.3）：ShellSystem.swift PermissionsProbe.request 同名键"},
    "vaultAccessGranted": {"owner": "shell",
                           "reason": "Documents 授权按壳的 bundle id 记账（§68.13）：PermissionsProbe vault 探针同名键"},
    "terminalApp": {"owner": "server", "landing": '"terminal_app"',
                    "reason": "「在终端打开」的执行者是 server（open -a，§68.7）——偏好落 settings_overrides terminal_app"},
    "hasCompletedFirstRun": {"owner": "server", "landing": "setup_done.json",
                             "reason": "首启标记 = server 侧 state/setup_done.json（§68.5）：换壳 / 换浏览器不重问"},
    "showMenuBarIcon": {"owner": "retired",
                        "reason": "D3：壳 Dock-only、菜单栏状态项退役（settings.menuBar 同判）"},
    "recordingConsentShown": {"owner": "retired",
                              "reason": "并入 recordingMode（壳无存值 = 未同意 = off，P0-11）+ setup_done.json；不再有第二把标记"},
}


# 调用链里算「控件」的标识 → role
CONTROL_ROLES = {
    "Toggle": "toggle",
    "Button": "button",
    "Menu": "menu",
    "Link": "link",
    "TextField": "textfield",
    "SecureField": "textfield",
    "TextEditor": "textfield",
    "Picker": "picker",
    "DatePicker": "picker",
    "Slider": "slider",
    "Stepper": "stepper",
    "NSMenuItem": "menu-item",
    "addItem": "menu-item",
    "addButton": "alert-button",
    "confirmationDialog": "dialog",
    "alert": "dialog",
    "Section": "section",
    "Tab": "tab",
}
# 只列不判的 role（说明性文案与 tooltip：web 自然有自己的句子）
INFORMATIONAL_ROLES = frozenset({"copy", "help", "dialog-text"})
# 遇到即停（穿过它就不再是同一个控件）
_BOUNDARIES = frozenset({"View", "body"})

_LANE_SLUG = {  # Kanban.swift motionKey → dashboard.json 分区键（§2 / server/lanes.py）
    "debt": "debt", "approval": "needs_approval", "running": "running",
    "review": "review", "completed": "completed", "archived": "archived",
}
_OS_SHORTCUT_KEYS = frozenset({"q", "w", "h", "m", "z", "x", "c", "v", "a"})
_SHORT_LABEL_EN = 32
_SHORT_LABEL_ZH = 20


def _source_key(source):
    """'File.swift:123' → ('File.swift', 123)，排序用。"""
    name, _, line = source.partition(":")
    return (name, int(line or 0))


# --------------------------------------------------------------------------- #
# 文件模型
# --------------------------------------------------------------------------- #

class SwiftFile(object):
    """一份 Swift 源的三视图 + 括号树 + 声明范围。"""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.raw = uc.read_text(path)
        self.stripped, self.masked = uc.scan_views(self.raw)
        self.tree = uc.BraceTree(self.masked)
        self.lines = uc.LineIndex(self.raw)
        self.types = uc.top_level_spans(self.masked)
        self.members = {t[1]: uc.member_spans(self.masked, t[2], t[3]) for t in self.types}
        self.funcs = uc.top_level_funcs(self.masked)

    def line(self, offset):
        return self.lines.line_of(offset)

    def source(self, offset):
        return "%s:%d" % (self.name, self.line(offset))

    def owner_type(self, offset):
        span = uc.innermost(self.types, offset)
        return span[1] if span else ""

    def owner_member(self, offset):
        type_name = self.owner_type(offset)
        span = uc.innermost(self.members.get(type_name, []), offset)
        return span[1] if span else ""

    def owner_func(self, offset):
        """offset 所在的顶层自由函数名（不在任何类型体内时才有意义）；没有 → ''。"""
        span = uc.innermost(self.funcs, offset)
        return span[1] if span else ""

    def type_span(self, name):
        for span in self.types:
            if span[1] == name:
                return span
        return None


def load_files(root):
    return [SwiftFile(p) for p in uc.iter_swift_files(root)]


def _by_name(files, name):
    for f in files:
        if f.name == name:
            return f
    return None


# --------------------------------------------------------------------------- #
# Settings 注册表（SettingsSectionDescriptor）
# --------------------------------------------------------------------------- #

_DESCRIPTOR = re.compile(
    r'SettingsSectionDescriptor\(\s*id:\s*"(\w+)",\s*titleZh:\s*"((?:[^"\\]|\\.)*)",\s*'
    r'titleEn:\s*"((?:[^"\\]|\\.)*)",.*?anchor:\s*(nil|"\w+"),\s*content:\s*AnyView\((\w+)(\(\))?\)',
    re.S)


def settings_registry(files):
    """[{id, zh, en, anchor, content, is_type, source}]，按注册顺序。"""
    f = _by_name(files, "Settings.swift")
    if f is None:
        return []
    out = []
    for m in _DESCRIPTOR.finditer(f.stripped):
        out.append({
            "id": m.group(1), "zh": uc.unescape_swift(m.group(2)),
            "en": uc.unescape_swift(m.group(3)),
            "anchor": None if m.group(4) == "nil" else m.group(4).strip('"'),
            "content": m.group(5), "is_type": bool(m.group(6)),
            "source": f.source(m.start()),
        })
    return out


def _settings_maps(registry):
    """(member → section id, type → section id)。"""
    members, types = {}, {}
    for entry in registry:
        target = types if entry["is_type"] else members
        target[entry["content"]] = entry["id"]
    return members, types


# --------------------------------------------------------------------------- #
# screen 归属
# --------------------------------------------------------------------------- #

class Attribution(object):
    """L() 所在位置 → screen：注册表类型 > SettingsFormView 的 group 成员 >
    (类型, 成员) 表 > 类型表 > (文件, 顶层自由函数) 表 > 注册表类型所在文件的默认 > 文件表 > misc。"""

    def __init__(self, registry, files):
        self.section_members, self.section_types = _settings_maps(registry)
        self.file_defaults = self._file_defaults(files)

    def _file_defaults(self, files):
        """定义了注册表类型的文件整份归到该 section（同文件的 helper 一并归属）。"""
        defaults = {}
        for f in files:
            for span in f.types:
                if span[1] in self.section_types:
                    defaults.setdefault(f.name, "settings." + self.section_types[span[1]])
        return defaults

    def _settings_section(self, type_name, member):
        """注册表命中 → 'settings.<id>'；否则 None。"""
        if type_name in self.section_types:
            return "settings." + self.section_types[type_name]
        if type_name == "SettingsFormView" and member in self.section_members:
            return "settings." + self.section_members[member]
        return None

    def screen_for(self, f, offset):
        type_name = f.owner_type(offset)
        member = f.owner_member(offset)
        section = self._settings_section(type_name, member)
        if section:
            return section
        return (MEMBER_SCREEN.get((type_name, member)) or TYPE_SCREEN.get(type_name)
                or _function_screen(f, offset, type_name)
                or self.file_defaults.get(f.name) or FILE_SCREEN.get(f.name, "misc"))


def _function_screen(f, offset, type_name):
    """不在任何类型体内的 L() → 所在顶层自由函数是否被 FUNCTION_SCREEN 点名；否则 None。"""
    if type_name:
        return None
    return FUNCTION_SCREEN.get((f.name, f.owner_func(offset)))


def owner_of(screen):
    """screen → web / shell / os / retired（最长前缀命中）。"""
    for prefix in sorted(SCREEN_OWNER, key=len, reverse=True):
        if screen == prefix or screen.startswith(prefix + "."):
            return SCREEN_OWNER[prefix]
    return "web"


# --------------------------------------------------------------------------- #
# controls（L() 归类）
# --------------------------------------------------------------------------- #

def _is_boundary(ident):
    """穿过它就不再是同一个控件：View 体 / body，或 label: 以外的参数闭包。"""
    return ident in _BOUNDARIES or (ident.startswith("closure:") and ident != "closure:label")


def _role_of_ident(ident):
    """单个调用者标识 → role；不是控件也不是 help → None。"""
    base = ident.rsplit(".", 1)[-1]
    if base == "help":
        return "help"
    return CONTROL_ROLES.get(base)


def _role_from_chain(chain):
    """调用链（最内层在前）→ (role, via)。控件标识优先；闭包参数 / View 体 = 边界。"""
    via = chain[0] if chain else ""
    for ident in chain:
        role = _role_of_ident(ident)
        if role:
            return role, via
        if _is_boundary(ident):
            break
    return "text", via


_AFTER_TEXT = re.compile(r"\)\s*\)\s*\.tag\(")   # L(...) 的 ) + Text(...) 的 ) + .tag(


def _is_option(f, offset):
    """`Text(L(...)).tag(x)` = Picker 选项（不看外层调用链，Picker 本身会把它认成 picker）。"""
    close = f.masked.find(")", offset)  # L(...) 的 ) —— masked 里字面量已成空白
    return close > 0 and bool(_AFTER_TEXT.match(f.masked, close))


_PLACEHOLDER = re.compile(r"\{[^}]*\}")


def _is_short(zh, en):
    """短标签 vs 长文案：插值占位不计长度；以句末标点收尾的是句子不是标签。"""
    zh, en = _PLACEHOLDER.sub("", zh), _PLACEHOLDER.sub("", en)
    if len(en) > _SHORT_LABEL_EN or len(zh) > _SHORT_LABEL_ZH:
        return False
    return not re.search(r"[.。!?！？]$", en.strip())


def _classify(f, offset, zh, en):
    if _is_option(f, offset):
        return "option", "Text"
    role, via = _role_from_chain(f.tree.chain(offset))
    if role == "text":
        role = "label" if _is_short(zh, en) else "copy"
    if role == "dialog" and len(_PLACEHOLDER.sub("", en)) > 2 * _SHORT_LABEL_EN:
        role = "dialog-text"   # 对话框标题常是问句，只按长度分
    return role, via


_NON_L_OPTION = re.compile(r'Text\(\s*"((?:[^"\\]|\\.)*)"\s*\)\s*\.tag\(')


_SKIP_TYPES = frozenset({"MainSection"})  # 栏目标题走 rail 节，不重复进 controls
_LANE_TITLE = re.compile(r"\b(?:column|collapsibleColumn)\(\s*title:\s*$")


def _is_lane_title(f, offset):
    """Kanban 列名走 lanes 节：`column(title: L(` / `collapsibleColumn(title: L(` 不进 controls。"""
    return bool(_LANE_TITLE.search(f.masked, max(0, offset - 80), offset))


def _controls_of(f, attribution):
    items = []
    for offset, zh, en in uc.find_l_calls(f.stripped, f.masked):
        if f.owner_type(offset) in _SKIP_TYPES or _is_lane_title(f, offset):
            continue
        role, via = _classify(f, offset, zh, en)
        items.append(_control(f, offset, zh, en, role, via, attribution))
    for m in _NON_L_OPTION.finditer(f.stripped):
        literal = uc.unescape_swift(m.group(1))
        items.append(_control(f, m.start(), literal, literal, "option", "Text", attribution))
    return items


def _control(f, offset, zh, en, role, via, attribution):
    screen = VIA_SCREEN.get(via) or attribution.screen_for(f, offset)
    owner = owner_of(screen)
    item = {
        "screen": screen, "role": role, "zh": zh, "en": en, "via": via,
        "source": f.source(offset), "owner": owner,
        "gated": role not in INFORMATIONAL_ROLES and (owner == "web" or screen in PROBED_SHELL_SCREENS),
        "_offset": offset, "_file": f.name,
    }
    if item["gated"] and owner != "web":
        item["probe"] = "notify_catalog"   # 非 vitest 探针的名字（parity_check 按它分派）
    return item


def collect_controls(files, attribution):
    items = []
    for f in files:
        items.extend(_controls_of(f, attribution))
    return items


# --------------------------------------------------------------------------- #
# rail（MainSection）+ ⌘n
# --------------------------------------------------------------------------- #

_CASES = re.compile(r"^\s*case\s+([\w, ]+)$", re.M)
_TITLE = re.compile(r"case \.(\w+):\s*return\s+L\(" + r'"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"')
_ICON = re.compile(r'case \.(\w+):\s*return\s+"([\w.]+)"')


def rail_items(files):
    f = _by_name(files, "MainWindow.swift")
    span = f.type_span("MainSection") if f else None
    if span is None:
        return []
    body = f.stripped[span[2]:span[3]]
    order = []
    for m in _CASES.finditer(body):
        order.extend(name.strip() for name in m.group(1).split(","))
    titles = {m.group(1): (m.group(2), m.group(3), span[2] + m.start()) for m in _TITLE.finditer(body)}
    icons = {m.group(1): m.group(2) for m in _ICON.finditer(body)}
    numbered = _rail_numbered(files)
    return [_rail_item(f, slug, i, titles, icons, numbered) for i, slug in enumerate(order)]


def _rail_item(f, slug, index, titles, icons, numbered):
    zh, en, offset = titles.get(slug, (slug, slug, 0))
    item = {"id": "rail:" + slug, "slug": slug, "zh": uc.unescape_swift(zh),
            "en": uc.unescape_swift(en), "icon": icons.get(slug, ""),
            "index": index, "source": f.source(offset), "owner": "web", "gated": True}
    if numbered:
        item["shortcut"] = "⌘%d" % (index + 1)
    retired = RAIL_OWNER.get(slug)   # owner 决策拿掉的侧栏项：只列不判，理由随行
    if retired:
        item["owner"] = retired["owner"]
        item["gated"] = False
        item["reason"] = retired["reason"]
    return item


def _rail_numbered(files):
    """AppDelegate 的 View 菜单是否给 MainSection 逐个配了 ⌘1..n。"""
    f = _by_name(files, "AppDelegate.swift")
    if f is None:
        return False
    return bool(re.search(r"MainSection\.allCases\.enumerated\(\)[\s\S]{0,400}?"
                          r'keyEquivalent:\s*"\\\(i \+ 1\)"', f.stripped))


# --------------------------------------------------------------------------- #
# lanes（Kanban.swift column / collapsibleColumn 调用）
# --------------------------------------------------------------------------- #

_LANE_CALL = re.compile(
    r"\b(column|collapsibleColumn)\(\s*title:\s*L\(" + r'"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"'
    r"[\s\S]*?motionKey:\s*\"(\w+)\"", re.S)


def lane_items(files):
    f = _by_name(files, "Kanban.swift")
    if f is None:
        return []
    lanes = []
    for m in _LANE_CALL.finditer(f.stripped):
        slug = _LANE_SLUG.get(m.group(4), m.group(4))
        lanes.append({
            "id": "lane:" + slug, "slug": slug, "zh": uc.unescape_swift(m.group(2)),
            "en": uc.unescape_swift(m.group(3)), "collapsible": m.group(1) == "collapsibleColumn",
            "index": len(lanes), "source": f.source(m.start()), "owner": "web", "gated": True,
        })
    return _mark_rails(lanes)


def _mark_rails(lanes):
    for lane in lanes:
        lane["rail"] = None
    if lanes and lanes[0]["collapsible"]:
        lanes[0]["rail"] = "left"
    if len(lanes) > 1 and lanes[-1]["collapsible"]:
        lanes[-1]["rail"] = "right"
    return lanes


def card_affordances(controls):
    """每列卡面的可点动词（board.<lane> 下 role ∈ button/menu/alert-button/link）。"""
    verbs = {}
    for c in controls:
        if not c["screen"].startswith("board.") or c["role"] not in ("button", "menu", "alert-button", "link"):
            continue
        lane = c["screen"].split(".", 1)[1]
        verbs.setdefault(lane, []).append({"id": c["id"], "zh": c["zh"], "en": c["en"], "source": c["source"]})
    return {lane: sorted(rows, key=lambda r: r["id"]) for lane, rows in sorted(verbs.items())}


# --------------------------------------------------------------------------- #
# settings keys
# --------------------------------------------------------------------------- #

_OV_KEY = re.compile(r'\b(?:ov|merged|overrides)\["(\w+)"\]|persistOverride\("(\w+)"|'
                     r'merged\.removeValue\(forKey:\s*"(\w+)"\)')
_FEAT_KEY = re.compile(r'\b(?:feats\["(\w+)"\]|flag\("(\w+)"\)|featureBinding\("(\w+)"|feats\.removeValue\(forKey:\s*"(\w+)"\))')
_TELE_KEY = re.compile(r'\btele\["(\w+)"\]')
_PREF_KEY = re.compile(r'(?:UserDefaults\.standard|\bd)\.(?:set|string|bool|double|object|integer|stringArray)'
                       r'\([^()]*?forKey:\s*"([\w.]+)"\)|Prefs\.(?:bool|string|int|double)\("([\w.]+)"')
_CONTAINER_KEYS = frozenset({"features", "telemetry"})


def _first_group(m):
    return next(g for g in m.groups() if g)


def _collect_keys(f, regex, prefix, store, sink):
    for m in regex.finditer(f.stripped):
        key = prefix + _first_group(m)
        if key in _CONTAINER_KEYS:
            continue
        sink.setdefault((store, key), []).append(f.source(m.start()))


def settings_keys(files):
    found = {}
    for f in files:
        _collect_keys(f, _OV_KEY, "", "overrides", found)
        _collect_keys(f, _FEAT_KEY, "features.", "overrides", found)
        _collect_keys(f, _TELE_KEY, "telemetry.", "overrides", found)
        _collect_keys(f, _PREF_KEY, "", "prefs", found)
    return [_setting_item(store, key, sources) for (store, key), sources in sorted(found.items())]


_ENGINE_PREF_PREFIXES = ("captions", "recordingMode", "lastActiveRecordingMode", "liveCaptionsEnabled")
_PREF_PROBES = {"shell": "shell_source", "server": "server_source"}


def _pref_owner(store, key):
    """prefs 键的归属：PREF_OWNER 表 > 引擎前缀（shell）> web。overrides 键一律 web。"""
    if store != "prefs":
        return {"owner": "web"}
    if key in PREF_OWNER:
        return PREF_OWNER[key]
    return {"owner": "shell" if key.startswith(_ENGINE_PREF_PREFIXES) else "web"}


def _setting_item(store, key, sources):
    attribution = _pref_owner(store, key)
    owner = attribution["owner"]
    item = {"id": "setting:%s:%s" % (store, key), "key": key, "store": store,
            "sources": sorted(set(sources)), "owner": owner, "gated": owner in ("web", "shell", "server")}
    if owner in _PREF_PROBES:
        item["probe"] = _PREF_PROBES[owner]
    for extra in ("landing", "reason"):
        if extra in attribution:
            item[extra] = attribution[extra]
    return item


# --------------------------------------------------------------------------- #
# shortcuts
# --------------------------------------------------------------------------- #

_KB = re.compile(r'\.keyboardShortcut\((?:"(\w)"(?:,\s*modifiers:\s*\[?([.\w, ]+)\]?)?|\.(\w+))\)')
_MENU_ITEM = re.compile(r'(?:NSMenuItem\(title:|addItem\(withTitle:)\s*L\(' + r'"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"'
                        r'[\s\S]{0,300}?keyEquivalent:\s*"([^"]*)"\)(?:\s*(\w+)\.keyEquivalentModifierMask = \[([.\w, ]+)\])?')
_MOD_GLYPH = {"control": "⌃", "option": "⌥", "shift": "⇧", "command": "⌘"}
_SPECIAL = {"defaultAction": "↩", "cancelAction": "⎋"}
_GLYPH_SLUG = {"⌃": "ctrl", "⌥": "opt", "⇧": "shift", "⌘": "cmd", "↩": "return", "⎋": "escape"}


def _glyphs(mods, key):
    """修饰键 → 字形（固定顺序 ⌃⌥⇧⌘）；keyEquivalent 里的大写字母 = 隐含 ⇧。"""
    names = {m.strip().lstrip(".") for m in (mods or "command").split(",")}
    if key.isalpha() and key.isupper():
        names.add("shift")
    return "".join(_MOD_GLYPH[n] for n in ("control", "option", "shift", "command") if n in names)


def _kb_key(m):
    if m.group(3):
        return _SPECIAL.get(m.group(3), m.group(3))
    return _glyphs(m.group(2), m.group(1)) + m.group(1).upper()


def _key_slug(key):
    return "-".join(_GLYPH_SLUG.get(ch, ch.lower()) for ch in key)


def _shortcut(f, offset, key, zh, en, attribution):
    screen = attribution.screen_for(f, offset)
    plain_os = key[:-1].lstrip("⇧") == "⌘" and key[-1].lower() in _OS_SHORTCUT_KEYS
    owner = "os" if plain_os else owner_of(screen)
    return {"id": "shortcut:%s:%s" % (screen, _key_slug(key) + ("-" + uc.slugify(en) if en else "")),
            "key": key, "zh": zh, "en": en, "screen": screen, "source": f.source(offset),
            "owner": owner, "gated": owner == "web"}


def _dedupe_ids(items):
    """同 id 按出现顺序补 #n（稳定：items 已按 source 排序）。"""
    seen = {}
    for item in items:
        seen[item["id"]] = seen.get(item["id"], 0) + 1
        if seen[item["id"]] > 1:
            item["id"] = "%s#%d" % (item["id"], seen[item["id"]])
    return items


def shortcuts(files, attribution):
    out = []
    for f in files:
        for m in _KB.finditer(f.stripped):
            out.append(_shortcut(f, m.start(), _kb_key(m), "", "", attribution))
        for m in _MENU_ITEM.finditer(f.stripped):
            if m.group(3):
                key = _glyphs(m.group(5), m.group(3)) + m.group(3).upper()
                out.append(_shortcut(f, m.start(), key, uc.unescape_swift(m.group(1)),
                                     uc.unescape_swift(m.group(2)), attribution))
    out.sort(key=lambda s: (_source_key(s["source"]), s["key"]))
    return _dedupe_ids(out)


# --------------------------------------------------------------------------- #
# notifications / screens / theme+layout 指针
# --------------------------------------------------------------------------- #

_KIND = re.compile(r'\.kind\s*==\s*"(\w+)"')


def notification_kinds(files):
    """通知 kind 词表：NotifyRelay.swift 按 kind 过滤/加声的分支（其余 kind 为 None）。
    owner shell、探针 notify_catalog：kind 必须登记在 server/notify_catalog.py（§66.2 追记）。"""
    kinds = {}
    f = _by_name(files, "NotifyRelay.swift")
    for m in _KIND.finditer(f.stripped if f else ""):
        kinds.setdefault(m.group(1), f.source(m.start()))
    items = [{"id": "notification:" + k, "kind": k, "source": src, "owner": "shell", "gated": True,
              "probe": "notify_catalog"} for k, src in sorted(kinds.items())]
    items.append({"id": "notification:general", "kind": None, "source": "act/lib/notify.py",
                  "owner": "shell", "gated": True, "probe": "notify_catalog"})
    return items


_WINDOWS = (  # 独立窗口 / sheet / 条：类型名 → (screen, zh, en)
    ("PermissionsView", "permissions", "权限体检", "Permissions checkup"),
    ("SetupWizardView", "setup_wizard", "初始设置向导", "Setup wizard"),
    ("DiagnosticsStrip", "board.diagnostics", "诊断", "Diagnostics"),
    ("ForceMergeSheet", "board.merge", "强制合并", "Force merge"),
    ("HelloBubbleView", "onboarding.hello_bubble", "首启气泡", "Hello bubble"),
    ("CaptionOverlay", "captions", "实时字幕悬浮窗", "Live captions overlay"),
)


def screens(files, rail, registry):
    # rail 页的 screen 名 = slug；整页退役的（SCREEN_OWNER ask → retired）随 owner_of 不判，
    # 只是侧栏项退役而页面并入别处的（deps → 设置页一区）仍是 web、照判
    out = [{"id": "screen:" + r["slug"], "kind": "rail-page", "zh": r["zh"], "en": r["en"],
            "source": r["source"], "owner": owner_of(r["slug"]), "gated": owner_of(r["slug"]) == "web"} for r in rail]
    for entry in registry:
        screen = "settings." + entry["id"]
        out.append({"id": "screen:" + screen, "kind": "settings-section", "zh": entry["zh"],
                    "en": entry["en"], "anchor": entry["anchor"], "source": entry["source"],
                    "owner": owner_of(screen), "gated": owner_of(screen) == "web"})
    out.extend(_window_screens(files))
    return out


def _window_screens(files):
    out = []
    for type_name, screen, zh, en in _WINDOWS:
        for f in files:
            span = f.type_span(type_name)
            if span is not None:
                out.append({"id": "screen:" + screen, "kind": "window", "zh": zh, "en": en,
                            "source": f.source(span[2]), "owner": owner_of(screen),
                            "gated": owner_of(screen) == "web"})
    return out


def theme_and_layout():
    """指针条目：数值住 ui/tokens/native-tokens.json（extract_native_tokens.py）。"""
    return [
        {"id": "theme:default", "value": "light", "owner": "web", "gated": True,
         "source": "owner decision 2026-09-02 (native follows macOS appearance; owner runs light)"},
        {"id": "layout:lane-width", "token": "layout.lane.width", "owner": "web", "gated": True,
         "source": "Kanban.swift column .frame(width: 400)"},
        {"id": "layout:strip-width", "token": "layout.strip.width", "owner": "web", "gated": True,
         "source": "Kanban.swift collapsedStrip .frame(width: 44)"},
        {"id": "layout:lane-gap", "token": "layout.lane.gap", "owner": "web", "gated": True,
         "source": "Kanban.swift HStack(spacing: 12)"},
        {"id": "layout:board-padding", "token": "layout.board.padding", "owner": "web", "gated": True,
         "source": "Kanban.swift .padding(16)"},
        {"id": "layout:rail-collapsed-width", "token": "layout.rail.collapsed_width", "owner": "web",
         "gated": True, "source": "MainWindow.swift collapsedWidth = 48"},
    ]


# --------------------------------------------------------------------------- #
# id 铸造 + 装配
# --------------------------------------------------------------------------- #

def assign_ids(controls):
    """control id = control:<screen>:<role>:<slug-en>[#n]，按 (file, line) 顺序稠密编号。"""
    controls.sort(key=lambda c: (c["_file"], c["_offset"]))
    seen = {}
    for c in controls:
        base = "control:%s:%s:%s" % (c["screen"], c["role"], uc.slugify(c["en"] or c["zh"]))
        seen[base] = seen.get(base, 0) + 1
        c["id"] = base if seen[base] == 1 else "%s#%d" % (base, seen[base])
        _apply_control_owner(c)
    for c in controls:
        del c["_file"], c["_offset"]
    return sorted(controls, key=lambda c: (c["screen"], _source_key(c["source"]), c["id"]))


def _apply_control_owner(control):
    """CONTROL_OWNER 点名的单条：owner 改成表值、不再判（gated False）、理由随行。id 已铸好才查表。"""
    entry = CONTROL_OWNER.get(control["id"])
    if not entry:
        return
    control["owner"] = entry["owner"]
    control["gated"] = False
    control["reason"] = entry["reason"]
    control.pop("probe", None)


def _digest(files):
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode("utf-8"))
        h.update(f.raw.encode("utf-8"))
    return h.hexdigest()


def build_inventory(root=uc.MAC_SOURCES):
    files = load_files(root)
    registry = settings_registry(files)
    attribution = Attribution(registry, files)
    controls = assign_ids(collect_controls(files, attribution))
    rail = rail_items(files)
    lanes = lane_items(files)
    return {
        "attribution": {"file_screen": FILE_SCREEN, "type_screen": TYPE_SCREEN,
                        "member_screen": {"%s.%s" % k: v for k, v in MEMBER_SCREEN.items()},
                        "function_screen": {"%s:%s" % k: v for k, v in FUNCTION_SCREEN.items()},
                        "screen_owner": SCREEN_OWNER, "via_screen": VIA_SCREEN,
                        "probed_shell_screens": sorted(PROBED_SHELL_SCREENS),
                        "pref_owner": PREF_OWNER, "control_owner": CONTROL_OWNER,
                        "rail_owner": RAIL_OWNER},
        "controls": controls,
        "lanes": {"order": [lane["slug"] for lane in lanes], "items": lanes,
                  "card_affordances": card_affordances(controls)},
        "notifications": notification_kinds(files),
        "rail": {"side": "left", "items": rail},
        "screens": screens(files, rail, registry),
        "settings_keys": settings_keys(files),
        "shortcuts": shortcuts(files, attribution),
        "source": {"dir": "mac/Sources", "files": len(files), "sha256": _digest(files)},
        "theme_layout": theme_and_layout(),
    }


def iter_items(inventory):
    """清单里所有带 id 的条目（门与账本的公共遍历）。"""
    for c in inventory["controls"]:
        yield c
    for group in ("rail", "lanes"):
        for item in inventory[group]["items"]:
            yield item
    for key in ("screens", "settings_keys", "shortcuts", "notifications", "theme_layout"):
        for item in inventory[key]:
            yield item


def stats(inventory):
    counts = {}
    for item in iter_items(inventory):
        kind = item["id"].split(":", 1)[0]
        bucket = counts.setdefault(kind, {"total": 0, "gated": 0})
        bucket["total"] += 1
        bucket["gated"] += 1 if item.get("gated") else 0
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--root", default=uc.MAC_SOURCES)
    parser.add_argument("--out", default=uc.INVENTORY_PATH)
    args = parser.parse_args(argv)
    inventory = build_inventory(args.root)
    text = uc.dump_json(inventory)
    if args.stats:
        for kind, c in sorted(stats(inventory).items()):
            print("%-14s total %4d  gated %4d" % (kind, c["total"], c["gated"]))
    if args.write:
        uc.write_text(args.out, text)
        print("wrote %s" % uc.display_path(args.out))
    return _check_fresh(args.out, text) if args.check else 0


def _check_fresh(path, text):
    current = uc.read_text(path) if os.path.exists(path) else ""
    if current != text:
        print("native-inventory.json is stale — rerun with --write", file=sys.stderr)
        return 1
    print("native-inventory.json is fresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
