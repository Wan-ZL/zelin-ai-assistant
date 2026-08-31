"""鉴权四闸（§49 auth model）——act/webui.py 防线的移植版。

server/ 不 import act（paths.py 的零依赖纪律），所以把 webui.py 的机制
**移植**过来而非复用；两处实现的差异都是有意的、逐条注明：

  * per-install instance token：``state/server.token``（0600），server 启动
    load-or-create；serve index.html 时注入 ``window.__ZAI_TOKEN__``（server
    端注入——token 只进同源页面，绝不经任何跨源可读的端点外发，webui 同
    思路）。一切 POST 必须回带 ``X-Zai-Token`` 头；GET 保持 token-light
    （SSE 的 EventSource 发不了自定义头，且响应恒无 CORS 头、跨源页面读
    不到任何内容）。app.py 顶部的 ``TODO(PR3): instance token`` 就此关闭。
  * Host 回环白名单（每个请求）——DNS-rebinding 防线：evil.example 把 DNS
    绑到 127.0.0.1，浏览器发的仍是 ``Host: evil.example``，直接 403。与
    webui 的「精确 host:port」不同，这里按 **hostname** 判（端口不参与）：
    vite dev proxy 会原样转发 ``Host: 127.0.0.1:5173``，读路径不该因此断；
    rebinding 防线只关乎 hostname，放宽端口不减防御。
  * Origin 白名单（每个 POST，header **present 才查**）——CSRF 防线：浏览器
    的跨源写恒带 Origin（text/plain simple request 也带），必须精确等于本面
    的回环 origin；缺席 = 非浏览器客户端（boardctl/curl），放行到 token 闸
    ——token 才是墙，Origin 是浏览器面的前置快拒。webui 无非浏览器客户端
    所以硬拒缺席，这里 §52 的 boardctl 是法定客户端，故按 present-only 查。
  * ``Content-Type: application/json``（每个 POST）——把「无预检 simple
    request」这一 CSRF 向量整类杀掉（纵深：Origin 闸已挡住浏览器面）。

契约：docs/CONTRACT.md §49（auth model 法源）。stdlib only。
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
from pathlib import Path

# POST 鉴权头（web/src/api.ts 与 act/boardctl.py 同字面量）
TOKEN_HEADER = "X-Zai-Token"  # nosec B105 - header NAME, not a secret

# Host 闸的回环 hostname 全集（端口不参与判定，见模块 docstring）
_LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})

# 注入进 index.html 的同源 token 载体（web/src/api.ts 读取端同字面量）
_TOKEN_SNIPPET = "<script>window.__ZAI_TOKEN__=%s;</script>"


def token_path(home: Path) -> Path:
    return home / "state" / "server.token"


def load_or_create_token(home: Path) -> str:
    """per-install token：读既有值，缺席则生成 + 0600 落盘（webui 同款）。"""
    p = token_path(home)
    try:
        existing = p.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    tok = secrets.token_urlsafe(32)
    p.parent.mkdir(parents=True, exist_ok=True)
    # O_CREAT|O_TRUNC 带 0600 mode——umask 只会更严；chmod 兜底钉死
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(tok + "\n")
    finally:
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return tok


def host_ok(host_header: object) -> bool:
    """Host 必须是回环 hostname（端口任意）——anti-rebind，每个请求都查。"""
    if not isinstance(host_header, str):
        return False
    host = host_header.strip().lower()
    if not host:
        return False
    if host.startswith("["):  # IPv6 字面量形如 [::1]:port
        name = host.split("]", 1)[0] + "]"
    elif ":" in host:
        name = host.rsplit(":", 1)[0]
    else:
        name = host
    return name in _LOOPBACK_HOSTNAMES


def allowed_origins(port: int) -> frozenset:
    """本面的合法同源 origin 集（webui allowed_origins 同形）。"""
    return frozenset({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})


def origin_ok(origin_header: object, allowed: frozenset) -> bool:
    """present 的 Origin 必须精确命中白名单（"null" 等一律 False）。"""
    if not isinstance(origin_header, str):
        return False
    return origin_header.strip().lower() in allowed


def content_type_is_json(ct_header: object) -> bool:
    """POST body 必须自报 application/json（charset 等参数位随意）。"""
    if not isinstance(ct_header, str):
        return False
    return ct_header.split(";", 1)[0].strip().lower() == "application/json"


def token_ok(got: object, want: str) -> bool:
    if not isinstance(got, str):
        return False
    return hmac.compare_digest(got, want)


def inject_token(html: bytes, token: str) -> bytes:
    """把 token 注入被服务的 index.html（</head> 前；无 head 则前置）。"""
    snippet = (_TOKEN_SNIPPET % json.dumps(token)).encode("utf-8")
    marker = b"</head>"
    if marker in html:
        return html.replace(marker, snippet + marker, 1)
    return snippet + html
