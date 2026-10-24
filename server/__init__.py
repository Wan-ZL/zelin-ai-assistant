"""Zelin's AI Assistant lite 的本地 HTTP + SSE server（stdlib + PyYAML；CONTRACT §49 / §54.2）。

唯一的 HTTP 面（v2.0.0）：读 ``state/recap/``（act/lib/recap_store）、设置 overrides、更新缓存、
权限 / 体检；可分离派生 ``python -m act.recap``（会议纪要的生成 / 回退，act/recap.py 仍是
``state/recap/recaps/`` 的唯一写者）。由 launchd 常驻（``com.zelin.aiassistant.server``，§78 封闭
清单里唯一的 LaunchAgent）。

安全边界：硬编码 bind 127.0.0.1（宪法：新增网络面仅 localhost）；四闸鉴权见 server/security.py。
v1 的 dashboard.json 投影透传、registry 只读解析与 inbox 写面随 Act 管线退役（全文见 tag full）。
"""
