"""store2 — 卡片账本的 SQLite 真源（schema v1 + 存取层 + 激活协议，CONTRACT §53）。

v0.48.8（D2）起接线：`act/lib/registry.py` 门面是唯一调用者（callers 永不
直接 import 本包）；激活/每日导出经 activate.py，热列推导单点 = hot.py。
DDL 真源 = schema.sql，设计出处 = schema.md。"""

from .store import (
    SCHEMA_VERSION,
    VERBS,
    IntegrityViolation,
    NotFound,
    Store,
    StoreError,
    TransitionDenied,
    VersionConflict,
)

__all__ = [
    "SCHEMA_VERSION",
    "VERBS",
    "IntegrityViolation",
    "NotFound",
    "Store",
    "StoreError",
    "TransitionDenied",
    "VersionConflict",
]
