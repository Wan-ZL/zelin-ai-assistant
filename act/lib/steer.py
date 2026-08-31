"""追加指令中继（steer relay）——owner 对 executing 卡的评论送进 live session。

契约：docs/CONTRACT.md §44.3-S（已入典；起草稿 docs/design/vnext-amendments.md）。

背景（vnext 锁定决策）：
运行中卡片上的 owner 评论不再是「打回重批」，而是对正在执行的 session 的
中途转向指令（steer）。投递复用既有 §44.3 briefing 机制的送达点——actd 只在
§39.2 安全窗口（roster blocked，或会话已死的 resume 时机）flush，working +
live pid 绝不打断。与 briefing 的三点差异：

1. **信任级别**：steer 文本是 owner 亲手打的（trusted）——用 OWNER UPDATE
   前缀直发，**不过** ``sanitize.fence_untrusted``（briefing 行来自外部内容，
   必须围栏）。runner 侧的 secrets scrub 照旧（那是防泄密，不是防注入）。
2. **dedup 键带时间戳 + inbox 文件 stem**：同一句话隔十分钟再打一遍是**新指
   令**（owner 在催/在重申），不是 crash-retry 重放——briefing 的纯文本去重
   语义在这里是错的。键 = ``<ts>|<stem>|<sha256(text)[:16]>``；同一 inbox
   文件被重放（unlink 失败）时 ts 与 stem 都相同 → 同键 → 去重；同一秒打的
   两条同文指令是两个 inbox 文件（stem 全局唯一）→ 两条 steer，绝不误吞。
   无 stem 的历史/脏条目退回 ``<ts>|<hash>`` 双段形。
3. **class='steer'**：note dict 自带 class 字段，与 store2 的 notes 表
   （comment/steer/fold）形状对齐（本 PR store2 不接线，字段先对齐）。

本模块**只做纯函数记账**（入队/出队/台账/诚实丢弃留痕），不做任何 I/O——
不 registry.save、不 notify、不写 analytics、不碰 roster。单写者纪律（§44）：
只有 actd 主循环在调用点落盘；notify/analytics 也归调用点（本模块把 trace
文本返回给调用者）。

wire 字段（``execution.*``，全部 add-only）：

======================  =====================================================
``pending_steers``      待投递队列，元素 = enqueue_steer 返回的 note dict，
                        cap 10（溢出时最老一条被挤出 + notes 留痕，绝不静默丢）
``delivered_steers``    已投递台账（环形，最近 20）。元素 = ``{key, text(截
                        200), ts, delivered_at}``（M8.3 C-3 终裁：board 投影
                        delivered 行需要全文）；读侧容忍历史裸 key 条目——
                        dedup 只看 key，投影跳过无 text 的旧条目。
``steer_queued``        入队时间戳环形列表（最近 10）——board 投影「已排队」
``steer_delivered``     送达时间戳环形列表（最近 10）——board 投影「已送达」
``steer_count``         成功送达累计（int）
``last_steer_at``       最近一次送达（UTC ISO）
``steer_attempts``      当前批次注入失败次数（3 次放弃 + 留痕，§44.3 同款）
======================  =====================================================

诚实处置（§39 红线）：owner 打的字在任何路径都不许静默蒸发——排队可见
（steer_queued + pending 队列本身）、送达可见（steer_delivered）、丢弃留痕
（drop_trace 写 ``[<date> 追加指令未送达]`` 进 notes 并返回 trace 供通知）。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from typing import Optional

# note dict 的 class 值——store2 notes 表（comment/steer/fold）对齐
STEER_CLASS = "steer"
# owner 亲打文本的投递前缀——极简，让 session 知道这是对当前任务的转向，
# 不是新任务也不是打回（§39.2 OWNER ANSWER 同款克制）
STEER_PREFIX = "OWNER UPDATE:\n"
# 文本上限按 Unicode code point 计（§39.2 answer 的 4000 同款；Python len
# 即 code point 数）——超限保头部截断，开头最值得保住（§39.2 判例）
MAX_STEER_CHARS = 4000
# pending 队列上限——一张卡积压 10 条未送达指令已是异常，更老的挤出留痕
PENDING_CAP = 10
# 已投递台账环形上限（delivered_briefings 同款：低频事件，20 条覆盖 retry 窗口）
DELIVERED_LEDGER_CAP = 20
# steer_queued / steer_delivered 时间戳环形上限（任务规格钉死：capped 10）
TS_RING_CAP = 10
# 每批注入失败上限——超过即放弃 + drop_trace（§44.3 briefing 3 次同款）
MAX_STEER_ATTEMPTS = 3
# 丢弃留痕里的原文截断长度（§39.2「原文：<text 截 200>」同款）
TRACE_CLIP = 200


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ring_append(ex: dict, key: str, value: str, cap: int) -> None:
    """execution 上的环形列表追加——容忍脏数据（非 list 整体重建）。"""
    ring = ex.get(key)
    items = [str(v) for v in ring if v] if isinstance(ring, list) else []
    items.append(value)
    ex[key] = items[-cap:]


def steer_key(text: str, ts: str, stem: Optional[str] = None) -> str:
    """时间戳 + inbox 文件 stem 承载的 dedup 键：同一文件重放（同 ts 同 stem
    同文）= 重复；同秒同文的两个不同文件 = 两条指令。无 stem（历史调用/
    脏条目重建）退回 ``ts|hash`` 双段形。"""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    if isinstance(stem, str) and stem.strip():
        return f"{ts}|{stem.strip()}|{digest}"
    return f"{ts}|{digest}"


def _delivered_keys(ex: dict) -> set:
    """已投递台账的 key 集合——同时容忍 C-3 dict 条目与历史裸 key 条目。"""
    raw = ex.get("delivered_steers")
    if not isinstance(raw, list):
        return set()
    keys = set()
    for entry in raw:
        if isinstance(entry, dict):
            k = entry.get("key")
            if k:
                keys.add(str(k))
        elif entry:
            keys.add(str(entry))
    return keys


# --------------------------------------------------------------------------- #
# 入队
# --------------------------------------------------------------------------- #
def enqueue_steer(req, text, ts: Optional[str] = None,
                  stem: Optional[str] = None) -> Optional[dict]:
    """把 owner 的追加指令排上卡片，等 actd 在安全窗口投递。

    fail-closed（§33 口径）：``text`` 非 str / 空白 → 返回 None 且不动卡片
    （垃圾值绝不入队）；超 ``MAX_STEER_CHARS`` 截断保头部。``ts`` 取 inbox
    动作自带的时间戳、``stem`` 取 inbox 文件名 stem（两者合成 crash-replay
    同键去重的键——只有真正的同文件重放才去重）；缺失时 ts 用当前时刻。

    去重查 pending **与已投递台账**（§44.3 delivered_briefings 判例：flush
    之后 pending 已清，仅查 pending 会让重放的同一条指令进会话两遍）。

    队列满（PENDING_CAP）时挤出最老一条并在 notes 留痕——新指令通常包含或
    取代旧指令，保新弃旧 + 绝不静默丢。

    只改内存里的 ``req``（execution / notes），**不落盘**——调用者（actd
    主循环，registry 单写者）负责 save。返回入队的 note dict；重复/垃圾
    返回 None（调用者据此 log noop）。
    """
    if not isinstance(text, str):
        return None
    body = text.strip()
    if not body:
        return None
    if len(body) > MAX_STEER_CHARS:
        body = body[:MAX_STEER_CHARS]
    stamp = ts if isinstance(ts, str) and ts.strip() else _iso_now()
    key = steer_key(body, stamp, stem)

    ex = dict(req.execution or {})
    pend = pending_steers(req)
    if key in {n["key"] for n in pend} or key in _delivered_keys(ex):
        return None                      # 重放（同 ts 同文）——去重，非新指令

    note = {"class": STEER_CLASS, "text": body, "ts": stamp, "key": key}
    pend.append(note)
    # 溢出：最老的挤出 + §39 式留痕（notes 是卡片的一部分，纯变异无 I/O）
    while len(pend) > PENDING_CAP:
        evicted = pend.pop(0)
        _append_trace(req, evicted, "队列已满，被更新的指令挤出")
    ex["pending_steers"] = pend
    _ring_append(ex, "steer_queued", stamp, TS_RING_CAP)
    req.execution = ex
    return note


def pending_steers(req) -> list:
    """读出待投递队列——容忍手改 YAML 的脏条目（非 dict / 无文本 / 无键的
    一律丢弃不崩，宪法第 11 条），返回可直接投递的 note dict 列表副本。"""
    ex = req.execution or {}
    raw = ex.get("pending_steers")
    if not isinstance(raw, list):
        return []
    out = []
    for n in raw:
        if not isinstance(n, dict):
            continue
        body = n.get("text")
        if not isinstance(body, str) or not body.strip():
            continue
        stamp = str(n.get("ts") or "")
        key = str(n.get("key") or "") or steer_key(body.strip(), stamp)
        out.append({"class": STEER_CLASS, "text": body.strip(),
                    "ts": stamp, "key": key})
    return out


# --------------------------------------------------------------------------- #
# 投递（prompt 组装归这里；stop-idle-then-resume 管道归 executor/调用点）
# --------------------------------------------------------------------------- #
def build_steer_prompt(notes: list) -> str:
    """整批待投递 steer → resume prompt。owner 亲打 = trusted，不围栏
    （briefing 的 fence_untrusted 是给外部内容的）；runner 侧 secrets scrub
    照旧由投递管道负责。批内按入队顺序列点。"""
    lines = "\n".join(f"- {n['text']}" for n in notes)
    return (STEER_PREFIX + lines
            + "\n\nThe lines above are a mid-flight course correction from "
              "the OWNER for your CURRENT task. Apply them and continue — "
              "this is not a new task and not a rework.")


def mark_delivered(req, notes: list, delivered_at: Optional[str] = None) -> None:
    """flush 成功后的记账：只把**实际送达**的 note 移出队列（flush 期间另一
    进程排入的新 steer 留给下一轮——§44.3 brief 的 sent-set 判例），台账进
    环形 20（元素 = ``{key, text(截 200), ts, delivered_at}``，M8.3 C-3——
    board 投影 delivered 行由此取全文），steer_delivered 时间戳环形 +1，
    steer_count 累计，steer_attempts 清零。不落盘，调用者 save。

    注意：resume 管道自己的账（session_id 换新、resume_attempts 清零）归
    executor 侧既有机制，这里绝不越界（单一职责，账目不混记）。
    """
    sent = {n["key"] for n in notes}
    if not sent:
        return
    now = delivered_at or _iso_now()
    ex = dict(req.execution or {})
    rest = [n for n in pending_steers(req) if n["key"] not in sent]
    if rest:
        ex["pending_steers"] = rest
    else:
        ex.pop("pending_steers", None)
    ex.pop("steer_attempts", None)
    # 旧台账保留（裸 key 历史条目按 C-3 容忍原样携带），本批同键条目剔除
    old = ex.get("delivered_steers")
    ledger = []
    if isinstance(old, list):
        for entry in old:
            k = entry.get("key") if isinstance(entry, dict) else entry
            if k and str(k) not in sent:
                ledger.append(entry)
    seen: set = set()
    for n in notes:                       # 保送达顺序，批内同键去重
        if n["key"] in seen:
            continue
        seen.add(n["key"])
        ledger.append({
            "key": n["key"],
            "text": n["text"][:TRACE_CLIP],
            "ts": n.get("ts") or "",
            "delivered_at": now,
        })
    ex["delivered_steers"] = ledger[-DELIVERED_LEDGER_CAP:]
    ex["steer_count"] = int(ex.get("steer_count", 0) or 0) + len(sent)
    ex["last_steer_at"] = now
    _ring_append(ex, "steer_delivered", now, TS_RING_CAP)
    req.execution = ex


def record_attempt(req) -> int:
    """flush 失败记一次尝试（队列保留，下一 pass 重试）。返回累计次数。"""
    ex = dict(req.execution or {})
    n = int(ex.get("steer_attempts", 0) or 0) + 1
    ex["steer_attempts"] = n
    req.execution = ex
    return n


def give_up_due(req) -> bool:
    """本批已烧满 MAX_STEER_ATTEMPTS 次 → 该放弃了（调用者接 drop_trace）。"""
    ex = req.execution or {}
    try:
        return int(ex.get("steer_attempts", 0) or 0) >= MAX_STEER_ATTEMPTS
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# 诚实丢弃（§39 红线：owner 的字任何路径不许静默蒸发）
# --------------------------------------------------------------------------- #
def _append_trace(req, note: dict, reason: str) -> str:
    """单条 note 的丢弃留痕：`[<date> 追加指令未送达] <原因>；原文：<截 200>`
    追进 notes（§39.2「回答未投递」冻结行文法的 steer 变体）。返回 trace 行。"""
    body = note.get("text", "")
    clip = body[:TRACE_CLIP] + ("…" if len(body) > TRACE_CLIP else "")
    tag = f"[{_dt.date.today().isoformat()} 追加指令未送达] {reason}；原文：{clip}"
    req.notes = (req.notes + "\n" + tag).strip() if req.notes else tag
    return tag


def drop_trace(req, notes: list, reason: str) -> list:
    """整批丢弃 + 留痕：把 ``notes`` 移出 pending 队列、steer_attempts 清零、
    每条在卡片 notes 留 §39 式痕迹。返回 trace 行列表——调用者拿去 notify /
    analytics（本模块不做 I/O）。不落盘，调用者 save。"""
    dropped = {n["key"] for n in notes}
    tags = [_append_trace(req, n, reason) for n in notes]
    ex = dict(req.execution or {})
    rest = [n for n in pending_steers(req) if n["key"] not in dropped]
    if rest:
        ex["pending_steers"] = rest
    else:
        ex.pop("pending_steers", None)
    ex.pop("steer_attempts", None)
    req.execution = ex
    return tags


# --------------------------------------------------------------------------- #
# board 投影辅助（dashboard 集成点用，add-only 字段直读）
# --------------------------------------------------------------------------- #
def delivered_entries(req) -> list:
    """已投递台账里可投影的条目（C-3 dict 形，带全文）。历史裸 key 条目与
    缺 text/ts 的脏条目跳过（投影侧规则：绝不渲染无法对账的 steer——ts 是
    dedup key 的组成部分，C-4）。返回 [{key, text, ts, delivered_at}]。"""
    ex = req.execution or {}
    raw = ex.get("delivered_steers")
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text, ts = entry.get("text"), entry.get("ts")
        if not (isinstance(text, str) and text.strip() and
                isinstance(ts, str) and ts.strip()):
            continue
        out.append({
            "key": str(entry.get("key") or ""),
            "text": text,
            "ts": ts,
            "delivered_at": (str(entry.get("delivered_at"))
                             if entry.get("delivered_at") else None),
        })
    return out


def steer_status(req) -> dict:
    """卡片 steer 状态一览：排队数 + 两条时间戳环 + 累计——board 的
    「已排队/已送达」诚实投影直接吃这个（缺省全零/空，绝不抛）。"""
    ex = req.execution or {}

    def _ring(key: str) -> list:
        v = ex.get(key)
        return [str(t) for t in v if t] if isinstance(v, list) else []

    try:
        count = int(ex.get("steer_count", 0) or 0)
    except (TypeError, ValueError):
        count = 0
    return {
        "steer_pending": len(pending_steers(req)),
        "steer_queued": _ring("steer_queued"),
        "steer_delivered": _ring("steer_delivered"),
        "steer_count": count,
        "last_steer_at": ex.get("last_steer_at"),
    }
