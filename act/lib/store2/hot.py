"""store2 热列推导 — canonical card dict（payload）→ cards 表热列（CONTRACT §53.2）。

payload 是真源（registry ``to_dict()`` 全文 JSON 化），热列只是查询投影：本模块
是「payload → 热列」这一条规则的**唯一落点**，migrate_yaml（一次性迁移）与
store.put_card（registry 门面的每次落盘）都从这里取值——两边各抄一份曾是
mapping doc 点名的漂移风险。

推导规则（逐条镜像 live registry 语义，payload 永远 verbatim、热列只装 schema
CHECK 能装下的归一值）：
- status：legacy ``merged_into:<id>`` 串热列归一为 ``merged`` + ``merged_into_id``；
  词表外的值 = error（schema 无法表达，调用方拒收）。
- prev_status：trashed/archived 缺回程票时按 live ``restore``/``unarchive``
  的 fallback 回填（detected / delivered）——schema CHECK 要求终态必带票。
- tier：越界（``tier: 7``）回落 ``T1``；title/type 非 str 兜底 str()；
  deadline 不合 ``YYYY-MM-DD`` 置 NULL；target_repo 非 str 兜底 str()。
- origin_trust：``policy.classify_origin(sources)``（§50 最小信任者定卡，与
  ``registry._stamp_origin`` 同一真源；payload 里的章由 registry 盖，这里只投影）。
"""
from __future__ import annotations

import re
from typing import Optional

from ..policy import classify_origin

STATUS_VOCAB = frozenset((
    "detected", "card_sent", "raising", "approved", "executing",
    "review", "delivered", "rejected", "trashed", "merged", "archived"))
MERGED_PREFIX = "merged_into:"           # legacy verbatim 状态串（registry 同名常量）
TIER_VOCAB = ("T0", "T1", "T2")          # schema CHECK；registry 本身不校验
DEADLINE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def _text(v) -> Optional[str]:
    return v if v is None or isinstance(v, str) else str(v)


def derive(norm: dict) -> "tuple[dict, list, list]":
    """canonical dict → ``(hot, warnings, errors)``。

    ``hot`` 键：status / prev_status / tier / type / title / origin_trust /
    target_repo / deadline / merged_into_id。errors 非空 = 这张卡的形态 schema
    装不下（调用方按各自纪律拒收：迁移整体 refuse，运行时抛 StoreError）。
    """
    warnings: list = []
    errors: list = []

    raw_status = norm.get("status")
    merged_into_id = None
    if isinstance(raw_status, str) and raw_status.startswith(MERGED_PREFIX):
        hot_status = "merged"
        merged_into_id = raw_status[len(MERGED_PREFIX):].strip()
        warnings.append(f"legacy status {raw_status!r} 热列归一为 merged"
                        "（payload 保留原串）")
        if not merged_into_id:
            errors.append("legacy merged_into: 串无父卡 id，schema 无法表达")
    elif raw_status in STATUS_VOCAB:
        hot_status = raw_status
        if raw_status == "merged":
            merged_into_id = _text(norm.get("merged_into"))
            if not merged_into_id:
                errors.append("status=merged 但无 merged_into 父指针（CHECK 拒收）")
    else:
        hot_status = None
        errors.append(f"status {raw_status!r} 不在 schema 词表内")

    prev = norm.get("prev_status")
    if prev is not None and prev not in STATUS_VOCAB:
        warnings.append(f"prev_status {prev!r} 不在词表，热列置 NULL/回填"
                        "（payload 保留原值）")
        prev = None
    if prev is None and hot_status == "trashed":
        prev = "detected"       # live registry.restore 的 fallback
        warnings.append("trashed 缺 prev_status，热列回填 detected")
    if prev is None and hot_status == "archived":
        prev = "delivered"      # live registry.unarchive 的 fallback
        warnings.append("archived 缺 prev_status，热列回填 delivered")

    tier = norm.get("tier")
    if tier not in TIER_VOCAB:
        warnings.append(f"tier {tier!r} 越界，热列回落 T1（payload 保留原值）")
        tier = "T1"

    title = norm.get("title")
    if not isinstance(title, str):
        warnings.append(f"title {title!r} 非 str，热列存 str 兜底")
        title = "" if title is None else str(title)
    typ = norm.get("type")
    if not isinstance(typ, str):
        warnings.append(f"type {typ!r} 非 str，热列存 str 兜底")
        typ = "" if typ is None else str(typ)

    dl = norm.get("deadline")
    hot_deadline = dl if isinstance(dl, str) and DEADLINE_RE.match(dl) else None
    if dl is not None and hot_deadline is None:
        warnings.append(f"deadline {dl!r} 不符 YYYY-MM-DD，热列置 NULL"
                        "（payload 保留原值）")

    tr = norm.get("target_repo")
    if tr is not None and not isinstance(tr, str):
        warnings.append(f"target_repo {tr!r} 非 str，热列存 str 兜底")
        tr = str(tr)

    hot = {
        "status": hot_status, "prev_status": prev, "tier": tier, "type": typ,
        "title": title, "origin_trust": classify_origin(norm.get("sources")),
        "target_repo": tr, "deadline": hot_deadline,
        "merged_into_id": merged_into_id,
    }
    return hot, warnings, errors


def source_rows(norm: dict) -> "tuple[list, list]":
    """payload.sources → sources 表投影行（``[{channel, who, date, ref, quote}]``）
    + 警告。非 dict 项跳过（payload 仍保留）；origin_key 一律 NULL——回溯推导
    强信号有全局 partial-unique 撞车风险，留给未来的写路径（§53.2）。"""
    warnings: list = []
    rows: list = []
    srcs = norm.get("sources") or []
    for i, s in enumerate(srcs if isinstance(srcs, list) else []):
        if not isinstance(s, dict):
            warnings.append(f"sources[{i}] 非 dict，投影跳过（payload 仍保留）")
            continue
        rows.append({
            "channel": _text(s.get("channel")) or "",
            "who": _text(s.get("who")), "date": _text(s.get("date")),
            "ref": _text(s.get("ref")), "quote": _text(s.get("quote")),
        })
    return rows, warnings
