"""server/setup.py — 首次运行向导的 server 半边（§15 v0.14 初始设置向导 → §68.5 web 版）。

owner（2026-09-02）：「我能够在另一台电脑上起一个空白环境……就能够直接使用。」
新机器上 ``bash install.sh`` 之后看板打开的第一页不该是空看板，而是一条向导：
config.yaml（从 config.example.yaml 复制）→ 完全磁盘访问（权限体检）→ 可选的
Gmail / Slack 凭证 → 完成。

- ``GET /api/setup`` → ``{"needed": bool, "done": bool, "config_exists", "config_example_exists",
  "secrets": {name: present}, "home", "protected_location"}``。
  ``needed`` = 未写完成标记 ∧（config.yaml 缺席 ∨ 三把主凭证一把都没有）。
- ``POST /api/setup/config-from-example`` → 复制 ``config.example.yaml`` → ``config.yaml``
  （**已存在则 409，绝不覆盖**——install.sh 步 2 同一条纪律）。
- ``POST /api/setup/complete`` → 写 ``state/setup_done.json`` ``{"completed_at": iso}``
  （原生 UserDefaults ``setupWizardCompleted`` 的 server 侧替身：标记在 home 下，
  换壳 / 换浏览器不重问）。``POST /api/setup/reset`` 删标记 = 「重新运行初始设置」。
- ``GET /api/setup/engine`` → ``{"cli_path", "version", "auth", "auth_sources": {oauth, env_key,
  secrets_file, legacy_file}, "ready"}``——原生 SetupWizard ``EngineDetector`` 的 server 半边
  （向导第 2 步「接入 AI 引擎」与末步「AI 引擎」行）：claude CLI 路径按 §55 第五幕的解析顺序
  （pin → 稳定副本 → ~/.local/bin → PATH），版本 = ``claude --version`` 首行；认证梯子与
  headless claude 实际用的一致——Claude Code 登录（钥匙串条目存在 / ``~/.claude/.credentials.json``
  非空；钥匙串探针只问「有没有」，永不 ``-w`` 读值）→ ``ANTHROPIC_API_KEY`` 环境变量 → §19
  ``config/secrets/anthropic-api-key.txt`` → 旧路径 ``~/.config/anthropic-key.txt``；``auth`` = 梯子上
  第一个命中的；``ready`` = CLI 在且任一认证在。子进程经 ``runner`` 注入缝（判例零 subprocess）。
- ``POST /api/setup/seed-dashboard {}`` → 跑一次 ``python -m act.lib.dashboard``（doctor 文档里
  「首次数据」的修法；原生 ``PipelineProbeModel.seedDashboard``），经 server/subproc；``{"ok", "rc"}``，
  失败 ``ok:false`` + ``error`` 尾巴（不 500——页面把它接在「生成失败: 」后面）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Optional

from server import paths, permissions, secrets_store, subproc
from server.errors import ConflictError, NotFoundError, UnknownFieldError

_PRIMARY_SECRETS = ("anthropic-api-key.txt", "slack-user-token.txt", "gmail-app-password.txt")

# 认证梯子（顺序 = 原生 EngineDetector.detectAuth；wire 词表，web 逐字镜像）
AUTH_LADDER = ("oauth", "env_key", "secrets_file", "legacy_file")
_KEYCHAIN_SERVICES = ("Claude Code-credentials", "Claude Code")
_ENGINE_TIMEOUT_S = 15
_SEED_TIMEOUT_S = 90

EngineRunner = Callable[[list, int], "tuple[int, str]"]


def _example_path(home: Path) -> Path:
    return home / "config.example.yaml"


def done(home: Path) -> bool:
    return paths.setup_done_path(home).is_file()


def snapshot(home: Path) -> dict:
    """``GET /api/setup``。"""
    config_exists = paths.config_path(home).is_file()
    secrets = {name: secrets_store.read_value(home, name) is not None for name in _PRIMARY_SECRETS}
    is_done = done(home)
    return {
        "needed": (not is_done) and (not config_exists or not any(secrets.values())),
        "done": is_done,
        "config_exists": config_exists,
        "config_example_exists": _example_path(home).is_file(),
        "secrets": secrets,
        "home": str(home),
        "protected_location": permissions.protected_location(home),
    }


def _reject_fields(payload: dict) -> None:
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})


def config_from_example(home: Path, payload: dict) -> dict:
    """``POST /api/setup/config-from-example``：复制模板；已存在 → 409 CONFLICT。"""
    _reject_fields(payload)
    target = paths.config_path(home)
    if target.exists():
        raise ConflictError("config.yaml already exists - not overwriting", {"path": str(target)})
    example = _example_path(home)
    if not example.is_file():
        raise NotFoundError("config.example.yaml not found", {"path": str(example)})
    tmp = target.with_suffix(".yaml.tmp")
    shutil.copyfile(example, tmp)
    os.replace(tmp, target)
    return {"ok": True, "path": str(target), "setup": snapshot(home)}


def complete(home: Path, payload: dict) -> dict:
    """``POST /api/setup/complete``：写完成标记（幂等）。"""
    _reject_fields(payload)
    p = paths.setup_done_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({"completed_at": stamp}) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return {"ok": True, "setup": snapshot(home)}


def reset(home: Path, payload: dict) -> dict:
    """``POST /api/setup/reset``：删完成标记（设置 → 通用「重新运行初始设置」）。"""
    _reject_fields(payload)
    try:
        paths.setup_done_path(home).unlink()
    except FileNotFoundError:
        pass
    return {"ok": True, "setup": snapshot(home)}


# --------------------------------------------------------------------------- #
# AI 引擎检测（§68.5；原生 SetupWizard.swift EngineDetector 的 server 半边）
# --------------------------------------------------------------------------- #
def _engine_runner(argv: list, timeout_s: int) -> "tuple[int, str]":
    """真跑一条探针命令（which / --version / security）；超时与 OSError 都变成非零 rc（server/subproc 同款）。"""
    rc, out, err = subproc.default_runner(argv, dict(os.environ), Path.cwd(), timeout_s)
    return rc, out + err


def _nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and bool(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def _keychain_login(run: EngineRunner, platform: str) -> bool:
    """Claude Code 的钥匙串登录条目在不在——只问存在（不带 ``-w``，永不读出密文）。"""
    if platform != "darwin":
        return False
    return any(run(["/usr/bin/security", "find-generic-password", "-s", service], _ENGINE_TIMEOUT_S)[0] == 0
               for service in _KEYCHAIN_SERVICES)


def _claude_version(run: EngineRunner, cli: Optional[str]) -> Optional[str]:
    if not cli:
        return None
    rc, out = run([cli, "--version"], _ENGINE_TIMEOUT_S)
    lines = (out or "").strip().splitlines()
    return lines[0][:40] if rc == 0 and lines else None


def auth_sources(home: Path, run: EngineRunner, platform: str, env: dict) -> dict:
    """梯子四档各自在不在（wire：``auth_sources``；顺序 = AUTH_LADDER）。"""
    user = Path.home()
    return {
        "oauth": _keychain_login(run, platform) or _nonempty_file(user / ".claude" / ".credentials.json"),
        "env_key": bool(str(env.get("ANTHROPIC_API_KEY") or "").strip()),
        "secrets_file": secrets_store.read_value(home, "anthropic-api-key.txt") is not None,
        "legacy_file": _nonempty_file(user / ".config" / "anthropic-key.txt"),
    }


def engine_snapshot(home: Path, runner: Optional[EngineRunner] = None,
                    platform: Optional[str] = None, env: Optional[dict] = None) -> dict:
    """``GET /api/setup/engine``。"""
    run = runner or _engine_runner
    cli = permissions.claude_bin(home)
    sources = auth_sources(home, run, platform or sys.platform, os.environ if env is None else env)
    auth = next((k for k in AUTH_LADDER if sources[k]), None)
    return {
        "cli_path": cli,
        "version": _claude_version(run, cli),
        "auth": auth,
        "auth_sources": sources,
        "ready": bool(cli and auth),
    }


def seed_dashboard(home: Path, payload: dict, runner=None) -> dict:
    """``POST /api/setup/seed-dashboard {}``：``python -m act.lib.dashboard`` 一次（首次数据）。"""
    _reject_fields(payload)
    rc, out, err = subproc.run_module(home, "act.lib.dashboard", [], timeout_s=_SEED_TIMEOUT_S, runner=runner)
    if rc != 0:
        return {"ok": False, "rc": rc,
                "error": subproc.tail(err or out) or "act.lib.dashboard exited %d" % rc}
    return {"ok": True, "rc": 0}
