"""全覆盖跑者的沙箱：/tmp 临时 HOME + PATH 前缀假货（CONTRACT §58 QA 闸门、§77.7 沙箱纪律）。

`scripts/qa/coverage_run.py` 的 install / uninstall / doctor / actd 类子进程一律跑在
这里造的沙箱里：HOME 在 /tmp（收尾整棵删），`launchctl` `open` `osascript` `crontab`
`claude` `pgrep` `pkill` 一律 PATH 前缀假货——真 gui domain / 真 crontab / owner 的壳
一个字节都不碰。其中两只假货**有状态**（台账只落在沙箱 HOME 里）：

- `crontab`：`crontab <file>` / `crontab -` 存内容、`crontab -l` 读回（没台账时 exit 1），
  install.sh 写下的 §18 ingest 行 doctor 才看得见；
- `launchctl`：`bootstrap` / `load` 记 label、`bootout` / `unload` 删，`list` 按真货的
  三列打印（PID / Status / Label），doctor 的 agent 行才问得出「装载了没」。
"""

import os
import shutil
import tempfile

_SHIM_BODY = """#!/bin/sh
# QA shim (scripts/qa/coverage_sandbox.py)：只记 argv，绝不碰真 gui domain
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$ZAA_SHIM_LOG"
exit 0
"""

# stub `claude`：executor 的注入缝之外的兜底（brief §3 flow:card_lifecycle）——
# 打印一行 canned 结果，永不联网、永不真跑 agent。
_CLAUDE_STUB = """#!/bin/sh
printf '%s %s\\n' claude "$*" >> "$ZAA_SHIM_LOG"
echo '{"result":"qa coverage stub","is_error":false}'
exit 0
"""

# `pgrep` / `pkill` 也必须是假货：install.sh 用 `pgrep -x ZelinAIBoard` 决定要不要杀 + 重开
# owner 正在跑的壳，uninstall.sh 直接 `pkill -TERM -x ZelinAIBoard` / `pkill -f screenpipe`——
# 这两条都不看 HOME。假货一律「没找到」（exit 1），install.sh 就走「壳没在跑」的分支。
# 2026-09-15 实测：没有这两只假货，第一轮全量跑把 live 壳杀了两次。
_ABSENT_BODY = """#!/bin/sh
# QA shim (scripts/qa/coverage_sandbox.py)：只记 argv，恒「没匹配到进程」——绝不碰 owner 的壳 / 引擎
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$ZAA_SHIM_LOG"
exit 1
"""

# `crontab` 的假货必须**有状态**：install.sh 写下的 §18 ingest 行，doctor 的
# `crontab -l` 要能原样读回来（只记 argv 的假货让 `cron ingest chain` 恒 FAIL——
# 一个与被测行为无关的红）。台账只落在沙箱 HOME 里，真 crontab 一个字节都不碰；
# 没有台账时 `-l` 照真货的样子 exit 1。
_CRONTAB_BODY = """#!/bin/sh
# QA shim (scripts/qa/coverage_sandbox.py)：沙箱 HOME 内的 crontab 台账，绝不碰真 crontab
printf '%s %s\\n' crontab "$*" >> "$ZAA_SHIM_LOG"
STORE="$HOME/.qa-crontab.tab"
case "${1:-}" in
    -l) [ -f "$STORE" ] || { echo "crontab: no crontab for qa-sandbox" >&2; exit 1; }
        cat "$STORE" ;;
    -r) rm -f "$STORE" ;;
    -)  cat > "$STORE" ;;
    *)  [ -f "$1" ] || { echo "crontab: cannot read $1" >&2; exit 1; }
        cat "$1" > "$STORE" ;;
esac
exit 0
"""

# `launchctl` 的假货同样有状态：install.sh 真的 bootstrap 了那几个 label（进的是
# 沙箱 HOME 里的 LaunchAgents），doctor 的 `actd` 行读的却是 `launchctl list`——
# 只记 argv 的假货让它恒报「not registered」。台账 = 沙箱 HOME 里的 label 清单，
# `list` 按真货的三列（PID / Status / Label）打印「已装载、上次退出 0」。
# 真 gui domain 一个字节都不碰（§77.7 沙箱纪律）。
_LAUNCHCTL_BODY = """#!/bin/sh
# QA shim (scripts/qa/coverage_sandbox.py)：沙箱 HOME 内的 launchd 台账，绝不碰真 gui domain
printf '%s %s\\n' launchctl "$*" >> "$ZAA_SHIM_LOG"
STORE="$HOME/.qa-launchd.labels"
[ -f "$STORE" ] || : > "$STORE"
verb="${1:-}"
last=""
for arg in "$@"; do last="$arg"; done
case "$verb" in
    bootstrap|load)
        case "$last" in
            *.plist) label="$(basename "$last" .plist)"
                     grep -Fxq "$label" "$STORE" || printf '%s\\n' "$label" >> "$STORE" ;;
        esac ;;
    bootout|unload)
        case "$last" in
            *.plist) label="$(basename "$last" .plist)" ;;
            *) label="${last##*/}" ;;
        esac
        grep -Fxv "$label" "$STORE" > "$STORE.next" 2>/dev/null
        mv "$STORE.next" "$STORE" ;;
    list)
        printf 'PID\\tStatus\\tLabel\\n'
        while IFS= read -r label; do
            [ -n "$label" ] && printf -- '-\\t0\\t%s\\n' "$label"
        done < "$STORE" ;;
esac
exit 0
"""

SHIMMED = ("launchctl", "open", "osascript", "crontab", "claude", "pgrep", "pkill")
ABSENT_SHIMS = ("pgrep", "pkill")
# 名字 → 专用 body（其余走 _SHIM_BODY / ABSENT_SHIMS 的 _ABSENT_BODY）
SHIM_BODIES = {"claude": _CLAUDE_STUB, "crontab": _CRONTAB_BODY,
               "launchctl": _LAUNCHCTL_BODY}


class TempHomes:
    """/tmp 下的临时 HOME 台账（收尾一并删除；只删 /tmp 下的路径）。"""

    def __init__(self):
        self.paths = []

    def make(self, slug):
        path = tempfile.mkdtemp(prefix="zaa-cov-%s-" % slug, dir="/tmp")
        self.paths.append(path)
        return path

    def cleanup(self):
        for path in list(self.paths):
            if path.startswith("/tmp/"):
                shutil.rmtree(path, ignore_errors=True)
            self.paths.remove(path)


def write_shims(home, names=SHIMMED):
    """<home>/.shims 里放假 launchctl/open/osascript/crontab/claude/pgrep/pkill；返回 (dir, log)。"""
    shim_dir = os.path.join(home, ".shims")
    os.makedirs(shim_dir, exist_ok=True)
    shim_log = os.path.join(home, "shims.log")
    for name in names:
        path = os.path.join(shim_dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(shim_body(name))
        os.chmod(path, 0o755)
    return shim_dir, shim_log


def shim_body(name):
    """假货脚本的正文：专用 body 优先，其次「恒没匹配到」，默认只记 argv。"""
    if name in SHIM_BODIES:
        return SHIM_BODIES[name]
    return _ABSENT_BODY if name in ABSENT_SHIMS else _SHIM_BODY


def shim_env(home, shim_dir, shim_log, extra=None):
    """HOME=临时目录 + PATH 前缀假货 + 去掉 node/npm（install.sh 的 UI 步会自己跳过）。

    `AIASSISTANT_UI_APPS_DIR` 指向临时 HOME 下的 Applications/：install.sh / uninstall.sh 对
    壳 bundle 的安装与删除都只认这个 seam（默认 /Applications 是 owner 的真 app——
    2026-09-15 第一轮全量跑没设它，把 live bundle 删了又装回一个 dev 构建，TCC 授权随
    cdhash 一起丢）。
    """
    env = {
        "HOME": home,
        "AIASSISTANT_UI_APPS_DIR": os.path.join(home, "Applications"),
        "PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % shim_dir,
        # `python3 -m …` 的 PYTHONPATH 由各调用点给；这里只保证 HOME/PATH 两条红线

        "ZAA_SHIM_LOG": shim_log,
        "ZAI_NO_OPEN": "1",
    }
    if extra:
        env.update(extra)
    return env

