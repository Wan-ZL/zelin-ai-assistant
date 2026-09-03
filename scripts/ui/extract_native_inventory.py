#!/usr/bin/env python3
"""原生 Mac app 的用户可见面清单 —— 机器提取，写进 ui/parity/native-inventory.json。

法典：docs/CONTRACT.md §64（UI 对齐契约）。mac/Sources 在 D3 下冻结、只读，
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
`FILE_SCREEN` / `TYPE_SCREEN` / `MEMBER_SCREEN` 三张归属表与 `SCREEN_OWNER`
（谁负责补齐：web / shell / os / retired）——表本身也进 JSON（`attribution`）。

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
}

# screen 前缀 → 负责补齐的一方。web = 看板必须补（进门）；shell = 原生残留
# （R2.2.3：字幕悬浮窗、系统通知、TCC 引导）；os = macOS 应用菜单惯例；
# retired = 计划明文退役（D3 菜单栏图标）。非 web 的条目只列不判。
SCREEN_OWNER = {
    "captions": "shell",
    "notifications": "shell",
    "menu.main": "shell",
    "menu.status": "retired",
    "app": "shell",
    "settings.menuBar": "retired",
    "onboarding.hello_bubble": "retired",
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
    (类型, 成员) 表 > 类型表 > 注册表类型所在文件的默认 > 文件表 > misc。"""

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
                or self.file_defaults.get(f.name) or FILE_SCREEN.get(f.name, "misc"))


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
    screen = attribution.screen_for(f, offset)
    return {
        "screen": screen, "role": role, "zh": zh, "en": en, "via": via,
        "source": f.source(offset), "owner": owner_of(screen),
        "gated": role not in INFORMATIONAL_ROLES and owner_of(screen) == "web",
        "_offset": offset, "_file": f.name,
    }


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


def _setting_item(store, key, sources):
    owner = "shell" if key.startswith(("captions", "recordingMode", "lastActiveRecordingMode",
                                       "liveCaptionsEnabled")) else "web"
    return {"id": "setting:%s:%s" % (store, key), "key": key, "store": store,
            "sources": sorted(set(sources)), "owner": owner, "gated": owner == "web"}


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
    """通知 kind 词表：NotifyRelay.swift 按 kind 过滤/加声的分支（其余 kind 为 None）。"""
    kinds = {}
    f = _by_name(files, "NotifyRelay.swift")
    for m in _KIND.finditer(f.stripped if f else ""):
        kinds.setdefault(m.group(1), f.source(m.start()))
    items = [{"id": "notification:" + k, "kind": k, "source": src, "owner": "shell", "gated": False}
             for k, src in sorted(kinds.items())]
    items.append({"id": "notification:general", "kind": None, "source": "act/lib/notify.py",
                  "owner": "shell", "gated": False})
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
    out = [{"id": "screen:" + r["slug"], "kind": "rail-page", "zh": r["zh"], "en": r["en"],
            "source": r["source"], "owner": "web", "gated": True} for r in rail]
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
    for c in controls:
        del c["_file"], c["_offset"]
    return sorted(controls, key=lambda c: (c["screen"], _source_key(c["source"]), c["id"]))


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
                        "screen_owner": SCREEN_OWNER},
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
        print("wrote %s" % os.path.relpath(args.out, uc.REPO_ROOT))
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
