"""统一 error envelope（BUILD-CONTRACT §2.1）。

所有 API 错误 → ``{"error":{"code":"...","message":"...","details":{}}}``。
codes 词表（CONTRACT §49，v0.48 已入典）：UNKNOWN_FIELD / INVALID_FIELD /
NOT_FOUND / INTERNAL_ERROR / NOT_IMPLEMENTED（501 专用，reveal 非 darwin——
add-only 正式收编，原 TODO(contract) 关闭）。
"""
from __future__ import annotations

from typing import Optional


class ApiError(Exception):
    """携带 HTTP status + envelope code 的受控错误——handler 捕获后渲染 envelope。"""

    status = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message: str, details: Optional[dict] = None,
                 status: Optional[int] = None, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code

    def envelope(self) -> dict:
        return {"error": {"code": self.code, "message": self.message,
                          "details": self.details}}


class UnknownFieldError(ApiError):
    """未知 JSON 字段零容忍（抄 dashi 纪律）→ 400。"""
    status = 400
    code = "UNKNOWN_FIELD"


class InvalidFieldError(ApiError):
    """字段存在但值/类型非法 → 400。"""
    status = 400
    code = "INVALID_FIELD"


class NotFoundError(ApiError):
    status = 404
    code = "NOT_FOUND"


class NotImplementedError501(ApiError):
    """非 darwin 的 reveal → 501。"""
    status = 501
    code = "NOT_IMPLEMENTED"
