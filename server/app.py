"""HTTP server 主体：路由 / error envelope / body 上限 / 静态资源。

- ThreadingHTTPServer，**硬编码 bind 127.0.0.1**（宪法：本地优先，新增网络面
  仅 localhost）；端口 env ``ZAI_PORT`` 默认 47820。
- error envelope 统一 ``{"error":{"code","message","details"}}``（errors.py）。
- POST body 上限 1MiB；未知 JSON 字段零容忍 400 UNKNOWN_FIELD（reveal 在
  本层校验，actions 的字段闸门归 inbox_writer/G1）。
- PR1 无 token（localhost 单用户过渡）。
  # TODO(PR3): instance token —— 在 _route_* 之前加统一鉴权挂点。
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

from server import board_source, files, inbox_writer, paths
from server.errors import (ApiError, InvalidFieldError, NotFoundError,
                           NotImplementedError501, UnknownFieldError)
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
    def _send_bytes(self, status: int, body: bytes, ctype: str,
                    extra: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
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
        elif path == "/api/events":
            self._serve_events(ctx.hub)
        elif path.startswith("/files/deliverables/"):
            rest = path[len("/files/deliverables/"):].split("/")
            if len(rest) != 2:
                raise NotFoundError("not found", {"path": path})
            body, ctype, extra = files.serve_deliverable(ctx.home, rest[0],
                                                         rest[1])
            extra["Cache-Control"] = "no-store"
            self._send_bytes(200, body, ctype, extra)
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
            self._send_json(200, inbox_writer.write_action(payload, home=ctx.home))
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
            # // TODO(contract): 413 的 envelope code 契约未点名，保守复用
            # INVALID_FIELD（不新增词表项）
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
        self._send_bytes(200, target.read_bytes(), ctype,
                         {"Cache-Control": cache})

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # 保留一行式访问日志到 stderr；SSE 心跳不经此处，噪音可控
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


class _Context:
    """挂在 server 实例上的共享只读上下文（测试注入缝）。"""

    def __init__(self, home: Path, hub: EventHub, static_dir: Path) -> None:
        self.home = home
        self.hub = hub
        self.static_dir = static_dir


def make_server(port: Optional[int] = None,
                home: "str | Path | None" = None,
                static_dir: Optional[Path] = None,
                start_watcher: bool = True) -> ThreadingHTTPServer:
    """组装 server（port=0 → 随机端口，测试用）。返回的实例带 ``.ctx`` 与
    ``.watcher``（可能为 None）；调用方负责 serve_forever / shutdown。"""
    if port is None:
        port = int(os.environ.get("ZAI_PORT", DEFAULT_PORT))
    resolved_home = paths.home_dir(home)
    hub = EventHub()
    httpd = ThreadingHTTPServer((BIND_HOST, port), Handler)
    httpd.ctx = _Context(resolved_home, hub,  # type: ignore[attr-defined]
                         static_dir or paths.web_dist_dir())
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
