"""store2 migrate — registry YAML（含 archive/）-> SQLite 一次性迁移（BUILD-CONTRACT §3）。

用法（repo 根目录运行）::

    python3 -m act.lib.store2.migrate_yaml --registry act/registry --db /tmp/store2.db \
        [--dry-run] [--allow-unknown]

设计要点（依据 docs/design/store2-mapping.md + store2/schema.md「给 B3 的约定」）：

- **payload = canonical to_dict() 全文**（schema.sql 冷列注释「原样存 registry
  YAML 的 JSON 化全文」）。热列（status/tier/title/…）是纯投影：round-trip 的
  特例（legacy status 串、不合 GLOB 的 deadline、回填的 prev_status）全部天然
  落在「payload 存 verbatim 真相、热列存 schema 能装下的归一值」这一条规则里，
  export_yaml 只读 payload 即可逐字节还原，不需要任何内部标记键。
- **逐卡 write-then-readback 等价校验**：INSERT 后回读 payload，dict 相等 +
  registry 同参 YAML dump 字节相等 + 热列与推导值逐列核对；任何一卡不等价 =
  整体 ROLLBACK + 非零退出（一次性工具，宁可拒绝不可默默失真）。
- **拒绝非空目标库**：目标已有任何 store2 行（或非 store2 内容）一律 refuse。
- **--dry-run 全程排演**：对 :memory: 库跑完整迁移（含 readback 校验），逐卡
  打印计划，目标库零接触。
- 容忍规则镜像 live registry（宪法第 11 条：解析失败不许崩 pass）：坏 YAML/
  空文件/非 dict 成员/缺 id 跳过 + 报告；archive 与 active 同 id 双份取 archive
  版（load() 的 crash-mid-move 语义）并报 residue；R-000-example.yaml 按文件名
  排除；list 形 YAML 文件合法（逐成员迁移）。**例外：未知顶层键比 live registry
  更严**——from_dict 是静默丢弃，一次性迁移丢字段不可逆，默认整体拒绝并点名
  键名，--allow-unknown 才降回丢弃语义。
- ids/时间戳 verbatim：id 原字符串入 PK；YAML 里的一切时间戳字段原样留在
  payload。热列 created/updated 是**新增合成列**（YAML 无祖先）：
  created = card.sent_at > sources[0].date > 文件 mtime（逐卡在 dry-run 标注
  取值来源；TODO(contract) 见 mapping doc §9.2），updated = 文件 mtime。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import email.utils
import json
import re
import sqlite3
from pathlib import Path

import yaml

from .export_yaml import dropped_keys, dump_card_yaml, normalize_card, say
# 热列顺序同包单源：INSERT 的列表与回读校验的 SELECT 列表都从它派生，
# schema 加列时迁移自动跟上（曾在这里手抄过一份 17 列 tuple）。
from .store import CARD_COLUMNS, SCHEMA_VERSION

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_UTC = _dt.timezone.utc

STATUS_VOCAB = frozenset((
    "detected", "card_sent", "raising", "approved", "executing",
    "review", "delivered", "rejected", "trashed", "merged", "archived"))
MERGED_PREFIX = "merged_into:"           # legacy verbatim 状态串（registry 同名常量）
_TIER_VOCAB = ("T0", "T1", "T2")         # schema CHECK；registry 本身不校验（mapping §1）
_DEADLINE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_YMD_RE = _DEADLINE_RE


class MigrateError(Exception):
    """迁移级错误——报告后非零退出，绝不半途留下半库。"""


# --------------------------------------------------------------------------- #
# 时间推导
# --------------------------------------------------------------------------- #
def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_UTC).strftime(TS_FMT)


def _date_only(s: str):
    """'YYYY-MM-DD' → 00:00:00Z 作 proxy；日期非法（02-30）→ None。"""
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=_UTC)
    except ValueError:
        return None


def _iso8601(s: str):
    try:
        dt = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_UTC)


def _rfc2822(s: str):
    try:
        dt = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_UTC)


def _parse_ts(s: str):
    """尽力解析 'YYYY-MM-DD' / ISO-8601 / RFC 2822（gmail 源日期），失败返回 None。"""
    s = s.strip()
    if _YMD_RE.match(s):
        return _date_only(s)
    return _iso8601(s) or _rfc2822(s)


def _epoch_iso(sa) -> "str | None":
    try:
        return _iso(_dt.datetime.fromtimestamp(sa, tz=_UTC))
    except (OverflowError, OSError, ValueError):
        return None


def _sent_at_iso(sa) -> "str | None":
    """card.sent_at → ISO：epoch 数（非 bool）或可解析的时间串；其余 None。"""
    if isinstance(sa, (int, float)) and not isinstance(sa, bool):
        return _epoch_iso(sa)
    if isinstance(sa, str):
        dt = _parse_ts(sa)
        return _iso(dt) if dt else None
    return None


def _first_source(norm: dict) -> "dict | None":
    srcs = norm.get("sources") or []
    if isinstance(srcs, list) and srcs and isinstance(srcs[0], dict):
        return srcs[0]
    return None


def _first_source_date_iso(norm: dict) -> "str | None":
    src = _first_source(norm)
    if src is None:
        return None
    dv = src.get("date")
    dt = _parse_ts(dv) if isinstance(dv, str) else None
    return _iso(dt) if dt else None


def _derive_created(norm: dict, mtime: float):
    """created 合成列：card.sent_at > sources[0].date > 文件 mtime（mapping §9.2）。"""
    card = norm.get("card")
    if isinstance(card, dict) and card.get("sent_at") is not None:
        created = _sent_at_iso(card["sent_at"])
        if created:
            return created, "card.sent_at"
    created = _first_source_date_iso(norm)
    if created:
        return created, "sources[0].date"
    return _iso(_dt.datetime.fromtimestamp(mtime, tz=_UTC)), "file-mtime"


# --------------------------------------------------------------------------- #
# 扫描（镜像 registry._iter_files/load_all 的容忍语义）
# --------------------------------------------------------------------------- #
def _read_members(path: Path, notes: list) -> "list | None":
    """一个卡文件的成员列表（单 doc 包成一项）；不可读/空文件记 note 并返回 None。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        notes.append(f"skip unreadable {path.name}: {e}")
        return None
    if data is None:
        notes.append(f"skip empty {path.name}")
        return None
    return data if isinstance(data, list) else [data]


def _member_id(item: dict) -> "str | None":
    rid = item.get("id")
    return rid if isinstance(rid, str) else (str(rid) if rid is not None else None)


def _member_entry(item, path: Path, mtime: float, in_archive: bool, notes: list) -> "tuple | None":
    """(rid, entry) for one file member; junk members are noted and skipped."""
    if not isinstance(item, dict):
        notes.append(f"skip non-dict member in {path.name}")
        return None
    rid = _member_id(item)
    if not rid:
        notes.append(f"skip card without id in {path.name}")
        return None
    return rid, {"raw": item, "file": path, "mtime": mtime, "in_archive": in_archive}


def _register(by_id: dict, rid: str, entry: dict, notes: list) -> None:
    """First sighting wins, except that the archive copy overrides an active
    residue (crash-mid-move 双份：archive 副本权威，active 残件只报不清)."""
    prev = by_id.get(rid)
    if prev is None:
        by_id[rid] = entry
    elif entry["in_archive"] and not prev["in_archive"]:
        notes.append(f"residue: {rid} 双份，取 archive 版 "
                     f"（active 残件 {prev['file'].name} 未清理）")
        by_id[rid] = entry
    else:
        notes.append(f"duplicate id {rid} in {entry['file'].name} 被忽略 "
                     f"（保留 {prev['file'].name}，镜像 load() 先到先得）")


def _walk(files, in_archive: bool, by_id: dict, notes: list) -> None:
    for path in files:
        items = _read_members(path, notes)
        if items is None:
            continue
        mtime = path.stat().st_mtime
        for item in items:
            found = _member_entry(item, path, mtime, in_archive, notes)
            if found is not None:
                _register(by_id, found[0], found[1], notes)


def scan_registry(reg_dir: Path):
    """扫 active + archive/ 两目录 → ({id: entry}, notes)。

    entry = {"raw", "file", "mtime", "in_archive"}；archive 版覆盖 active 版
    （crash-mid-move residue，archive 权威——live load() 明文规则）。
    """
    if not reg_dir.is_dir():
        raise MigrateError(f"registry 目录不存在: {reg_dir}")
    notes: list = []
    by_id: dict = {}
    active = sorted(p for p in reg_dir.glob("*.yaml")
                    if p.name != "R-000-example.yaml")   # 文档样例永不入库
    _walk(active, False, by_id, notes)
    arch_dir = reg_dir / "archive"
    if arch_dir.is_dir():
        _walk(sorted(arch_dir.glob("*.yaml")), True, by_id, notes)
    return by_id, notes


# --------------------------------------------------------------------------- #
# 逐卡计划（热列推导 + 警告收集）
# --------------------------------------------------------------------------- #
def plan_card(rid: str, entry: dict, *, allow_unknown: bool = False,
              coerce_cost: bool = True):
    """entry → {"hot": {...}, "norm": dict, "sources": [...], "created_from",
    "warnings": [...], "errors": [...]}。errors 非空 = 本卡无法忠实入库。

    热列推导单点 = act/lib/store2/hot.py（store.put_card 同源，防两处漂移）。
    ``coerce_cost``：CLI 迁移保持历史行为（垃圾 cost 归 None，_coerce_cost
    语义）；激活协议传 False——payload 必须与备份 YAML 逐字段零差异
    （R2.1.3），归一会制造假 diff。
    """
    from . import hot as _hot
    warnings: list = []
    errors: list = []
    norm = normalize_card(entry["raw"])
    _note_unknown_keys(entry["raw"], allow_unknown, warnings, errors)
    if coerce_cost:
        _coerce_cost_field(norm, warnings)

    hot_cols, hot_warnings, hot_errors = _hot.derive(norm)
    warnings += hot_warnings
    errors += hot_errors
    src_rows, src_warnings = _hot.source_rows(norm)
    warnings += src_warnings

    created, created_from = _derive_created(norm, entry["mtime"])
    updated = _iso(_dt.datetime.fromtimestamp(entry["mtime"], tz=_UTC))
    hot_full = dict(hot_cols)
    hot_full.update({
        "id": rid, "created": created, "updated": updated, "version": 1,
        "board_rev": 1, "tombstone": 0, "last_actor_type": "system",
        "payload": _payload_json(norm, errors),
    })
    return {"hot": hot_full, "norm": norm, "sources": src_rows,
            "created_from": created_from, "warnings": warnings, "errors": errors}


def _note_unknown_keys(raw: dict, allow_unknown: bool, warnings: list, errors: list) -> None:
    """未知顶层键：入库即静默丢字段——默认整体拒绝（列出键名，人工核对后修
    export_yaml 词表或显式 --allow-unknown 降级为 from_dict 丢弃语义）。"""
    for k in dropped_keys(raw):
        if allow_unknown:
            warnings.append(f"未知顶层键 {k!r} 被丢弃（--allow-unknown 已放行，"
                            "对齐 from_dict 语义）")
        else:
            errors.append(f"未知顶层键 {k!r}：入库即静默丢字段，拒绝"
                          "（补进 export_yaml 词表，或 --allow-unknown 显式放行）")


def _coerce_cost_field(norm: dict, warnings: list) -> None:
    """cost_estimate_usd：镜像 live analyze._coerce_cost 的容忍（mapping §7）：
    float() 失败的垃圾值归 None；能过 float() 的数值**保 verbatim**（不转
    float——int 5 转 5.0 会破坏干净卡的字节 round-trip）。"""
    cost = norm.get("cost_estimate_usd")
    if cost is None:
        return
    try:
        float(cost)
    except (TypeError, ValueError):
        warnings.append(f"cost_estimate_usd {cost!r} 非数字，归 None"
                        "（_coerce_cost 语义——此处 payload 也归一）")
        norm["cost_estimate_usd"] = None


def _payload_json(norm: dict, errors: list) -> "str | None":
    """payload = verbatim JSON 全文——JSON 装不下的值（手编 YAML 的未加引号
    日期/datetime、``!!binary``）无法忠实入库 = errors，绝不让 TypeError
    逃出去变成调用方的 traceback / 激活重试风暴（宪法第 11 条，§53.3）。"""
    try:
        return json.dumps(norm, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        errors.append(f"payload 无法 JSON 序列化（{e}）——多为手编 YAML 里"
                      "未加引号的日期/时间或 !!binary 值，给值加引号后重试")
        return None


def _ready_plans(remaining: list, inserted: set) -> list:
    """Plans whose merged parent (if any) is already inserted, id-sorted."""
    ready = [p for p in remaining
             if not p["hot"]["merged_into_id"]
             or p["hot"]["merged_into_id"] in inserted]
    ready.sort(key=lambda p: p["hot"]["id"])
    return ready


def topo_order(plans: list) -> list:
    """merged 父卡先插（merged_into_id 自引用 FK 立即校验）。解不开 = 报错。"""
    inserted: set = set()
    remaining = list(plans)
    ordered: list = []
    while remaining:
        ready = _ready_plans(remaining, inserted)
        if not ready:
            stuck = ", ".join(
                f'{p["hot"]["id"]}→{p["hot"]["merged_into_id"]}' for p in remaining)
            raise MigrateError(f"merged 父指针无法解析（父卡缺失或成环）: {stuck}")
        for p in ready:
            ordered.append(p)
            inserted.add(p["hot"]["id"])
        remaining = [p for p in remaining if p not in ready]
    return ordered


_topo_order = topo_order   # 本模块内部沿用的旧名（判例仍引用）


# --------------------------------------------------------------------------- #
# 目标库检查 + 写入 + 回读校验
# --------------------------------------------------------------------------- #
_DATA_TABLES = ("cards", "sources", "notes", "dispatches", "activities")


def _refuse_if_foreign_schema(con: sqlite3.Connection, tables: set, db_path: Path) -> None:
    uv = con.execute("PRAGMA user_version").fetchone()[0]
    if uv != SCHEMA_VERSION or not set(_DATA_TABLES) <= tables:
        raise MigrateError(
            f"refuse: {db_path} 已有非 store2-v{SCHEMA_VERSION} 内容"
            f"（user_version={uv}）")


def _refuse_if_rows(con: sqlite3.Connection) -> None:
    for t in _DATA_TABLES:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - 白名单表名
        if n:
            raise MigrateError(f"refuse: 目标库非空（{t} 有 {n} 行）——"
                               "一次性迁移只进全新库")


def _refuse_if_written(con: sqlite3.Connection) -> None:
    row = con.execute("SELECT value FROM board_revision WHERE id = 1").fetchone()
    if row and row[0] != 0:
        raise MigrateError(f"refuse: board_revision={row[0]}，目标库已被写过")


def _inspect_existing(con: sqlite3.Connection, db_path: Path) -> str:
    """An existing file must be an empty file or a pristine store2 schema."""
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not tables:
        return "empty-file"
    _refuse_if_foreign_schema(con, tables, db_path)
    _refuse_if_rows(con)
    _refuse_if_written(con)
    return "schema-only"


def check_target(db_path: Path) -> str:
    """目标必须是不存在/空文件/纯 schema 三者之一，否则 refuse。"""
    if not db_path.exists():
        return "fresh"
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        try:
            return _inspect_existing(con, db_path)
        except sqlite3.DatabaseError as e:
            # 垃圾/损坏文件：连 sqlite_master 都读不了——干净 refuse，不落 traceback
            raise MigrateError(f"refuse: {db_path} 不是可读的 SQLite 库（{e}）")
    finally:
        con.close()


def apply_schema(con: sqlite3.Connection) -> None:
    """PUBLIC (activate.py 的首跑迁移也用它)：schema.sql 全部 IF NOT EXISTS / OR IGNORE，幂等。"""
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    con.executescript(schema)


_apply_schema = apply_schema   # 本模块内部沿用的旧名（判例仍引用）


_CARD_INSERT_SQL = ("INSERT INTO cards ({}) VALUES ({})".format(
    ", ".join(CARD_COLUMNS), ", ".join(":" + c for c in CARD_COLUMNS)))
_CARD_SELECT_SQL = "SELECT {} FROM cards WHERE id = ?".format(", ".join(CARD_COLUMNS))


def _insert_plan(con: sqlite3.Connection, p: dict, run_ts: str) -> None:
    con.execute(_CARD_INSERT_SQL, p["hot"])
    for row in p["sources"]:
        con.execute(
            "INSERT INTO sources (card_id, channel, who, date, ref, quote,"
            " origin_key, created_at) VALUES (?,?,?,?,?,?,NULL,?)",
            (p["hot"]["id"], row["channel"], row["who"], row["date"],
             row["ref"], row["quote"], run_ts))


def _check_payload(rid: str, back: dict, norm: dict) -> None:
    if back != norm:
        raise MigrateError(
            f"readback: {rid} payload 与 canonical dict 不等价"
            "（多为 JSON 无法承载的形态，如非 str 键/日期对象）")
    if dump_card_yaml(back) != dump_card_yaml(norm):
        raise MigrateError(f"readback: {rid} YAML dump 字节不等（键序漂移）")


def _check_hot(rid: str, got: dict, hot: dict) -> None:
    for col in got:
        if got[col] != hot[col]:
            raise MigrateError(
                f"readback: {rid} 热列 {col} 不一致 "
                f"({got[col]!r} != {hot[col]!r})")


def _check_source_count(con: sqlite3.Connection, rid: str, expected: int) -> None:
    n = con.execute("SELECT COUNT(*) FROM sources WHERE card_id = ?", (rid,)).fetchone()[0]
    if n != expected:
        raise MigrateError(f"readback: {rid} sources 行数 {n} != {expected}")


def _readback_plan(con: sqlite3.Connection, p: dict) -> None:
    """逐卡 write-then-readback 等价校验。"""
    rid = p["hot"]["id"]
    row = con.execute(_CARD_SELECT_SQL, (rid,)).fetchone()
    if row is None:
        raise MigrateError(f"readback: {rid} 插入后查不到")
    got = dict(zip(CARD_COLUMNS, row))
    back = json.loads(got.pop("payload"))
    _check_payload(rid, back, p["norm"])
    _check_hot(rid, got, p["hot"])
    _check_source_count(con, rid, len(p["sources"]))


def run_migration(con: sqlite3.Connection, plans: list, run_ts: str) -> None:
    """按 topo 序 INSERT + 逐卡回读等价校验。调用方持有事务与 rollback。"""
    for p in plans:
        _insert_plan(con, p, run_ts)
    con.execute("UPDATE board_revision SET value = 1 WHERE id = 1")
    for p in plans:
        _readback_plan(con, p)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m act.lib.store2.migrate_yaml",
        description="一次性 registry YAML（含 archive/）-> store2 SQLite 迁移")
    ap.add_argument("--registry", required=True,
                    help="registry 目录（含 *.yaml 与 archive/ 子目录）")
    ap.add_argument("--db", required=True, help="目标 SQLite 路径（必须为空库）")
    ap.add_argument("--dry-run", action="store_true",
                    help="对 :memory: 排演全程并逐卡打印计划，目标零接触")
    ap.add_argument("--allow-unknown", action="store_true",
                    help="把未知顶层键从整体拒绝降级为 WARN + 丢弃"
                         "（from_dict 的静默丢弃语义，显式开关后才允许）")
    args = ap.parse_args(argv)

    try:
        by_id, scan_notes = scan_registry(Path(args.registry))
        plans = topo_order(_build_plans(by_id, args.allow_unknown))
        target_state = check_target(Path(args.db))
        run_ts = _iso(_dt.datetime.now(tz=_UTC))
        _migrate_into(_open_target(args.db, args.dry_run), plans, run_ts)
        _report(plans, scan_notes, args.dry_run, args.db, target_state)
        return 0
    except MigrateError as e:
        say(f"migrate: REFUSED/FAILED — {e}", err=True)
        return 2


def _plan_or_errors(rid: str, entry: dict, allow_unknown: bool) -> tuple:
    """(plan, []) 或 (None, [错误行…])——坏形态 = 干净 REFUSED（exit 2），不 traceback
    （与激活协议同一兜底）。"""
    try:
        p = plan_card(rid, entry, allow_unknown=allow_unknown)
    except (TypeError, ValueError) as e:
        return None, [f"{rid}: plan_card failed ({e.__class__.__name__}: {e})"]
    if p["errors"]:
        return None, [f"{rid}: {e}" for e in p["errors"]]
    return p, []


def _build_plans(by_id: dict, allow_unknown: bool) -> list:
    """每张卡一个 plan；任何一张无法忠实入库 = 整体拒绝（先打印全部错误）。"""
    plans, card_errors = [], []
    for rid in sorted(by_id):
        p, errs = _plan_or_errors(rid, by_id[rid], allow_unknown)
        card_errors += errs
        if p is not None:
            plans.append(p)
    if card_errors:
        for e in card_errors:
            say(f"migrate: ERROR {e}", err=True)
        raise MigrateError(f"{len(card_errors)} 张卡无法忠实入库，整体拒绝"
                           "（修复源文件或修 schema 后重来）")
    return plans


def _open_target(db: str, dry_run: bool) -> sqlite3.Connection:
    """dry-run 对 :memory: 排演全程，目标零接触。"""
    if dry_run:
        return sqlite3.connect(":memory:")
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(db)


def _migrate_into(con: sqlite3.Connection, plans: list, run_ts: str) -> None:
    """schema → 单事务 INSERT + 回读；任何失败整体 ROLLBACK，绝不留半库。"""
    try:
        con.isolation_level = None          # 手动事务
        con.execute("PRAGMA foreign_keys = ON")   # per-connection（schema.md 约定）
        _apply_schema(con)
        con.execute("BEGIN IMMEDIATE")
        try:
            run_migration(con, plans, run_ts)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()


def _say_plan_lines(plans: list, dry_run: bool) -> None:
    if dry_run:
        for p in plans:
            h = p["hot"]
            extra = "".join(f"\n    WARN {w}" for w in p["warnings"])
            say(f"  {h['id']}: status={h['status']} tier={h['tier']} "
                f"origin_trust={h['origin_trust']} "
                f"created={h['created']}({p['created_from']}) "
                f"sources={len(p['sources'])}{extra}")
        return
    for p in plans:
        for w in p["warnings"]:
            say(f"migrate: WARN {p['hot']['id']}: {w}")


def _report(plans: list, scan_notes: list, dry_run: bool, db: str, target_state: str) -> None:
    for msg in scan_notes:
        say(f"migrate: NOTE {msg}")
    _say_plan_lines(plans, dry_run)
    n_src = sum(len(p["sources"]) for p in plans)
    n_warn = sum(len(p["warnings"]) for p in plans)
    mode = "DRY-RUN（:memory: 排演通过，目标零接触）" if dry_run else "DONE"
    say(f"migrate: {mode} — {len(plans)} cards, {n_src} source rows, "
        f"{n_warn} warnings, target={db}({target_state}), "
        f"board_revision=1, readback 等价校验全过")


if __name__ == "__main__":
    raise SystemExit(main())
