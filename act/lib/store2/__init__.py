"""store2 — v-next SQLite 地基（schema v1 + 存取层）。PR2 不接线：actd 不 import 本包，
YAML registry 仍是生产真源。DDL 真源 = schema.sql，设计出处 = schema.md。"""

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
