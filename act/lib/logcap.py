"""act 日志的自压缩（防腐 #4：每个 append-only 文件出生就带 size-cap）。

``state/recap.log`` 由 act/recap.py 的 ``_log()`` 逐行 append（cron 轮与 server 派生的运行都写），
从不轮转。自压缩模式：超过 ~1MB 只保留最近半数行，atomic tmp+replace。v1 的 actd.log /
syncd.log 随 Act 管线退役（全文见 tag full）。

压缩 best-effort：任何失败都吞掉——日志护理绝不反噬调用方。
"""
from __future__ import annotations

from pathlib import Path

# 超过 ~1MB 压缩到最近半数行
MAX_BYTES = 1 << 20

# 行减半缩不动时（单行 > cap 的病态日志）按字节保尾的截断标记行
_TRUNCATION_MARKER = "…[logcap: oversized line truncated, tail kept]…"


def cap(path: Path, max_bytes: int = MAX_BYTES) -> None:
    """超限时把 ``path`` 压缩到最近半数行（尾部 = 最新）。行减半缩不下去时
    （如单行 > cap）退化为按字节保尾 + 头部垫一行截断标记——cap() 绝不允许
    变成每轮空转的 no-op。best-effort、幂等；文件不存在 / 不超限 / 任何 IO
    失败都静默返回。"""
    try:
        if path.stat().st_size <= max_bytes:
            return
        # errors="replace"：写入方本就以 errors="replace" 写入；压缩读回
        # 遇到坏字节也绝不能崩（日志内容可能来自任意外部文本）。
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        keep = lines[len(lines) // 2:]
        text = "\n".join(keep) + "\n"
        if len(text.encode("utf-8")) > max_bytes:
            # 半数行仍超限：按字节保尾（最新在尾），目标 ~cap/2 与行减半的
            # 稳态一致；切进多字节字符中间由 errors="replace" 兜住。
            tail = text.encode("utf-8")[-(max_bytes // 2):]
            text = (_TRUNCATION_MARKER + "\n"
                    + tail.decode("utf-8", errors="replace"))
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
