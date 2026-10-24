"""server/secrets_store.py — 凭证文件的 web 侧写者与探针（CONTRACT §19 / §49 / §68.3）。

- ``GET /api/secrets`` → 每个已知凭证的**状态**（present / verifiable / mtime + add-only
  ``legacy``：secrets 文件缺席但 §19 第二层——``~/.config/anthropic-key.txt``——的文件非空；
  只判在不在，永不读内容出去），**绝不回显值**——值是 write-only。
- ``PUT /api/secrets/{name}`` body ``{"value": "<token>"}`` → 写
  ``<home>/config/secrets/<name>``（dir 0700 / file 0600；多行粘贴只留首个非空行，
  与 act/lib/secrets.write_secret 同一契约）；空值 = 删文件。``volcano-speech-key.txt``
  不截首行——粘贴经 ``volcano_speech_credential``（§36 v0.37.1）归一，回执 add-only
  ``legacy_pair``（字幕凭证随 Live captions 在 PR-B 退役，届时连同这两行一起删）。
- ``POST /api/secrets/{name}/verify`` → 用文件里的值做一次最小活探针（Anthropic
  ``GET /v1/models``）。探针经 ``prober`` 注入缝——测试绝不碰网络。火山（字幕）两把 key 没有
  免费探针，``verifiable: false``，verify → 400。body 可带 add-only 的 ``{"value": "<token>"}`` =
  **粘贴即验证**（先验后存、无效的 key 永不落盘）——只探这个值、**不写文件**。
  三分判决：``ok:true`` / ``ok:false, network:false`` = key 本身的错（401 / 403）→ 回执多带
  add-only ``reason {zh, en}``；``ok:false, network:true`` = **判决未知**（DNS / 超时 / 529 /
  5xx），不带 ``reason``。

v2.0.0：Slack user token 与 Gmail 应用密码两行随雷达退役（xoxb- 门口拒绝、auth.test 自动填
``owner_slack_user_id``、IMAP 探针一并删除；全文见 tag full）。Anthropic key 是唯一的模型凭证——
§78 的隔离运行（``--bare``）只认它，不读 Claude Code 登录或钥匙串。

文件名是 §19 的固定词表（server 不 import act.lib.secrets：它 import 期就把
SECRETS_DIR 钉在进程 env 的 HOME 上，测试注入 home 会失真——判例
tests/test_server_secrets.py 钉住两侧名单逐字一致）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from server import paths, settings_catalog
from server.errors import InvalidFieldError, NotFoundError, UnknownFieldError

# §19 固定文件名（act/lib/secrets.py 同名常量 + 原生 SecretsIO.volcano*File）
SECRETS: tuple = (
    {"name": "anthropic-api-key.txt", "probe": "anthropic",
     "label": {"zh": "Anthropic API key", "en": "Anthropic API key"}},
    {"name": "volcano-speech-key.txt", "probe": None,
     "label": {"zh": "豆包语音凭证（字幕识别）", "en": "Doubao speech credential (captions)"}},
    {"name": "volcano-ark-key.txt", "probe": None,
     "label": {"zh": "Ark API key（字幕翻译）", "en": "Ark API key (caption translation)"}},
)
_BY_NAME = {s["name"]: s for s in SECRETS}
VOLCANO_SPEECH = "volcano-speech-key.txt"

# 原生 Settings.swift humanAuthReason（audit 6.1）的分类人话：什么错了 + 修它的那一个动作，raw 跟在括号里。
# server-owned {zh, en}，web 按 UI 语言取键（防腐 #10，与 XOXB_REFUSAL 同一机制）。``{raw}`` = 探针的 detail 原文。
AUTH_REASONS: dict = {
    "anthropic": {
        "zh": "key 无效——到 console.anthropic.com 重新生成，回来粘贴保存（{raw}）",
        "en": "The key is invalid — regenerate it at console.anthropic.com, then paste and save ({raw})",
    },
}
# Anthropic 只有这两个状态码说明 key 本身有问题（原生 KeyProbe.anthropic）；其余非 2xx = 服务侧 / 判决未知
ANTHROPIC_UNAUTHORIZED_STATUSES: frozenset = frozenset({401, 403})
# §19 第二层的**位置**（不是内容）：(config.yaml 显式路径的键路径 | None, 旧默认路径)。与
# act/lib/secrets.LEGACY_ANTHROPIC_KEY_PATH 同一字面。
_LEGACY_PATHS: dict = {
    "anthropic-api-key.txt": (None, "~/.config/anthropic-key.txt"),
}

_DIR_MODE = 0o700
_FILE_MODE = 0o600
VALUE_MAX = 4096
_PROBE_TIMEOUT_S = 15

Prober = Callable[[str, str, dict], "tuple[bool, str, dict]"]


# --------------------------------------------------------------------------- #
# 文件面
# --------------------------------------------------------------------------- #
def _first_token_line(text: str) -> str:
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    return lines[0] if lines else ""


# --------------------------------------------------------------------------- #
# 豆包语音凭证的粘贴归一（§36 v0.37.1）——mac/Sources/CaptionCore.swift
# ``VolcanoSpeechCredential.parse`` / ``fileRepresentation`` 的 Python 镜像。Swift enum 仍是真源
# （壳的 decode() 是逐字节冻结的副本，只认两行 ``appid:`` / ``token:``）；server 是 web 侧唯一的写者，
# 不在这里归一，旧版凭证对就永远到不了壳认得的形状。判例用同一组 fixture 钉两侧不漂。
# --------------------------------------------------------------------------- #
# 一行 "AppID<sep>Token"：旧版 App ID 是 6–12 位数字，Access Token 是长的不透明串——新版 API key
# 从来没有「数字 + 分隔符」这个前缀形状。
_LEGACY_ONE_LINE = re.compile(r"^(\d{6,12})[\s:：,;，；]+(\S{20,})$")
# 带标签的一行（两行粘贴被单行输入框拍扁：换行变空格或直接丢）——控制台自己的标签活过了拍扁，
# 标出丢掉的换行原来在哪；token 标签在这里是必需的（无标签的拍扁对已被上一条认了）。
_LEGACY_LABELED_ONE_LINE = re.compile(
    r"^(?:app[\s_-]*(?:id|key)\s*[:：])?\s*(\d{6,12})\s*[\s:：,;，；]*(?:access[\s_-]*token|token|secret)\s*[:：]\s*(\S{20,})$",
    re.IGNORECASE)
_KNOWN_LABELS = ("appid", "appkey", "accesstoken", "token", "secret")
_COLON = re.compile(r"[:：]")
_LABEL_NOISE = re.compile(r"[\s_-]")


def _is_app_id(s: str) -> bool:
    """6–12 位 ASCII 数字——旧版控制台的 App ID 形状。"""
    return 6 <= len(s) <= 12 and s.isascii() and s.isdigit()


def _looks_like_token(s: str) -> bool:
    """一个长的不透明 token（控制台的 Access Token 是 32 位）。"""
    return len(s) >= 20 and re.search(r"\s", s) is None


def _strip_label(line: str) -> str:
    """"App ID: 321…" / "ACCESS_TOKEN：2tz…" / "appid:321…" → 裸值。冒号前的前缀归一（小写；去空格、
    下划线、连字符）后必须是**已知**的凭证标签——别的前缀原样保留，因为裸 key 可能合法地含冒号。"""
    colon = _COLON.search(line)
    if colon is None:
        return line
    label = _LABEL_NOISE.sub("", line[:colon.start()].lower())
    if label not in _KNOWN_LABELS:
        return line
    return line[colon.end():].strip()


def _legacy_pair_from_lines(lines: list) -> "Optional[tuple[str, str]]":
    """两个非空行：首行剥标签是 App ID、次行剥标签像 token → (id, token)；否则 None。"""
    app_id, token = _strip_label(lines[0]), _strip_label(lines[1])
    if _is_app_id(app_id) and _looks_like_token(token):
        return app_id, token
    return None


def _legacy_pair_from_line(line: str) -> "Optional[tuple[str, str]]":
    """一行 "AppID<sep>Token"（不带 / 带标签）→ (id, token)；否则 None。"""
    for regex in (_LEGACY_ONE_LINE, _LEGACY_LABELED_ONE_LINE):
        m = regex.match(line)
        if m:
            return m.group(1), m.group(2)
    return None


def volcano_speech_credential(text: str) -> Optional[dict]:
    """粘贴自动识别 → ``{"legacy": bool, "file": <存盘内容>}``；空 = None。

    旧版形状：两个非空行且两半真像那一对（App ID 在前；控制台的 "App ID:" / "Access Token:" 标签——
    以及我们自己的存盘标签——先剥掉），或一行 "AppID<sep>Token"（带标签或不带）。其余一律是新版
    API key，原样放行——包括被硬折行的 key：拼回而不是撕成一对假凭证。"""
    trimmed = str(text).strip()
    if not trimmed:
        return None
    lines = [ln.strip() for ln in trimmed.splitlines() if ln.strip()]
    if len(lines) >= 2:
        pair, api_key = _legacy_pair_from_lines(lines), "".join(lines)
    else:
        pair, api_key = _legacy_pair_from_line(trimmed), trimmed
    if pair is None:
        return {"legacy": False, "file": api_key}
    return {"legacy": True, "file": "appid:%s\ntoken:%s" % pair}


def _lookup(name: str) -> dict:
    entry = _BY_NAME.get(name or "")
    if entry is None:
        raise NotFoundError("unknown secret", {"name": str(name)[:100]})
    return entry


def read_value(home: Path, name: str) -> Optional[str]:
    """server 内部读（探针用）；对外永不回显。"""
    try:
        raw = (paths.secrets_dir(home) / name).read_text(encoding="utf-8")
    except OSError:
        return None
    return _first_token_line(raw) or None


def _nonempty_file(raw: str) -> bool:
    try:
        return bool(_first_token_line(Path(raw).expanduser().read_text(encoding="utf-8")))
    except (OSError, ValueError, UnicodeDecodeError):
        return False


def legacy_present(home: Path, name: str, config_doc: Optional[dict] = None) -> bool:
    """§19 第二 / 三层有非空文件（config.yaml 显式路径优先，再旧默认路径）？无旧路径的凭证恒 False。"""
    spec = _LEGACY_PATHS.get(name)
    if spec is None:
        return False
    key_path, default = spec
    explicit = settings_catalog.walk_config(config_doc if config_doc is not None else settings_catalog.load_config_doc(home),
                                            key_path) if key_path else None
    raw = explicit if isinstance(explicit, str) and explicit.strip() else default
    return _nonempty_file(raw)


def _status(home: Path, entry: dict, config_doc: Optional[dict] = None) -> dict:
    p = paths.secrets_dir(home) / entry["name"]
    present = read_value(home, entry["name"]) is not None
    mtime = None
    if present:
        try:
            mtime = int(p.stat().st_mtime)
        except OSError:
            mtime = None
    return {"name": entry["name"], "label": dict(entry["label"]), "present": present,
            "verifiable": entry["probe"] is not None, "mtime": mtime,
            "legacy": (not present) and legacy_present(home, entry["name"], config_doc)}


def snapshot(home: Path) -> dict:
    """``GET /api/secrets``：``{"secrets": [{name, label, present, verifiable, mtime, legacy}]}``。"""
    config_doc = settings_catalog.load_config_doc(home)
    return {"secrets": [_status(home, e, config_doc) for e in SECRETS]}


def write_value(home: Path, name: str, payload: dict) -> dict:
    """``PUT /api/secrets/{name}``：写 0600 文件（空值 = 删）；返回该条状态 + add-only
    ``legacy_pair``（只有豆包语音凭证识别为旧版 App ID + Access Token 对时 True）。"""
    entry = _lookup(name)
    unknown = set(payload) - {"value"}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    value = payload.get("value")
    if not isinstance(value, str):
        raise InvalidFieldError("value must be a string", {"field": "value"})
    if len(value) > VALUE_MAX:
        raise InvalidFieldError("value is too long", {"field": "value", "max": VALUE_MAX})
    token, legacy_pair = _stored_form(entry["name"], value)
    path = paths.secrets_dir(home) / entry["name"]
    if not token:
        _remove(path)
    else:
        _write_file(path, token)
    receipt = _status(home, entry)
    receipt["legacy_pair"] = legacy_pair
    return receipt


def _stored_form(name: str, value: str) -> "tuple[str, bool]":
    """粘贴 → (落盘内容, 识别为旧版凭证对?)。默认只留首个非空行（§19 一行 token）；
    豆包语音凭证走 ``volcano_speech_credential`` 归一（§36 v0.37.1，可能两行）。"""
    if name == VOLCANO_SPEECH:
        cred = volcano_speech_credential(value)
        return ("", False) if cred is None else (cred["file"], cred["legacy"])
    return _first_token_line(value), False


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _write_file(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod(path.parent, _DIR_MODE)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(token + "\n", encoding="utf-8")
    _chmod(tmp, _FILE_MODE)
    os.replace(tmp, path)


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 探针（默认实现走网络；测试注入 prober）
# --------------------------------------------------------------------------- #
def _http_json(req: urllib.request.Request) -> "tuple[int, dict]":
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:  # noqa: S310 - fixed https hosts
            return resp.status, _parse_json(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _parse_json(exc.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ProbeNetworkError(str(exc))


def _parse_json(raw: bytes) -> dict:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


class ProbeNetworkError(Exception):
    """网络 / 服务层失败（DNS / 超时 / 拒连、服务过载、非 token 形状的错误码）——凭证的判决**未知**，
    与「凭证无效」区分开报给用户（原生 KeyProbe.Outcome.failed）。"""


def _probe_anthropic(token: str, _ctx: dict) -> "tuple[bool, str, dict]":
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": token, "anthropic-version": "2023-06-01"})
    status, doc = _http_json(req)
    if 200 <= status < 300:
        return True, "key accepted by api.anthropic.com", {}
    detail = "api.anthropic.com answered HTTP %d%s" % (status, _api_error_suffix(doc))
    if status in ANTHROPIC_UNAUTHORIZED_STATUSES:
        return False, detail, {}
    raise ProbeNetworkError(detail)   # 429 / 529 / 5xx：服务侧，key 的判决未知


def _api_error_suffix(doc: dict) -> str:
    """Anthropic 错误体 ``{"error": {"message": …}}`` 的人话尾巴（原生 apiErrorMessage）；没有就空。"""
    err = doc.get("error")
    message = err.get("message") if isinstance(err, dict) else None
    return ": %s" % _clip(message) if isinstance(message, str) and message.strip() else ""


def _clip(text: str) -> str:
    return re.sub(r"\s+", " ", text)[:200]


_PROBES: dict = {"anthropic": _probe_anthropic}


def default_prober(kind: str, token: str, ctx: dict) -> "tuple[bool, str, dict]":
    return _PROBES[kind](token, ctx)


def _probe_target(home: Path, name: str, value: Optional[str] = None) -> "tuple[str, str]":
    """(probe kind, token)；无探针 / 尚未保存 → 400。``value`` 给了就探它（不落盘）。"""
    kind = _lookup(name)["probe"]
    if kind is None:
        raise InvalidFieldError("this credential has no verification probe", {"name": name})
    if value is not None:
        token = _first_token_line(value)
        if not token:
            raise InvalidFieldError("value is empty", {"field": "value"})
        return kind, token
    token = read_value(home, name)
    if token is None:
        raise InvalidFieldError("nothing saved yet - save the credential first", {"name": name})
    return kind, token


def verify_payload_value(payload: dict) -> Optional[str]:
    """``POST …/verify`` 的 body：空 = 探已保存的；``{"value": str}`` = 粘贴即验证；其余 400。"""
    unknown = set(payload) - {"value"}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    if "value" not in payload:
        return None
    value = payload.get("value")
    if not isinstance(value, str):
        raise InvalidFieldError("value must be a string", {"field": "value"})
    if len(value) > VALUE_MAX:
        raise InvalidFieldError("value is too long", {"field": "value", "max": VALUE_MAX})
    return value


def human_auth_reason(kind: str, raw: str) -> dict:
    """原生 ``humanAuthReason``：探针种类 + raw detail → ``{zh, en}`` 分类人话，raw 跟在括号里。"""
    template = AUTH_REASONS.get(kind) or {"zh": "{raw}", "en": "{raw}"}
    return {lang: sentence.replace("{raw}", raw) for lang, sentence in template.items()}


def verify(home: Path, name: str, prober: Optional[Prober] = None,
           value: Optional[str] = None) -> dict:
    """``POST /api/secrets/{name}/verify`` → ``{"ok": bool, "network": bool, "detail": str, "extra": {}}``
    三分判决：凭证本身的错 ``ok:false, network:false`` 再多带 add-only ``reason {zh, en}``
    （``human_auth_reason``）；网络 / 服务层失败（``ProbeNetworkError``）``ok:false, network:true``
    = 判决未知，不带 ``reason``。"""
    kind, token = _probe_target(home, name, value)
    try:
        ok, detail, extra = (prober or default_prober)(kind, token, {})
    except ProbeNetworkError as exc:
        return {"ok": False, "network": True, "detail": "network error: %s" % _clip(str(exc)),
                "extra": {}}
    return _with_reason({"ok": ok, "network": False, "detail": detail, "extra": extra}, kind)


def _with_reason(receipt: dict, kind: str) -> dict:
    """凭证本身的错才挂 add-only ``reason``：ok 的、探针没跑的前提失败（``extra.precondition``）都不挂。"""
    if not receipt["ok"] and not receipt["extra"].get("precondition"):
        receipt["reason"] = human_auth_reason(kind, receipt["detail"])
    return receipt
