"""HTTP server 主体：路由 / error envelope / body 上限 / 静态资源。

- ThreadingHTTPServer，**硬编码 bind 127.0.0.1**（宪法：本地优先，新增网络面
  仅 localhost）；端口 env ``ZAI_PORT`` 默认 47820。
- error envelope 统一 ``{"error":{"code","message","details"}}``（errors.py）。
- POST body 上限 1MiB；未知 JSON 字段零容忍 400 UNKNOWN_FIELD（reveal 在
  本层校验，actions 的字段闸门归 inbox_writer/G1）。
- 鉴权四闸（原 PR3 TODO，v0.48.1 落地；机制与差异见 server/security.py）：
  Host 回环白名单（每请求，anti-rebind）→ Origin 白名单（写请求，present 才
  查，anti-CSRF）→ Content-Type: application/json（写请求，杀 simple-request
  向量）→ per-install instance token（写请求一律必带 X-Zai-Token；token 由
  server 注入被服务的 index.html）。写请求 = POST 与 PUT（§59 设置面加的
  第二个写动词，四闸逐字同款）。GET 保持 token-light（同源纪律 + 永不
  发 CORS 头，跨源页面读不到任何响应）。
- 设置面（§59）：GET/PUT /api/settings/models、GET/POST
  /api/claude-code/default-model，校验与落盘在 server/settings.py。
- 看板 parity 面（§54）：GET /api/lanes（列说明文案的 server-owned 目录，
  server/lanes.py）、GET /api/notifications（系统通知目录：壳直发的通知句 +
  §28 kind 词表，server/notify_catalog.py）、POST /api/ai-fix（「让 AI 修」=
  起 act.ai_fix 的 Terminal 修复会话，server/ai_fix_launch.py）。
- 素材库（§62）：GET /api/materials/list?status=、POST /api/materials/add、
  POST /api/materials/dismiss（server/material_box.py，存储在 act/lib/materials.py）。
- 会议 recap 面（§63）：GET/PUT /api/settings/recap（三把旋钮）、POST
  /api/recaps/mark（「复制」/「标记已发送」本地标记），server/recaps.py。
- 设置全套 / 权限体检 / 诊断 / 首次运行向导（§68，P4 legacy-app parity）：
  GET /api/settings（目录）+ GET/PUT /api/settings/{section}（server/settings_catalog.py）、
  GET /api/secrets + PUT /api/secrets/{name} + POST /api/secrets/{name}/verify
  （server/secrets_store.py，值 write-only 永不回显）、GET /api/permissions、
  GET /api/doctor、GET /api/diagnostics、GET /api/logs/{name}、GET /api/setup +
  GET /api/setup/engine + POST /api/setup/{config-from-example,complete,reset,seed-dashboard}、GET /api/about +
  POST /api/update/check、GET /api/mcp、GET /api/claude-sessions、
  POST /api/terminal（在终端接管会话）、POST /api/repair/actd（横幅一键修复）。
- 问问助手（§27 / §54.4 左侧导航栏页）：GET /api/ask/history（只读 state/ask_history.json）、
  POST /api/ask {question}（子进程 ``python -m act.ask``，server/ask_assistant.py）；
  Slack 接入区 GET /api/slack/manifest（repo config/slack-app-manifest.json 原文，server/slack_manifest.py）；
  关于页 POST /api/uninstall/terminal（在 Terminal 跑 uninstall.sh 的 .command，server/uninstall_launch.py）；
  开发者区 POST /api/maintainer/terminal（cd <repo> && claude [--resume]，server/maintainer_launch.py）。
  精确表之外多一张**前缀表**（`/api/cards/`、`/api/settings/`、`/api/logs/`、
  `/api/secrets/`）：精确命中先于前缀（`/api/settings/models` / `recap` 走自己的模块）。
- 每日整理面（§70）：GET/PUT /api/settings/daily-loop（五把旋钮，同一
  diff-write 语义），server/settings.py。
- 显示偏好（§54.1 第 12 项）：GET/PUT /api/settings/display（字号 / 字重 / 描边
  三把旋钮，看板落成 :root 上的 CSS 变量），server/display.py。
- 后台雷达行（§48.7）：GET /api/radars（launchd 已加载 / 模板间隔）、POST
  /api/radars/reinstall {source}（= bash install.sh --reinstall-agent <label>），
  server/radars.py；目录字段的 POST /api/folders/{open,create} {key}（路径由
  server 从设置目录读，§68.1），server/folders.py。

契约：docs/CONTRACT.md §49（路由/SSE/CSP/auth model/error envelope/
localhost 例外的法源）、§59（设置面）、§62（素材库）、§63（会议 recap）、
§67（skill 商店：GET/POST /api/skills，写者是 act/lib/skills.py）、§68（parity 面）、
§70（每日整理设置面）。
"""
from __future__ import annotations

import errno
import json
import mimetypes
import os
import queue
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, unquote, urlsplit

from server import (about, ai_fix_launch, ask_assistant, board_source, claude_sessions,
                    diagnostics, display, doctor_run, failure_catalog, files,
                    folders, health, inbox_writer, ingest_run, lanes,
                    maintainer_launch, material_box, mcp_servers, notify_catalog,
                    paths, permissions, radars, recaps, repair, secrets_store,
                    security, self_improve_lane, settings, settings_catalog,
                    setup, slack_manifest, terminal_launch, uninstall_launch)
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

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def _dispatch(self, method: str) -> None:
        try:
            self._handle(method)
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

    def _handle(self, method: str) -> None:
        """一次请求的主线：路径解码 → 鉴权 → 按动词路由（异常交 _dispatch 兜）。"""
        path = unquote(urlsplit(self.path).path)
        if "\x00" in path:
            raise InvalidFieldError("NUL in path")
        self._check_auth(method)
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

    def _check_auth(self, method: str) -> None:
        # Host 闸：每个请求（页面加载也算）——DNS-rebinding 防线。
        if not security.host_ok(self.headers.get("Host")):
            self._reject(ForbiddenError("bad host"))
        if method in ("POST", "PUT"):
            self._check_write_auth()
        # GET/HEAD token-light：无 CORS 头，跨源页面读不到响应

    def _check_write_auth(self) -> None:
        """写请求的后三闸：Origin（present 才查）→ Content-Type → instance token。"""
        ctx = self.server.ctx  # type: ignore[attr-defined]
        origin = self.headers.get("Origin")
        if origin is not None and not security.origin_ok(
                origin, ctx.allowed_origins):
            self._reject(ForbiddenError("bad origin"))
        if not security.content_type_is_json(
                self.headers.get("Content-Type")):
            # 415 复用 INVALID_FIELD（§49 的 413 先例：status 已表意，
            # 不为 loopback 面扩词表）
            self._reject(InvalidFieldError(
                "Content-Type must be application/json", status=415))
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
        elif path.startswith("/files/deliverables/"):
            self._send_deliverable(ctx, path)
        elif path.startswith("/files/"):
            raise NotFoundError("not found", {"path": path})
        else:
            self._serve_static(ctx.static_dir, path)

    def _route_api_get(self, ctx, path: str) -> None:
        if path == "/api/board":
            body = board_source.board_bytes(ctx.home)
            self._send_bytes(200, body, "application/json; charset=utf-8",
                             {"Cache-Control": "no-store"})
        elif path == "/api/events":
            self._serve_events(ctx.hub)
        else:
            # 纯 JSON 读面（health / 设置面 / 目录 / 诊断…）——表驱动：精确表先，前缀表后
            handler = _lookup(_GET_JSON_ROUTES, _GET_PREFIX_ROUTES, path)
            if handler is None:
                raise NotFoundError("not found", {"path": path})
            self._send_json(200, handler(ctx, self._query()))

    def _send_deliverable(self, ctx, path: str) -> None:
        rest = path[len("/files/deliverables/"):].split("/")
        if len(rest) != 2:
            raise NotFoundError("not found", {"path": path})
        body, ctype, extra = files.serve_deliverable(ctx.home, rest[0], rest[1])
        extra["Cache-Control"] = "no-store"
        self._send_bytes(200, body, ctype, extra, frameable=True)

    # ------------------------------------------------------------------ #
    # POST 路由
    # ------------------------------------------------------------------ #
    def _route_post(self, path: str) -> None:
        ctx = self.server.ctx  # type: ignore[attr-defined]
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

    def _read_json_body(self) -> dict:
        length = _content_length(self.headers.get("Content-Length"))
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
        # 保留一行式访问日志到 stderr；SSE 心跳不经此处，噪音可控
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


# --------------------------------------------------------------------------- #
# 请求体 / 静态资源的纯函数小件（Handler 方法只做发送）
# --------------------------------------------------------------------------- #
def _content_length(raw: Optional[str]) -> int:
    """Content-Length 头 → 字节数；缺失/非数/负数 400，超上限 413。"""
    if raw is None:
        raise InvalidFieldError("Content-Length required")
    try:
        length = int(raw)
    except ValueError:
        raise InvalidFieldError("bad Content-Length")
    if length < 0:
        raise InvalidFieldError("bad Content-Length")
    if length > MAX_BODY_BYTES:
        # CONTRACT §49（v0.48 追认）：413 复用 INVALID_FIELD——status 已
        # 表意，不为 loopback 面扩词表
        raise InvalidFieldError("body too large",
                                {"limit": MAX_BODY_BYTES}, status=413)
    return length


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


def _static_ctype(target: Path) -> str:
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
def _post_actions(ctx, payload: dict) -> dict:
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
    return result


def _post_reveal(ctx, payload: dict) -> dict:
    unknown = set(payload) - {"card_id", "target"}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    if "target" in payload and "card_id" not in payload:
        # §68.4 doctor 行「显示文件」（config_invalid）：词表项，不是路径
        target = payload.get("target")
        if not isinstance(target, str):
            raise InvalidFieldError("target must be a string")
        return files.reveal_target(ctx.home, target)
    card_id = payload.get("card_id")
    if not isinstance(card_id, str):
        raise InvalidFieldError("card_id must be a string")
    return files.reveal(ctx.home, card_id)


def _post_claude_code_default(ctx, payload: dict) -> dict:
    # §59 owner 的显式一键「设为 <id>」：只改 model 键、先备份、坏文件拒改
    unknown = set(payload) - {"model"}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    return settings.set_claude_code_default(payload.get("model"))


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
    fast = query.get("fast", "1") not in ("0", "false", "no")
    return doctor_run.report(ctx.home, fast=fast, refresh=_flag(query, "refresh"))


def _post_secret_verify(ctx, rest: str, payload: dict) -> dict:
    # /api/secrets/<name>/verify —— 尾段 "<name>/verify"；body 必须是 {}（零容忍）
    if not rest.endswith("/verify"):
        raise NotFoundError("not found", {"path": "/api/secrets/" + rest})
    # body 空 = 探已保存的；{value} = 粘贴即验证（不落盘，§68.3）
    return secrets_store.verify(ctx.home, rest[:-len("/verify")],
                                value=secrets_store.verify_payload_value(payload))


# GET 表 handler 形状：(ctx, query) → dict；query = URL query 的扁平 dict（_query）。
_GET_JSON_ROUTES = {
    # §47.4 管线活性（token-light GET，同源纪律同 /api/board）：心跳年龄 +
    # 看板新鲜度 + 连崩计数 → 一个 verdict，web 顶部横幅据此诚实报「后台
    # 服务卡住/停了」——退役中的 Mac app 横幅的替身。
    "/api/health": lambda ctx, query: health.snapshot(ctx.home),
    # §59 两把模型旋钮的 effective 值 + canonical 下拉全集（server-owned）
    "/api/settings/models": lambda ctx, query: settings.models_snapshot(ctx.home),
    # §70 每日自我改进循环的五把旋钮（D10；web 设置页「每日整理」）
    "/api/settings/daily-loop": lambda ctx, query: settings.daily_loop_snapshot(ctx.home),
    # §59 follow 模式继承的 Claude Code 全局默认（~/.claude/settings.json）
    "/api/claude-code/default-model": lambda ctx, query: settings.claude_code_default(),
    # §54 列说明文案目录（server-owned，防腐 #10）：web 列头「?」气泡逐字镜像
    "/api/lanes": lambda ctx, query: lanes.catalog(),
    # §28 / §66.2 系统通知目录：壳直发的通知句（双语）+ 队列 kind 词表（server-owned）
    "/api/notifications": lambda ctx, query: notify_catalog.catalog(),
    # §62 素材库：?status=open（默认，弹窗）| all | 单个状态；只读折叠台账
    "/api/materials/list": lambda ctx, query: material_box.list_items(ctx.home, query),
    # §63 会议 recap 三把旋钮（enabled / default_language / slack_draft_enabled）
    "/api/settings/recap": lambda ctx, query: recaps.snapshot(ctx.home),
    # §67 skill 商店：manifest + 本机每个 skill 的状态（enabled / disabled / copy /
    # custom / foreign）；token-light GET，写面在 POST /api/skills
    "/api/skills": lambda ctx, query: settings.skills_snapshot(ctx.home),
    # §68 设置目录全集（通用 section 的 field 描述 + effective 值；文案 server-owned）
    "/api/settings": lambda ctx, query: settings_catalog.snapshot(ctx.home),
    # §68 凭证状态（present / verifiable；值永不回显）
    "/api/secrets": lambda ctx, query: secrets_store.snapshot(ctx.home),
    # §68.3 权限体检（FDA 清单 + TCC 相关 doctor 行）
    "/api/permissions": lambda ctx, query: permissions.snapshot(ctx.home, refresh=_flag(query, "refresh")),
    # §68.4 诊断页（doctor + health + deploy_state + install_report + 日志清单）
    "/api/diagnostics": lambda ctx, query: diagnostics.snapshot(ctx.home, refresh=_flag(query, "refresh")),
    "/api/doctor": _get_doctor,
    # §68.5 首次运行向导（engine = 原生 EngineDetector：claude CLI + 认证梯子）
    "/api/setup": lambda ctx, query: setup.snapshot(ctx.home),
    "/api/setup/engine": lambda ctx, query: setup.engine_snapshot(ctx.home),
    # §68.6 关于 + 更新
    "/api/about": lambda ctx, query: about.snapshot(ctx.home),
    # §68.9 MCP servers 只读列表（Skills 商店 = §67，上面的 /api/skills）
    "/api/mcp": lambda ctx, query: mcp_servers.mcp(ctx.home),
    # §68.10 导入 Claude Code 工作：扫描预览
    "/api/claude-sessions": lambda ctx, query: claude_sessions.scan(ctx.home, query.get("window")),
    # §27 问问助手：最近的问答（只读；写者是 act.ask）
    "/api/ask/history": lambda ctx, query: ask_assistant.history(ctx.home),
    # Slack 接入区「复制 App Manifest」：repo 的 config/slack-app-manifest.json 原文
    "/api/slack/manifest": lambda ctx, query: slack_manifest.manifest(ctx.home),
    # §54.1 第 12 项 显示偏好三把旋钮（text_size / text_weight / stroke）+ server-owned 词表
    "/api/settings/display": lambda ctx, query: display.snapshot(ctx.home),
    # §48.7 后台雷达 agent 状态（问 launchd 本人；间隔读模板）
    "/api/radars": lambda ctx, query: radars.snapshot(ctx.home),
    # §25 / §68.4 失败目录（原生 FailureCatalog.message 的 server-owned 投影；防腐 #10）
    "/api/failures": lambda ctx, query: failure_catalog.catalog(),
}

# 前缀表 handler 形状：(ctx, rest, query) → dict；rest = 前缀之后的尾段（非空）。
_GET_PREFIX_ROUTES = {
    # 投影 + registry 详情增补（§49；主键或工作编号 §60.3）
    "/api/cards/": lambda ctx, rest, query: board_source.card_detail(ctx.home, rest),
    # §68.1 单 section（/api/settings/models、/recap 走上面的精确表）
    "/api/settings/": lambda ctx, rest, query: settings_catalog.section_snapshot(ctx.home, rest),
    # §68.4 日志尾巴（白名单 + size-cap）
    "/api/logs/": lambda ctx, rest, query: diagnostics.tail(ctx.home, rest, query.get("lines")),
    # §15.2 手动触发的 job 轮询（POST 立刻回 job id，脚本在后台线程跑）
    "/api/ingest/jobs/": lambda ctx, rest, query: ingest_run.job_status(rest),
}

_POST_JSON_ROUTES = {
    "/api/actions": _post_actions,
    "/api/reveal": _post_reveal,
    "/api/claude-code/default-model": _post_claude_code_default,
    # §54 让 AI 修：字段白名单 + 上下文推导 + 子进程都在 ai_fix_launch
    "/api/ai-fix": lambda ctx, payload: ai_fix_launch.launch(ctx.home, payload),
    # §62 素材库：加入（url?/note?）与放弃（id）——字段白名单与状态机在 server/material_box.py
    "/api/materials/add": lambda ctx, payload: material_box.add(ctx.home, payload),
    "/api/materials/dismiss": lambda ctx, payload: material_box.dismiss(ctx.home, payload),
    # §63 「复制」/「标记已发送」本地标记 → state/recap/marks.json（server 独写）
    "/api/recaps/mark": lambda ctx, payload: recaps.mark(ctx.home, payload),
    # §65.4 恢复自动草稿 PR 通道（敏感路径护栏挂起后 owner 的看板出口）
    "/api/self-improve/resume": lambda ctx, payload: self_improve_lane.resume(ctx.home, payload),
    # §67 启用/停用一个 skill（= ~/.claude/skills 软链接的建/删；自定义副本拒改 409）
    "/api/skills": lambda ctx, payload: settings.update_skill(ctx.home, payload),
    # §68.7 在终端接管会话（命令由 server 从投影推导）
    "/api/terminal": lambda ctx, payload: terminal_launch.launch(ctx.home, payload),
    # §68.8 横幅一键修复：actd 已加载 → kickstart；未加载 → 409 指向 install.sh
    "/api/repair/actd": lambda ctx, payload: repair.kickstart_actd(payload),
    # §68.5 向导三步
    "/api/setup/config-from-example": lambda ctx, payload: setup.config_from_example(ctx.home, payload),
    "/api/setup/complete": lambda ctx, payload: setup.complete(ctx.home, payload),
    "/api/setup/reset": lambda ctx, payload: setup.reset(ctx.home, payload),
    # §68.5 末步「首次数据 · 立即生成一次」= python -m act.lib.dashboard 一次
    "/api/setup/seed-dashboard": lambda ctx, payload: setup.seed_dashboard(ctx.home, payload),
    # §26 手动「立即检查」
    "/api/update/check": lambda ctx, payload: about.check_now(ctx.home, payload),
    # §27 问问助手：一问一答（子进程 act.ask，≤75 s）
    "/api/ask": lambda ctx, payload: ask_assistant.ask(ctx.home, payload),
    # §68.6 关于页「在 Terminal 中卸载…」：.command + open，server 自己不删任何东西
    "/api/uninstall/terminal": lambda ctx, payload: uninstall_launch.launch(payload, home=ctx.home),
    # §68.1 开发者 · 开发会话「在终端打开开发会话」：cd <repo_path> && claude [--resume <id>]，参数全由 server 读
    "/api/maintainer/terminal": lambda ctx, payload: maintainer_launch.launch(ctx.home, payload),
    # §48.7 「重新安装」后台雷达：install.sh 自己的渲染器 + launchctl（server 不写 plist）
    "/api/radars/reinstall": lambda ctx, payload: radars.reinstall(ctx.home, payload),
    # §68.1 目录字段「打开」/「创建」：路径 = 已保存的 effective 值，客户端只传 key
    "/api/folders/open": lambda ctx, payload: folders.open_folder(ctx.home, payload),
    "/api/folders/create": lambda ctx, payload: folders.create_folder(ctx.home, payload),
    # §15.2 录制页「手动触发」：同一条 ingest/ 脚本、同一套退出码（server 只起子进程；回 job id，GET /api/ingest/jobs/ 轮询）
    "/api/ingest/export": lambda ctx, payload: ingest_run.export_now(ctx.home, payload),
    "/api/ingest/run": lambda ctx, payload: ingest_run.ingest_now(ctx.home, payload),
    # §68.6 关于页「一键更新」：提前 kickstart §56 自动部署 agent（未加载 409 → 页面退回 release 页）
    "/api/update/install": lambda ctx, payload: about.install_now(payload),
}

_POST_PREFIX_ROUTES = {
    # §68.3 POST /api/secrets/<name>/verify
    "/api/secrets/": _post_secret_verify,
}

_PUT_JSON_ROUTES = {
    # §59 两把模型旋钮（diff-write overrides）
    "/api/settings/models": lambda ctx, payload: settings.update_models(ctx.home, payload),
    # §63 会议 recap 旋钮（同一 diff-write 语义）
    "/api/settings/recap": lambda ctx, payload: recaps.update(ctx.home, payload),
    # §70 每日自我改进循环的五把旋钮（同一 diff-write 语义）
    "/api/settings/daily-loop": lambda ctx, payload: settings.update_daily_loop(ctx.home, payload),
    # §54.1 第 12 项 显示偏好旋钮（同一 diff-write 语义；server 是这三个键的唯一读写者）
    "/api/settings/display": lambda ctx, payload: display.update(ctx.home, payload),
}

_PUT_PREFIX_ROUTES = {
    # §68.1 通用 section 的 diff-write（models / recap 走精确表）
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
