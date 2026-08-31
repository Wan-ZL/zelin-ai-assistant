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
import re
import secrets
import stat
from pathlib import Path

# POST 鉴权头（web/src/api.ts 与 act/boardctl.py 同字面量）
TOKEN_HEADER = "X-Zai-Token"  # nosec B105 - header NAME, not a secret

# Host 闸的回环 hostname 全集（端口不参与判定，见模块 docstring）
_LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})

# 注入进 index.html 的同源 token 载体（web/src/api.ts 读取端同字面量）
_TOKEN_SNIPPET = "<script>window.__ZAI_TOKEN__=%s;</script>"

# token 字符集——secrets.token_urlsafe 的产物恒在此集内（base64url 的
# [A-Za-z0-9_-]）。读回时**校验**（belt-and-braces）：磁盘上的坏 token
# （被人塞入 `</script>`、换行、引号）不许进注入路径——即便 M1 的转义漏了
# 一环，一个畸形 token 也在铸造前就被拒、重铸干净值。
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def token_path(home: Path) -> Path:
    return home / "state" / "server.token"


def _valid_token(value: object) -> bool:
    return isinstance(value, str) and bool(value) and bool(_TOKEN_RE.match(value))


def load_or_create_token(home: Path) -> str:
    """per-install token：读既有值（校验 + 重新加固权限），否则生成 + 0600 落盘。

    加固纪律（M2/M3 审计；平台口径见各条）：
    - **权限（POSIX-only）**：既有文件必须 0600。历史上 0644 创建的 token
      （任何本地账户可读 = RCE 级泄露）在读路径 chmod 收回；group/other 可读
      且 chmod 失败时**拒用**该文件、重铸（宁可换 token 也不用一个别人读得到
      的）。Windows 上 st_mode 的组/他人位是合成值（可写文件一律 ~0o666）、
      真实访问控制在 ACL（继承自用户目录）且 ``os.fchmod`` 不存在——照 POSIX
      逻辑执行会每次启动误判重铸 + AttributeError，故整块按 POSIX-only 关切
      处理（Windows CI 判例钉过）。
    - **符号链接（跨平台）**：读与写都先过可移植的 ``is_symlink()`` 拒绝，
      POSIX 侧再叠 ``O_NOFOLLOW`` 补 check→open 的 TOCTOU 窗——state/
      server.token 若是指向他处的 symlink，绝不跟随（防被诱导 truncate/覆盖
      任意文件、或从攻击者控制的路径读回 token）。Windows 无 O_NOFOLLOW
      （flag 为 0，只靠它会真的跟随过去），is_symlink 检查就是那里的防线。
    - **内容（跨平台）**：坏字符（``</script>``/换行/引号）的 token 一律
      弃用重铸。
    """
    p = token_path(home)
    existing = _read_token_hardened(p)
    if existing is not None:
        return existing
    tok = secrets.token_urlsafe(32)
    p.parent.mkdir(parents=True, exist_ok=True)
    # O_NOFOLLOW：写路径也不跟随 symlink（先删既有非常规文件再建）。O_CREAT|
    # O_TRUNC 带 0600 mode——umask 只会更严；chmod 兜底钉死。
    try:
        if p.is_symlink():
            p.unlink()
    except OSError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(p), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(tok + "\n")
    finally:
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return tok


def _read_token_hardened(p: Path) -> "str | None":
    """读既有 token：symlink 拒跟随、权限收回（POSIX）、内容校验。
    坏则 None（触发重铸）。平台口径见 load_or_create_token docstring。"""
    # symlink 拒绝先走可移植检查（Windows 无 O_NOFOLLOW，flag 是 0——只靠
    # flag 在那里会真的跟随过去）；POSIX 侧 O_NOFOLLOW 仍保留，补
    # is_symlink→open 之间的 TOCTOU 窗。
    try:
        if p.is_symlink():
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(p), flags)
    except OSError:
        return None
    try:
        # 权限收回是 POSIX-only 关切（Windows 合成 mode 位 + 无 fchmod，
        # 见 load_or_create_token docstring 第一条）
        if os.name == "posix":
            st = os.fstat(fd)
            # group/other 任一可读/写 → 先尝试收回 0600；收不回就弃用重铸
            if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                try:
                    os.fchmod(fd, 0o600)
                except OSError:
                    return None
        with os.fdopen(fd, "r", encoding="utf-8", closefd=True) as fh:
            fd = -1  # fdopen 已接管关闭权
            existing = fh.read().strip()
    except OSError:
        return None
    finally:
        if fd >= 0:  # 拒用/异常路径：fd 尚未交给 fdopen，必须亲手关
            try:
                os.close(fd)
            except OSError:
                pass
    return existing if _valid_token(existing) else None


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
    """present 的 Origin 必须精确命中白名单（"null" 等一律 False）。

    **为什么 present-only 安全（M5/M6 审计留证，改闸前必读）**：缺 Origin 的
    请求被放行到 token 闸，看似给了 CSRF 一条缝——但一个**跨源浏览器**发起的
    写**必然带 Origin**（fetch/form/sendBeacon/no-cors 全部如此），所以缺
    Origin = 非浏览器客户端（boardctl/curl），token 才是它们的墙。真正兜住
    「缺 Origin 的浏览器写」这一类的是**Content-Type 闸**：跨源无预检
    simple request 只能发 text/plain（等三种），被 CT 闸挡死；能发
    application/json 的跨源请求必然触发预检 + 带 Origin，落回本函数。
    **纪律**：一旦放宽 Content-Type 闸（接受 text/plain 等），present-only
    的 Origin 判定会**静默失去**这层兜底——两闸是耦合的，动一个必须复核另一
    个。"""
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


def _js_string_literal(token: str) -> str:
    """token → 安全内联进 <script> 的 JS 字符串字面量。

    json.dumps 不转义 ``<`` 或 ``/``，所以一个含 ``</script>`` 的 token 能
    提前闭合脚本标签逃逸（M1，已复现）。转义 ``<``（挡 ``</script>`` 与
    ``<!--``）与 ``/``（挡 ``</``）为 ``\\u003c`` / ``\\u002f``——都是合法
    JSON/JS 转义，语义不变。token 本身已过 _TOKEN_RE（base64url，无这些
    字符），双保险：即便字符集将来放宽，注入也不破。"""
    return (json.dumps(token)
            .replace("<", "\\u003c")
            .replace("/", "\\u002f"))


def inject_token(html: bytes, token: str) -> bytes:
    """把 token 注入被服务的 index.html（</head> 前；无 head 则前置）。"""
    snippet = (_TOKEN_SNIPPET % _js_string_literal(token)).encode("utf-8")
    marker = b"</head>"
    if marker in html:
        return html.replace(marker, snippet + marker, 1)
    return snippet + html
