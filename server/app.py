"""HTTP server 主体：路由 / error envelope / body 上限 / 静态资源。

- ThreadingHTTPServer，**硬编码 bind 127.0.0.1**（宪法：本地优先，新增网络面
  仅 localhost）；端口 env ``ZAI_PORT`` 默认 47820。
- error envelope 统一 ``{"error":{"code","message","details"}}``（errors.py）。
- POST body 上限 1MiB；未知 JSON 字段零容忍 400 UNKNOWN_FIELD（reveal 在
  本层校验，actions 的字段闸门归 inbox_writer/G1）。
- 鉴权四闸（原 PR3 TODO，v0.48.1 落地；机制与差异见 server/security.py）：
  Host 回环白名单（每请求，anti-rebind）→ Origin 白名单（POST，present 才
  查，anti-CSRF）→ Content-Type: application/json（POST，杀 simple-request
  向量）→ per-install instance token（POST 一律必带 X-Zai-Token；token 由
  server 注入被服务的 index.html）。GET 保持 token-light（同源纪律 + 永不
  发 CORS 头，跨源页面读不到任何响应）。

契约：docs/CONTRACT.md §49（路由/SSE/CSP/auth model/error envelope/
localhost 例外的法源）。
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

from server import board_source, files, health, inbox_writer, paths, security
from server.errors import (ApiError, ForbiddenError, InvalidFieldError,
                           NotFoundError, NotImplementedError501,
                           UnauthorizedError, UnknownFieldError)
from server.sse import (CONNECTED_FRAME, HEARTBEAT_FRAME, HEARTBEAT_SECONDS,
                        EventHub)
from server.watcher import BoardWatcher

BIND_HOST = "127.0.0.1"   # 硬编码——绝不做成可配置（隐私宪法）
DEFAULT_PORT = 47820
MAX_BODY_BYTES = 1 << 20  # 1MiB

# web/dist 缺席时的占位页（A5 的 vite build 落地前 dev-preview 也能自检）
_PLACEHOLDER_HTML = (b"<!doctype html><meta charset='utf-8'>"
                     b"<title>zai server</title>"
                     b"<p>server is up. <code>web/dist</code> not built yet "
                     b"&mdash; run <code>cd web && npm run build</code>.</p>"
                     b"<p><a href='/api/board'>/api/board</a></p>")


class Handler(BaseHTTPRequestHandler):
    server_version = "zai-server/0.1"
    protocol_version = "HTTP/1.1"
    # slowloris 兜底：本地单用户，15s 足够（act/webui.py 同款）
    timeout = 15

    # ------------------------------------------------------------------ #
    # 基础发送
    # ------------------------------------------------------------------ #
    def _emit_security_headers(self, *, frameable: bool = False) -> None:
        """每个响应（含 SSE 流）共用的安全头——单一真源，防某条路径漏发。

        永不发 Access-Control-Allow-Origin（跨源页面不许读任何响应）。反嵌
        （webui X-Frame-Options 同款）：token 注入页绝不进别人的 iframe；例外
        = 交付物（详情抽屉经同源 <iframe sandbox> 预览，放行 SAMEORIGIN，其
        CSP sandbox 由 files.py 自带，不叠 frame-ancestors）。"""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if frameable:
            self.send_header("X-Frame-Options", "SAMEORIGIN")
        else:
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "frame-ancestors 'none'")

    def _send_bytes(self, status: int, body: bytes, ctype: str,
                    extra: Optional[dict] = None, *,
                    frameable: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._emit_security_headers(frameable=frameable)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8",
                         {"Cache-Control": "no-store"})

    def _send_api_error(self, err: ApiError) -> None:
        self._send_json(err.status, err.envelope())

    # ------------------------------------------------------------------ #
    # 请求入口（统一 try/except：受控错误 → envelope；其余 → 500 不泄栈）
    # ------------------------------------------------------------------ #
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        try:
            path = unquote(urlsplit(self.path).path)
            if "\x00" in path:
                raise InvalidFieldError("NUL in path")
            self._check_auth(method)
            if method == "GET":
                self._route_get(path)
            else:
                self._route_post(path)
        except ApiError as err:
            self._send_api_error(err)
        except NotImplementedError:
            # 防御性兜底（inbox_writer 已落地，正常路径不再走到这）——诚实 501，不装成功
            self._send_api_error(NotImplementedError501(
                "this endpoint is not wired up yet"))
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端提前挂断——正常噪音
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._send_api_error(ApiError("internal error"))

    # ------------------------------------------------------------------ #
    # 鉴权四闸（§49 auth model；机制与 webui 差异注在 server/security.py）
    # ------------------------------------------------------------------ #
    def _check_auth(self, method: str) -> None:
        ctx = self.server.ctx  # type: ignore[attr-defined]
        # Host 闸：每个请求（页面加载也算）——DNS-rebinding 防线。
        # 拒绝路径 body 未读，残字节会污染 keep-alive——连接必须关。
        if not security.host_ok(self.headers.get("Host")):
            self.close_connection = True
            raise ForbiddenError("bad host")
        if method != "POST":
            return  # GET/HEAD token-light：无 CORS 头，跨源页面读不到响应
        origin = self.headers.get("Origin")
        if origin is not None and not security.origin_ok(
                origin, ctx.allowed_origins):
            self.close_connection = True
            raise ForbiddenError("bad origin")
        if not security.content_type_is_json(
                self.headers.get("Content-Type")):
            # 415 复用 INVALID_FIELD（§49 的 413 先例：status 已表意，
            # 不为 loopback 面扩词表）
            self.close_connection = True
            raise InvalidFieldError("Content-Type must be application/json",
                                    status=415)
        if not security.token_ok(self.headers.get(security.TOKEN_HEADER),
                                 ctx.token):
            self.close_connection = True
            raise UnauthorizedError("missing or bad token")

    # ------------------------------------------------------------------ #
    # GET 路由
    # ------------------------------------------------------------------ #
    def _route_get(self, path: str) -> None:
        ctx = self.server.ctx  # type: ignore[attr-defined]
        if path == "/api/board":
            body = board_source.board_bytes(ctx.home)
            self._send_bytes(200, body, "application/json; charset=utf-8",
                             {"Cache-Control": "no-store"})
        elif path.startswith("/api/cards/"):
            card_id = path[len("/api/cards/"):]
            self._send_json(200, board_source.card_detail(ctx.home, card_id))
        elif path == "/api/health":
            # §47.4 管线活性（token-light GET，同源纪律同 /api/board）：心跳
            # 年龄 + 看板新鲜度 + 连崩计数 → 一个 verdict，web 顶部横幅据此
            # 诚实报「后台服务卡住/停了」——退役中的 Mac app 横幅的替身。
            self._send_json(200, health.snapshot(ctx.home))
        elif path == "/api/events":
            self._serve_events(ctx.hub)
        elif path.startswith("/files/deliverables/"):
            rest = path[len("/files/deliverables/"):].split("/")
            if len(rest) != 2:
                raise NotFoundError("not found", {"path": path})
            body, ctype, extra = files.serve_deliverable(ctx.home, rest[0],
                                                         rest[1])
            extra["Cache-Control"] = "no-store"
            self._send_bytes(200, body, ctype, extra, frameable=True)
        elif path.startswith("/api/") or path.startswith("/files/"):
            raise NotFoundError("not found", {"path": path})
        else:
            self._serve_static(ctx.static_dir, path)

    # ------------------------------------------------------------------ #
    # POST 路由
    # ------------------------------------------------------------------ #
    def _route_post(self, path: str) -> None:
        ctx = self.server.ctx  # type: ignore[attr-defined]
        if path == "/api/actions":
            payload = self._read_json_body()
            # 字段级白名单 + 动词闸门在 inbox_writer（G1）——单一职责，
            # 校验规则只写一处（module docstring 已冻结）
            result = inbox_writer.write_action(payload, home=ctx.home)
            # steer 标注（M6，add-only 响应键；inbox 文件形状零改动）：
            # executing 卡上的 **owner** comment 会被 actd 按 steer 类经
            # §44.3 briefing 机制转投递——这里只诚实报「queued」（落盘即排
            # 队），delivered/dropped 状态由投影回流（vnext-amendments.md
            # §M6.1）。agent ingress（via:"agent"，T-28）的 comment 只记录
            # 不 steer——标注必须反映实际裁决：steer:false、无 steer_status。
            if result.get("action") == "comment" and board_source.is_executing(
                    ctx.home, str(payload.get("id") or "")):
                owner = result.get("via") == "web"
                result["steer"] = owner
                if owner:
                    result["steer_status"] = "queued"
            self._send_json(200, result)
        elif path == "/api/reveal":
            payload = self._read_json_body()
            unknown = set(payload) - {"card_id"}
            if unknown:
                raise UnknownFieldError("unknown field",
                                        {"fields": sorted(unknown)})
            card_id = payload.get("card_id")
            if not isinstance(card_id, str):
                raise InvalidFieldError("card_id must be a string")
            self._send_json(200, files.reveal(ctx.home, card_id))
        else:
            raise NotFoundError("not found", {"path": path})

    def _read_json_body(self) -> dict:
        length_raw = self.headers.get("Content-Length")
        if length_raw is None:
            raise InvalidFieldError("Content-Length required")
        try:
            length = int(length_raw)
        except ValueError:
            raise InvalidFieldError("bad Content-Length")
        if length < 0:
            raise InvalidFieldError("bad Content-Length")
        if length > MAX_BODY_BYTES:
            # CONTRACT §49（v0.48 追认）：413 复用 INVALID_FIELD——status 已
            # 表意，不为 loopback 面扩词表
            raise InvalidFieldError("body too large",
                                    {"limit": MAX_BODY_BYTES}, status=413)
        raw = self.rfile.read(length)
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise InvalidFieldError("body is not valid JSON")
        if not isinstance(doc, dict):
            raise InvalidFieldError("body must be a JSON object")
        return doc

    # ------------------------------------------------------------------ #
    # SSE
    # ------------------------------------------------------------------ #
    def _serve_events(self, hub: EventHub) -> None:
        if self.command == "HEAD":
            self._send_bytes(200, b"", "text/event-stream; charset=utf-8")
            return
        q = hub.subscribe()
        # 流式响应无 Content-Length：本连接不复用（EventSource 断线即全量
        # refetch + 重连，无 last-event-id 契约）
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            # M4：SSE 流也过同一套安全头（nosniff/Referrer-Policy/X-Frame/CSP）
            # ——此前手写头漏发，事件流成了唯一无 nosniff 的响应面。
            self._emit_security_headers()
            self.end_headers()
            self.wfile.write(CONNECTED_FRAME)
            self.wfile.flush()
            while True:
                try:
                    frame = q.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    frame = HEARTBEAT_FRAME  # 25s 注释行心跳
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 客户端断开——正常退出
        except Exception:
            # 流已开：此刻 _dispatch 的兜底再写 500 envelope 只会污染
            # event-stream——记日志、静默断流（客户端断线即全量 refetch + 重连）
            traceback.print_exc(file=sys.stderr)
        finally:
            hub.unsubscribe(q)

    # ------------------------------------------------------------------ #
    # 静态资源（web/dist）
    # ------------------------------------------------------------------ #
    def _serve_static(self, dist: Path, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        try:
            real_dist = dist.resolve(strict=True)
            target = (dist / rel).resolve()
        except OSError:
            # dist 尚未 build：根路径给占位页，其余 404
            if path == "/":
                self._send_bytes(200, _PLACEHOLDER_HTML,
                                 "text/html; charset=utf-8")
                return
            raise NotFoundError("not found", {"path": path})
        # 包含性检查挡住 ../ 穿越；目录请求回落 index.html
        if not str(target).startswith(str(real_dist) + os.sep) \
                and target != real_dist:
            raise NotFoundError("not found", {"path": path})
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # SPA 深链（无扩展名路径）回落 index.html；带扩展名的按缺失处理
            index = real_dist / "index.html"
            if "." not in Path(rel).name and index.is_file():
                target = index
            else:
                raise NotFoundError("not found", {"path": path})
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        cache = ("public, max-age=31536000, immutable"
                 if "/assets/" in str(target) else "no-cache")
        body = target.read_bytes()
        if target.name == "index.html":
            # instance token server 端注入（security.inject_token 的同源
            # 纪律）：只有本面服务的页面拿得到，跨源端点永不外发
            body = security.inject_token(
                body, self.server.ctx.token)  # type: ignore[attr-defined]
        self._send_bytes(200, body, ctype, {"Cache-Control": cache})

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # 保留一行式访问日志到 stderr；SSE 心跳不经此处，噪音可控
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


class _Context:
    """挂在 server 实例上的共享只读上下文（测试注入缝）。"""

    def __init__(self, home: Path, hub: EventHub, static_dir: Path,
                 token: str, allowed_origins: frozenset) -> None:
        self.home = home
        self.hub = hub
        self.static_dir = static_dir
        self.token = token
        self.allowed_origins = allowed_origins


def make_server(port: Optional[int] = None,
                home: "str | Path | None" = None,
                static_dir: Optional[Path] = None,
                start_watcher: bool = True) -> ThreadingHTTPServer:
    """组装 server（port=0 → 随机端口，测试用）。返回的实例带 ``.ctx`` 与
    ``.watcher``（可能为 None）；调用方负责 serve_forever / shutdown。"""
    if port is None:
        port = int(os.environ.get("ZAI_PORT", DEFAULT_PORT))
    resolved_home = paths.home_dir(home)
    # token 读不出也写不进（OSError）= fail-closed：宁可起不来，不裸奔
    token = security.load_or_create_token(resolved_home)
    hub = EventHub()
    httpd = ThreadingHTTPServer((BIND_HOST, port), Handler)
    bound_port = httpd.server_address[1]  # port=0 时这里才是真端口
    httpd.ctx = _Context(resolved_home, hub,  # type: ignore[attr-defined]
                         static_dir or paths.web_dist_dir(),
                         token, security.allowed_origins(bound_port))
    httpd.watcher = None  # type: ignore[attr-defined]
    if start_watcher:
        watcher = BoardWatcher(resolved_home, hub)
        watcher.start()
        httpd.watcher = watcher  # type: ignore[attr-defined]
    return httpd


def main() -> int:
    httpd = make_server()
    host, port = httpd.server_address[:2]
    print(f"zai server: http://{host}:{port}  "
          f"(AIASSISTANT_HOME={httpd.ctx.home})",  # type: ignore[attr-defined]
          flush=True)
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if httpd.watcher is not None:  # type: ignore[attr-defined]
            httpd.watcher.stop()  # type: ignore[attr-defined]
        httpd.server_close()
    return 0
