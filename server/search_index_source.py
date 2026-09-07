"""server/search_index_source.py — 看板搜索的会话内容层（CONTRACT §37.2 第三条 / §49 路由；owner 决策 D45）：
``GET /api/search-index``。

原生 Mac Store 直接按 (mtime, size) 懒读 ``state/search_index.json``（actd 在 harvest / promotion 触点维护，
act/lib/search_index.py；形状 ``{card_id: {"updated_at", "text"}}``，每卡尾裁 ~50KB）；web 页面没有文件系统，
所以这一层的 web 面是 server 的**只读**投影：

- 响应 ``{"entries": {card_id: text}, "truncated": bool}``——只发 ``text``（``updated_at`` 不发，web 不消费），
  每条再按 :data:`TEXT_CAP` 尾裁一次（镜像 act 侧上限；手编 / 旧版文件的超长条目不许把响应撑爆）。
- **ETag / 304**：``ETag = "<mtime_ns>-<size>"``（原生 Store 的 (mtime, size) 重验戳的 HTTP 形），
  ``If-None-Match`` 命中 → 304 无体——store 每次搜索开始与每版看板落地都重验一次，文件没变零传输。
- **size cap**：文件超过 :data:`MAX_FILE_BYTES` 不读（一个失控的索引不许把 server 内存与响应撑到几十 MB），
  响应空 ``entries`` + ``truncated: true``——层诚实缺席，字段搜索照常。
- 缺席 → 200 空 ``entries``、**不带 ETag**（新装机 / 还没索引过任何会话时的常态，不是错误——宪法第 11 条：
  层缺席只是这一层缺席，字段搜索照常；不用 404 是因为 web 对它的处置与坏文件完全一样 = 空层，没有第二种含义
  要靠状态码区分，也不为此把 ``$AIASSISTANT_HOME`` 的绝对路径写进 envelope）；坏 JSON / 顶层不是 dict →
  200 空 ``entries``（原生「corrupt → layer absent, never a crash」），永不 500。
- 路径 = ``paths.search_index_path(home)`` 一处，**不接受任何客户端参数**（query 一律忽略）；token-light GET、
  ``no-store``、同源纪律同 ``/api/board``。文件本身仍是 Mac-local / server-local 非契约面：**永不进 dashboard.json**。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from server import paths

# 镜像 act/lib/search_index.TEXT_CAP（server 绝不 import act；tests/test_server_search_index.py 钉漂移）
TEXT_CAP = 50_000
# 读门：文件超过它不读、不解析——响应空 entries + truncated:true
MAX_FILE_BYTES = 32 * 1024 * 1024

CONTENT_TYPE = "application/json; charset=utf-8"


def etag_of(st) -> str:
    """(mtime_ns, size) → 强 ETag 字面（带引号）。"""
    return '"%d-%d"' % (st.st_mtime_ns, st.st_size)


def etag_matches(if_none_match: Optional[str], etag: str) -> bool:
    """``If-None-Match`` 是否命中：逗号分隔多值、``W/`` 弱前缀剥掉后逐字比；``*`` 命中一切。"""
    if not if_none_match:
        return False
    for raw in if_none_match.split(","):
        candidate = raw.strip()
        if candidate.startswith("W/"):
            candidate = candidate[2:]
        if candidate == "*" or candidate == etag:
            return True
    return False


def _entries(data) -> dict:
    """``{card_id: {text}}`` → ``{card_id: text[-TEXT_CAP:]}``；非 dict 条目 / 非 str text 静默跳过。"""
    out: dict = {}
    if not isinstance(data, dict):
        return out
    for key, entry in data.items():
        text = entry.get("text") if isinstance(entry, dict) else None
        if isinstance(text, str) and text:
            out[str(key)] = text[-TEXT_CAP:]
    return out


def _empty() -> dict:
    """层缺席（文件不在 / 坏文件）的空投影——web 对两者一条路：字段搜索照常、零章。"""
    return {"entries": {}, "truncated": False}


def _encode(doc: dict) -> bytes:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")


def snapshot(path: Path, size: int) -> dict:
    """文件 → ``{"entries", "truncated"}``：超过读门不读；坏 JSON → 空 entries（层缺席，永不抛）。"""
    if size > MAX_FILE_BYTES:
        return {"entries": {}, "truncated": True}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    return {"entries": _entries(data), "truncated": False}


def response(home: Path, if_none_match: Optional[str] = None) -> "tuple[int, bytes, dict]":
    """``GET /api/search-index`` → ``(status, body, extra_headers)``：200 + 投影 / 304 空体；
    缺席 → 200 空表、无 ETag（宪法第 11 条：层缺席不是错误，永不 500 / 404）。"""
    path = paths.search_index_path(home)
    try:
        st = path.stat()
    except OSError:
        # 文件还没被 actd 写过（新装机 / 从未 harvest）——没有 (mtime, size) 可做 ETag，也没什么可重验
        return 200, _encode(_empty()), {"Cache-Control": "no-store"}
    etag = etag_of(st)
    headers = {"ETag": etag, "Cache-Control": "no-store"}
    if etag_matches(if_none_match, etag):
        return 304, b"", headers
    return 200, _encode(snapshot(path, st.st_size)), headers
