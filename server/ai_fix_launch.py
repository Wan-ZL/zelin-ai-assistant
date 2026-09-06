"""「让 AI 修」的 web 侧入口：``POST /api/ai-fix {card_id, lang?}`` 或 ``{source: "doctor", lang?}``。

原生看板的 让 AI 修（mac/Sources/Doctor.swift ``AIFix.launch``）不是 inbox
动作——它在本机起 ``python3 -m act.ai_fix --open --context-file <f>``：生成
带诊断包的 ``.command`` 文件并在 Terminal.app 打开一个交互式 claude 修复
会话。本模块是同一条命令的 server 落点，web 卡片上的按钮打到这里。

安全纪律（对齐 ``server/files.py reveal``）：
- 客户端只给 ``card_id``（SAFE_ID 白名单）或 ``source: "doctor"``（§54.4 依赖检查页的
  「让 AI 修」= 原生 DepsView 同名按钮）；**上下文文本由 server 推导**（该卡的 ``last_error`` /
  ``dispatch_error``，或 doctor ``--fast`` 报告里的 FAIL / WARN 行），绝不接受客户端原始文本
  进修复 prompt；
- 子进程 = ``sys.executable -m act.ai_fix``（server 不 import ``act.ai_fix``
  ——它是 entrypoint，不在 server 允许的 act.lib 层；依赖方向门 §58.4）；
  cwd 与 ``AIASSISTANT_HOME`` 都是 server 的 home；``lang`` 只许 zh / en，
  经 ``AIASSISTANT_UI_LANG`` 传给 python 侧（§15 文案随 UI 语言）；
- 非 darwin → 501（``.command`` 只有 Terminal.app 会执行；reveal 同款）；
  config.yaml ``doctor.ai_fix_enabled: false`` 时 act.ai_fix 退出码 2 → 501，
  整句人话原样转出；其它非零 → 500 带输出尾巴（原生显示 out.suffix(300)）。
- ``runner`` 是测试注入缝——测试绝不真起子进程（仓规：绝不 spawn 真 claude）。

契约：docs/CONTRACT.md §49（路由表）、§54（web 看板 parity：让 AI 修）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

from server.board_source import SAFE_ID_RE, locate_card
from server.errors import (ApiError, InvalidFieldError, NotFoundError,
                           NotImplementedError501, UnknownFieldError)

Runner = Callable[[list, dict, Path], "tuple[int, str]"]

_ALLOWED_FIELDS = frozenset({"card_id", "lang", "source"})
_SOURCES = frozenset({"doctor"})
_LANGS = frozenset({"zh", "en"})
_TIMEOUT_S = 120        # doctor --fast + 写文件 + open；原生无超时，这里兜底
_OUTPUT_TAIL = 300      # 原生 AIFix.launch 的 String(out.suffix(300))
_DISABLED_RC = 2        # act.ai_fix.main：doctor.ai_fix_enabled=false 的退出码
_DISABLED_MSG = "Fix with AI is disabled in config.yaml (doctor.ai_fix_enabled: false)"


def _default_runner(argv: list, env: dict, cwd: Path) -> "tuple[int, str]":
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=env, check=False,
                              capture_output=True, text=True, timeout=_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, "ai_fix timed out after %ds" % _TIMEOUT_S
    except OSError as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _valid_id(value) -> bool:
    return isinstance(value, str) and bool(SAFE_ID_RE.match(value))


def _validate_target(payload: dict) -> Optional[str]:
    """``card_id``（SAFE_ID）或 ``source: "doctor"`` 二选一；返回 card_id（doctor 时 None）。"""
    source = payload.get("source")
    card_id = payload.get("card_id")
    if source is None:
        if not _valid_id(card_id):
            raise InvalidFieldError("card_id must be a card id", {"id": card_id})
        return card_id
    if source not in _SOURCES:
        raise InvalidFieldError("source must be doctor", {"source": source})
    if card_id is not None:
        raise InvalidFieldError("give either card_id or source, not both", {"id": card_id})
    return None


def _validate_lang(payload: dict) -> Optional[str]:
    lang = payload.get("lang")
    if lang is not None and lang not in _LANGS:
        raise InvalidFieldError("lang must be zh or en", {"lang": lang})
    return lang


def _validate(payload: dict) -> "tuple[Optional[str], Optional[str]]":
    """返回 (card_id, lang)；``source: "doctor"`` 时 card_id 为 None（上下文来自 doctor 报告）。"""
    unknown = set(payload) - _ALLOWED_FIELDS
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    return _validate_target(payload), _validate_lang(payload)


def _first_text(row: dict, keys: tuple) -> Optional[str]:
    """按顺序取第一个非空字符串字段。"""
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def context_for(home: Path, card_id: str) -> str:
    """修复会话的上下文 = 该卡投影行上的错误文本（原生传 prefix + raw）；
    没有错误文本时退到卡名——让 claude 至少知道用户在看哪张卡。"""
    lane, row = locate_card(home, card_id)
    if row is None:
        raise NotFoundError("card not found", {"id": card_id})
    error = _first_text(row, ("last_error", "dispatch_error"))
    name = _first_text(row, ("display_title", "name", "title")) or card_id
    head = "card %s (%s lane): %s" % (card_id, lane, name)
    if not error:
        return head
    return head + "\nerror: %s" % error


def context_for_doctor(home: Path, doctor_runner=None) -> str:
    """依赖检查页的上下文 = doctor --fast 报告里没过的行（name / detail / fix），server 自己跑的。"""
    from server import doctor_run  # 局部 import：避免与 board_source 同层的循环
    report = doctor_run.report(home, fast=True, runner=doctor_runner)
    # status 词表 = §25 小写 ok|warn|fail（server/doctor_run 归一）；上下文里印成大写徽记（doctor 文本版的 [FAIL] 同款）
    bad = [row for row in report.get("checks", []) if row.get("status") in ("fail", "warn")]
    head = "doctor --fast: %d check(s) not OK" % len(bad)
    lines = ["%s %s: %s%s" % (str(row.get("status")).upper(), row.get("name"), row.get("detail") or "",
                              (" (fix: %s)" % row["fix"]) if row.get("fix") else "") for row in bad]
    return "\n".join([head] + lines)


def _run(home: Path, context: str, lang: Optional[str], run: Runner) -> "tuple[int, str]":
    """把上下文落到临时文件、起 ``act.ai_fix --open``、用完即删。"""
    env = dict(os.environ)
    env["AIASSISTANT_HOME"] = str(home)
    if lang:
        env["AIASSISTANT_UI_LANG"] = lang
    fd, ctx_path = tempfile.mkstemp(prefix="zelin-ai-fix-context-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(context)
        argv = [sys.executable, "-m", "act.ai_fix", "--open", "--context-file", ctx_path]
        return run(argv, env, home)
    finally:
        try:
            os.unlink(ctx_path)
        except OSError:
            pass


def _tail(out: str) -> str:
    return (out or "").strip()[-_OUTPUT_TAIL:]


def _last_line(text: str) -> str:
    lines = text.splitlines()
    return lines[-1] if lines else ""


def _translate(rc: int, out: str) -> dict:
    """退出码 → 响应 / envelope：0 成功（stdout 末行 = .command 路径）、2 =
    config 关闭（501）、其它 = 500 带输出尾巴。"""
    tail = _tail(out)
    if rc == _DISABLED_RC:
        raise NotImplementedError501(tail or _DISABLED_MSG)
    if rc != 0:
        raise ApiError(tail or ("ai_fix exited with %d" % rc), {"rc": rc})
    return {"ok": True, "command_file": _last_line(tail)}


def launch(home: Path, payload: dict, runner: Optional[Runner] = None) -> dict:
    """校验 → 推导上下文 → 起 ``act.ai_fix --open`` → ``{"ok": true, "command_file"}``。"""
    card_id, lang = _validate(payload)
    if sys.platform != "darwin":
        raise NotImplementedError501("Fix with AI opens Terminal.app — macOS only")
    context = context_for(home, card_id) if card_id else context_for_doctor(home)
    rc, out = _run(home, context, lang, runner or _default_runner)
    return _translate(rc, out)
