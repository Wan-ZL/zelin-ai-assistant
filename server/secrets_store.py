"""server/secrets_store.py — 凭证文件的 web 侧写者与探针（CONTRACT §19 / §49 / §68.3）。

- ``GET /api/secrets`` → 每个已知凭证的**状态**（present / verifiable / mtime + add-only
  ``legacy``：secrets 文件缺席但 §19 第二层——``~/.config/anthropic-key.txt``——的文件非空；
  只判在不在，永不读内容出去），**绝不回显值**——值是 write-only。
- ``PUT /api/secrets/{name}`` body ``{"value": "<token>"}`` → 写
  ``<home>/config/secrets/<name>``（dir 0700 / file 0600；多行粘贴只留首个非空行，
  与 act/lib/secrets.write_secret 同一契约）；空值 = 删文件。
- ``POST /api/secrets/{name}/verify`` → 用文件里的值做一次最小活探针（Anthropic
  ``GET /v1/models``）。探针经 ``prober`` 注入缝——测试绝不碰网络。body 可带 add-only 的 ``{"value": "<token>"}`` =
  **粘贴即验证**（先验后存、无效的 key 永不落盘）——只探这个值、**不写文件**。
  三分判决：``ok:true`` / ``ok:false, network:false`` = key 本身的错（401 / 403）→ 回执多带
  add-only ``reason {zh, en}``；``ok:false, network:true`` = **判决未知**（DNS / 超时 / 529 /
  5xx），不带 ``reason``。

v2.0.0：Slack user token 与 Gmail 应用密码两行随雷达退役（xoxb- 门口拒绝、auth.test 自动填
``owner_slack_user_id``、IMAP 探针一并删除）；火山两把字幕 key（``volcano-speech-key.txt`` /
``volcano-ark-key.txt``）随 Live captions 退役（§36 tombstone），PUT 回执的 ``legacy_pair`` 与豆包语音凭证的
粘贴归一一并删除（全文见 tag full）。Anthropic key 是唯一的模型凭证——
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

# §19 固定文件名（act/lib/secrets.py 同名常量）
SECRETS: tuple = (
    {"name": "anthropic-api-key.txt", "probe": "anthropic",
     "label": {"zh": "Anthropic API key", "en": "Anthropic API key"}},
)
_BY_NAME = {s["name"]: s for s in SECRETS}

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
    """``PUT /api/secrets/{name}``：写 0600 文件（空值 = 删）；返回该条状态。"""
    entry = _lookup(name)
    unknown = set(payload) - {"value"}
    if unknown:
        raise UnknownFieldError("unknown field", {"fields": sorted(unknown)})
    value = payload.get("value")
    if not isinstance(value, str):
        raise InvalidFieldError("value must be a string", {"field": "value"})
    if len(value) > VALUE_MAX:
        raise InvalidFieldError("value is too long", {"field": "value", "max": VALUE_MAX})
    token = _first_token_line(value)   # §19：只留首个非空行
    path = paths.secrets_dir(home) / entry["name"]
    if not token:
        _remove(path)
    else:
        _write_file(path, token)
    return _status(home, entry)


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
