"""server/notify_catalog.py — 系统通知目录（notification catalog）：壳直发的通知句 + §28 队列的 kind 词表。

``GET /api/notifications`` 返回产品会弹的每一类系统通知的双语文案（server-owned 单源，
防腐 #10）。两半：

- ``shell_notices`` —— **壳自己**（不经 §28 队列）直发的系统通知：录制引擎的自愈 / 回退 /
  授权失效三句（`RecordingController.postSystemNotice`，Recording.swift 逐字节搬进 shell/）
  与通知中继的「还有 N 条通知」汇总句（NotifyRelay.swift）。原生 app 是文案规格（D3 冻结），
  这里是它们的 server 侧落点：每条 title / body 与 shell/Sources 的 ``L("zh","en")`` 逐字
  一致（判例 tests/test_server_notify_catalog.py 钉住；正文来自 §25 FailureCatalog 的按
  ``body_failure_id`` 引用，不复制第二份）。占位以 ``{name}`` 写（Swift 侧是 ``\\(expr)``）。
- ``kinds`` —— §28 队列条目的 ``kind`` 词表：``review_ready``（完成提醒，受 ``review_notify``
  三档控制）、``recap_ready``（§63 会议 recap）、``general``（无 kind 的其余守护进程通知：
  新卡待审批 / 任务停下 / 派发失败 / 雷达停摆 / 需重新登录……文案住 act/lib/notify.py 的
  msg_* 构造器，按 UI 语言即时生成，不在此重复）。

§66.2 追记的 ``[ui-parity]`` 门以本目录判 ``notification:<kind>`` 与 ``control:notifications:*``
（探针 notify_catalog）：清单里每个 kind 都要登记在 ``kinds``，每句壳直发的通知都要在
``shell_notices`` 里有同一对 zh / en。

契约：docs/CONTRACT.md §28（通知中继）、§49（路由表）、§66.2（探针）。
"""
from __future__ import annotations

import re
from typing import Optional

_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def _notice(nid: str, title_zh: str, title_en: str, *, source: str,
            body_zh: str = "", body_en: str = "", body_failure_id: Optional[str] = None) -> dict:
    return {"id": nid, "title": {"zh": title_zh, "en": title_en},
            "body": {"zh": body_zh, "en": body_en}, "body_failure_id": body_failure_id,
            "source": source}


# 壳直发的系统通知（title 是 §66 清单里 gated 的 control:notifications:label:*；body 只列不判）。
SHELL_NOTICES: tuple = (
    _notice("recording_live", "录制已就绪", "Recording is live",
            body_zh="屏幕权限已生效，录制引擎已自动重启",
            body_en="Screen Recording is now granted — the engine restarted automatically",
            source="shell/Sources/Recording.swift pollScreenPermission (consent-race self-heal)"),
    _notice("screen_audio_not_ready", "还开不了「屏幕+音频」", "Screen + Audio is not ready",
            body_failure_id="engine_ffmpeg_missing",
            source="shell/Sources/Recording.swift setMode (ffmpeg probe failed)"),
    _notice("screen_tcc_lost", "屏幕录制授权失效了", "Screen Recording permission lost",
            body_failure_id="screen_tcc_lost",
            source="shell/Sources/Recording.swift pollScreenPermission (grant revoked)"),
    _notice("recording_mode_reverted", "已退回原来的录制模式", "Reverted to the previous recording mode",
            body_zh="「{failed}」没能开启——{cause}；已退回「{kept}」继续录制",
            body_en="{failed} could not start — {cause}; reverted to {kept} and recording continues",
            source="shell/Sources/Recording.swift rollbackNote (engine died right after a mode switch)"),
    _notice("relay_overflow", "还有 {n} 条通知", "+{n} more notifications",
            body_zh="打开 App 查看看板", body_en="Open the app to see the board",
            source="shell/Sources/NotifyRelay.swift drain (burst cap 5, §28)"),
)

# §28 队列 kind 词表（general = 无 kind 的条目）。
KINDS: tuple = (
    {"kind": "review_ready",
     "title": {"zh": "任务完成提醒", "en": "Task-done alert"},
     "help": {"zh": "卡片进入「待验收」时（act/lib/notify.msg_review_ready）；受「通知 · 任务完成提醒」三档控制：关 / 横幅 / 横幅+声音。",
              "en": "When a card reaches In review (act/lib/notify.msg_review_ready); governed by the Notifications · Task-done alert knob: off / banner / banner + sound."},
     "preference": "review_notify"},
    {"kind": "recap_ready",
     "title": {"zh": "会议纪要已生成", "en": "Meeting recap ready"},
     "help": {"zh": "会后 recap 落地时（§63；正文不进通知，点击打开看板）。",
              "en": "When a post-meeting recap lands (§63; the body never rides in the banner — click opens the board)."},
     "preference": None},
    {"kind": "general",
     "title": {"zh": "其余守护进程通知", "en": "Other daemon notifications"},
     "help": {"zh": "新卡待审批 / 任务停下来了 / 派发失败或已停止重试 / 雷达停摆 / 需要重新登录 / 自我改进通道事件——文案由 act/lib/notify.py 按界面语言即时生成。",
              "en": "New card awaiting approval / a task stopped / launch failed or stopped retrying / a radar went quiet / login needed again / self-improve lane events — copy is generated per UI language by act/lib/notify.py."},
     "preference": None},
)


def kind_names() -> list:
    return [k["kind"] for k in KINDS]


def fragments(template: str) -> list:
    """模板 → 去掉 ``{占位}`` 后的静态片段（比对 Swift 插值句时两边同做）。"""
    return [part for part in _PLACEHOLDER.split(template) if part]


def same_template(a: str, b: str) -> bool:
    """两句只差占位名（`{n}` vs `{overflow.count}`）即视为同一句。"""
    return fragments(a) == fragments(b)


def _body(notice: dict) -> dict:
    if notice["body_failure_id"] is None:
        return dict(notice["body"])
    from act.lib import failures
    entry = failures.FAILURES.get(notice["body_failure_id"], {})
    return {"zh": entry.get("plain_zh", ""), "en": entry.get("plain_en", "")}


def resolve_notice(notice: dict) -> dict:
    """对外投影：body 按 body_failure_id 从 §25 FailureCatalog 取（单源），其余原样。"""
    return {"id": notice["id"], "title": dict(notice["title"]), "body": _body(notice),
            "source": notice["source"]}


def sentences() -> list:
    """目录里每一句（title 与 body 各算一句）的 (zh, en) 对——§66.2 探针的比对面。"""
    out = []
    for notice in SHELL_NOTICES:
        resolved = resolve_notice(notice)
        out.append((resolved["title"]["zh"], resolved["title"]["en"]))
        out.append((resolved["body"]["zh"], resolved["body"]["en"]))
    return out


def has_sentence(zh: str, en: str) -> bool:
    """清单里的一条通知句（zh, en；插值已成 {expr}）是否登记在目录（只差占位名即算同一句）。"""
    return any(same_template(zh, szh) and same_template(en, sen) for szh, sen in sentences())


def catalog() -> dict:
    """``GET /api/notifications``：``{"shell_notices": [...], "kinds": [...]}``。"""
    return {"shell_notices": [resolve_notice(n) for n in SHELL_NOTICES],
            "kinds": [dict(k, title=dict(k["title"]), help=dict(k["help"])) for k in KINDS]}
