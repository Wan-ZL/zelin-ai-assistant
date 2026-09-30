"""server/notify_catalog.py — 系统通知目录（notification catalog）：壳直发的通知句 + §28 队列的 kind 词表。

``GET /api/notifications`` 返回产品会弹的每一类系统通知的双语文案（server-owned 单源，
防腐 #10）。两半：

- ``shell_notices`` —— **壳自己**（不经 §28 队列）直发的系统通知：录制引擎的自愈 / 回退 /
  授权失效三句（`RecordingController.postSystemNotice`，Recording.swift 逐字节搬进 shell/）
  与通知中继的「还有 N 条通知」汇总句（NotifyRelay.swift）。原生 app 是文案规格（D3 冻结），
  这里是它们的 server 侧落点：每条 title / body 与 shell/Sources 的 ``L("zh","en")`` 逐字
  一致（判例 tests/test_server_notify_catalog.py 钉住；正文来自 §25 FailureCatalog 的按
  ``body_failure_id`` 引用，不复制第二份）。占位以 ``{name}`` 写（Swift 侧是 ``\\(expr)``）；
  带插值的句子另给 ``slots``（每个占位的取值词表，同样与壳 L() 逐字——回滚句的模式名 / 死因，
  2026-09-03 add-only）。
- ``kinds`` —— §28 队列条目的 ``kind`` 词表：``review_ready``（完成提醒，受 ``review_notify``
  三档控制）、``proposal`` / ``needs_input`` / ``failure``（issue #29 的分类开关，偏好键
  ``notify_proposals`` / ``notify_needs_input`` / ``notify_failures``；抑制在写方而不在壳，
  见 act/lib/notify.suppression_reason）、``receipt``（手动按钮的回执，无分类开关、不受安静时段管——
  词表 truth = ``act/lib/notify.QUIET_HOURS_EXEMPT``）、``review_stale``（待验收卡归档前的最后一次告知，
  §70.2 追记二；同样无分类开关、不受安静时段管——每日整理出厂 03:30 就在安静窗里）、
  ``recap_ready``（§63 会议 recap）、``general``（无 kind 的其余守护进程通知：
  潜在任务新卡 / 任务停下 / 派发失败 / 雷达停摆 / 需重新登录……文案住 act/lib/notify.py 的
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
            body_zh: str = "", body_en: str = "", body_failure_id: Optional[str] = None,
            slots: Optional[dict] = None) -> dict:
    return {"id": nid, "title": {"zh": title_zh, "en": title_en},
            "body": {"zh": body_zh, "en": body_en}, "body_failure_id": body_failure_id,
            "slots": {k: [{"zh": zh, "en": en} for zh, en in v] for k, v in (slots or {}).items()},
            "source": source}


# 录制模式回滚句的插值词表（`{failed}` / `{kept}` = RecordingController.label(forMode:)，
# `{cause}` = rollbackNote 的三种死因）：壳 Recording.swift 组句，页面经桥 `recording.note` 原文
# 显示——短标签在 §66 清单里 gated（control:notifications:label:*），所以在这里登记为 slots
# （add-only 键；`sentences()` 把每个 slot 值也算一句）。
_MODE_LABELS = (("关", "Off"), ("屏幕 + 音频", "Screen + Audio"), ("仅屏幕", "Screen Only"))
_ROLLBACK_CAUSES = (
    ("缺 ffmpeg（brew install ffmpeg 装好后再切一次）", "ffmpeg is missing (brew install ffmpeg, then switch again)"),
    ("缺 Node.js", "Node.js is missing"),
    ("引擎没能启动", "its engine failed to start"),
)


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
            slots={"failed": _MODE_LABELS, "kept": _MODE_LABELS, "cause": _ROLLBACK_CAUSES},
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
    # §78：提案列退役，这一类数的是「潜在任务列来了新卡」。``kind`` 与
    # ``preference`` 两个令牌是持久化的（队列条目 / 存量偏好键），逐字不动。
    {"kind": "proposal",
     "title": {"zh": "新提案提醒", "en": "New-proposal alert"},
     "help": {"zh": "雷达 / 捕获往潜在任务列铸出新卡等你拍板时（含回锅与 §40 批量汇总）；受「通知 · 新提案通知」开关与安静时段控制。",
              "en": "When a radar or capture files a new card into the Backlog lane for your decision (including returned cards and the §40 batch summary); governed by the Notifications · New-proposal alerts switch and by quiet hours."},
     "preference": "notify_proposals"},
    {"kind": "needs_input",
     "title": {"zh": "任务停下来了", "en": "Needs-input alert"},
     "help": {"zh": "会话停在等一句话时（受阻收割 / 反复中断暂停救活 / 派发停止重试）；受「通知 · 任务停下来时通知」开关与安静时段控制。",
              "en": "When a session stops and waits on you (blocked-session harvest / auto-recovery paused / dispatch stopped retrying); governed by the Notifications · Needs-input alerts switch and by quiet hours."},
     "preference": "notify_needs_input"},
    {"kind": "failure",
     "title": {"zh": "失败提醒", "en": "Failure alert"},
     "help": {"zh": "需要重新登录 / 雷达停摆 / 派发失败 / 会话没停住 / registry 护栏；受「通知 · 失败通知」开关控制（默认开），不受安静时段管。",
              "en": "Login needed again / a radar gone quiet / dispatch failed / a session that would not stop / the registry guard; governed by the Notifications · Failure alerts switch (on by default) and never silenced by quiet hours."},
     "preference": "notify_failures"},
    {"kind": "receipt",
     "title": {"zh": "手动操作的回执", "en": "Receipt for something you pressed"},
     "help": {"zh": "你刚按下的按钮的回音（今日唯一一处：设置 · 每周摘要的「现在生成一份」——运行是分离的，成功 / 没数据 / 失败三条都回这里）；没有分类开关，也不受安静时段管：按了就一定响。",
              "en": "The answer to a button you just pressed (today the only one is Settings · Weekly digest \"Generate now\" — the run detaches, and all three outcomes (generated / no data / failed) come back here); no category switch and quiet hours never applies: you pressed it, so it rings."},
     "preference": None},
    {"kind": "review_stale",
     "title": {"zh": "归档前的最后一次告知", "en": "Last call before archiving"},
     "help": {"zh": "待验收卡闲置到设置 · 每日整理的「待验收的卡多少天没动算过时」（出厂 14 天）时，整轮一条汇总：「N 张待验收卡要归档了」（act/lib/notify.msg_review_stale）——下一轮才收进回收站（可恢复）。没有分类开关，也不受安静时段管：每日整理出厂在 03:30 跑，正落在出厂安静窗里，守安静时段就等于永远不告知、卡照样被收走。要一条都不收就把那把旋钮设成 0（规则整条关掉）。",
              "en": "When a card has sat in review past Settings · Daily tidy-up “Days a card can sit in Review before it ages out” (14 out of the box), one summary per pass: “N cards in review are about to be archived” (act/lib/notify.msg_review_stale) — the next pass is what moves them to the trash (restorable). No category switch and quiet hours never applies: the daily tidy-up runs at 03:30 out of the box, inside the default quiet window, so honouring quiet hours would mean never being told while the cards go anyway. Set that knob to 0 to turn the rule off entirely."},
     "preference": None},
    {"kind": "recap_ready",
     "title": {"zh": "会议纪要已生成", "en": "Meeting recap ready"},
     "help": {"zh": "会后 recap 落地时（§63；正文不进通知，点击打开看板）。",
              "en": "When a post-meeting recap lands (§63; the body never rides in the banner — click opens the board)."},
     "preference": None},
    {"kind": "general",
     "title": {"zh": "其余守护进程通知", "en": "Other daemon notifications"},
     "help": {"zh": "没落进上面四类的其余守护进程通知：自动恢复中（无需操作）/ 免批派发的观察模式通知 / 自我改进通道事件 / 简报——文案由 act/lib/notify.py 按界面语言即时生成。没有分类开关，但同样守安静时段。",
              "en": "Every daemon notification outside the four categories above: auto-recovery in progress (nothing to do) / observation-mode auto-dispatch / self-improve lane events / digests — copy is generated per UI language by act/lib/notify.py. No category switch, but quiet hours still applies."},
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
    """对外投影：body 按 body_failure_id 从 §25 FailureCatalog 取（单源），其余原样；
    ``slots`` = 插值词表（add-only；无插值的句子是空 dict）。"""
    return {"id": notice["id"], "title": dict(notice["title"]), "body": _body(notice),
            "slots": {k: [dict(v) for v in vs] for k, vs in notice.get("slots", {}).items()},
            "source": notice["source"]}


def sentences() -> list:
    """目录里每一句（title 与 body 各算一句，插值 slot 的每个取值也算一句）的 (zh, en) 对——
    §66.2 探针的比对面。"""
    out = []
    for notice in SHELL_NOTICES:
        resolved = resolve_notice(notice)
        out.append((resolved["title"]["zh"], resolved["title"]["en"]))
        out.append((resolved["body"]["zh"], resolved["body"]["en"]))
        for values in resolved["slots"].values():
            out.extend((v["zh"], v["en"]) for v in values)
    return out


def has_sentence(zh: str, en: str) -> bool:
    """清单里的一条通知句（zh, en；插值已成 {expr}）是否登记在目录（只差占位名即算同一句）。"""
    return any(same_template(zh, szh) and same_template(en, sen) for szh, sen in sentences())


def catalog() -> dict:
    """``GET /api/notifications``：``{"shell_notices": [...], "kinds": [...]}``。"""
    return {"shell_notices": [resolve_notice(n) for n in SHELL_NOTICES],
            "kinds": [dict(k, title=dict(k["title"]), help=dict(k["help"])) for k in KINDS]}
