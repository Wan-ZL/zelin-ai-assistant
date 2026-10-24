"""HTTP server 主体：路由 / error envelope / body 上限 / 静态资源（CONTRACT §49）。

- ThreadingHTTPServer，**硬编码 bind 127.0.0.1**（宪法：本地优先，新增网络面
  仅 localhost）；端口 env ``ZAI_PORT`` 默认 47820。
- 日志噪音闸（§54.2 追记，issue #314）：`_Server.handle_error` 只吞三个连接类
  异常（客户端关 tab / 刷新时的 reset，抛在 Handler 之前）、其余照打全栈；
  `log_message` 行首带本地 ISO 时间戳；`log_request` 把轮询路径按 `(path, 状态码)`
  分桶、5 分钟窗口采样（状态码一变立刻写，被吃掉的条数随下一行报出来，
  `ZAI_LOG_POLLS=1` 关采样）。日志只减噪、永不删（§55 审计 L3）。
- error envelope 统一 ``{"error":{"code","message","details"}}``（errors.py）。
- POST body 上限 1MiB；未知 JSON 字段零容忍 400 UNKNOWN_FIELD（各 handler 的模块自己
  做字段白名单）。
- 鉴权四闸（机制见 server/security.py）：Host 回环白名单（每请求，anti-rebind）→
  Origin 白名单（写请求，present 才查，anti-CSRF）→ Content-Type: application/json
  （写请求，杀 simple-request 向量）→ per-install instance token（写请求一律必带
  X-Zai-Token；token 由 server 注入被服务的 index.html）。写请求 = POST 与 PUT。
  GET 保持 token-light（同源纪律 + 永不发 CORS 头，跨源页面读不到任何响应）。

v2.0.0 路由集（§49 修订；server 是唯一的 HTTP 面，读 recap_store / settings / update /
permissions，可派生 ``act.recap``）：

- 会议纪要（§63，server/recaps.py）：GET /api/recaps（列表 + 三栏计数）、
  POST /api/recaps/generate、POST /api/recaps/revert（分离派生 ``python -m act.recap``）、
  POST /api/recaps/mark、GET /api/recaps/history?key=、GET/PUT /api/settings/recap。
- 设置（§59 / §68.1）：GET /api/settings（目录）+ GET/PUT /api/settings/{section}
  （server/settings_catalog.py）、GET/PUT /api/settings/models（server/settings.py）、
  GET/PUT /api/settings/display（server/display.py）。
- 凭证（§68.3）：GET /api/secrets + PUT /api/secrets/{name} + POST /api/secrets/{name}/verify。
- 关于 / 更新（§26 / §79）：GET /api/about、POST /api/update/check、POST /api/update/install。
- 权限 / 体检 / 诊断（§68.3 / §68.4）：GET /api/permissions、GET /api/doctor、GET /api/diagnostics、
  GET /api/logs/{name}。
- 向导（§68.5）：GET /api/setup、GET /api/setup/engine、GET /api/setup/vaults、
  POST /api/setup/{config-from-example,complete,reset}。
- 录制与导入（§15.2 / §72.1）：POST /api/ingest/export、POST /api/ingest/run、
  GET /api/ingest/jobs/{id}、GET /api/screenpipe/disk。
- 目录（§25 / §28）：GET /api/failures、GET /api/notifications。
- 目录字段 / 定位（§68.1）：POST /api/folders/{open,create}、POST /api/reveal {target, mode?}。
- 活性与推送：GET /api/health、GET /api/events（SSE；``state/recap/`` 变 → ``recaps.updated``）。

退役（tombstone，全文见 tag full）：/api/board、/api/cards/*、/api/actions、/api/attachments、
/api/worktrees*、/api/skills、/api/mcp、/api/claude-sessions、/api/claude-code/default-model、
/api/terminal、/api/maintainer/*、/api/uninstall/terminal、/api/repair/actd、
/api/setup/seed-dashboard、/api/slack/*、/api/sync*、/api/voice*、/api/radars*、/api/analytics、
/api/telemetry/*、/api/lanes、/api/search-index、/api/ai-fix、/api/materials/*、
/api/settings/daily-loop、/api/self-improve/*、/files/deliverables/*；SSE ``board.updated``。

契约：docs/CONTRACT.md §49（路由/SSE/CSP/auth model/error envelope/localhost 例外的法源）、
§54.2（server 生命周期与本进程的访问日志纪律）。
"""
from __future__ import annotations

import errno
import json
import mimetypes
import os
import queue
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, unquote, urlsplit

from server import (about, diagnostics, display, doctor_run, failure_catalog,
                    folders, health, ingest_run, notify_catalog, paths,
                    permissions, recaps, screenpipe_disk, secrets_store,
                    security, settings, settings_catalog, setup)
from server.errors import (ApiError, ForbiddenError, InvalidFieldError,
                           NotFoundError, NotImplementedError501,
                           UnauthorizedError)
from server.sse import (CONNECTED_FRAME, HEARTBEAT_FRAME, HEARTBEAT_SECONDS,
                        EventHub)
from server.watcher import RecapWatcher

BIND_HOST = "127.0.0.1"   # 硬编码——绝不做成可配置（隐私宪法）
DEFAULT_PORT = 47820
MAX_BODY_BYTES = 1 << 20  # 1MiB
# 413 / 400 前把在路上的 body 读掉丢弃的时间上限（Handler._body_length）：真在发体的
# 客户端毫秒级读完；只发头探路的（判例）等这么久就放行
LINGER_SECONDS = 1.0

# web/dist 缺席时的占位页（A5 的 vite build 落地前 dev-preview 也能自检）
_PLACEHOLDER_HTML = (b"<!doctype html><meta charset='utf-8'>"
                     b"<title>zai server</title>"
                     b"<p>server is up. <code>web/dist</code> not built yet "
                     b"&mdash; run <code>cd web && npm run build</code>.</p>"
                     b"<p><a href='/api/recaps'>/api/recaps</a></p>")

# 访问日志的噪音闸（§54.2 追记 2026-09-14）：日志只减噪、永不删（§55 审计 L3）。
# 客户端关 tab / 刷新时 keep-alive 连接被 reset——异常发生在 handle_one_request
# 读请求行时，在 Handler 之前，_dispatch 的 except 够不着它。
_QUIET_CONN_ERRORS = (BrokenPipeError, ConnectionResetError,
                      ConnectionAbortedError)
# 页面轮询这两条；按 (path, 状态码) 分桶采样，其余路径一律照写
_POLL_PATHS = ("/api/recaps", "/api/health")
_POLL_WINDOW_SECONDS = 300.0
_LOG_POLLS_ENV = "ZAI_LOG_POLLS"  # =1 → 关掉采样（逐条写，排障用的 debug 档）


class _PollSampler:
    """轮询端点的访问日志采样器：每个 ``(path, code)`` 每 ``window`` 秒最多一行。

    分桶键含状态码，所以**状态一变立刻写**（新桶 = 没有 last_ts；状态变化是信
    号，不是噪音），只有同 path 同码的重复才被掐掉——「同一条轮询路径反复返回
    同一个错误」和「反复返回 200」是同一种洪水（owner 那份日志里 1549 行一模
    一样的 `/api/board 404`）。被吃掉的条数记在 ``(last_ts, suppressed)`` 里，
    随下一条真写出去的行报出来——日志从此不说谎（没有静默丢弃）。桶数有界：
    ``len(_POLL_PATHS) × 见过的状态码``，几十条上限，不会长。线程安全：
    ThreadingHTTPServer 每连接一个线程。"""

    def __init__(self, window: float = _POLL_WINDOW_SECONDS) -> None:
        self.window = window
        self._lock = threading.Lock()
        self._seen: "dict[tuple[str, int], tuple[float, int]]" = {}

    def decide(self, path: str, code: int, now: float) -> "tuple[bool, int]":
        """→ ``(emit, suppressed)``；``emit=False`` = 这条被采样掉。"""
        key = (path, code)
        with self._lock:
            last, suppressed = self._seen.get(key, (None, 0))
            if last is not None and now - last < self.window:
                self._seen[key] = (last, suppressed + 1)
                return (False, 0)
            self._seen[key] = (now, 0)
            return (True, suppressed)


_POLL_SAMPLER = _PollSampler()


def _log_polls_verbatim(env: "dict[str, str] | None" = None) -> bool:
    """``ZAI_LOG_POLLS=1`` → 轮询也逐条写（采样关闭）。"""
    raw = (env if env is not None else os.environ).get(_LOG_POLLS_ENV, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _status_int(code) -> Optional[int]:
    """访问日志的状态码 → ``int``；形状不明（``"-"`` / None）→ None。"""
    try:
        return int(getattr(code, "value", code))
    except (TypeError, ValueError):
        return None


def _quiet_candidate(path: str, code) -> bool:
    """这条访问日志该不该过采样器：轮询路径 + 认得出的状态码就算。

    非 2xx 也进闸——采样器按 ``(path, code)`` 分桶，状态一变立刻写，掐掉的只是
    「同一条轮询路径反复返回同一个码」（含反复 404）。状态码形状不明的行不进
    闸，逐条写。"""
    return path in _POLL_PATHS and _status_int(code) is not None


def _access_line(address: str, text: str, now: "float | None" = None) -> str:
    """一行访问日志：``<本地 ISO 时间戳> <client> - <text>``（自带换行）。

    BaseHTTPRequestHandler 的 `log_date_time_string` 不带年也不带时区，对不上
    「我 14:34 点了哪个按钮、server 收到了什么」；这里换成可排序、跨时区可读
    的一种形状（`2026-09-14T14:34:05-0400`）。"""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))
    return "%s %s - %s\n" % (stamp, address, text)


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

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def _dispatch(self, method: str) -> None:
        try:
            self._handle(method)
        except ApiError as err:
            self._send_api_error(err)
        except NotImplementedError:
            # 防御性兜底（正常路径不会走到这）——诚实 501，不装成功
            self._send_api_error(NotImplementedError501(
                "this endpoint is not wired up yet"))
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端提前挂断——正常噪音
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._send_api_error(ApiError("internal error"))

    def _handle(self, method: str) -> None:
        """一次请求的主线：路径解码 → 鉴权 → 按动词路由（异常交 _dispatch 兜）。"""
        path = unquote(urlsplit(self.path).path)
        if "\x00" in path:
            raise InvalidFieldError("NUL in path")
        self._check_auth(method, path)
        if method == "GET":
            self._route_get(path)
        elif method == "PUT":
            self._route_put(path)
        else:
            self._route_post(path)

    # ------------------------------------------------------------------ #
    # 鉴权四闸（§49 auth model；机制与 webui 差异注在 server/security.py）
    # ------------------------------------------------------------------ #
    def _reject(self, err: ApiError) -> None:
        """闸门拒绝：body 未读，残字节会污染 keep-alive——连接必须关。"""
        self.close_connection = True
        raise err

    def _check_auth(self, method: str, path: str) -> None:
        # Host 闸：每个请求（页面加载也算）——DNS-rebinding 防线。
        if not security.host_ok(self.headers.get("Host")):
            self._reject(ForbiddenError("bad host"))
        if method in ("POST", "PUT"):
            self._check_write_auth(path)
        # GET/HEAD token-light：无 CORS 头，跨源页面读不到响应

    def _check_write_auth(self, path: str) -> None:
        """写请求的后三闸：Origin（present 才查）→ Content-Type → instance token。

        Content-Type 闸按路径取 media type：二进制体路由（``_POST_RAW_ROUTES``，
        今日只有 §10bis 的贴图上传 image/png）之外一律 application/json——放宽
        纪律见 security.content_type_is docstring。"""
        ctx = self.server.ctx  # type: ignore[attr-defined]
        origin = self.headers.get("Origin")
        if origin is not None and not security.origin_ok(
                origin, ctx.allowed_origins):
            self._reject(ForbiddenError("bad origin"))
        want = _write_media_type(path)
        if not security.content_type_is(self.headers.get("Content-Type"), want):
            # 415 复用 INVALID_FIELD（§49 的 413 先例：status 已表意，
            # 不为 loopback 面扩词表）
            self._reject(InvalidFieldError(
                f"Content-Type must be {want}", status=415))
        if not security.token_ok(self.headers.get(security.TOKEN_HEADER),
                                 ctx.token):
            self._reject(UnauthorizedError("missing or bad token"))

    # ------------------------------------------------------------------ #
    # GET 路由
    # ------------------------------------------------------------------ #
    def _route_get(self, path: str) -> None:
        ctx = self.server.ctx  # type: ignore[attr-defined]
        if path.startswith("/api/"):
            self._route_api_get(ctx, path)
        elif path.startswith("/files/"):
            raise NotFoundError("not found", {"path": path})   # v1 交付物面已退役
        else:
            self._serve_static(ctx.static_dir, path)

    def _route_api_get(self, ctx, path: str) -> None:
        if path == "/api/events":
            self._serve_events(ctx.hub)
        else:
            # 纯 JSON 读面（health / 设置面 / 目录 / 诊断…）——表驱动：精确表先，前缀表后
            handler = _lookup(_GET_JSON_ROUTES, _GET_PREFIX_ROUTES, path)
            if handler is None:
                raise NotFoundError("not found", {"path": path})
            self._send_json(200, handler(ctx, self._query()))

    # ------------------------------------------------------------------ #
    # POST 路由
    # ------------------------------------------------------------------ #
    def _route_post(self, path: str) -> None:
        ctx = self.server.ctx  # type: ignore[attr-defined]
        raw = _POST_RAW_ROUTES.get(path)
        if raw is not None:
            # 二进制体路由（§10bis 贴图上传）：不解析 JSON、自带 body 上限
            _media_type, limit, fn = raw
            self._send_json(200, fn(ctx, self._read_raw_body(limit)))
            return
        handler = _lookup(_POST_JSON_ROUTES, _POST_PREFIX_ROUTES, path)
        if handler is None:
            raise NotFoundError("not found", {"path": path})
        # body 只在路由命中后才读（未知路径 404 不消费 body）
        self._send_json(200, handler(ctx, self._read_json_body()))

    # ------------------------------------------------------------------ #
    # PUT 路由（§59 设置面；四闸同 POST）
    # ------------------------------------------------------------------ #
    def _route_put(self, path: str) -> None:
        ctx = self.server.ctx  # type: ignore[attr-defined]
        handler = _lookup(_PUT_JSON_ROUTES, _PUT_PREFIX_ROUTES, path)
        if handler is None:
            raise NotFoundError("not found", {"path": path})
        # 字段白名单 + 形状校验 + diff-write 都在各 handler 的模块里（单一职责）
        self._send_json(200, handler(ctx, self._read_json_body()))

    def _query(self) -> dict:
        """URL query → 扁平 dict（同名键后者胜；空值保留）——GET 表路由的第二个实参。"""
        return dict(parse_qsl(urlsplit(self.path).query, keep_blank_values=True))

    def _read_raw_body(self, limit: int) -> bytes:
        """二进制体（贴图上传）：Content-Length 过 ``limit`` 后整段读回，不解析。"""
        return self.rfile.read(self._body_length(limit))

    def _body_length(self, limit: int = MAX_BODY_BYTES) -> int:
        """Content-Length 闸（``_content_length``）的 Handler 半边：400 / 413 时 body
        一字未读——残字节留在 keep-alive 上会被当成下一条请求行（400 + 断连，客户端
        看到的是 BrokenPipe 而不是 envelope）。所以 413 拒绝前先 **lingering close**：
        把已在路上的 body 读掉丢弃（体确定 > ``limit``，读前 ``limit`` 字节即够；最多
        ``LINGER_SECONDS``——只发头不发体的客户端不会把连接卡到 15s），再经 ``_reject``
        关连接（400 = 头缺失 / 非数，长度未知，不读）。裁决本身仍只看 Content-Length：
        超限的体不解析、不落盘。"""
        try:
            return _content_length(self.headers.get("Content-Length"), limit)
        except ApiError as err:
            if err.status == 413:
                self._discard_body(limit)
            self._reject(err)
            raise  # unreachable（_reject 必抛）；让类型检查看到出口

    def _discard_body(self, count: int) -> None:
        """读掉并丢弃 ``count`` 字节的请求体；超时 / 客户端半路挂断就到此为止。"""
        self.connection.settimeout(LINGER_SECONDS)
        try:
            while count > 0:
                chunk = self.rfile.read(min(count, 1 << 16))
                if not chunk:
                    break  # 客户端已关写端：没有更多了
                count -= len(chunk)
        except OSError:
            pass  # 客户端不发体（超时）/ 连接重置：能读多少算多少，照常回 envelope

    def _read_json_body(self) -> dict:
        raw = self.rfile.read(self._body_length())
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
            self._stream_events(q)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 客户端断开——正常退出
        except Exception:
            # 流已开：此刻 _dispatch 的兜底再写 500 envelope 只会污染
            # event-stream——记日志、静默断流（客户端断线即全量 refetch + 重连）
            traceback.print_exc(file=sys.stderr)
        finally:
            hub.unsubscribe(q)

    def _stream_events(self, q: "queue.Queue") -> None:
        """头 + connected 帧，然后阻塞转发订阅队列（空等 25s 发心跳注释行）。"""
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

    # ------------------------------------------------------------------ #
    # 静态资源（web/dist）
    # ------------------------------------------------------------------ #
    def _serve_static(self, dist: Path, path: str) -> None:
        target = _static_target(dist, path)
        if target is None:
            # dist 尚未 build：根路径给占位页，其余 404
            if path == "/":
                self._send_bytes(200, _PLACEHOLDER_HTML,
                                 "text/html; charset=utf-8")
                return
            raise NotFoundError("not found", {"path": path})
        body = target.read_bytes()
        if target.name == "index.html":
            # instance token server 端注入（security.inject_token 的同源
            # 纪律）：只有本面服务的页面拿得到，跨源端点永不外发
            body = security.inject_token(
                body, self.server.ctx.token)  # type: ignore[attr-defined]
        self._send_bytes(200, body, _static_ctype(target),
                         {"Cache-Control": _static_cache(target)})

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # 一行式访问日志到 stderr，行首带本地 ISO 时间戳（§54.2 追记）；本方法
        # 是这条行的唯一写者，SSE 心跳不经此处，噪音可控
        sys.stderr.write(_access_line(self.address_string(), fmt % args))

    def log_request(self, code="-", size="-") -> None:
        """访问日志的采样闸（§54.2 追记）：轮询端点同 path 同码每 5 分钟一行。

        状态码一变立刻写（状态变化 = 信号）；非轮询路径、以及 ``ZAI_LOG_POLLS=1``
        一律逐条写——被采样掉的条数由下一条真写出去的行报出来。"""
        suffix = ""
        path = (getattr(self, "path", "") or "").split("?", 1)[0]
        if not _log_polls_verbatim() and _quiet_candidate(path, code):
            emit, suppressed = _POLL_SAMPLER.decide(
                path, _status_int(code), time.time())
            if not emit:
                return
            if suppressed:
                suffix = " (+%d suppressed in the last %ds)" % (
                    suppressed, int(_POLL_SAMPLER.window))
        self.log_message('"%s" %s %s%s', self.requestline,
                         str(getattr(code, "value", code)), str(size), suffix)


# --------------------------------------------------------------------------- #
# 请求体 / 静态资源的纯函数小件（Handler 方法只做发送）
# --------------------------------------------------------------------------- #
def _content_length(raw: Optional[str], limit: int = MAX_BODY_BYTES) -> int:
    """Content-Length 头 → 字节数；缺失/非数/负数 400，超 ``limit`` 413
    （JSON 面 1MiB；二进制体路由各自带上限，见 ``_POST_RAW_ROUTES``）。"""
    if raw is None:
        raise InvalidFieldError("Content-Length required")
    try:
        length = int(raw)
    except ValueError:
        raise InvalidFieldError("bad Content-Length")
    if length < 0:
        raise InvalidFieldError("bad Content-Length")
    if length > limit:
        # CONTRACT §49（v0.48 追认）：413 复用 INVALID_FIELD——status 已
        # 表意，不为 loopback 面扩词表
        raise InvalidFieldError("body too large",
                                {"limit": limit}, status=413)
    return length


def _write_media_type(path: str) -> str:
    """写请求 Content-Type 闸要求的 media type：二进制体路由登记的类型，否则 JSON。"""
    raw = _POST_RAW_ROUTES.get(path)
    return raw[0] if raw is not None else "application/json"


def _inside(target: Path, real_dist: Path) -> bool:
    """包含性检查：target 在 dist 之内（或就是 dist）——挡住 ../ 穿越。"""
    return str(target).startswith(str(real_dist) + os.sep) or target == real_dist


def _pick_file(target: Path, real_dist: Path, rel: str, path: str) -> Path:
    """目录请求回落 index.html；SPA 深链（无扩展名路径）回落 index.html；
    带扩展名的缺失按 404。"""
    if target.is_dir():
        target = target / "index.html"
    if target.is_file():
        return target
    index = real_dist / "index.html"
    if "." not in Path(rel).name and index.is_file():
        return index
    raise NotFoundError("not found", {"path": path})


def _static_target(dist: Path, path: str) -> Optional[Path]:
    """web/dist 里要发的文件；None = dist 尚未 build（调用方决定占位页/404）。"""
    rel = path.lstrip("/") or "index.html"
    try:
        real_dist = dist.resolve(strict=True)
        target = (dist / rel).resolve()
    except OSError:
        return None
    if not _inside(target, real_dist):
        raise NotFoundError("not found", {"path": path})
    return _pick_file(target, real_dist, rel, path)


# 契约常量优先于宿主机的 mimetypes 表（§73）：`mimetypes` 读 Windows 注册表与
# /etc/mime.types，两者都能改写内置映射——PWA 安装清单发错类型就装不上，而这条
# wire 类型是法条不是本机配置。表里没有的扩展名照旧走 mimetypes。
_STATIC_CTYPES = {".webmanifest": "application/manifest+json"}


def _static_ctype(target: Path) -> str:
    pinned = _STATIC_CTYPES.get(target.suffix.lower())
    if pinned:
        return pinned
    return mimetypes.guess_type(target.name)[0] or "application/octet-stream"


def _static_cache(target: Path) -> str:
    # vite 的 hashed assets 可长缓存；其余（index.html 等）每次回源
    return ("public, max-age=31536000, immutable"
            if "/assets/" in str(target) else "no-cache")


# --------------------------------------------------------------------------- #
# 表驱动的 JSON 路由（读面 / 写面）——新增一个 JSON 端点 = 加一行，不再在
# _route_get/_route_post 里堆 elif（§58 复杂度门）。写面四闸在 _check_auth，
# 与表无关；每个 handler 自己做字段白名单（UNKNOWN_FIELD 零容忍）。
# --------------------------------------------------------------------------- #
def _lookup(exact: dict, prefixes: dict, path: str):
    """精确表命中优先；否则前缀表（``/api/logs/<name>`` 命中 ``/api/logs/``，尾段非空才算），
    前缀 handler 形状 (ctx, rest, arg) 在此归一成精确表的 (ctx, arg)。"""
    handler = exact.get(path)
    if handler is not None:
        return handler
    for prefix, fn in prefixes.items():
        if path.startswith(prefix) and len(path) > len(prefix):
            rest = path[len(prefix):]
            return lambda ctx, arg, fn=fn, rest=rest: fn(ctx, rest, arg)
    return None


def _flag(query: dict, key: str) -> bool:
    return str(query.get(key, "")).lower() in ("1", "true", "yes")


def _get_doctor(ctx, query: dict) -> dict:
    # §68.4 追记：?lang=zh|en → 子进程 AIASSISTANT_UI_LANG（doctor 人话随 web 当前语言；进缓存键）
    fast = query.get("fast", "1") not in ("0", "false", "no")
    return doctor_run.report(ctx.home, fast=fast, refresh=_flag(query, "refresh"),
                             lang=doctor_run.parse_lang(query.get("lang")))


def _get_diagnostics(ctx, query: dict) -> dict:
    return diagnostics.snapshot(ctx.home, refresh=_flag(query, "refresh"),
                                lang=doctor_run.parse_lang(query.get("lang")))


def _post_secret_verify(ctx, rest: str, payload: dict) -> dict:
    # /api/secrets/<name>/verify —— 尾段 "<name>/verify"；body 必须是 {}（零容忍）
    if not rest.endswith("/verify"):
        raise NotFoundError("not found", {"path": "/api/secrets/" + rest})
    # body 空 = 探已保存的；{value} = 粘贴即验证（不落盘，§68.3）
    return secrets_store.verify(ctx.home, rest[:-len("/verify")],
                                value=secrets_store.verify_payload_value(payload))


# GET 表 handler 形状：(ctx, query) → dict；query = URL query 的扁平 dict（_query）。
_GET_JSON_ROUTES = {
    # 活性（token-light GET）：server 在答话 + cron 链新鲜度 + 上次 recap 一轮（server/health.py）
    "/api/health": lambda ctx, query: health.snapshot(ctx.home),
    # §63 会议纪要列表（v2.0.0：取代 dashboard.json 的 recaps[] / recap_counts）
    "/api/recaps": lambda ctx, query: recaps.list_recaps(ctx.home, query),
    # §63.9 上一版：单份纪要存着的每一版 + 正文（?key=…）。只读
    "/api/recaps/history": lambda ctx, query: recaps.history(ctx.home, query),
    # §63 会议 recap 两把旋钮（enabled / default_language）+ 只读 default_shape
    "/api/settings/recap": lambda ctx, query: recaps.snapshot(ctx.home),
    # §59 模型旋钮（pipeline + fallback）的 effective 值 + canonical 下拉全集（server-owned）
    "/api/settings/models": lambda ctx, query: settings.models_snapshot(ctx.home),
    # §68.1 显示偏好三把旋钮（原 §54.1 第 12 项，该节 v2.0.0 退役）
    "/api/settings/display": lambda ctx, query: display.snapshot(ctx.home),
    # §68.1 设置目录全集（通用 section 的 field 描述 + effective 值；文案 server-owned）
    "/api/settings": lambda ctx, query: settings_catalog.snapshot(ctx.home),
    # §68.3 凭证状态（present / verifiable；值永不回显）
    "/api/secrets": lambda ctx, query: secrets_store.snapshot(ctx.home),
    # §68.3 权限体检（FDA 清单 + TCC 相关 doctor 行）
    "/api/permissions": lambda ctx, query: permissions.snapshot(ctx.home, refresh=_flag(query, "refresh")),
    # §68.4 诊断页（doctor + health + install_report + 日志清单；?lang= 透传 doctor）
    "/api/diagnostics": _get_diagnostics,
    "/api/doctor": _get_doctor,
    # §68.5 首次运行向导
    "/api/setup": lambda ctx, query: setup.snapshot(ctx.home),
    "/api/setup/engine": lambda ctx, query: setup.engine_snapshot(ctx.home),
    "/api/setup/vaults": lambda ctx, query: setup.vaults_snapshot(),
    # §26 / §68.6 关于 + 更新（缓存过期时在后台起一次 24h 自闸的检查）
    "/api/about": lambda ctx, query: about.snapshot(ctx.home),
    # §28 系统通知目录：壳直发的通知句（双语）+ 队列 kind 词表（server-owned）
    "/api/notifications": lambda ctx, query: notify_catalog.catalog(),
    # §25 失败目录（server-owned 投影；防腐 #10）
    "/api/failures": lambda ctx, query: failure_catalog.catalog(),
    # §72.1 录制数据磁盘占用：缓存快照立刻回，扫目录在后台线程；?refresh=1 强制重算
    "/api/screenpipe/disk": lambda ctx, query: screenpipe_disk.snapshot(ctx.home, refresh=_flag(query, "refresh")),
}

# 前缀表 handler 形状：(ctx, rest, query) → dict；rest = 前缀之后的尾段（非空）。
_GET_PREFIX_ROUTES = {
    # §68.1 单 section（/api/settings/models、/recap、/display 走上面的精确表）
    "/api/settings/": lambda ctx, rest, query: settings_catalog.section_snapshot(ctx.home, rest),
    # §68.4 日志尾巴（白名单 + size-cap）
    "/api/logs/": lambda ctx, rest, query: diagnostics.tail(ctx.home, rest, query.get("lines")),
    # §15.2 手动触发的 job 轮询（POST 立刻回 job id，脚本在后台线程跑）
    "/api/ingest/jobs/": lambda ctx, rest, query: ingest_run.job_status(rest),
}

_POST_JSON_ROUTES = {
    # §63 「重新生成 / 现在生成」→ 分离派生 python -m act.recap --generate（+ §63.8 请求台账）
    "/api/recaps/generate": lambda ctx, payload: recaps.generate(ctx.home, payload),
    # §63.9 「回退到这一版」→ 分离派生 python -m act.recap --revert
    "/api/recaps/revert": lambda ctx, payload: recaps.revert(ctx.home, payload),
    # §63 「复制」/「标记已发送」/「忽略」本地标记 → state/recap/marks.json（server 独写）
    "/api/recaps/mark": lambda ctx, payload: recaps.mark(ctx.home, payload),
    # §68.4 / §68.1 词表 reveal：config.yaml 或语气档案（路径由 server 推导）
    "/api/reveal": lambda ctx, payload: folders.reveal(ctx.home, payload),
    # §68.5 向导三步
    "/api/setup/config-from-example": lambda ctx, payload: setup.config_from_example(ctx.home, payload),
    "/api/setup/complete": lambda ctx, payload: setup.complete(ctx.home, payload),
    "/api/setup/reset": lambda ctx, payload: setup.reset(ctx.home, payload),
    # §26 手动「立即检查」
    "/api/update/check": lambda ctx, payload: about.check_now(ctx.home, payload),
    # §79 关于页「立即升级」：分离起 scripts/upgrade.sh（新大版本 409 manual_upgrade；升级器未落地 501）
    "/api/update/install": lambda ctx, payload: about.install_now(payload, home=ctx.home),
    # §68.1 目录字段「打开」/「创建」：路径 = 已保存的 effective 值，客户端只传 key
    "/api/folders/open": lambda ctx, payload: folders.open_folder(ctx.home, payload),
    "/api/folders/create": lambda ctx, payload: folders.create_folder(ctx.home, payload),
    # §15.2 录制页「手动触发」：同一条 ingest/ 脚本（server 只起子进程；回 job id）
    "/api/ingest/export": lambda ctx, payload: ingest_run.export_now(ctx.home, payload),
    "/api/ingest/run": lambda ctx, payload: ingest_run.ingest_now(ctx.home, payload),
}

_POST_PREFIX_ROUTES = {
    # §68.3 POST /api/secrets/<name>/verify
    "/api/secrets/": _post_secret_verify,
}

# 二进制体路由：path → (media type, body 上限, handler(ctx, bytes) → dict)。四闸里的 Content-Type 闸按
# 这张表取 media type（_write_media_type）。v2.0.0：§10bis 贴图上传随卡片退役，表为空（机制保留）。
_POST_RAW_ROUTES: dict = {}

_PUT_JSON_ROUTES = {
    # §59 模型旋钮（diff-write overrides）
    "/api/settings/models": lambda ctx, payload: settings.update_models(ctx.home, payload),
    # §63 会议 recap 旋钮（同一 diff-write 语义）
    "/api/settings/recap": lambda ctx, payload: recaps.update(ctx.home, payload),
    # §68.1 显示偏好旋钮（同一 diff-write 语义；原 §54.1 第 12 项）
    "/api/settings/display": lambda ctx, payload: display.update(ctx.home, payload),
}

_PUT_PREFIX_ROUTES = {
    # §68.1 通用 section 的 diff-write（models / recap / display 走精确表）
    "/api/settings/": lambda ctx, rest, payload: settings_catalog.update_section(ctx.home, rest, payload),
    # §68.3 凭证写入（0600；空值 = 删）
    "/api/secrets/": lambda ctx, rest, payload: secrets_store.write_value(ctx.home, rest, payload),
}


class _Context:
    """挂在 server 实例上的共享只读上下文（测试注入缝）。"""

    def __init__(self, home: Path, hub: EventHub, static_dir: Path,
                 token: str, allowed_origins: frozenset) -> None:
        self.home = home
        self.hub = hub
        self.static_dir = static_dir
        self.token = token
        self.allowed_origins = allowed_origins


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer + 连接类异常静默（§54.2 追记 2026-09-14）。

    浏览器关 tab / 刷新时 keep-alive 连接被 reset，异常在 `handle_one_request`
    读请求行时抛出——Handler 的 `_dispatch` 够不着它，socketserver 的默认
    `handle_error` 于是往 server.launchd.log 里打整段 traceback（live 机器上
    101 段，issue #314）。只吞三个连接类异常，其余照打全栈——静掉的是噪音，
    不是真崩溃。"""

    def handle_error(self, request, client_address) -> None:
        if isinstance(sys.exc_info()[1], _QUIET_CONN_ERRORS):
            return  # 客户端提前挂断——正常噪音（Handler._dispatch 同一判据）
        super().handle_error(request, client_address)


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
    httpd = _Server((BIND_HOST, port), Handler)
    bound_port = httpd.server_address[1]  # port=0 时这里才是真端口
    httpd.ctx = _Context(resolved_home, hub,  # type: ignore[attr-defined]
                         static_dir or paths.web_dist_dir(),
                         token, security.allowed_origins(bound_port))
    httpd.watcher = None  # type: ignore[attr-defined]
    if start_watcher:
        watcher = RecapWatcher(resolved_home, hub)
        watcher.start()
        httpd.watcher = watcher  # type: ignore[attr-defined]
    return httpd


# EADDRINUSE 下的退出码：launchd/systemd 常驻托管（§54）会按 KeepAlive/Restart
# 重拉，所以这里只要一行人话、绝不整段 traceback——端口被壳的 spawn 兜底或手动
# `-m server` 占着时，每个 throttle 周期一段 traceback会把 server.launchd.log
# 刷成 imessageradar 那种 14 MB 孤儿日志（§55 审计 L3）。
EX_PORT_BUSY = 75  # EX_TEMPFAIL


def _port_busy_line() -> str:
    port = os.environ.get("ZAI_PORT", DEFAULT_PORT)
    return (f"zai server: 127.0.0.1:{port} is busy — another server is already "
            f"listening (the shell's spawn fallback or a manual `python3 -m "
            f"server`); exiting {EX_PORT_BUSY} without a traceback")


def _serve(httpd: ThreadingHTTPServer) -> None:
    """serve_forever 直到 Ctrl-C；无论如何停 watcher、关 socket。"""
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if httpd.watcher is not None:  # type: ignore[attr-defined]
            httpd.watcher.stop()  # type: ignore[attr-defined]
        httpd.server_close()


def main() -> int:
    try:
        httpd = make_server()
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        print(_port_busy_line(), flush=True)
        return EX_PORT_BUSY
    host, port = httpd.server_address[:2]
    print(f"zai server: http://{host}:{port}  "
          f"(AIASSISTANT_HOME={httpd.ctx.home})",  # type: ignore[attr-defined]
          flush=True)
    _serve(httpd)
    return 0
