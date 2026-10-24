"""server/about.py — 「关于」与更新的 server 半边（§26 / §56.1 / §68.6 / §79）。

- ``GET /api/about`` → ``{"version", "home", "repo", "update_available", "update_check",
  "check_enabled"}``：版本真源 = ``act.__version__``（§56.1）；``update_check`` =
  ``state/update_check.json`` 的公开子集（checked_at / latest / url；ETag 不外发）；
  ``update_available`` 由**本模块现算**（v2.0.0：dashboard.json 投影随 actd 退役）——缓存的
  latest 严格新于运行版本且 ``updates.check_enabled`` 开着时才是 dict
  ``{current, latest, url, pkg_asset_url, manual_upgrade}``（v1 的 act 侧同名投影已随
  dashboard.json 退役，这里是唯一一份），否则 None；``check_enabled`` =
  ``updates.check_enabled`` 的 effective 值（override → config.yaml → 默认 true；坏的 overrides
  文件当空，关于页不因它 409）。**周期检查的触发者**（§26 v2.0.0 修订）：缓存缺席或超过 24h 且
  检查开着时，GET 在后台线程里起一次 ``python -m act.lib.update_check``（它自己仍按 24h 预算
  自闸），响应永远立刻回缓存——GET 不等网络。环境变量 ``AIASSISTANT_UPDATE_AUTOCHECK=0`` 关掉
  这个后台触发（测试套件设它：判例绝不出网；手动「立即检查」不受影响）。
- ``POST /api/update/check`` → ``python -m act.lib.update_check --force``（§26 手动「立即检查」；
  ``updates.check_enabled: false`` 时它自己拒发网络请求），stdout 那一行 JSON 原样透出。
- ``POST /api/update/install {}`` → §79 升级器：先过两道 409，再分离起 ``scripts/upgrade.sh``
  （ff-only 到 origin/main 上的 v* tag + ``bash install.sh --non-interactive --from-upgrade``；
  永不碰录制引擎）。

  1. **跨大版本只许手动**（§26 追记 2026-10-23 桥接版本；1.x 的 §56.3 已退役，PR-0 语义保留）：缓存的 latest
     的 major 大于运行版本（``update_check.needs_manual_upgrade``）→ 409 ``manual_upgrade``，
     details 带 ``manual_upgrade`` / ``current`` / ``latest`` / ``fix``（``git fetch --tags origin &&
     git checkout v<latest> && bash install.sh``）。缓存读不出 = 不拒（宪法第 11 条）。
  2. **升级器还没落地** → 501：``scripts/upgrade.sh`` 随 PR-D 进 repo（lite 2.0 的切片顺序）；
     在那之前本端点诚实地说「这个 checkout 还没有升级器」，details 带同一条手动命令。

  v2.0.0 退役（全文见 tag full）：§56 自动部署 agent 的 kickstart、``deploy_state`` 的现读与
  ``deploy_refused`` 409、``repair`` 模块——2.0 没有自动部署 agent，升级只走 §79。
  ``spawner`` / ``update_reader`` 注入缝（防腐 #3：绝不 module-global）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

from act.lib import update_check   # 依赖方向：server → act.lib（§58.3 规则 3）
from server import background_jobs, paths, settings_catalog, subproc
from server.errors import ConflictError, NotImplementedError501, UnknownFieldError

_UPDATE_TIMEOUT_S = 40
CHECK_ENABLED_FIELD = ("updates", "updates_check_enabled")   # settings_catalog 里那把旋钮（§68.1）
UPGRADE_SCRIPT = "scripts/upgrade.sh"                           # §79（PR-D 落地）
UPGRADE_LOG = "upgrade.log"
CHECK_INTERVAL_S = update_check.CHECK_INTERVAL_SECONDS

_JOBS = background_jobs.Threads("update-check")

UpdateReader = Callable[[], Optional[dict]]
Spawner = Callable[[list, Path, Path], None]


def version() -> str:
    try:
        from act import __version__  # act 包本身在依赖方向门的白名单内（server → act）
        return str(__version__)
    except Exception:  # noqa: BLE001 - 版本答不上也不许 500，诚实报 unknown
        return "unknown"


def _read_json(p: Path) -> Optional[dict]:
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def update_check_public(home: Path) -> Optional[dict]:
    doc = _read_json(paths.update_check_path(home))
    if doc is None:
        return None
    return {k: doc.get(k) for k in ("checked_at", "latest", "url", "pkg_asset_url")}


def check_enabled(home: Path) -> bool:
    """``updates.check_enabled`` 的 effective 值；overrides 坏文件当空（落到 config.yaml）。"""
    section, key = CHECK_ENABLED_FIELD
    field = settings_catalog.field_index(settings_catalog.lookup(section))[key]
    try:
        overrides = settings_catalog.read_overrides(home)
    except ConflictError:
        overrides = {}
    value, _src = settings_catalog.effective(field, overrides, settings_catalog.load_config_doc(home))
    return value is not False


def update_available(update: Optional[dict], enabled: bool, current: str) -> Optional[dict]:
    """§26 payload — dict ONLY when the cached latest is strictly newer and the
    check is on (an ABSENT / None value is the contract for "no known update")."""
    latest = (update or {}).get("latest")
    if not enabled or not isinstance(latest, str) or not update_check.is_newer(latest, current):
        return None
    return {"current": current, "latest": latest,
            "url": update.get("url") or update_check.RELEASES_PAGE_URL,
            "pkg_asset_url": update.get("pkg_asset_url"),
            "manual_upgrade": update_check.needs_manual_upgrade(latest, current)}


def _parse_iso(value) -> Optional[float]:
    if not isinstance(value, str):
        return None
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


def check_due(update: Optional[dict], now: Optional[float] = None) -> bool:
    """The cache is absent / unparsable / older than the 24h budget."""
    checked = _parse_iso((update or {}).get("checked_at"))
    current = now if now is not None else _dt.datetime.now(_dt.timezone.utc).timestamp()
    return checked is None or current - checked >= CHECK_INTERVAL_S


AUTOCHECK_ENV = "AIASSISTANT_UPDATE_AUTOCHECK"


def autocheck_enabled() -> bool:
    """The GET-triggered background check; ``AIASSISTANT_UPDATE_AUTOCHECK=0`` turns it off."""
    return os.environ.get(AUTOCHECK_ENV, "1").strip() != "0"


def _start_periodic_check(home: Path, runner=None) -> None:
    """One background ``python -m act.lib.update_check`` (self-gated to 24h); never two at once."""
    if _JOBS.live_count():
        return

    def job() -> None:
        subproc.run_module(home, "act.lib.update_check", [], timeout_s=_UPDATE_TIMEOUT_S,
                           runner=runner)

    _JOBS.spawn(job)


def join_jobs_for_tests(timeout: float = background_jobs.JOIN_TIMEOUT_S) -> bool:
    return _JOBS.join(timeout)


def snapshot(home: Path, runner=None, now: Optional[float] = None) -> dict:
    """``GET /api/about``."""
    update = update_check_public(home)
    enabled = check_enabled(home)
    current = version()
    if enabled and autocheck_enabled() and check_due(update, now):
        _start_periodic_check(home, runner)
    return {
        "version": current,
        "home": str(home),
        "repo": str(paths.repo_root()),
        "update_available": update_available(update, enabled, current),
        "update_check": update,
        "check_enabled": enabled,
    }


def check_now(home: Path, payload: dict, runner=None) -> dict:
    """``POST /api/update/check``：§26 ``--force`` 一次；返回 CLI 的 JSON 行。"""
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    rc, out, err = subproc.run_module(home, "act.lib.update_check", ["--force"],
                                      timeout_s=_UPDATE_TIMEOUT_S, runner=runner)
    doc = subproc.parse_json_output(out)
    if doc is None:
        return {"ok": False, "error": subproc.tail(err or out) or ("update_check exited %d" % rc)}
    return doc


# --------------------------------------------------------------------------- #
# POST /api/update/install — §79
# --------------------------------------------------------------------------- #
def manual_fix(latest: str) -> str:
    """The one manual upgrade command (inlined since deploy_state retired)."""
    tag = ("v" + latest) if latest else "v<latest>"
    return "git fetch --tags origin && git checkout %s && bash install.sh" % tag


def _read_update(home: Optional[Path], update_reader: Optional[UpdateReader]) -> Optional[dict]:
    """§26 缓存的公开子集（请求时现读）；读不到 / 坏文件 / 没给 home → None（宪法第 11 条）。"""
    if update_reader is None and home is None:
        return None
    try:
        doc = update_reader() if update_reader is not None else update_check_public(home)
    except Exception:  # noqa: BLE001 - 更新缓存答不上来不许拖垮「立即升级」
        return None
    return doc if isinstance(doc, dict) else None


def _refuse_if_manual_upgrade(update: Optional[dict]) -> None:
    """§26 / §56.3 追记：新的大版本只许手动升级——409 ``manual_upgrade`` 把手动命令交给页面。"""
    current = version()
    latest = _latest_of(update)
    if not update_check.needs_manual_upgrade(latest, current):
        return
    fix = manual_fix(latest)
    raise ConflictError(
        "v%s is a new major version (running v%s) - upgrades never cross a major version by "
        "themselves; upgrade by hand: %s" % (latest, current, fix),
        {"reason": "manual_upgrade", "manual_upgrade": True, "current": current,
         "latest": latest, "fix": fix})


def _default_spawner(argv: list, cwd: Path, log_path: Path) -> None:
    """Detached ``bash scripts/upgrade.sh`` — outlives the request (and the server
    restart the upgrade itself performs)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as fh:
        subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=fh, stderr=fh,
                         start_new_session=True)


def _latest_of(update: Optional[dict]) -> str:
    latest = (update or {}).get("latest")
    return latest if isinstance(latest, str) else ""


def _require_upgrader(root: Path, latest: str) -> Path:
    """The §79 script, or 501 while this checkout has none (it lands with PR-D)."""
    script = root / UPGRADE_SCRIPT
    if not script.is_file():
        raise NotImplementedError501(
            "this checkout has no upgrader yet (%s) - upgrade by hand: %s"
            % (UPGRADE_SCRIPT, manual_fix(latest)),
            {"reason": "upgrader_missing", "script": UPGRADE_SCRIPT, "fix": manual_fix(latest)})
    return script


def install_now(payload: dict, home: Optional[Path] = None,
                update_reader: Optional[UpdateReader] = None,
                spawner: Optional[Spawner] = None,
                repo: Optional[Path] = None) -> dict:
    """``POST /api/update/install {}`` → §79 upgrader (detached). 409 ``manual_upgrade``
    for a new major; 501 while ``scripts/upgrade.sh`` is not in this checkout yet."""
    if payload:
        raise UnknownFieldError("unknown field", {"fields": sorted(payload)})
    update = _read_update(home, update_reader)
    _refuse_if_manual_upgrade(update)
    root = repo or paths.repo_root()
    latest = _latest_of(update)
    script = _require_upgrader(root, latest)
    log_path = (home or root) / "state" / UPGRADE_LOG
    (spawner or _default_spawner)(["/bin/bash", str(script)], root, log_path)
    return {"ok": True, "action": "upgrade", "script": UPGRADE_SCRIPT, "log": str(log_path),
            "latest": latest or None}
