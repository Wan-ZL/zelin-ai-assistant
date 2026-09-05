"""server/slack_directory.py — Slack 接入区「加载频道和成员」（§68.1 追记 / §54.4 / §15）：``GET /api/slack/directory[?refresh=1][&lang=zh|en]``。

模块名按对象「频道 / 成员目录」取 ``slack_directory``（防腐 #9：与真源 ``act/lib/slack_setup.py``
同名会撞「同一 basename 禁止出现在两个目录层级」；App Manifest 那半边在 ``server/slack_manifest.py``）。

原生 SettingsSlack.fetchDirectory 起 runtime python ``-m act.lib.slack_setup --directory [--refresh]``
（分页 + 1 h 缓存 + 双语错误句都在 python 侧，tests/test_slack_setup.py 钉）；server 同一条命令经
server/subproc（注入缝，测试绝不真起），stdout 的一行 JSON 原样透传：``{"ok", "fetched_at",
"channels": [{id, name}], "users": [{id, name, real_name}]}`` 或 ``{"ok": false, "error", "message"}``。
解释器起不来（rc 127）→ ``error: "no_python"``（原生「找不到可用的 python（…）」那一支）；没给 JSON →
``error: "directory_failed"`` 带尾巴；都不 500。勾选结果由页面写 ``PUT /api/settings/slack`` 的 list 字段。

2026-09-05 追记（§68.1；原生 SettingsSlack.swift:330 的 ``env["AIASSISTANT_UI_LANG"] = LanguageMirror.current``）：
``?lang=zh|en``（app.py 用 server/doctor_run.parse_lang 校验，其它值 400、不起子进程）经 ``extra_env``
传给子进程——``ok:false`` 的 ``message`` 是 act/lib/slack_setup.error_message 按 failures.ui_lang 第一级挑的
双语句，web 原文显示（SlackDirectoryPicker），所以它要跟看板当前语言而不是守护进程的 locale。
不带 ``lang`` = 老行为（python 侧按持久化设置 / locale 定）。目录本身（频道名 / 成员名）与语言无关，
1 h 缓存仍在 act 侧、不按语言分份。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from server import subproc

DIRECTORY_TIMEOUT_S = 60


def directory(home: Path, refresh: bool = False, runner=None, lang: Optional[str] = None) -> dict:
    """``GET /api/slack/directory``：CLI 的 JSON 行透传；子进程起不来 / 没给 JSON → ``ok:false`` 不 500。
    ``lang``（已校验的 zh / en，None = 不注入）→ 子进程 ``AIASSISTANT_UI_LANG``，错误句随看板语言。"""
    args = ["--directory"] + (["--refresh"] if refresh else [])
    extra_env = {"AIASSISTANT_UI_LANG": lang} if lang else None
    rc, out, err = subproc.run_module(home, "act.lib.slack_setup", args,
                                      timeout_s=DIRECTORY_TIMEOUT_S, runner=runner, extra_env=extra_env)
    doc = subproc.parse_json_output(out)
    if doc is None:
        tail = subproc.tail(err or out) or ("directory exited %d" % rc)
        return {"ok": False, "error": "no_python" if rc == 127 else "directory_failed", "message": tail}
    doc.setdefault("channels", [])
    doc.setdefault("users", [])
    return doc
