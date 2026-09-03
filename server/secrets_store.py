"""server/secrets_store.py — 凭证文件的 web 侧写者与探针（CONTRACT §19 / §49 / §68）。

原生 Settings 的「凭证」行（CredentialRowView / KeyProbe）落到 server：

- ``GET /api/secrets`` → 每个已知凭证的**状态**（present / verifiable / mtime），
  **绝不回显值**——值是 write-only：写进去之后 web 只能看见「已保存」。
- ``PUT /api/secrets/{name}`` body ``{"value": "<token>"}`` → 写
  ``<home>/config/secrets/<name>``（dir 0700 / file 0600；多行粘贴只留首个非空行，
  与 act/lib/secrets.write_secret 同一契约）；空值 = 删文件（回落 config.yaml 显式
  路径 / 旧默认路径的解析顺序）。
- ``POST /api/secrets/{name}/verify`` → 用文件里的值做一次最小活探针
  （Anthropic ``GET /v1/models``、Slack ``auth.test``——成功顺手把 ``user_id``
  写进 override ``owner_slack_user_id``（§15.3 v0.14 身份零手填）、Gmail IMAP
  LOGIN）。探针经 ``prober`` 注入缝——测试绝不碰网络。火山（字幕）两把 key 没有
  免费探针，``verifiable: false``，verify → 400。

文件名是 §19 的固定词表（server 不 import act.lib.secrets：它 import 期就把
SECRETS_DIR 钉在进程 env 的 HOME 上，测试注入 home 会失真——判例
tests/test_server_secrets.py 钉住两侧名单逐字一致）。
"""
from __future__ import annotations

import imaplib
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
    {"name": "slack-user-token.txt", "probe": "slack",
     "label": {"zh": "Slack user token（xoxp-）", "en": "Slack user token (xoxp-)"}},
    {"name": "gmail-app-password.txt", "probe": "gmail",
     "label": {"zh": "Gmail 应用专用密码", "en": "Gmail app password"}},
    {"name": "volcano-speech-key.txt", "probe": None,
     "label": {"zh": "豆包语音凭证（字幕识别）", "en": "Doubao speech credential (captions)"}},
    {"name": "volcano-ark-key.txt", "probe": None,
     "label": {"zh": "Ark API key（字幕翻译）", "en": "Ark API key (caption translation)"}},
)
_BY_NAME = {s["name"]: s for s in SECRETS}

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


def _status(home: Path, entry: dict) -> dict:
    p = paths.secrets_dir(home) / entry["name"]
    present = read_value(home, entry["name"]) is not None
    mtime = None
    if present:
        try:
            mtime = int(p.stat().st_mtime)
        except OSError:
            mtime = None
    return {"name": entry["name"], "label": dict(entry["label"]), "present": present,
            "verifiable": entry["probe"] is not None, "mtime": mtime}


def snapshot(home: Path) -> dict:
    """``GET /api/secrets``：``{"secrets": [{name, label, present, verifiable, mtime}]}``。"""
    return {"secrets": [_status(home, e) for e in SECRETS]}


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
    token = _first_token_line(value)
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
    """网络层失败（DNS / 超时 / 拒连）——与「凭证无效」区分开报给用户。"""


def _probe_anthropic(token: str, _ctx: dict) -> "tuple[bool, str, dict]":
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": token, "anthropic-version": "2023-06-01"})
    status, _doc = _http_json(req)
    if status == 200:
        return True, "key accepted by api.anthropic.com", {}
    return False, "api.anthropic.com answered HTTP %d" % status, {}


def _probe_slack(token: str, _ctx: dict) -> "tuple[bool, str, dict]":
    req = urllib.request.Request(
        "https://slack.com/api/auth.test", data=b"", method="POST",
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/x-www-form-urlencoded"})
    _status, doc = _http_json(req)
    if doc.get("ok") is True:
        extra = {k: doc.get(k) for k in ("user_id", "user", "team") if doc.get(k)}
        return True, "auth.test ok", extra
    return False, "auth.test failed: %s" % (doc.get("error") or "unknown"), {}


def _probe_gmail(token: str, ctx: dict) -> "tuple[bool, str, dict]":
    address = str(ctx.get("address") or "").strip()
    if not address:
        return False, "no Gmail address configured (Sources → Gmail address)", {}
    try:
        conn = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=_PROBE_TIMEOUT_S)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise ProbeNetworkError(str(exc))
    try:
        conn.login(address, token)
    except imaplib.IMAP4.error as exc:
        return False, "IMAP LOGIN rejected: %s" % _clip(str(exc)), {}
    finally:
        _quiet_logout(conn)
    return True, "IMAP LOGIN ok as %s" % address, {}


def _quiet_logout(conn) -> None:
    try:
        conn.logout()
    except Exception:  # noqa: BLE001 - 已经拿到判决，收尾失败无关
        pass


def _clip(text: str) -> str:
    return re.sub(r"\s+", " ", text)[:200]


_PROBES: dict = {"anthropic": _probe_anthropic, "slack": _probe_slack, "gmail": _probe_gmail}


def default_prober(kind: str, token: str, ctx: dict) -> "tuple[bool, str, dict]":
    return _PROBES[kind](token, ctx)


def _gmail_context(home: Path) -> dict:
    section = settings_catalog.lookup("sources")
    field = settings_catalog.field_index(section)["gmail_address"]
    value, _src = settings_catalog.effective(
        field, settings_catalog.read_overrides(home), settings_catalog.load_config_doc(home))
    return {"address": value or ""}


def _probe_target(home: Path, name: str) -> "tuple[str, str]":
    """(probe kind, token)；无探针 / 尚未保存 → 400。"""
    kind = _lookup(name)["probe"]
    if kind is None:
        raise InvalidFieldError("this credential has no verification probe", {"name": name})
    token = read_value(home, name)
    if token is None:
        raise InvalidFieldError("nothing saved yet - save the credential first", {"name": name})
    return kind, token


def _autofill_slack_owner(home: Path, kind: str, ok: bool, extra: dict) -> None:
    """§15.3 v0.14：auth.test 成功 → owner_slack_user_id 自动写入（身份零手填）。"""
    if ok and kind == "slack" and isinstance(extra.get("user_id"), str):
        settings_catalog.set_flat_override(home, "owner_slack_user_id", extra["user_id"])


def verify(home: Path, name: str, prober: Optional[Prober] = None) -> dict:
    """``POST /api/secrets/{name}/verify`` → ``{"ok": bool, "detail": str, "extra": {}}``。
    网络失败 ``ok:false`` + ``network:true``（不是凭证的错）。Slack 成功自动填
    ``owner_slack_user_id``。"""
    kind, token = _probe_target(home, name)
    ctx = _gmail_context(home) if kind == "gmail" else {}
    try:
        ok, detail, extra = (prober or default_prober)(kind, token, ctx)
    except ProbeNetworkError as exc:
        return {"ok": False, "network": True, "detail": "network error: %s" % _clip(str(exc)),
                "extra": {}}
    _autofill_slack_owner(home, kind, ok, extra)
    return {"ok": ok, "network": False, "detail": detail, "extra": extra}
