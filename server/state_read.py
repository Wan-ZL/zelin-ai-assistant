"""``state/`` 只读面的 errno 归类（§0 宪法第 3 条；§49 / §47.4 的 2026-09-18
追记，issue #423）。

一条分界线，两个 server 读者共用：**「这条路径上没有那个文件」**（ENOENT /
ENOTDIR / EISDIR——AIASSISTANT_HOME 指错、``state/`` 被写成了普通文件、
``dashboard.json`` 是个目录）与**「读不了它、而且不是因为它不在」**（EACCES /
EPERM / EIO…）是两件事：前者是「没有新数据」，后者是「坏掉的通道」，宪法
第 3 条要求二者严格区分。措辞上不写「文件在」——errno 能证的只是「失败的不是
『没有这个文件』」，EACCES 打在父目录上时这个进程无从确认文件在不在。历史上 server 把两类都塞进同一个 ``except OSError``
当缺席处理，真 errno 于是在任何面上都看不见——issue #423 里那句 404
「dashboard.json not found」对着一个 425 KB 的好文件连答了约 1 小时 40 分，
而这个错误映射把真正的 errno 藏了好几周。

本模块只做归类与取值：不决定 HTTP 状态、不抛 ApiError、不写日志。调用方各自
决定怎么说——``server/board_source.py`` 分流 404 / 503，``server/health.py``
把失败记进 ``/api/health`` 的 ``unreadable`` 块。
"""
from __future__ import annotations

import errno
import json
from pathlib import Path
from typing import Optional

# 「路径上没有那个文件」的 errno 家族：ENOENT = 真缺席；ENOTDIR = 路径中段不是
# 目录（``state/`` 被写成普通文件）；EISDIR = 目标本身是个目录。三者都该答
# 「没有新数据」，共用既有那句「is actd … pointed at this AIASSISTANT_HOME?」。
# 其余一律算「坏掉的通道」——包括 ELOOP 这类路径病，它们不是「首次安装还没写」。
ABSENT_ERRNOS = frozenset((errno.ENOENT, errno.ENOTDIR, errno.EISDIR))


def is_absent(exc: OSError) -> bool:
    """这个 ``OSError`` 说的是「文件不在」，而不是「读不了它」。"""
    return exc.errno in ABSENT_ERRNOS


def failure_detail(exc: OSError) -> dict:
    """读失败的结构化两项：``errno``（int|null）+ ``strerror``（str|null）。

    不用 ``str(exc)``：它会把绝对路径再拼一遍（envelope 的 ``details.path``
    已经有了）、随 locale 变、也没法被下游按值分支。手工合成的 ``OSError("x")``
    两项都是 ``None``——照实发 null，绝不补 0（0 会被读成「没出错」）。
    """
    return {"errno": exc.errno, "strerror": exc.strerror}


def _parsed(raw: str) -> Optional[dict]:
    """文本 → 顶层 dict；不是 JSON / 顶层不是对象 → None（§49 路由表：撕裂如实报 null）。"""
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def read_json(p: Path) -> "tuple[Optional[dict], Optional[dict]]":
    """读一个 state JSON → ``(doc, failure)``。

    ``doc`` 是顶层 dict，读不到 / 解不出 / 顶层不是对象都给 ``None``——与旧
    ``health._read_json`` 逐字同义，既有判例不动。``failure`` 只在**读不了**
    时非空（``failure_detail`` 的形），文件不在与内容撕裂都给 ``None``：那两
    种是 §49 路由表早就立法的「文件缺失/撕裂 → 如实报 null/stale」，不是新增
    故障源。

    ``UnicodeDecodeError`` 单列一条，别省：它继承 ``ValueError`` 而**不是**
    ``OSError``，旧代码那个 ``except (OSError, ValueError)`` 顺手接住了它，
    只写 ``except OSError`` 就会让一个非 UTF-8 的 state 文件把 ``snapshot()``
    炸穿（docstring 说「Never raises」、§49 说 `/api/health` 永不 500）。字节
    坏了与 JSON 坏了是同一种「撕裂」，都给 ``(None, None)``。
    """
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        return None, (None if is_absent(exc) else failure_detail(exc))
    except UnicodeDecodeError:
        return None, None
    return _parsed(raw), None
