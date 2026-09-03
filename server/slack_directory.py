"""server/slack_directory.py — Slack 接入区「加载频道和成员」（§68.1 追记 / §54.4）：``GET /api/slack/directory[?refresh=1]``。

模块名按对象「频道 / 成员目录」取 ``slack_directory``（防腐 #9：与真源 ``act/lib/slack_setup.py``
同名会撞「同一 basename 禁止出现在两个目录层级」；App Manifest 那半边在 ``server/slack_manifest.py``）。

原生 SettingsSlack.fetchDirectory 起 runtime python ``-m act.lib.slack_setup --directory [--refresh]``
（分页 + 1 h 缓存 + 双语错误句都在 python 侧，tests/test_slack_setup.py 钉）；server 同一条命令经
server/subproc（注入缝，测试绝不真起），stdout 的一行 JSON 原样透传：``{"ok", "fetched_at",
"channels": [{id, name}], "users": [{id, name, real_name}]}`` 或 ``{"ok": false, "error", "message"}``。
解释器起不来（rc 127）→ ``error: "no_python"``（原生「找不到可用的 python（…）」那一支）；没给 JSON →
``error: "directory_failed"`` 带尾巴；都不 500。勾选结果由页面写 ``PUT /api/settings/slack`` 的 list 字段。
"""
from __future__ import annotations

from pathlib import Path

from server import subproc

DIRECTORY_TIMEOUT_S = 60


def directory(home: Path, refresh: bool = False, runner=None) -> dict:
    """``GET /api/slack/directory``：CLI 的 JSON 行透传；子进程起不来 / 没给 JSON → ``ok:false`` 不 500。"""
    args = ["--directory"] + (["--refresh"] if refresh else [])
    rc, out, err = subproc.run_module(home, "act.lib.slack_setup", args,
                                      timeout_s=DIRECTORY_TIMEOUT_S, runner=runner)
    doc = subproc.parse_json_output(out)
    if doc is None:
        tail = subproc.tail(err or out) or ("directory exited %d" % rc)
        return {"ok": False, "error": "no_python" if rc == 127 else "directory_failed", "message": tail}
    doc.setdefault("channels", [])
    doc.setdefault("users", [])
    return doc
