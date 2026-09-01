"""store2 激活 — YAML registry → SQLite 真源的首跑切换 + 每日 YAML 导出。

契约：docs/CONTRACT.md §53.3（激活协议：备份 → 迁移 → 导出 → 逐字段比对 →
零差异才写标记；任何差异 = YAML 仍是真源 + 响亮拒绝）、§53.4（每日导出）、
§53.6（doctor 行 + 回滚）。owner 决策 D2 / 需求 R2.1.2–R2.1.3。

调用面：
* ``tick()`` —— actd 每个 pass 调一次（已激活 = 一次 stat；未激活 = 尝试激活；
  激活后 = 每日导出节流）。任何异常留给调用方记日志，绝不崩 pass。
* ``python3 -m act.lib.store2.activate [--report | --export-now]`` —— 手动。

**没有半状态**（设计要点）：
* 迁移从**备份目录**读（不是 live 目录）——备份与 DB 必然一致；写标记前再
  校一次 live 目录与备份的 manifest（sha256），中途有别的进程写了 YAML 就拒绝
  本次尝试（60 s 后重试），绝不把迁移快照之后落的卡丢在真源之外。
* 拒绝 = 删掉刚建的 DB（无标记的 DB 一律视为可丢弃的派生物）+ 写
  ``state/store2_activation.json``（result/reason/diff 摘要 + retry_after）——
  doctor 据此 FAIL 并给出修复路径；YAML 照常是真源，管线零感知。
* 备份目录永不覆盖：``state/backups/registry-<UTC ts>[-n]/``，旁边同名
  ``.manifest.json`` 记每个文件的 sha256。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Callable, Optional

from .. import config, registry
from . import export_yaml, migrate_yaml
from .export_yaml import normalize_card, say
from .store import SCHEMA_VERSION

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
RETRY_AFTER_REFUSED_S = 6 * 3600     # 数据差异：同样的输入会同样失败，别每 10 s 刷
RETRY_AFTER_RACE_S = 60              # live 目录在迁移中被写：很快就能再试
EXPORT_MARKER_NAME = "registry_export.json"
DIFF_CAP = 50                        # activation.json 里最多留多少行差异

# scan_registry 的 note 前缀里，哪些意味着「这张卡会在迁移中被丢掉」——激活
# 一律拒绝（一次性 CLI 只是报告 + 跳过；真源切换不能有静默丢卡）
_LOSSY_NOTE_PREFIXES = ("skip unreadable", "skip non-dict", "skip card without id",
                        "duplicate id")


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime(TS_FMT)


def _atomic_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


# --------------------------------------------------------------------------- #
# 备份 + manifest
# --------------------------------------------------------------------------- #
def manifest(root: Path) -> dict:
    """``{relative path: sha256}``，只收 ``*.yaml``（含 archive/）。目录不存在 = 空。"""
    out: dict = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*.yaml")):
        try:
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError as e:
            out[str(p.relative_to(root))] = f"unreadable:{e.__class__.__name__}"
    return out


def fresh_backup_dir(now: _dt.datetime) -> Path:
    """``state/backups/registry-<ts>/``，已存在则加 ``-2``/``-3``……永不覆盖。"""
    base = registry.registry_backups_dir()
    stamp = now.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cand = base / f"registry-{stamp}"
    n = 2
    while cand.exists() or cand.with_suffix(".manifest.json").exists():
        cand = base / f"registry-{stamp}-{n}"
        n += 1
    return cand


def backup_registry(now: _dt.datetime) -> "tuple[Path, dict]":
    """整目录复制（含 archive/、list 文件、坏文件、样例——verbatim），返回
    (备份目录, manifest)。manifest 同时落在 ``<backup>.manifest.json``。"""
    src = config.REGISTRY_DIR
    dest = fresh_backup_dir(now)
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    man = manifest(dest)
    _atomic_json(dest.with_suffix(".manifest.json"),
                 {"created_at": _iso(now), "source": str(src), "files": man})
    return dest, man


# --------------------------------------------------------------------------- #
# 逐字段比对（R2.1.3）
# --------------------------------------------------------------------------- #
def _cards_by_id(root: Path) -> "tuple[dict, list]":
    """目录 → {id: canonical dict}（scan_registry 的容忍语义：archive 权威、
    样例排除、list 文件展开），外加 notes。"""
    if not root.is_dir():
        return {}, []
    by_id, notes = migrate_yaml.scan_registry(root)
    return {rid: normalize_card(e["raw"]) for rid, e in by_id.items()}, notes


def parity_diff(backup_dir: Path, export_dir: Path) -> list:
    """备份 YAML ↔ 导出 YAML 逐卡逐字段比对，返回差异行（空 = 零差异）。

    两边都过 ``normalize_card``（= registry from_dict∘to_dict）：键序/引号
    等表现层差异不算，字段值差异、多卡、少卡都算。"""
    a, _ = _cards_by_id(backup_dir)
    b, _ = _cards_by_id(export_dir)
    diffs: list = []
    for rid in sorted(set(a) - set(b)):
        diffs.append(f"{rid}: present in backup, missing from export")
    for rid in sorted(set(b) - set(a)):
        diffs.append(f"{rid}: present in export, missing from backup")
    for rid in sorted(set(a) & set(b)):
        if a[rid] == b[rid]:
            continue
        keys = sorted(k for k in set(a[rid]) | set(b[rid])
                      if a[rid].get(k) != b[rid].get(k))
        for k in keys:
            diffs.append(f"{rid}.{k}: backup={_short(a[rid].get(k))} "
                         f"export={_short(b[rid].get(k))}")
    return diffs


def _short(v, n: int = 60) -> str:
    s = json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n - 1] + "…"


# --------------------------------------------------------------------------- #
# 导出（首跑 + 每日）
# --------------------------------------------------------------------------- #
def export_marker_path() -> Path:
    return config.STATE_DIR / EXPORT_MARKER_NAME


def refresh_export(now: Optional[_dt.datetime] = None) -> Path:
    """立刻把 store2 全量导出到 ``state/registry-export/``（prune：镜像 DB，
    tombstone 卡的文件随之消失——导出目录大小天然 = 活卡数，不会长）。"""
    now = now or _now()
    out = registry.registry_export_dir()
    export_yaml.export_db(registry.store2_db_path(), out, prune=True)
    _atomic_json(export_marker_path(),
                 {"last_run": now.astimezone().date().isoformat(),
                  "last_run_at": _iso(now)})
    return out


def daily_export(now: Optional[_dt.datetime] = None) -> Optional[str]:
    """每本地日一次（R2.1.2）；已做过 = None，否则返回一行日志。"""
    now = now or _now()
    today = now.astimezone().date().isoformat()
    marker = _read_json(export_marker_path())
    if marker.get("last_run") == today:
        return None
    out = refresh_export(now)
    return f"daily YAML export refreshed -> {out}"


# --------------------------------------------------------------------------- #
# 激活
# --------------------------------------------------------------------------- #
def _dispose_db(db: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(db) + suffix).unlink()
        except FileNotFoundError:
            pass


def _refuse(now: _dt.datetime, reason: str, *, diff: Optional[list] = None,
            backup_dir: Optional[Path] = None, retry_after_s: int) -> dict:
    result = {
        "result": "refused", "at": _iso(now), "reason": reason,
        "diff": list(diff or [])[:DIFF_CAP], "diff_total": len(diff or []),
        "backup_dir": str(backup_dir) if backup_dir else None,
        "retry_after": _iso(now + _dt.timedelta(seconds=retry_after_s)),
        "truth": registry.BACKEND_YAML,
    }
    _atomic_json(registry.store2_activation_path(), result)
    return result


def first_run(now: Optional[_dt.datetime] = None) -> dict:
    """备份 → 迁移（从备份读）→ 导出 → 逐字段比对 → live 目录未变 → 写标记。

    返回结果 dict（``result`` ∈ activated | refused）；同一内容也落在
    ``state/store2_activation.json``。调用方负责日志。
    """
    now = now or _now()
    db = registry.store2_db_path()
    registry.reset_store_cache()
    if db.exists():
        # 无标记的 DB = 上次未完成/被拒的派生物，YAML 仍是真源——丢弃重来
        _dispose_db(db)

    # 全新安装（没有任何 YAML 卡）也走同一条路：空备份 → 空库 → 零差异 → 激活
    backup_dir, man = backup_registry(now)

    # —— 迁移（读备份，保证备份 ↔ DB 一致）——
    by_id, notes = migrate_yaml.scan_registry(backup_dir) if backup_dir.is_dir() \
        else ({}, [])
    lossy = [" ".join(n.split()) for n in notes if n.startswith(_LOSSY_NOTE_PREFIXES)]
    if lossy:
        return _refuse(now, "registry has files the migration would drop: "
                       + "; ".join(lossy), diff=lossy, backup_dir=backup_dir,
                       retry_after_s=RETRY_AFTER_REFUSED_S)
    plans, errors = [], []
    for rid in sorted(by_id):
        p = migrate_yaml.plan_card(rid, by_id[rid], allow_unknown=False,
                                   coerce_cost=False)
        if p["errors"]:
            errors += [f"{rid}: {e}" for e in p["errors"]]
        else:
            plans.append(p)
    if errors:
        return _refuse(now, f"{len(errors)} card(s) cannot be represented in store2",
                       diff=errors, backup_dir=backup_dir,
                       retry_after_s=RETRY_AFTER_REFUSED_S)
    try:
        plans = migrate_yaml._topo_order(plans)
        migrate_yaml.check_target(db)
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db))
        try:
            con.isolation_level = None
            con.execute("PRAGMA foreign_keys = ON")
            migrate_yaml._apply_schema(con)
            con.execute("BEGIN IMMEDIATE")
            try:
                migrate_yaml.run_migration(con, plans, _iso(now))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()
    except (migrate_yaml.MigrateError, sqlite3.Error) as e:
        _dispose_db(db)
        return _refuse(now, f"migration failed: {e}", backup_dir=backup_dir,
                       retry_after_s=RETRY_AFTER_REFUSED_S)

    # —— 导出 + 逐字段比对 ——
    export_dir = registry.registry_export_dir()
    export_yaml.export_db(db, export_dir, prune=True)
    diff = parity_diff(backup_dir, export_dir)
    if diff:
        _dispose_db(db)
        return _refuse(now, f"export differs from backup in {len(diff)} field(s)"
                       " — YAML stays the truth", diff=diff, backup_dir=backup_dir,
                       retry_after_s=RETRY_AFTER_REFUSED_S)

    # —— live 目录在此期间没被别的进程写过 ——
    if manifest(config.REGISTRY_DIR) != man:
        _dispose_db(db)
        return _refuse(now, "registry changed while migrating (another writer);"
                       " will retry", backup_dir=backup_dir,
                       retry_after_s=RETRY_AFTER_RACE_S)

    # —— 标记：从这一刻起 store2 是真源 ——
    from act import __version__ as app_version
    marker = {
        "activated_at": _iso(now), "backup_dir": str(backup_dir),
        "cards": len(plans), "schema_version": SCHEMA_VERSION,
        "app_version": app_version,
    }
    _atomic_json(registry.store2_truth_path(), marker)
    _atomic_json(export_marker_path(),
                 {"last_run": now.astimezone().date().isoformat(),
                  "last_run_at": _iso(now)})
    registry.reset_store_cache()
    result = {"result": "activated", "at": _iso(now), "cards": len(plans),
              "backup_dir": str(backup_dir), "truth": registry.BACKEND_SQLITE,
              "notes": notes}
    _atomic_json(registry.store2_activation_path(), result)
    return result


def status(now: Optional[_dt.datetime] = None) -> dict:
    """当前数据层状态（doctor / --report 共用）。

    ``state`` ∈ active | yaml_forced | db_missing | refused | cooldown | pending。
    ``yaml_forced`` = 回滚开关（config `registry.backend: yaml` / env / 测试
    override）在起作用——不管标记在不在，YAML 是真源。"""
    now = now or _now()
    marker = _read_json(registry.store2_truth_path())
    act = _read_json(registry.store2_activation_path())
    forced = registry.backend_forced()
    db = registry.store2_db_path()
    out = {"backend": registry.backend(), "forced": forced,
           "config_backend": config.registry_backend_setting(),
           "marker": marker or None, "activation": act or None,
           "db_exists": db.exists(), "db_path": str(db)}
    if forced == registry.BACKEND_YAML:
        out["state"] = "yaml_forced"
        out["marker_present"] = bool(marker)
    elif marker and not db.exists():
        out["state"] = "db_missing"
    elif marker:
        out["state"] = "active"
        # 激活后仍有人往 registry 目录写 YAML（切换前已在跑的雷达进程）= 那张卡
        # 不在真源里——doctor WARN，运维手动导入（§53.6）
        late = []
        try:
            t0 = _dt.datetime.strptime(marker.get("activated_at", ""), TS_FMT)
            t0 = t0.replace(tzinfo=_dt.timezone.utc).timestamp()
            for p in registry.registry_yaml_files(include_archived=True):
                try:
                    if p.stat().st_mtime > t0 + 1:
                        late.append(p.name)
                except OSError:
                    continue
        except ValueError:
            pass
        out["late_yaml_writes"] = sorted(late)
        exp = _read_json(export_marker_path())
        out["export_last_run"] = exp.get("last_run")
    elif act.get("result") == "refused":
        try:
            retry = _dt.datetime.strptime(act.get("retry_after", ""), TS_FMT)
            retry = retry.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            retry = now
        out["state"] = "cooldown" if retry > now else "refused"
    else:
        out["state"] = "pending"
    return out


def ensure(now: Optional[_dt.datetime] = None) -> dict:
    """幂等：需要就激活。返回 status()-形 dict（激活尝试的结果并在 ``attempt``）。

    只在 **auto** 下尝试（没有 override / env / config 强制值）：测试 sandbox 与
    CI 的双后端跑靶都是强制值，永不在这里偷偷迁移。"""
    now = now or _now()
    st = status(now)
    if st["state"] not in ("pending", "refused"):
        return st
    if registry.backend_forced() is not None:
        return st
    attempt = first_run(now)
    st = status(now)
    st["attempt"] = attempt
    return st


def tick(now: Optional[_dt.datetime] = None) -> list:
    """actd 每 pass 一次：激活（如需）+ 每日导出（已激活时）。返回日志行。"""
    now = now or _now()
    lines: list = []
    st = ensure(now)
    attempt = st.get("attempt")
    if attempt:
        if attempt["result"] == "activated":
            lines.append(f"ACTIVATED — SQLite is now the registry truth "
                         f"({attempt['cards']} cards; YAML backup at "
                         f"{attempt['backup_dir']}; export at "
                         f"{registry.registry_export_dir()})")
        else:
            lines.append("ACTIVATION REFUSED — YAML stays the truth: "
                         f"{attempt['reason']}")
            for d in attempt.get("diff", [])[:10]:
                lines.append(f"  diff: {d}")
            if attempt.get("diff_total", 0) > 10:
                lines.append(f"  … {attempt['diff_total'] - 10} more (see "
                             f"{registry.store2_activation_path()})")
            lines.append(f"  backup kept at {attempt.get('backup_dir')}; "
                         f"retry after {attempt.get('retry_after')}; "
                         "run `python3 -m act.doctor` for the summary")
    if st["state"] == "db_missing":
        lines.append(f"FAIL — {registry.STORE2_TRUTH_NAME} present but "
                     f"{registry.store2_db_path()} is missing; see "
                     "docs/TROUBLESHOOTING.md (store2 回滚)")
    if registry.backend() == registry.BACKEND_SQLITE and st["state"] == "active":
        line = daily_export(now)
        if line:
            lines.append(line)
    return lines


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None, log: Callable[[str], None] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m act.lib.store2.activate",
        description="store2 激活（备份→迁移→比对→标记）/ 状态报告 / 立即导出")
    ap.add_argument("--report", action="store_true", help="只打印状态 JSON")
    ap.add_argument("--export-now", action="store_true",
                    help="立刻刷新 state/registry-export/（需已激活）")
    args = ap.parse_args(argv)
    log = log or say
    if args.report:
        print(json.dumps(status(), ensure_ascii=False, indent=1, default=str))
        return 0
    if args.export_now:
        if registry.backend() != registry.BACKEND_SQLITE:
            say("export-now: store2 is not the active backend", err=True)
            return 2
        out = refresh_export()
        say(f"export refreshed -> {out}")
        return 0
    lines = tick()
    for line in lines:
        log(line)
    st = status()
    say(f"store2: state={st['state']} backend={st['backend']}")
    return 0 if st["state"] in ("active", "yaml_forced") else 2


if __name__ == "__main__":
    sys.exit(main())
