#!/usr/bin/env python3
"""boardctl — agent 侧看板 CLI（scoped channel，M5；契约 docs/CONTRACT.md §52）。

给 headless Claude-Code agent 用的窄接口：读看板/卡片详情，投 capture
候选（走 triage 三选一闸门，等价一条手动 note），给卡片留 comment/
progress note。**刻意没有 approve/reject/accept/move/archive/merge 等
决策动词**——那些属于 owner（Mac app / web 看板）；本 CLI 的动词面 =
信任矩阵里 agent 通道的全部权限，多一个都不给。

传输面（永不越过）：
- 读 = ``GET http://127.0.0.1:$ZAI_PORT/api/board`` / ``/api/cards/{id}``；
- 写 = ``POST /api/actions``，仅 ``capture`` 与 ``comment`` 两动词——
  落的是 ``state/inbox/*.json``，消费与复验仍归 actd（§44 单写者不破）。
  §49 auth model：写请求回带 per-install instance token（X-Zai-Token，
  读自 ``$AIASSISTANT_HOME/state/server.token``——能读到这个 0600 文件
  本身就是「同用户本机进程」的证明，token 墙要放行的正是这类客户端；
  读不到就裸发，server 的 401 envelope 如实透传给调用方）。
- 自报家门（T-28 ingress 落款）：两个写动词**恒带** ``actor:"agent"``
  （硬编码，不是 flag）——server 落款 ``via:"agent"``，actd 据此把 capture
  落 agent_capture 通道（永不自动派发）、comment 只记录不 steer。省略
  actor 是契约违规：落款供 owner 取证，硬后盾（天花板/强制扩写/人工
  审批列）不依赖它。

输出契约（adapted from dashi-taskboard cli/taskctl.mjs，Apache-2.0，见
NOTICE）：成功 = stdout 单个 JSON object 带 ``schemaVersion``；错误 =
stderr 单个 JSON object ``{"schemaVersion","error":{"code","message",
"details"?}}``；exit codes：0 成功 / 2 输入非法 / 3 server 不可达 /
4 API 或响应错误 / 5 冲突（HTTP 409）。唯一非 JSON 的成功输出是
``--help``（纯文本，exit 0，不请求 server）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_PORT = 47820          # server/app.py DEFAULT_PORT 同值
DEFAULT_HOME = "~/Projects/zelin-ai-assistant"  # server/paths.py DEFAULT_HOME 同值
TOKEN_HEADER = "X-Zai-Token"  # server/security.py TOKEN_HEADER 同值
TIMEOUT_SECONDS = 10

# server/board_source.py SAFE_ID_RE 同款（id 参与 URL 路径拼接，先在客户端
# fail-closed，省一次注定 400 的往返）
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# 投影分区词表（board_source.SECTIONS 同步维护——新增 lane 两处一起改）
LANES = ("needs_approval", "running", "needs_input", "review",
         "completed", "debt", "trash", "archived")

# §10bis capture images 上限（inbox_writer._CAPTURE_IMAGES_MAX 同值）
CAPTURE_IMAGES_MAX = 4

EXIT_OK = 0
EXIT_USAGE = 2        # 输入非法（含本地文件读失败）
EXIT_UNAVAILABLE = 3  # server 不可达 / 超时
EXIT_API = 4          # HTTP 非 2xx（除 409）/ 响应不是合法 JSON object
EXIT_CONFLICT = 5     # HTTP 409（PR-current server 不发；留给 CAS 时代）

BOOL_OPTIONS = frozenset({"json", "help"})   # --json 是显式化 no-op（taskctl 同款）
REPEATABLE_OPTIONS = frozenset({"image"})

COMMAND_OPTIONS = {
    "board": frozenset({"lane", "json"}),
    "card": frozenset({"json"}),
    "capture": frozenset({"text", "text-file", "image", "json"}),
    "comment": frozenset({"body", "body-file", "json"}),
}

_HELP_ROOT = """Usage: boardctl SUBCOMMAND [operands] [--options]

Subcommands:
  board [--lane LANE]
      Read the whole board projection, or one lane of it.
  card CARD_ID
      Read one card's detail (projection row + registry fields).
  capture (--text TEXT | --text-file FILE) [--image /abs/path.png ...]
      Submit a candidate note. It enters the same triage gate as any
      hand-written note; it never becomes approved work by itself.
  comment CARD_ID (--body TEXT | --body-file FILE)
      Attach a comment / progress note to an existing card.

This CLI deliberately has NO approve/reject/accept/move/archive/merge
verbs — card state transitions belong to the owner.

Environment:
  ZAI_PORT   zai server port (default 47820); the server binds
             127.0.0.1 only.
  AIASSISTANT_HOME
             home dir holding state/server.token — the per-install
             instance token every write must carry (missing file =>
             the server answers 401 UNAUTHORIZED).

Output: one JSON object with "schemaVersion" on stdout. Errors are one
JSON object on stderr. Exit codes: 0 ok, 2 invalid input, 3 service
unavailable, 4 API/response error, 5 conflict."""

HELP_TEXT = {
    "": _HELP_ROOT,
    "board": """Usage: boardctl board [--lane LANE] [--json]

Reads GET /api/board. Without --lane the full projection is returned
under "board". With --lane only that lane's rows are returned under
"cards". Lanes: needs_approval, running, needs_input, review,
completed, debt, trash, archived.""",
    "card": """Usage: boardctl card CARD_ID [--json]

Reads GET /api/cards/CARD_ID: the projection row plus read-only
registry fields (plan, definition_of_done, sources, notes, ...) and a
"lane" key. CARD_ID is the card's primary key (P-xxx, or a legacy
R-xxx) or its work number (the R-xxx shown on approved/running cards);
the response "id" is always the primary key. CARD_ID must match
[A-Za-z0-9][A-Za-z0-9_-]{0,63}.""",
    "capture": """Usage: boardctl capture (--text TEXT | --text-file FILE)
                        [--image /abs/path.png ...] [--json]

Submits {"action":"capture","text":...,"actor":"agent"} to POST
/api/actions. The capture enters the triage pipeline as a candidate
and never mints approved or running work: it is stamped via:"agent"
server-side, so it always lands on the agent_capture channel and is
never eligible for auto-dispatch. --image may repeat (max 4 absolute
paths). There is no direct-run option on this channel by design.""",
    "comment": """Usage: boardctl comment CARD_ID (--body TEXT | --body-file FILE)
                        [--json]

Submits {"action":"comment","id":CARD_ID,"comment":...,"actor":
"agent"} to POST /api/actions. CARD_ID is the primary key or the work
number (either resolves). Use it for progress notes: what
changed, how it was verified, outcome, remaining risks. The body must
be non-empty. Agent comments are recorded on the card for the owner;
they are never relayed into a live work session as owner steering.""",
}


class CtlError(Exception):
    """携带 envelope code + exit code 的受控错误——main 捕获后渲染 stderr JSON。"""

    def __init__(self, message: str, *, code: str = "USAGE_ERROR",
                 exit_code: int = EXIT_USAGE, details=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.exit_code = exit_code
        self.details = details


def _usage(message: str) -> CtlError:
    return CtlError(message)


# --------------------------------------------------------------------------- #
# 参数解析（taskctl 式手写小解析器——argparse 的 stderr 纯文本/exit 2 形状
# 不满足本契约的 error-JSON 要求，自己写才能全权控制输出）
# --------------------------------------------------------------------------- #
def _option_value(name: str, argv: list, i: int) -> str:
    """``--name`` 后面那个值（argv[i+1]）；缺 → usage 错。"""
    if i + 1 >= len(argv):
        raise _usage(f"--{name} requires a value")
    return argv[i + 1]


def _take_option(name: str, argv: list, i: int, options: dict) -> int:
    """消费 argv[i] 处的 ``--name``（含其值），返回已消费到的下标。"""
    if not name:
        raise _usage("empty option name")
    if name in BOOL_OPTIONS:
        options[name] = True
        return i
    value = _option_value(name, argv, i)
    if name in REPEATABLE_OPTIONS:
        options.setdefault(name, []).append(value)
    elif name in options:
        raise _usage(f"--{name} given more than once")
    else:
        options[name] = value
    return i + 1


def parse_args(argv: list) -> dict:
    positionals: list = []
    options: dict = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if isinstance(token, str) and token.startswith("--"):
            i = _take_option(token[2:], argv, i, options)
        else:
            positionals.append(token)
        i += 1
    return {"command": positionals[0] if positionals else None,
            "operands": positionals[1:],
            "options": options}


def _expect_operands(command: str, operands: list, count: int) -> None:
    if len(operands) != count:
        raise _usage(f"{command} takes exactly {count} operand(s), "
                     f"got {len(operands)}")


# --------------------------------------------------------------------------- #
# HTTP 客户端（stdlib urllib；server 硬绑 127.0.0.1，端口来自 $ZAI_PORT）
# --------------------------------------------------------------------------- #
def _base_url(environ) -> str:
    raw = (environ.get("ZAI_PORT") or "").strip()
    if not raw:
        port = DEFAULT_PORT
    else:
        try:
            port = int(raw)
        except ValueError:
            port = -1
        if not (1 <= port <= 65535):
            raise _usage("ZAI_PORT must be an integer port (1..65535)")
    return f"http://127.0.0.1:{port}"


def _home_dir(environ) -> Path:
    """home 推导——与 ``server/paths.py::home_dir(None)`` **逐字同款**。

    刻意 NOT strip、NOT 把空串当缺省：server 端 ``explicit or environ.get(
    "AIASSISTANT_HOME", DEFAULT_HOME)`` 在 explicit=None 时就是
    ``environ.get("AIASSISTANT_HOME", DEFAULT_HOME)``——env 缺席 → DEFAULT_HOME，
    env="" → ``Path("")``（=cwd），env="  " → ``Path("  ")``。boardctl 早先
    做了 ``.strip() or DEFAULT_HOME``，于是 env=""/空白时它读 DEFAULT_HOME 而
    server 写到别处 → 两边 token 文件分叉 → 永久 401（M7 审计）。drift-pin
    见 tests/test_boardctl.py。"""
    raw = environ.get("AIASSISTANT_HOME", DEFAULT_HOME)
    return Path(raw).expanduser()


def _instance_token(environ) -> "str | None":
    """读 per-install instance token（§49：server 对一切写动作要 token）。

    home 推导与 server 端同款（_home_dir）——两边同机同推导才拿得到同一个
    文件。读不到 = None（不发头，server 会 401，错误 envelope 如实透传——
    绝不在客户端猜/造 token）。
    """
    p = _home_dir(environ) / "state" / "server.token"
    try:
        tok = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return tok or None


def _error_envelope(raw: bytes) -> dict:
    """server 错误 body 里的 ``error`` 对象；不是 JSON / 形状不对 → {}。"""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    err = doc.get("error") if isinstance(doc, dict) else None
    return err if isinstance(err, dict) else {}


def _str_field(err: dict, key: str, default: str) -> str:
    value = err.get(key)
    return value if isinstance(value, str) else default


def _api_error(status: int, raw: bytes) -> CtlError:
    """HTTP 非 2xx → CtlError：透传 server envelope 的 code/message/details，
    解析不了就退化为 HTTP_<status>（taskctl extractApiError 同款分层）。"""
    err = _error_envelope(raw)
    details = err.get("details") if isinstance(err.get("details"), dict) else None
    return CtlError(_str_field(err, "message", f"server returned HTTP {status}"),
                    code=_str_field(err, "code", f"HTTP_{status}"),
                    details=details or None,   # 空 dict 与缺席同义
                    exit_code=EXIT_CONFLICT if status == 409 else EXIT_API)


def _request(base_url: str, method: str, path: str, body,
             token: "str | None") -> urllib.request.Request:
    # X-ZAI-Client 是未来 server 侧 actor 墙的辨识挂点（PR-current server
    # 忽略请求头）——不动 JSON wire，见 docs/design/vnext-amendments.md M5。
    headers = {"Accept": "application/json", "X-ZAI-Client": "boardctl"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        if token:
            headers[TOKEN_HEADER] = token  # §49：写动作回带 instance token
    return urllib.request.Request(base_url + path, data=data,
                                  headers=headers, method=method)


def _decode_response(raw: bytes) -> dict:
    """成功响应必须是一个 JSON object；否则 INVALID_RESPONSE（exit 4）。"""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        doc = None
    if not isinstance(doc, dict):
        raise CtlError("server returned an invalid JSON response",
                       code="INVALID_RESPONSE", exit_code=EXIT_API)
    return doc


def _http(base_url: str, method: str, path: str, body=None,
          token: "str | None" = None) -> dict:
    req = _request(base_url, method, path, body, token)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        with err:  # HTTPError 兼 file-like——显式关闭，防 ResourceWarning
            raise _api_error(err.code, err.read())
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        reason = getattr(err, "reason", None) or err
        raise CtlError(f"cannot reach zai server at {base_url} — "
                       "is it running?", code="SERVICE_UNAVAILABLE",
                       exit_code=EXIT_UNAVAILABLE,
                       details={"cause": str(reason)})
    return _decode_response(raw)


# --------------------------------------------------------------------------- #
# 文本来源（--text/--body 内联 or --*-file 读文件,二选一）
# --------------------------------------------------------------------------- #
def _text_source(options: dict, inline_key: str, file_key: str,
                 what: str) -> str:
    inline = options.get(inline_key)
    fname = options.get(file_key)
    if (inline is None) == (fname is None):
        raise _usage(f"{what} requires exactly one of "
                     f"--{inline_key} or --{file_key}")
    if inline is not None:
        return inline
    try:
        with open(fname, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as err:
        raise CtlError(f"cannot read --{file_key} {fname}",
                       code="FILE_READ_FAILED", exit_code=EXIT_USAGE,
                       details={"cause": str(err)})


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_board(operands: list, options: dict, base_url: str,
              token: "str | None" = None) -> dict:
    _expect_operands("board", operands, 0)
    doc = _http(base_url, "GET", "/api/board")
    lane = options.get("lane")
    if lane is None:
        return {"board": doc}
    if lane not in LANES:
        raise _usage("unknown lane; expected one of: " + ", ".join(LANES))
    rows = doc.get(lane)
    return {"lane": lane, "cards": rows if isinstance(rows, list) else []}


def cmd_card(operands: list, options: dict, base_url: str,
             token: "str | None" = None) -> dict:
    _expect_operands("card", operands, 1)
    card_id = operands[0]
    if not SAFE_ID_RE.match(card_id):
        raise _usage("card id must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    return {"card": _http(base_url, "GET", "/api/cards/" + card_id)}


def cmd_capture(operands: list, options: dict, base_url: str,
                token: "str | None" = None) -> dict:
    _expect_operands("capture", operands, 0)
    text = _text_source(options, "text", "text-file", "capture")
    if not text.strip():
        raise _usage("capture text must not be empty")
    # scoped channel：刻意不提供 mode:"run"/preset——agent 的 capture 只进
    # triage 候选（信任矩阵：AI-proposed 需 owner 批准），直跑是 owner 动词。
    # actor:"agent" 硬编码（T-28 自报家门）：server 落款 via:"agent"，
    # actd 落 agent_capture 通道——本 CLI 的每次写都自我标识，无开关。
    payload = {"action": "capture", "text": text, "actor": "agent"}
    images = options.get("image") or []
    if images:
        payload["images"] = _validated_images(images)
    return _http(base_url, "POST", "/api/actions", payload, token=token)


def _validated_images(images: list) -> list:
    """--image 路径集：≤ 上限、不重复、全绝对路径（server 端 §10bis 同款闸门，
    客户端先 fail-closed 省一次注定 400 的往返）。"""
    if len(images) > CAPTURE_IMAGES_MAX:
        raise _usage(f"--image allows at most {CAPTURE_IMAGES_MAX} paths")
    if len(set(images)) != len(images):
        raise _usage("--image paths must not repeat")
    if not all(p.startswith("/") for p in images):
        raise _usage("--image paths must be absolute")
    return list(images)


def cmd_comment(operands: list, options: dict, base_url: str,
                token: "str | None" = None) -> dict:
    _expect_operands("comment", operands, 1)
    card_id = operands[0]
    if not SAFE_ID_RE.match(card_id):
        raise _usage("card id must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    body = _text_source(options, "body", "body-file", "comment")
    if not body.strip():
        raise _usage("comment body must not be empty")
    # actor:"agent" 硬编码（T-28）：agent 评论上卡记录、绝不转 OWNER UPDATE
    return _http(base_url, "POST", "/api/actions",
                 {"action": "comment", "id": card_id, "comment": body,
                  "actor": "agent"}, token=token)


HANDLERS = {
    "board": cmd_board,
    "card": cmd_card,
    "capture": cmd_capture,
    "comment": cmd_comment,
}


# --------------------------------------------------------------------------- #
# 入口（stdout/stderr/environ 是测试注入缝——taskctl main(overrides) 同款）
# --------------------------------------------------------------------------- #
def _help_text(command, operands: list, options: dict) -> str:
    """--help 的文本；taskctl 纪律：help 必须独占（无 operand、无其它 option）。"""
    scope = command or ""
    if scope not in HELP_TEXT or operands or options != {"help": True}:
        raise _usage("help is available for boardctl and its "
                     "subcommands: board, card, capture, comment")
    return HELP_TEXT[scope]


def _dispatch(command, operands: list, options: dict, env) -> dict:
    """子命令分派：未知动词 / 动词外 option 一律 usage 错。"""
    if command not in HANDLERS:
        # 决策动词（approve/accept/...）也落到这里——permission wall 的
        # CLI 面：不存在的子命令连 usage 提示都不承认它
        raise _usage("expected one of: board, card, capture, comment "
                     "(this CLI has no card-state verbs by design; "
                     "approvals belong to the owner)")
    unknown = set(options) - COMMAND_OPTIONS[command]
    if unknown:
        raise _usage(f"unknown option(s) for {command}: "
                     + ", ".join("--" + n for n in sorted(unknown)))
    return HANDLERS[command](operands, options, _base_url(env),
                             _instance_token(env))


def _run(argv: list, env, out) -> int:
    """解析 → help 或分派 → stdout 单个 JSON object（CtlError 交 main 渲染）。"""
    parsed = parse_args(argv)
    command, operands, options = (parsed["command"], parsed["operands"],
                                  parsed["options"])
    if options.get("help"):
        out.write(_help_text(command, operands, options) + "\n")
        return EXIT_OK
    result = _dispatch(command, operands, options, env)
    result["schemaVersion"] = SCHEMA_VERSION
    out.write(json.dumps(result, ensure_ascii=False) + "\n")
    return EXIT_OK


def _write_error(err_out, code: str, message: str, details=None) -> None:
    """stderr 单个 JSON object ``{"schemaVersion","error":{code,message[,details]}}``。"""
    payload = {"schemaVersion": SCHEMA_VERSION,
               "error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    err_out.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _resolve_io(argv, stdout, stderr, environ) -> tuple:
    """注入缝缺省回落到进程真实 argv/stdout/stderr/environ。"""
    return (list(sys.argv[1:]) if argv is None else list(argv),
            stdout if stdout is not None else sys.stdout,
            stderr if stderr is not None else sys.stderr,
            environ if environ is not None else os.environ)


def main(argv=None, *, stdout=None, stderr=None, environ=None) -> int:
    argv, out, err_out, env = _resolve_io(argv, stdout, stderr, environ)
    try:
        return _run(argv, env, out)
    except CtlError as err:
        _write_error(err_out, err.code, err.message, err.details)
        return err.exit_code
    except Exception as exc:  # 兜底：不泄栈（taskctl normalizeError 同款）
        # exit 1 = §52.4 词表的「未预期内部崩溃」档：一切已分类错误必须走
        # 2-5（CtlError），落到这里 = boardctl 自身的 bug 线索。
        _write_error(err_out, "INTERNAL_ERROR", str(exc) or type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
