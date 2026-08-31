"""act 常驻守护进程无界日志的自压缩（registry_writes.jsonl 同款模式）。

actd / syncd 是 KeepAlive 常驻进程，``state/actd.log`` 与 ``state/syncd.log``
由各自进程内的 ``_log()`` 逐行 append、从不轮转——live 实测 syncd.log 涨到
74MB。复用 act/lib/registry.py 写入台账（``_WRITES_JOURNAL_MAX_BYTES``）的
既有自压缩模式：超过 ~1MB 只保留最近半数行，atomic tmp+replace。

单写者语义：每个日志文件只有它自己的 daemon 写（syncd 的 docstring 明言
"own log file, never touches actd's"），rewrite 竞态比 registry 台账的多进程
场景更弱。压缩 best-effort：任何失败都吞掉——日志护理绝不反噬 daemon 本体。
"""
from __future__ import annotations

from pathlib import Path

# 与 registry._WRITES_JOURNAL_MAX_BYTES 同款上限：超过 ~1MB 压缩到最近半数行
MAX_BYTES = 1 << 20


def cap(path: Path, max_bytes: int = MAX_BYTES) -> None:
    """超限时把 ``path`` 压缩到最近半数行（尾部 = 最新）。best-effort、幂等；
    文件不存在 / 不超限 / 任何 IO 失败都静默返回。"""
    try:
        if path.stat().st_size <= max_bytes:
            return
        # errors="replace"：actd._log 本就以 errors="replace" 写入；压缩读回
        # 遇到坏字节也绝不能崩（日志内容可能来自任意外部文本）。
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        keep = lines[len(lines) // 2:]
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass
