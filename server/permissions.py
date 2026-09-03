"""server/permissions.py — 「权限体检」页的 server 半边（§15 v0.13 / §55 / §68.3）。

macOS TCC 按**可执行文件**记账（§55 三幕 + D20）：launchd 会话里的守护 python、
claude CLI、构建 UI 的 node 各自都要一次「完全磁盘访问」才能读外置卷上的 repo；
壳（GUI）另有屏幕录制 / 麦克风 / 通知三项。原生 Permissions.swift 只探 GUI 那三项
（那部分留在壳里，经 §61 桥回报）；这里补上 D20 家族缺的一半：

``GET /api/permissions`` →
    {"home", "on_external_volume",
     "fda": {"needed": bool, "pane": "<x-apple.systempreferences URL>",
             "executables": [{"role", "path", "realpath", "exists", "note": {zh,en}}]},
     "panes": {"screen", "microphone", "notifications", "full_disk", "files_folders"},
     "doctor": [<doctor rows whose failure_id / name is TCC-shaped>],
     "doctor_ran_at": iso|null,
     "vault": {"status": "granted"|"unknown", "root": "<vault root>"}}

路径全部是**可复制的绝对路径**（系统设置里点 + → ⌘⇧G → 粘贴）。doctor 行来自
server/doctor_run（``--fast``；缓存 15 s），只挑 TCC 相关的：``launchd claude`` /
``launchd volume access`` / ``cron write access`` / ``cron ingest chain`` /
``cron disk access`` / ``board ui build`` / ``launchd paths``（以及任何 failure_id 属 TCC
词表的行）。``vault`` = 原生 PermissionsModel 的**被动**笔记库（Documents）探针：只看
``state/vault_sync_mode`` 是否为 ``mirror``（ingest 链经壳身份 courier 拉成功过 = 授权确实
生效）；server 永不去读 ~/Documents（那一读会在壳之外触发一次性 TCC 弹窗）——主动请求
是壳的活（桥 ``requestPermission {kind:"vault"}``，§68.13）。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Optional

from server import doctor_run, paths, settings_catalog

SHELL_APP_PATH = "/Applications/Zelin's AI Assistant.app"   # §54 名字互换后的产品路径（act.lib.version.BOARD_APP_NAME）
_SYS_PREFS = "x-apple.systempreferences:com.apple.preference.security?Privacy_"
PANES = {
    "full_disk": _SYS_PREFS + "AllFiles",
    "screen": _SYS_PREFS + "ScreenCapture",
    "microphone": _SYS_PREFS + "Microphone",
    "notifications": "x-apple.systempreferences:com.apple.preference.notifications",
    # 笔记库（Documents）授权被拒后的第二次机会（原生 requestVaultAccess 的深链）
    "files_folders": _SYS_PREFS + "FilesAndFolders",
}
_DEFAULT_OBSIDIAN_RAW = "~/Documents/Obsidian Vault/2 - raw"

# §25 里凡是「TCC 挡住了」的失败 id
TCC_FAILURE_IDS = frozenset({
    "claude_blind", "deploy_blind_tcc", "cron_tcc_blocked", "cron_fda_blocked",
    "ui_build_tcc_blocked", "interpreter_blind", "screen_tcc_lost",
})
TCC_ROW_NAMES = frozenset({
    "launchd claude", "launchd volume access", "cron write access",
    "cron ingest chain", "cron disk access", "board ui build", "launchd paths",
})


def _daemon_python(home: Path) -> Optional[str]:
    try:
        doc = json.loads(paths.runtime_json_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    py = doc.get("python") if isinstance(doc, dict) else None
    return py if isinstance(py, str) and py.startswith("/") else None


# §55 第五幕 mirror of act/lib/config.stable_claude_bin(): the stable daemon
# copy install.sh maintains at a fixed $HOME path — the one claude path whose
# Full Disk Access grant survives Claude Code updates. Same env override as the
# pipeline (`AIASSISTANT_STABLE_CLAUDE`) so both sides agree; drift-pinned by
# tests/test_server_permissions_setup.py.
def stable_claude_bin() -> Path:
    override = os.environ.get("AIASSISTANT_STABLE_CLAUDE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "ZelinAIAssistant" / "bin" / "claude"


def claude_bin(home: Path) -> Optional[str]:
    """Resolution order = act/lib/config.resolve_claude_bin (§55 第五幕):
    pin → stable copy → ~/.local/bin → PATH."""
    execution = settings_catalog.load_config_doc(home).get("execution")
    pinned = execution.get("claude_bin") if isinstance(execution, dict) else None
    if isinstance(pinned, str) and pinned.strip():
        return os.path.expanduser(pinned.strip())
    stable = stable_claude_bin()
    if stable.exists():
        return str(stable)
    local = Path.home() / ".local" / "bin" / "claude"
    if local.exists():
        return str(local)
    return shutil.which("claude")


def _claude_note(path: Optional[str]) -> tuple:
    if path and path == str(stable_claude_bin()):
        return ("claude CLI 的稳定副本（install.sh 在此路径原地刷新，授权一次即可，claude 更新不再失效——CONTRACT §55 第五幕）",
                "Stable daemon copy of claude (install.sh refreshes it in place, so this grant survives Claude Code updates — CONTRACT §55)")
    return ("claude CLI（派工 agent 与管线判断；这是按版本换路径的二进制——跑一次 bash install.sh 生成稳定副本后授权只需做一次）",
            "claude CLI (dispatch agents + pipeline judgment; this path moves with every update — run bash install.sh once to create the stable copy so the grant is one-time)")


def _entry(role: str, path: Optional[str], zh: str, en: str) -> dict:
    exists = bool(path) and os.path.exists(path)
    real = os.path.realpath(path) if path and exists else path
    return {"role": role, "path": path, "realpath": real, "exists": exists,
            "note": {"zh": zh, "en": en}}


def executables(home: Path) -> list:
    """需要「完全磁盘访问」的可执行文件清单（路径可复制；不存在的如实标 exists:false）。"""
    claude = claude_bin(home)
    return [
        _entry("daemon_python", _daemon_python(home),
               "守护进程解释器（actd / 雷达 / server / 自动部署都用它；launchd 会话里没有终端的授权可借）",
               "Daemon interpreter (actd / radars / server / auto-deploy all run on it; a launchd session borrows no terminal grant)"),
        _entry("claude", claude, *_claude_note(claude)),
        _entry("node", shutil.which("node"),
               "node（自动部署构建看板 UI；缺授权时 install.sh 的 ui 步记 skipped_tcc）",
               "node (auto-deploy builds the board UI; without the grant install.sh records ui=skipped_tcc)"),
        _entry("shell_app", SHELL_APP_PATH,
               "看板 app（屏幕录制 / 麦克风 / 通知三项按它的 bundle id 记账，用下方三个按钮授权）",
               "Board app (Screen Recording / Microphone / Notifications key on its bundle id — grant via the three buttons below)"),
    ]


def _is_tcc_row(row: dict) -> bool:
    return (str(row.get("failure_id") or "") in TCC_FAILURE_IDS
            or str(row.get("name") or "") in TCC_ROW_NAMES)


def tcc_rows(report: dict) -> list:
    return [r for r in report.get("checks", []) if isinstance(r, dict) and _is_tcc_row(r)]


def protected_location(home: Path) -> bool:
    """repo 是否住在 TCC 保护的位置：可移动卷（§55 三幕的真因）或
    ~/Documents / ~/Desktop / ~/Downloads（macOS 对这三处同样按进程记账）。"""
    text = str(home)
    if text.startswith("/Volumes/"):
        return True
    user = str(Path.home())
    return any(text.startswith(user + "/" + d + "/") or text == user + "/" + d
               for d in ("Documents", "Desktop", "Downloads"))


def vault_root(home: Path) -> str:
    """笔记库根 = 生效 ``obsidian_raw``（override → config.yaml → 默认）的父目录——
    与原生 PermissionsModel.vaultRootPath 同一解析。"""
    field = settings_catalog.field_index(settings_catalog.lookup("obsidian")).get("obsidian_raw")
    raw = None
    if field is not None:
        try:
            overrides = settings_catalog.read_overrides(home)
        except Exception:  # noqa: BLE001 - 坏 overrides 不该让权限页 409；退回 config / 默认层
            overrides = {}
        raw, _src = settings_catalog.effective(field, overrides, settings_catalog.load_config_doc(home))
    raw = raw if isinstance(raw, str) and raw.strip() else _DEFAULT_OBSIDIAN_RAW
    return os.path.dirname(os.path.expanduser(raw.strip()))


def vault_status(home: Path) -> str:
    """被动探针：``state/vault_sync_mode`` == ``mirror`` → granted；否则 unknown（不读 ~/Documents）。"""
    try:
        mode = (home / "state" / "vault_sync_mode").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return "granted" if mode == "mirror" else "unknown"


def snapshot(home: Path, *, refresh: bool = False, runner=None) -> dict:
    """``GET /api/permissions``。"""
    report = doctor_run.report(home, fast=True, refresh=refresh, runner=runner)
    external = str(home).startswith("/Volumes/")
    return {
        "home": str(home),
        "on_external_volume": external,
        "fda": {"needed": protected_location(home), "pane": PANES["full_disk"],
                "executables": executables(home)},
        "panes": dict(PANES),
        "doctor": tcc_rows(report),
        "doctor_ran_at": report.get("ran_at"),
        "doctor_ok": bool(report.get("ok")),
        "vault": {"status": vault_status(home), "root": vault_root(home)},
    }
