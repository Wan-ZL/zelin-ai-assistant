#!/usr/bin/env python3
"""壳（shell/）UI 的辅助功能探针 —— `axprobe:` 这一种覆盖证据的执行者。

法典：docs/CONTRACT.md §58（质量仪表：每条场景要一条可执行证据）。被探的行为
各有法条，探针只读它们的可观测量、不新增行为：

  - `dock_badge`     Dock 图标徽章 = 等你动作的卡数（§15 v0.46 ②：提案 + 需输入 +
                     待验收；web `pushBadge` → 桥 `setBadge` → 壳 `DockBadge.set`，
                     §54.1 / §61.6）。观测量 = Dock 进程里该 app tile 的
                     `NSDockTile.badgeLabel` 经 LaunchServices 发布（`lsappinfo … StatusLabel`），
                     比对 `GET /api/board` 的 counts；Dock 进程的 `AXStatusLabel` 只作回落
                     （2026-09-17 实测：徽章明明是 42，AX 仍回 missing value）。
  - `hotkey_focus`   全局 ⌃⌥Space = 聚焦提案列捕获框（§68.13，与菜单 显示 ▸ 聚焦
                     捕获框 ⌘L 同一条路 `focusCaptureField` → `quick_capture`）。
                     观测量 = 壳前置 + `AXFocusedUIElement` 是提案 composer 的
                     textarea（placeholder 逐字取自 web BoardLanes/AppShell）。
  - `menu_open_page` app 菜单 关于 / 设置… / 权限体检… → `open_page {page}`（§54.4
                     追记 D40 / §61.6；壳 `openBoardPage`）。观测量 = 窗口标题
                     （`Zelin's AI Assistant — <页>`，web pageTitles.ts 经
                     WKWebView.title KVO 抬成 NSWindow.title，§54.1 追记）。
  - `notify_relay`   §28 通知中继（`state/notify_queue` 消费者）活着的只读观测量：
                     壳 5 s 引擎 tick 既 `NotifyRelay.drain()` 又 touch
                     `state/shell.heartbeat`（§68.7，server 据它判「队列有消费者」，
                     新鲜阈值 15 s）+ 队列里没有超过 §28 staleAfter 的积压。
  - `terminal_takeover` §68.7 终端接管：`POST /api/terminal {card_id}` 入队 →
                     壳 `TerminalRelay` 经 Apple Events 在终端新开窗口。**写动作，
                     只在 `--allow-enqueue` 下跑。** 命令永远由 server 从投影行推导
                     （§68.7：绝不接受客户端文本），所以探针只给 card_id。

只读纪律：除 `terminal_takeover` 外全部是 GET / 读 state 文件 / AX 读属性；探针
永不写 live 的 `state/`、`act/registry/`、config。`hotkey_focus` 与
`menu_open_page` 会前置壳窗口——两者收尾都把原先的前台 app 重新激活
（`menu_open_page` 另按一次 ⌃⌥Space 让看板回到任务台页，用的是壳自己的机制）。

辅助功能（TCC）缺失不绕行：起手先问 `UI elements enabled`，或任何 AX 调用报
-1719 / -25211 → 打印一行 BLOCKED JSON 并 exit 3，由 owner 去系统设置授权。

判例：tests/test_shell_ui_probe.py（注入假 osascript + 假 HTTP，不碰真 AX、不联网）。

用法：
    python3 scripts/qa/shell_ui_probe.py --list
    python3 scripts/qa/shell_ui_probe.py --probe dock_badge --json
    python3 scripts/qa/shell_ui_probe.py --summary [--allow-enqueue]
退出码：0 = 执行的探针全 present；1 = 有 MISSING；2 = 用法错；3 = BLOCKED。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# 常量（truth 指针写在注释里，禁手抄字面量到文档）
# ---------------------------------------------------------------------------

#: 壳可执行名（pgrep -x 键；truth = shell/build.sh EXEC_NAME）
APP_PROCESS = "ZelinAIBoard"
APP_BUNDLE_ID = "com.zelin.ai-board"     # CFBundleIdentifier（§54）——LaunchServices 按它查徽章
BADGE_SETTLE_DELAY = 2.0                # s：徽标 0 而看板 >0 时重读的间隔（网页刚重拉看板）
BADGE_SETTLE_TRIES = 3
#: 壳显示名（Dock tile / app 菜单标题；truth = shell/Info.plist CFBundleDisplayName）
APP_DISPLAY_NAME = "Zelin's AI Assistant"
#: 看板 server 端口（truth = 壳 ShellConfig.port / server 默认）
DEFAULT_PORT = 47820
#: AIASSISTANT_HOME 回落（truth = server/paths.py DEFAULT_HOME）
DEFAULT_HOME = "~/Projects/zelin-ai-assistant"
#: 壳心跳新鲜阈值，秒（truth = server/terminal_launch.py HEARTBEAT_FRESH_S）
HEARTBEAT_FRESH_S = 15.0
#: §28 通知条目过期阈值，秒（truth = shell/Sources/NotifyRelay.swift staleAfter）
NOTIFY_STALE_AFTER_S = 600.0
#: 提案列 composer 的 placeholder 双语逐字（truth = web/src/components/board/BoardLanes.tsx
#: 提案列 + web/src/components/shell/AppShell.tsx BoardMissingState）
COMPOSER_PLACEHOLDERS = (
    "一句话，AI 来研究并提案…",
    "One sentence — AI researches and proposes…",
)
#: 徽章口径的三条泳道（truth = web/src/app.tsx badgeCount）
BADGE_LANES = ("needs_approval", "needs_input", "review")
#: 焦点元素可接受的 AX 角色（WebKit 把 <textarea> 暴露成 AXTextArea）
TEXT_ROLES = ("AXTextArea", "AXTextField")
#: 菜单项 → 期望页标题片段（truth = shell MenuSpec.menus + web pageTitles.ts PAGE_LABELS）
MENU_PAGES = (
    ("about", "关于 " + APP_DISPLAY_NAME, "About " + APP_DISPLAY_NAME, "关于", "About"),
    ("settings", "设置…", "Settings…", "设置", "Settings"),
    ("permissions", "权限体检…", "Permissions Checkup…", "权限体检", "Permissions Checkup"),
)
#: 看板页标题片段（menu_open_page 收尾还原后的观测；truth = web pageTitles.ts board）
BOARD_TITLE_PARTS = ("任务台", "Workbench")
#: 快速捕获全局快捷键（truth = shell QuickCaptureHotkey：⌃⌥Space = key code 49）
HOTKEY_KEYCODE = 49
#: 终端进程名（truth = server/terminal_launch.py TERMINAL_APP_NAMES["ghostty"]）
DEFAULT_TERMINAL_PROCESS = "Ghostty"
#: AX 未授权的 osascript 错误码（-1719 = AX API disabled、-25211 = not authorized）
AX_DENIED_CODES = ("-1719", "-25211")
#: BLOCKED JSON 里给 owner 的动作（逐字，goal 指定）
OWNER_ACTION = "系统设置 → 隐私与安全性 → 辅助功能 → 加入 Ghostty 或 python3"

PROBE_IDS = ("dock_badge", "hotkey_focus", "menu_open_page", "notify_relay",
             "terminal_takeover")
#: --summary 默认跑的（terminal_takeover 是写动作，要 --allow-enqueue）
SUMMARY_PROBES = ("dock_badge", "hotkey_focus", "menu_open_page", "notify_relay")

EXIT_OK = 0
EXIT_MISSING = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3


# ---------------------------------------------------------------------------
# home 解析（注入缝：显式 > env > 候选里第一个有 state/server.token 的）
# ---------------------------------------------------------------------------

def worktree_parent(path: str) -> "str | None":
    """`<checkout>/.claude/worktrees/<name>` → `<checkout>`（在 worktree 里跑时
    live checkout 才是壳的 AIASSISTANT_HOME）。不在 worktree 里 → None。"""
    marker = os.sep + ".claude" + os.sep + "worktrees" + os.sep
    idx = path.find(marker)
    return path[:idx] if idx > 0 else None


def home_candidates(script_path: str) -> "list[str]":
    """按优先序的 home 候选（不做存在性判断——调用方挑）。"""
    root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(script_path)),
                                         "..", ".."))
    out = [root]
    parent = worktree_parent(root + os.sep)
    if parent:
        out.insert(0, parent)
    out.append(os.path.expanduser(DEFAULT_HOME))
    return out


def first_home_with_token(candidates, exists=os.path.exists) -> "str | None":
    """候选里第一个带 `state/server.token` 的（= 真的有个 server 在那个 home 上）。"""
    for cand in candidates:
        if exists(os.path.join(cand, "state", "server.token")):
            return cand
    return None


def resolve_home(explicit: "str | None" = None, env: "dict | None" = None,
                 script_path: "str | None" = None,
                 exists=os.path.exists) -> str:
    """AIASSISTANT_HOME：显式 > env > 第一个带 state/server.token 的候选 > 第一个候选。"""
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    env = os.environ if env is None else env
    from_env = env.get("AIASSISTANT_HOME")
    if from_env:
        return os.path.abspath(os.path.expanduser(from_env))
    cands = home_candidates(script_path or __file__)
    return first_home_with_token(cands, exists) or cands[0]


# ---------------------------------------------------------------------------
# 真环境（osascript / HTTP / state 文件）—— 测试注入同名方法的假货
# ---------------------------------------------------------------------------

class OsaResult:
    """osascript 一次调用的结果（rc + stdout + stderr，都已 strip）。"""

    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.rc = int(rc)
        self.out = (out or "").strip()
        self.err = (err or "").strip()

    @property
    def ax_denied(self) -> bool:
        blob = self.err + " " + self.out
        return any(code in blob for code in AX_DENIED_CODES)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "OsaResult(rc=%d, out=%r, err=%r)" % (self.rc, self.out, self.err)


def error_text(err: dict) -> str:
    """`{"message", "details"}` 形的 server 错误体 → 一行人话（§7 错误信封）。"""
    msg = err.get("message")
    if not isinstance(msg, str):
        return json.dumps(err, ensure_ascii=False)[:200]
    details = err.get("details")
    if details is None:
        return msg
    return msg + " " + json.dumps(details, ensure_ascii=False)


class HttpResult:
    """一次 HTTP 调用（status + 文本 + 尽力解析的 JSON；网络错 status=0）。"""

    def __init__(self, status: int, body: str = "", error: str = ""):
        self.status = int(status)
        self.body = body or ""
        self.error = error or ""

    @property
    def json(self) -> "dict | list | None":
        try:
            return json.loads(self.body)
        except Exception:
            return None

    def message(self) -> str:
        """server 错误体的人话（`{"error": {"message": ...}}`），否则截断的正文。"""
        data = self.json
        err = data.get("error") if isinstance(data, dict) else None
        if not isinstance(err, dict):
            return (self.error or self.body or "")[:200]
        return error_text(err)


class LiveEnv:
    """真机环境：osascript 子进程 + 本机 server（默认只 GET）+ 只读 state 文件。"""

    def __init__(self, home: str, port: int = DEFAULT_PORT, timeout: float = 60.0):
        self.home = home
        self.port = int(port)
        self.timeout = timeout
        self._token = None

    # --- AX ---------------------------------------------------------------
    def osascript(self, script: str) -> OsaResult:
        try:
            proc = subprocess.run(["osascript", "-"], input=script, capture_output=True,
                                  text=True, timeout=self.timeout)
        except Exception as exc:  # osascript 缺失 / 超时
            return OsaResult(127, "", "%s: %s" % (type(exc).__name__, exc))
        return OsaResult(proc.returncode, proc.stdout, proc.stderr)

    # --- LaunchServices（徽章真源）------------------------------------------
    def lsappinfo(self, bundle_id: str = APP_BUNDLE_ID) -> OsaResult:
        """`lsappinfo info -only StatusLabel -app <bundle id>`：NSDockTile.badgeLabel 的
        发布面，不依赖 Dock 进程的 AX 树。"""
        try:
            proc = subprocess.run(["lsappinfo", "info", "-only", "StatusLabel", "-app", bundle_id],
                                  capture_output=True, text=True, timeout=self.timeout)
        except Exception as exc:  # lsappinfo 缺失 / 超时
            return OsaResult(127, "", "%s: %s" % (type(exc).__name__, exc))
        return OsaResult(proc.returncode, proc.stdout, proc.stderr)

    # --- HTTP -------------------------------------------------------------
    def token(self) -> str:
        """`<home>/state/server.token`（只读；**永不打印**）。"""
        if self._token is None:
            path = os.path.join(self.home, "state", "server.token")
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    self._token = fh.read().strip()
            except OSError:
                self._token = ""
        return self._token

    def http(self, method: str, path: str, payload: "dict | None" = None) -> HttpResult:
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-Zai-Token", self.token())
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return HttpResult(resp.status, resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            return HttpResult(exc.code, exc.read().decode("utf-8", "replace"))
        except Exception as exc:
            return HttpResult(0, "", "%s: %s" % (type(exc).__name__, exc))

    # --- state 文件（只读） ------------------------------------------------
    def age_s(self, *parts: str) -> "float | None":
        try:
            return time.time() - os.stat(os.path.join(self.home, *parts)).st_mtime
        except OSError:
            return None

    def entries(self, *parts: str) -> "list[str]":
        try:
            return sorted(os.listdir(os.path.join(self.home, *parts)))
        except OSError:
            return []

    def entry_age_s(self, name: str, *parts: str) -> "float | None":
        return self.age_s(*(list(parts) + [name]))

    def pgrep(self, name: str) -> str:
        """`pgrep -x <name>` 的 stdout（空 = 没在跑）——BLOCKED 报告要逐字贴。"""
        try:
            proc = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True,
                                  timeout=10)
        except Exception:
            return ""
        return proc.stdout.strip()


# ---------------------------------------------------------------------------
# AppleScript 拼装
# ---------------------------------------------------------------------------

def osa_str(value: str) -> str:
    """Python 串 → AppleScript 双引号串字面量。"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


AX_ENABLED_SCRIPT = 'tell application "System Events" to return UI elements enabled'


def dock_badge_script(app_name: str = APP_DISPLAY_NAME) -> str:
    return "\n".join([
        'tell application "System Events"',
        '\ttell process "Dock"',
        '\t\ttry',
        '\t\t\tset v to value of attribute "AXStatusLabel" of UI element %s of list 1'
        % osa_str(app_name),
        '\t\t\tif v is missing value then',
        '\t\t\t\treturn "badge:none"',
        '\t\t\telse',
        '\t\t\t\treturn "badge:" & (v as text)',
        '\t\t\tend if',
        '\t\ton error e',
        '\t\t\treturn "error:" & e',
        '\t\tend try',
        '\tend tell',
        'end tell',
    ])


def hotkey_focus_script(process: str = APP_PROCESS, keycode: int = HOTKEY_KEYCODE,
                        tries: int = 12, attempts: int = 3) -> str:
    """⌃⌥Space → 轮询焦点元素 → 还原原前台 app。一次 osascript 跑完（壳窗口可能被
    owner 随时关掉，分多次调用会撞上空窗口期；`AXFocusedUIElement` 也只在壳前置时
    可读）。重按最多 `attempts` 次：`focusCaptureField` 幂等（壳自己的「已开着 → 只
    重新聚焦」那条路），第一按常是冷启前置窗口、焦点还停在 WKWebView 的 AXGroup 上。
    返回 tab 分隔六段：prev / frontmost / role / placeholder / description / 窗口标题。"""
    text_role_test = " or ".join("axRole is %s" % osa_str(r) for r in TEXT_ROLES)
    return "\n".join([
        'tell application "System Events" to set prevApp to name of first application process whose frontmost is true',
        'set axRole to "none"',
        'set axPlace to ""',
        'set axDesc to ""',
        'set frontApp to ""',
        'set winTitle to "nowindow"',
        'repeat with attempt from 1 to %d' % attempts,
        '\ttell application "System Events" to key code %d using {control down, option down}' % keycode,
        '\trepeat with i from 1 to %d' % tries,
        '\t\tdelay 0.25',
        '\t\ttell application "System Events"',
        '\t\t\tset frontApp to name of first application process whose frontmost is true',
        '\t\t\ttell process %s' % osa_str(process),
        '\t\t\t\ttry',
        '\t\t\t\t\tset winTitle to name of window 1',
        '\t\t\t\tend try',
        '\t\t\t\ttry',
        '\t\t\t\t\tset fe to value of attribute "AXFocusedUIElement"',
        '\t\t\t\t\tset axRole to (value of attribute "AXRole" of fe) as text',
        '\t\t\t\t\ttry',
        '\t\t\t\t\t\tset axPlace to (value of attribute "AXPlaceholderValue" of fe) as text',
        '\t\t\t\t\tend try',
        '\t\t\t\t\ttry',
        '\t\t\t\t\t\tset axDesc to (value of attribute "AXDescription" of fe) as text',
        '\t\t\t\t\tend try',
        '\t\t\t\ton error',
        '\t\t\t\t\tset axRole to "none"',
        '\t\t\t\tend try',
        '\t\t\tend tell',
        '\t\tend tell',
        # 焦点先落在 WKWebView 的 AXGroup 上，composer 拿到焦点晚几拍——等到文本角色再停
        '\t\tif %s then exit repeat' % text_role_test,
        '\tend repeat',
        '\tif %s then exit repeat' % text_role_test,
        'end repeat',
        'try',
        '\ttell application "System Events" to tell application process prevApp to set frontmost to true',
        'end try',
        'return prevApp & tab & frontApp & tab & axRole & tab & axPlace & tab & axDesc & tab & winTitle',
    ])


def menu_open_page_script(process: str = APP_PROCESS, app_name: str = APP_DISPLAY_NAME,
                          pages=MENU_PAGES, tries: int = 20,
                          keycode: int = HOTKEY_KEYCODE) -> str:
    """app 菜单里逐项点击 → 轮询窗口标题到期望页 → ⌃⌥Space 还原看板页 + 原前台 app。
    每行 `slug<tab>标题`，最后一行 `restored<tab>标题`。"""
    triples = ", ".join(
        "{%s, %s, %s, %s, %s}" % (osa_str(slug), osa_str(zh_item), osa_str(en_item),
                                  osa_str(zh_title), osa_str(en_title))
        for slug, zh_item, en_item, zh_title, en_title in pages)
    return "\n".join([
        'set outText to ""',
        'tell application "System Events"',
        '\tset prevApp to name of first application process whose frontmost is true',
        '\ttell process %s' % osa_str(process),
        '\t\tset frontmost to true',
        '\t\tdelay 0.4',
        '\t\trepeat with tri in {%s}' % triples,
        '\t\t\tset slug to item 1 of tri',
        '\t\t\tset winTitle to "noclick"',
        '\t\t\ttry',
        '\t\t\t\ttry',
        '\t\t\t\t\tclick menu item (item 2 of tri) of menu 1 of menu bar item %s of menu bar 1'
        % osa_str(app_name),
        '\t\t\t\ton error',
        '\t\t\t\t\tclick menu item (item 3 of tri) of menu 1 of menu bar item %s of menu bar 1'
        % osa_str(app_name),
        '\t\t\t\tend try',
        '\t\t\t\tset winTitle to "nowindow"',
        '\t\t\t\trepeat with i from 1 to %d' % tries,
        '\t\t\t\t\tdelay 0.25',
        '\t\t\t\t\ttry',
        '\t\t\t\t\t\tset winTitle to name of window 1',
        '\t\t\t\t\ton error',
        '\t\t\t\t\t\tset winTitle to "nowindow"',
        '\t\t\t\t\tend try',
        '\t\t\t\t\tif winTitle contains (item 4 of tri) or winTitle contains (item 5 of tri) then exit repeat',
        '\t\t\t\tend repeat',
        '\t\t\ton error e',
        '\t\t\t\tset winTitle to "error:" & e',
        '\t\t\tend try',
        '\t\t\tset outText to outText & slug & tab & winTitle & linefeed',
        '\t\tend repeat',
        '\tend tell',
        'end tell',
        '-- 还原：⌃⌥Space 走壳自己的 quick_capture（不在看板页 → 页面 pushState 回看板）',
        'set backTitle to "nowindow"',
        'try',
        '\ttell application "System Events" to key code %d using {control down, option down}' % keycode,
        '\trepeat with i from 1 to %d' % tries,
        '\t\tdelay 0.25',
        '\t\ttell application "System Events" to tell process %s' % osa_str(process),
        '\t\t\ttry',
        '\t\t\t\tset backTitle to name of window 1',
        '\t\t\ton error',
        '\t\t\t\tset backTitle to "nowindow"',
        '\t\t\tend try',
        '\t\tend tell',
        '\t\tif backTitle contains %s or backTitle contains %s then exit repeat'
        % (osa_str(BOARD_TITLE_PARTS[0]), osa_str(BOARD_TITLE_PARTS[1])),
        '\tend repeat',
        'end try',
        'try',
        '\ttell application "System Events" to tell application process prevApp to set frontmost to true',
        'end try',
        'return outText & "restored" & tab & backTitle',
    ])


def window_count_script(process: str) -> str:
    """`<process>` 的窗口数；进程不在 → `count:0`（终端还没起也算 0）。"""
    return "\n".join([
        'tell application "System Events"',
        '\ttry',
        '\t\tset n to count of windows of process %s' % osa_str(process),
        '\t\treturn "count:" & (n as text)',
        '\ton error e',
        '\t\treturn "count:0"',
        '\tend try',
        'end tell',
    ])


# ---------------------------------------------------------------------------
# BLOCKED / 结果形状
# ---------------------------------------------------------------------------

def blocked(probe: str, error: str, owner_action: str = OWNER_ACTION) -> dict:
    return {"probe": probe, "blocked": True, "error": error, "owner_action": owner_action}


def attach(out: dict, **fields) -> dict:
    """非空字段才进 JSON（缺席比空串好读：reason / note 只在真有话说时出现）。"""
    for key, value in fields.items():
        if value:
            out[key] = value
    return out


def payload(res: HttpResult) -> dict:
    """HTTP 响应体里的 JSON 对象（不是对象就当空——LLM 之外的输入同样不可信）。"""
    data = res.json
    return data if isinstance(data, dict) else {}


def osa_failure(res: OsaResult) -> "str | None":
    """osascript 这一次调用本身失败（含 AX 未授权 -1719 / -25211）→ 逐字错误串。"""
    if res.rc == 0 and not res.ax_denied:
        return None
    return res.err or res.out or "osascript rc=%d" % res.rc


def ax_gate(env, probe: str) -> "dict | None":
    """起手的辅助功能闸门：非 true / 报错 → BLOCKED dict；正常 → None。"""
    res = env.osascript(AX_ENABLED_SCRIPT)
    failure = osa_failure(res)
    if failure:
        return blocked(probe, failure)
    if res.out.lower() != "true":
        return blocked(probe, "UI elements enabled = %s" % (res.out or "<empty>"))
    return None


def shell_gate(env, probe: str) -> "dict | None":
    """壳没在跑：逐字贴 `pgrep -x` 的（空）输出，不伪装 present。"""
    out = env.pgrep(APP_PROCESS)
    if out:
        return None
    return blocked(probe,
                   "pgrep -x %s -> <empty> (the live shell is not running)" % APP_PROCESS,
                   owner_action="open -a %s.app" % APP_DISPLAY_NAME)


def live_shell_gate(env, probe: str) -> "dict | None":
    """两道前置：辅助功能授权 + 壳在跑。任一不满足 = BLOCKED，不绕行。"""
    return ax_gate(env, probe) or shell_gate(env, probe)


def ax_result_or_blocked(probe: str, res: OsaResult) -> "dict | None":
    failure = osa_failure(res)
    return blocked(probe, failure) if failure else None


# ---------------------------------------------------------------------------
# dock_badge（§15 v0.46 ② / §54.1）
# ---------------------------------------------------------------------------

def lane_count(board: dict, counts: dict, key: str) -> int:
    """一条泳道的卡数：counts 优先（后端口径），其次泳道数组长度（web badgeCount 同款）。"""
    value = counts.get(key)
    if isinstance(value, int):
        return value
    lane = board.get(key)
    return len(lane) if isinstance(lane, list) else 0


def board_badge_expectation(res: HttpResult) -> "tuple[int, str]":
    """`GET /api/board` → (期望徽章数, 备注)。板子拿不到（首份 dashboard.json 还没
    写出来 = 404）→ 期望 0：没有卡就没有徽章。"""
    if res.status != 200 or not isinstance(res.json, dict):
        return 0, "board unavailable (status=%d: %s) — expecting no badge" % (
            res.status, res.message())
    board = res.json
    counts = payload(res).get("counts")
    counts = counts if isinstance(counts, dict) else {}
    return sum(lane_count(board, counts, key) for key in BADGE_LANES), ""


def parse_badge(out: str) -> "tuple[int | None, str, str]":
    """Dock tile 读数 → (徽章数, 原始标签, 问题说明)。`badge:none` = 没徽章 = 0。"""
    if out.startswith("error:"):
        return None, out, "Dock tile not readable: " + out[len("error:"):]
    label = out[len("badge:"):] if out.startswith("badge:") else out
    if label in ("none", ""):
        return 0, label, ""
    if label.isdigit():
        return int(label), label, ""
    return None, label, "badge label %r is not a number" % label


def badge_mismatch(badge: "int | None", expected: int) -> str:
    if badge == expected:
        return ""
    return "Dock badge %s != board count %d" % (badge, expected)


def parse_ls_badge(out: str) -> "tuple[int | None, str, str]":
    """`lsappinfo … StatusLabel` 输出 → (徽章数, 原始标签, 问题说明)。
    `"StatusLabel"={ "label"="42" }` → 42；`kCFNULL` / `[ NULL ]` / 空 = 没徽章 = 0。"""
    if "StatusLabel" not in out:
        return None, out.strip()[:80], "LaunchServices has no StatusLabel (app not running?)"
    match = re.search(r'"label"\s*=\s*"([^"]*)"', out)
    label = match.group(1) if match else "none"
    if label in ("", "none") or "kCFNULL" in out or "[ NULL ]" in out:
        return 0, "none", ""
    if label.isdigit():
        return int(label), label, ""
    return None, label, "badge label %r is not a number" % label


def _ls_badge(env) -> "tuple[int | None, str, str]":
    res = env.lsappinfo(APP_BUNDLE_ID) if hasattr(env, "lsappinfo") else OsaResult(127, "", "no lsappinfo seam")
    if res.rc != 0:
        return None, res.err[:80], "lsappinfo rc=%d: %s" % (res.rc, res.err[:80])
    return parse_ls_badge(res.out)


def _settle_badge(env, badge: "int | None", expected: int) -> "tuple[int | None, str, str]":
    """徽标读到 0 而看板 >0：网页可能刚重拉看板还没 push，隔 BADGE_SETTLE_DELAY 重读几次。"""
    label, problem = ("none", "") if badge == 0 else ("", "")
    for _ in range(BADGE_SETTLE_TRIES):
        if badge != 0 or expected == 0:
            break
        time.sleep(BADGE_SETTLE_DELAY)
        badge, label, problem = _ls_badge(env)
    return badge, label, problem


def probe_dock_badge(env, **_kw) -> dict:
    gate = live_shell_gate(env, "dock_badge")
    if gate:
        return gate
    badge, label, problem = _ls_badge(env)
    source = "LaunchServices StatusLabel (NSDockTile.badgeLabel)"
    if badge is None:                       # LaunchServices 拿不到 → 回落 Dock 的 AX 树
        res = env.osascript(dock_badge_script())
        fail = ax_result_or_blocked("dock_badge", res)
        if fail:
            return fail
        badge, label, problem = parse_badge(res.out)
        source = "AXStatusLabel of the Dock tile (fallback)"
    board = env.http("GET", "/api/board")
    expected, note = board_badge_expectation(board)
    if source.startswith("LaunchServices"):
        badge, label, problem = _settle_badge(env, badge, expected) if badge == 0 else (badge, label, problem)
    out = {"probe": "dock_badge", "present": badge == expected, "badge": badge,
           "badge_raw": label, "expected": expected, "board_status": board.status,
           "source": source}
    return attach(out, note=note, reason=problem or badge_mismatch(badge, expected))


# ---------------------------------------------------------------------------
# hotkey_focus（§68.13）
# ---------------------------------------------------------------------------

def parse_hotkey_output(text: str) -> dict:
    parts = (text.split("\t") + [""] * 6)[:6]
    return {"prev_frontmost": parts[0], "frontmost": parts[1], "role": parts[2],
            "placeholder": parts[3], "description": parts[4], "window_title": parts[5]}


def hotkey_reason(seen: dict, checks: dict) -> str:
    why = []
    if not checks["frontmost_ok"]:
        why.append("frontmost=%r != %s" % (seen["frontmost"], APP_PROCESS))
    if not checks["role_ok"]:
        why.append("focused role=%r not in %s" % (seen["role"], list(TEXT_ROLES)))
    if not checks["field_ok"]:
        why.append("placeholder=%r is not the proposal composer" % seen["placeholder"])
    return "; ".join(why)


def off_board_title(title: str) -> bool:
    """窗口标题说「不在任务台页」。读不到标题（壳没开窗）不算——那是另一种失败。"""
    if not title or title == "nowindow":
        return False
    return not any(part in title for part in BOARD_TITLE_PARTS)


def setup_wizard_gate(env, probe: str, seen: dict, reason: str) -> "dict | None":
    """§68.5 首启向导拦在看板页前面（`GET /api/setup` needed=true，看板页 replaceState
    换到 `?page=setup`）→ 提案 composer 根本不在 DOM 里：这不是壳的快捷键坏了，记
    BLOCKED（前置条件不满足）而不是 MISSING，也不去替 owner 点「先去看板」绕开它。"""
    title = seen.get("window_title", "")
    if not off_board_title(title):
        return None
    if payload(env.http("GET", "/api/setup")).get("needed") is not True:
        return None
    return blocked(
        probe,
        "%s; GET /api/setup needed=true and the window is on %r — the §68.5 setup wizard "
        "replaces the board page, so the proposal composer is not in the DOM" % (reason, title),
        owner_action="在看板窗口的首启向导里点「先去看板（下次再来）」，或让 actd 写出 "
                     "state/dashboard.json，然后重跑 --probe hotkey_focus")


def probe_hotkey_focus(env, **_kw) -> dict:
    gate = live_shell_gate(env, "hotkey_focus")
    if gate:
        return gate
    res = env.osascript(hotkey_focus_script())
    fail = ax_result_or_blocked("hotkey_focus", res)
    if fail:
        return fail
    seen = parse_hotkey_output(res.out)
    checks = {"frontmost_ok": seen["frontmost"] == APP_PROCESS,
              "role_ok": seen["role"] in TEXT_ROLES,
              "field_ok": seen["placeholder"] in COMPOSER_PLACEHOLDERS}
    out = {"probe": "hotkey_focus", "present": all(checks.values()),
           "hotkey": "control-option-space (key code %d)" % HOTKEY_KEYCODE,
           "restored_frontmost": seen["prev_frontmost"]}
    out.update(checks)
    out.update(seen)
    if out["present"]:
        return out
    out["reason"] = hotkey_reason(seen, checks)
    return setup_wizard_gate(env, "hotkey_focus", seen, out["reason"]) or out


# ---------------------------------------------------------------------------
# menu_open_page（§54.4 D40 / §61.6）
# ---------------------------------------------------------------------------

def parse_menu_output(text: str) -> dict:
    seen = {}
    for line in text.splitlines():
        slug, _sep, title = line.partition("\t")
        seen[slug.strip()] = title.strip()
    return seen


def menu_page_verdicts(seen: dict) -> "tuple[dict, list]":
    """每个菜单项 → (页判定表, 没开成的那些)。观测量 = 窗口标题里的页标签。"""
    pages, missing = {}, []
    for slug, _zh_item, _en_item, zh_title, en_title in MENU_PAGES:
        title = seen.get(slug) or "<no output>"
        opened = zh_title in title or en_title in title
        pages[slug] = {"window_title": title, "opened": opened}
        if not opened:
            missing.append("%s -> %r" % (slug, title))
    return pages, missing


def menu_reason(missing: list) -> str:
    if not missing:
        return ""
    return "menu item did not open the page: " + "; ".join(missing)


def probe_menu_open_page(env, **_kw) -> dict:
    gate = live_shell_gate(env, "menu_open_page")
    if gate:
        return gate
    res = env.osascript(menu_open_page_script())
    fail = ax_result_or_blocked("menu_open_page", res)
    if fail:
        return fail
    seen = parse_menu_output(res.out)
    pages, missing = menu_page_verdicts(seen)
    restored = seen.get("restored", "")
    out = {"probe": "menu_open_page", "present": not missing, "pages": pages,
           "observable": "NSWindow title (web document.title via WKWebView.title KVO)",
           "restored_title": restored,
           "restored_board": any(part in restored for part in BOARD_TITLE_PARTS)}
    return attach(out, reason=menu_reason(missing))


# ---------------------------------------------------------------------------
# notify_relay（§28 + §68.7 心跳）
# ---------------------------------------------------------------------------

def queue_entries(env) -> "tuple[list, list]":
    """(`state/notify_queue` 里的 .json 条目, 其中超过 §28 staleAfter 的)。
    `.json.tmp` 是原子写的半成品，两边都不数。"""
    entries = [n for n in env.entries("state", "notify_queue") if n.endswith(".json")]
    stale = [n for n in entries
             if (env.entry_age_s(n, "state", "notify_queue") or 0.0) > NOTIFY_STALE_AFTER_S]
    return entries, stale


def notify_reason(age: "float | None", stale: list) -> str:
    if age is None:
        return "state/shell.heartbeat missing"
    if age > HEARTBEAT_FRESH_S:
        return ("heartbeat is %.1f s old (> %.0f s): the 5 s tick that drains notify_queue "
                "is not running" % (age, HEARTBEAT_FRESH_S))
    if stale:
        return "%d queue entries older than %.0f s are not being drained" % (
            len(stale), NOTIFY_STALE_AFTER_S)
    return ""


def probe_notify_relay(env, **_kw) -> dict:
    """§28 中继的只读观测量：壳 5 s tick（drain + heartbeat）+ 队列没有积压。"""
    age = env.age_s("state", "shell.heartbeat")
    entries, stale = queue_entries(env)
    health = env.http("GET", "/api/health")
    reason = notify_reason(age, stale)
    out = {"probe": "notify_relay", "present": not reason,
           "heartbeat": "state/shell.heartbeat",
           "heartbeat_age_s": None if age is None else round(age, 1),
           "heartbeat_fresh_after_s": HEARTBEAT_FRESH_S,
           "queue": "state/notify_queue", "queue_depth": len(entries),
           "stale_entries": len(stale), "stale_after_s": NOTIFY_STALE_AFTER_S,
           "health_status": health.status}
    return attach(out, reason=reason)


# ---------------------------------------------------------------------------
# terminal_takeover（§68.7，唯一的写动作探针）
# ---------------------------------------------------------------------------

def row_has_session(row: dict) -> bool:
    """投影行有可接管的会话（copy_cmd 优先，其次 session_id；口径逐字照
    server/terminal_launch.py command_for）。"""
    for key in ("copy_cmd", "session_id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def lane_rows(board: dict):
    """板子里所有 (lane, row)——非数组泳道与非字典行都跳过。"""
    for lane, rows in board.items():
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict):
                yield lane, row


def takeover_card_id(res: HttpResult) -> "tuple[str | None, str]":
    """板子里第一张「有会话可接管」的卡。找不到 → (None, 原因)。"""
    if res.status != 200 or not isinstance(res.json, dict):
        return None, "GET /api/board -> %d: %s" % (res.status, res.message())
    for lane, row in lane_rows(res.json):
        if isinstance(row.get("id"), str) and row_has_session(row):
            return row["id"], "lane=%s" % lane
    return None, "no board card has copy_cmd or session_id to take over"


def parse_count(out: str) -> int:
    return int(out[len("count:"):]) if out.startswith("count:") else 0


def window_count(env, terminal: str) -> "tuple[int | None, dict | None]":
    """(终端窗口数, BLOCKED dict)——AX 报错时数字是 None。"""
    res = env.osascript(window_count_script(terminal))
    fail = ax_result_or_blocked("terminal_takeover", res)
    if fail:
        return None, fail
    return parse_count(res.out), None


def wait_for_window(env, terminal: str, n_before: int, wait_s: float, sleep) -> int:
    """≤ wait_s 秒内轮询终端窗口数，一涨就返回（壳 TerminalRelay 是 1 s 节拍）。"""
    remaining = wait_s
    n_after = n_before
    while remaining > 0:
        sleep(0.5)
        remaining -= 0.5
        counted, _fail = window_count(env, terminal)
        n_after = n_after if counted is None else counted
        if n_after > n_before:
            break
    return n_after


def takeover_reason(present: bool, terminal: str, n_before: int, wait_s: float) -> str:
    if present:
        return ""
    return ("%s window count stayed at %d for %.0f s — the shell relay did not open a "
            "terminal" % (terminal, n_before, wait_s))


def probe_terminal_takeover(env, allow_enqueue: bool = False,
                            terminal: str = DEFAULT_TERMINAL_PROCESS,
                            wait_s: float = 10.0, sleep=time.sleep, **_kw) -> dict:
    if not allow_enqueue:
        return {"probe": "terminal_takeover", "skipped": True,
                "reason": "write probe: pass --allow-enqueue to run it"}
    gate = live_shell_gate(env, "terminal_takeover")
    if gate:
        return gate
    card_id, why = takeover_card_id(env.http("GET", "/api/board"))
    if card_id is None:
        return blocked("terminal_takeover", why,
                       owner_action="让看板上出现一张带会话的卡（§68.7 的命令由 server "
                                    "从投影行推导，探针不能注入命令文本）")
    n_before, fail = window_count(env, terminal)
    if fail:
        return fail
    post = env.http("POST", "/api/terminal", {"card_id": card_id})
    out = {"probe": "terminal_takeover", "present": False, "card_id": card_id,
           "card_note": why, "post_status": post.status, "terminal": terminal,
           "windows_before": n_before}
    if post.status != 200:
        out["reason"] = "POST /api/terminal -> %d: %s" % (post.status, post.message())
        return out
    out["queue_id"] = payload(post).get("queue_id")
    n_after = wait_for_window(env, terminal, n_before, wait_s, sleep)
    out["windows_after"] = n_after
    out["present"] = n_after > n_before
    return attach(out, reason=takeover_reason(out["present"], terminal, n_before, wait_s))


PROBE_FUNCS = {
    "dock_badge": probe_dock_badge,
    "hotkey_focus": probe_hotkey_focus,
    "menu_open_page": probe_menu_open_page,
    "notify_relay": probe_notify_relay,
    "terminal_takeover": probe_terminal_takeover,
}


def run_probe(env, probe: str, **kwargs) -> dict:
    return PROBE_FUNCS[probe](env, **kwargs)


def classify(result: dict) -> str:
    """一条探针结果 → PRESENT / MISSING / BLOCKED / SKIPPED。"""
    if result.get("blocked"):
        return "BLOCKED"
    if result.get("skipped"):
        return "SKIPPED"
    return "PRESENT" if result.get("present") else "MISSING"


def summary_line(results: "list[dict]") -> str:
    """goal 逐字规定的一行：`SHELL probes=<k> present=<k>`（k = 执行了的探针）。"""
    ran = [r for r in results if not r.get("skipped")]
    present = [r for r in ran if r.get("present")]
    return "SHELL probes=%d present=%d" % (len(ran), len(present))


def exit_code(results: "list[dict]") -> int:
    kinds = [classify(r) for r in results]
    if "BLOCKED" in kinds:
        return EXIT_BLOCKED
    return EXIT_MISSING if "MISSING" in kinds else EXIT_OK


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="壳 UI 的 AX 探针（axprobe: 证据）")
    p.add_argument("--list", action="store_true", help="列出探针 id")
    p.add_argument("--probe", metavar="ID", help="跑一个探针（id 见 --list）")
    p.add_argument("--summary", action="store_true",
                   help="跑所有适用探针，末行 `SHELL probes=<k> present=<k>`")
    p.add_argument("--json", action="store_true", help="机器可读 JSON（--probe 默认即是）")
    p.add_argument("--allow-enqueue", action="store_true",
                   help="允许写动作探针（terminal_takeover 会 POST /api/terminal）")
    p.add_argument("--home", help="AIASSISTANT_HOME（默认 env，其次带 state/server.token 的 checkout）")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="看板 server 端口")
    p.add_argument("--terminal", default=DEFAULT_TERMINAL_PROCESS, help="终端进程名")
    return p


def selected_probes(args) -> "list[str]":
    """这次要跑哪些：--probe 指名一个；--summary 跑只读四个（+ --allow-enqueue 第五个）。"""
    if args.probe:
        return [args.probe]
    wanted = list(SUMMARY_PROBES)
    if args.allow_enqueue:
        wanted.append("terminal_takeover")
    return wanted


def usage_error(args, out) -> "int | None":
    """用法错：没给模式 / 不认识的探针 id。"""
    if not args.probe and not args.summary:
        build_parser().print_usage(out)
        return EXIT_USAGE
    if args.probe and args.probe not in PROBE_FUNCS:
        out.write("unknown probe: %s (see --list)\n" % args.probe)
        return EXIT_USAGE
    return None


def main(argv=None, env=None, out=sys.stdout) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        out.write("".join(pid + "\n" for pid in PROBE_IDS))
        return EXIT_OK
    usage = usage_error(args, out)
    if usage is not None:
        return usage
    if env is None:
        env = LiveEnv(resolve_home(args.home), args.port)
    kwargs = {"allow_enqueue": args.allow_enqueue, "terminal": args.terminal}
    results = []
    for pid in selected_probes(args):
        result = run_probe(env, pid, **kwargs)
        results.append(result)
        out.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    if args.summary:
        out.write(summary_line(results) + "\n")
    return exit_code(results)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
