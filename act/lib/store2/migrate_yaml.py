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

from ..policy import classify_origin
from .export_yaml import dropped_keys, dump_card_yaml, normalize_card, say
# 热列顺序同包单源：INSERT 的列表与回读校验的 SELECT 列表都从它派生，
# schema 加列时迁移自动跟上（曾在这里手抄过一份 17 列 tuple）。
from .store import CARD_COLUMNS

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


def _parse_ts(s: str):
    """尽力解析 'YYYY-MM-DD' / ISO-8601 / RFC 2822（gmail 源日期），失败返回 None。"""
    s = s.strip()
    if _YMD_RE.match(s):
        try:  # date-only 取 00:00:00Z 作 proxy
            return _dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=_UTC)
        except ValueError:
            return None
    try:
        dt = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=_UTC)
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=_UTC)
    except (TypeError, ValueError, IndexError):
        return None


def _derive_created(norm: dict, mtime: float):
    """created 合成列：card.sent_at > sources[0].date > 文件 mtime（mapping §9.2）。"""
    card = norm.get("card")
    if isinstance(card, dict) and card.get("sent_at") is not None:
        sa = card["sent_at"]
        if isinstance(sa, (int, float)) and not isinstance(sa, bool):
            try:
                return _iso(_dt.datetime.fromtimestamp(sa, tz=_UTC)), "card.sent_at"
            except (OverflowError, OSError, ValueError):
                pass
        elif isinstance(sa, str):
            dt = _parse_ts(sa)
            if dt:
                return _iso(dt), "card.sent_at"
    srcs = norm.get("sources") or []
    if isinstance(srcs, list) and srcs and isinstance(srcs[0], dict):
        dv = srcs[0].get("date")
        if isinstance(dv, str):
            dt = _parse_ts(dv)
            if dt:
                return _iso(dt), "sources[0].date"
    return _iso(_dt.datetime.fromtimestamp(mtime, tz=_UTC)), "file-mtime"


# --------------------------------------------------------------------------- #
# 扫描（镜像 registry._iter_files/load_all 的容忍语义）
# --------------------------------------------------------------------------- #
def scan_registry(reg_dir: Path):
    """扫 active + archive/ 两目录 → ({id: entry}, notes)。

    entry = {"raw", "file", "mtime", "in_archive"}；archive 版覆盖 active 版
    （crash-mid-move residue，archive 权威——live load() 明文规则）。
    """
    if not reg_dir.is_dir():
        raise MigrateError(f"registry 目录不存在: {reg_dir}")
    notes: list = []
    by_id: dict = {}

    def _walk(files, in_archive: bool):
        for path in files:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as e:
                notes.append(f"skip unreadable {path.name}: {e}")
                continue
            if data is None:
                notes.append(f"skip empty {path.name}")
                continue
            items = data if isinstance(data, list) else [data]
            mtime = path.stat().st_mtime
            for item in items:
                if not isinstance(item, dict):
                    notes.append(f"skip non-dict member in {path.name}")
                    continue
                rid = item.get("id")
                rid = rid if isinstance(rid, str) else (
                    str(rid) if rid is not None else None)
                if not rid:
                    notes.append(f"skip card without id in {path.name}")
                    continue
                entry = {"raw": item, "file": path, "mtime": mtime,
                         "in_archive": in_archive}
                prev = by_id.get(rid)
                if prev is None:
                    by_id[rid] = entry
                elif in_archive and not prev["in_archive"]:
                    # crash-mid-move 双份：archive 副本权威，active 残件只报不清
                    notes.append(f"residue: {rid} 双份，取 archive 版 "
                                 f"（active 残件 {prev['file'].name} 未清理）")
                    by_id[rid] = entry
                else:
                    notes.append(f"duplicate id {rid} in {path.name} 被忽略 "
                                 f"（保留 {prev['file'].name}，镜像 load() 先到先得）")

    active = sorted(p for p in reg_dir.glob("*.yaml")
                    if p.name != "R-000-example.yaml")   # 文档样例永不入库
    _walk(active, in_archive=False)
    arch_dir = reg_dir / "archive"
    if arch_dir.is_dir():
        _walk(sorted(arch_dir.glob("*.yaml")), in_archive=True)
    return by_id, notes


# --------------------------------------------------------------------------- #
# 逐卡计划（热列推导 + 警告收集）
# --------------------------------------------------------------------------- #
def plan_card(rid: str, entry: dict, *, allow_unknown: bool = False):
    """entry → {"hot": {...}, "norm": dict, "sources": [...], "created_from",
    "warnings": [...], "errors": [...]}。errors 非空 = 本卡无法忠实入库。"""
    warnings: list = []
    errors: list = []
    norm = normalize_card(entry["raw"])
    for k in dropped_keys(entry["raw"]):
        # 未知顶层键：入库即静默丢字段——默认整体拒绝（列出键名，人工核对
        # 后修 export_yaml 词表或显式 --allow-unknown 降级为 from_dict 丢弃语义）
        if allow_unknown:
            warnings.append(f"未知顶层键 {k!r} 被丢弃（--allow-unknown 已放行，"
                            "对齐 from_dict 语义）")
        else:
            errors.append(f"未知顶层键 {k!r}：入库即静默丢字段，拒绝"
                          "（补进 export_yaml 词表，或 --allow-unknown 显式放行）")

    # -- status：legacy 'merged_into:<id>' 串热列归一成 merged（schema CHECK 只认
    #    11 词），payload 里保留 verbatim —— export 走 payload，round-trip 不失真
    raw_status = norm.get("status")
    merged_into_id = None
    if isinstance(raw_status, str) and raw_status.startswith(MERGED_PREFIX):
        hot_status = "merged"
        merged_into_id = raw_status[len(MERGED_PREFIX):].strip()
        warnings.append(f"legacy status {raw_status!r} 热列归一为 merged"
                        "（payload 保留原串）")
        if not merged_into_id:
            errors.append("legacy merged_into: 串无父卡 id，schema 无法表达")
    elif raw_status in STATUS_VOCAB:
        hot_status = raw_status
        if raw_status == "merged":
            mi = norm.get("merged_into")
            merged_into_id = mi if isinstance(mi, str) else (
                str(mi) if mi is not None else None)
            if not merged_into_id:
                errors.append("status=merged 但无 merged_into 父指针（CHECK 拒收）")
    else:
        hot_status = None
        errors.append(f"status {raw_status!r} 不在 schema 词表内")

    # -- prev_status：trashed/archived 缺失时按 live restore/unarchive fallback
    #    回填热列（payload 不加键——原文件没有就还是没有）
    prev = norm.get("prev_status")
    if prev is not None and prev not in STATUS_VOCAB:
        warnings.append(f"prev_status {prev!r} 不在词表，热列置 NULL/回填"
                        "（payload 保留原值）")
        prev = None
    if prev is None and hot_status == "trashed":
        prev = "detected"       # live registry.restore 的 fallback
        warnings.append("trashed 缺 prev_status，热列回填 detected")
    if prev is None and hot_status == "archived":
        prev = "delivered"      # live registry.unarchive 的 fallback
        warnings.append("archived 缺 prev_status，热列回填 delivered")

    # -- tier：schema CHECK 比 registry 严（registry 不校验，`tier: 7` 能存活，
    #    mapping §1/§7）——越界值热列回落默认 'T1'（dataclass 默认），payload 保
    #    verbatim，绝不因 LLM 污染崩整轮（宪法第 11 条；B4 测试钉死此语义）
    tier = norm.get("tier")
    if tier not in _TIER_VOCAB:
        warnings.append(f"tier {tier!r} 越界，热列回落 T1（payload 保留原值）")
        tier = "T1"

    # -- cost_estimate_usd：镜像 live analyze._coerce_cost 的容忍（mapping §7）：
    #    float() 失败的垃圾值归 None；能过 float() 的数值**保 verbatim**（不转
    #    float——int 5 转 5.0 会破坏干净卡的字节 round-trip）
    cost = norm.get("cost_estimate_usd")
    if cost is not None:
        try:
            float(cost)
        except (TypeError, ValueError):
            warnings.append(f"cost_estimate_usd {cost!r} 非数字，归 None"
                            "（_coerce_cost 语义——此处 payload 也归一）")
            norm["cost_estimate_usd"] = None

    # -- title/type：热列 NOT NULL——payload 保 verbatim，热列兜底成 str
    title = norm.get("title")
    if not isinstance(title, str):
        warnings.append(f"title {title!r} 非 str，热列存 str 兜底")
        title = "" if title is None else str(title)
    typ = norm.get("type")
    if not isinstance(typ, str):
        warnings.append(f"type {typ!r} 非 str，热列存 str 兜底")
        typ = "" if typ is None else str(typ)

    # -- deadline：GLOB 不过的值热列置 NULL（payload 保 verbatim）
    dl = norm.get("deadline")
    hot_deadline = dl if isinstance(dl, str) and _DEADLINE_RE.match(dl) else None
    if dl is not None and hot_deadline is None:
        warnings.append(f"deadline {dl!r} 不符 YYYY-MM-DD，热列置 NULL"
                        "（payload 保留原值）")

    # -- origin_trust：§50 canonical 裁决（T-15 已定）——policy.classify_origin
    #    对**全部** sources 取最小信任（fold 进过外部渠道的手打卡判 external），
    #    未知/畸形 channel fail-closed 落 external，与 live 铸卡侧同一真源
    origin_trust = classify_origin(norm.get("sources"))
    srcs = norm.get("sources") or []

    created, created_from = _derive_created(norm, entry["mtime"])
    updated = _iso(_dt.datetime.fromtimestamp(entry["mtime"], tz=_UTC))

    tr = norm.get("target_repo")
    if tr is not None and not isinstance(tr, str):
        warnings.append(f"target_repo {tr!r} 非 str，热列存 str 兜底")
        tr = str(tr)

    # -- sources 投影行（payload 已存 verbatim 全文；此处只做查询投影）。
    #    origin_key 一律 NULL：回溯推导强信号有全局 partial-unique 撞车风险
    #    （同一 gmail thread 喂过多卡），留给接线后的写路径。TODO(contract)
    src_rows = []
    for i, s in enumerate(srcs if isinstance(srcs, list) else []):
        if not isinstance(s, dict):
            warnings.append(f"sources[{i}] 非 dict，投影跳过（payload 仍保留）")
            continue

        def _txt(v):
            return v if v is None or isinstance(v, str) else str(v)

        src_rows.append({
            "channel": _txt(s.get("channel")) or "",
            "who": _txt(s.get("who")), "date": _txt(s.get("date")),
            "ref": _txt(s.get("ref")), "quote": _txt(s.get("quote")),
        })

    hot = {
        "id": rid, "status": hot_status, "prev_status": prev, "tier": tier,
        "type": typ, "title": title, "origin_trust": origin_trust,
        "target_repo": tr, "deadline": hot_deadline, "created": created,
        "updated": updated, "version": 1, "merged_into_id": merged_into_id,
        "board_rev": 1, "tombstone": 0, "last_actor_type": "system",
        "payload": json.dumps(norm, ensure_ascii=False),
    }
    return {"hot": hot, "norm": norm, "sources": src_rows,
            "created_from": created_from, "warnings": warnings, "errors": errors}


def _topo_order(plans: list) -> list:
    """merged 父卡先插（merged_into_id 自引用 FK 立即校验）。解不开 = 报错。"""
    inserted: set = set()
    remaining = list(plans)
    ordered: list = []
    while remaining:
        ready = [p for p in remaining
                 if not p["hot"]["merged_into_id"]
                 or p["hot"]["merged_into_id"] in inserted]
        if not ready:
            stuck = ", ".join(
                f'{p["hot"]["id"]}→{p["hot"]["merged_into_id"]}' for p in remaining)
            raise MigrateError(f"merged 父指针无法解析（父卡缺失或成环）: {stuck}")
        ready.sort(key=lambda p: p["hot"]["id"])
        for p in ready:
            ordered.append(p)
            inserted.add(p["hot"]["id"])
        remaining = [p for p in remaining if p not in ready]
    return ordered


# --------------------------------------------------------------------------- #
# 目标库检查 + 写入 + 回读校验
# --------------------------------------------------------------------------- #
_DATA_TABLES = ("cards", "sources", "notes", "dispatches", "activities")


def check_target(db_path: Path) -> str:
    """目标必须是不存在/空文件/纯 schema 三者之一，否则 refuse。"""
    if not db_path.exists():
        return "fresh"
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        try:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                return "empty-file"
            uv = con.execute("PRAGMA user_version").fetchone()[0]
            if uv != 1 or not set(_DATA_TABLES) <= tables:
                raise MigrateError(
                    f"refuse: {db_path} 已有非 store2-v1 内容（user_version={uv}）")
            for t in _DATA_TABLES:
                n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - 白名单表名
                if n:
                    raise MigrateError(f"refuse: 目标库非空（{t} 有 {n} 行）——"
                                       "一次性迁移只进全新库")
            row = con.execute(
                "SELECT value FROM board_revision WHERE id = 1").fetchone()
            if row and row[0] != 0:
                raise MigrateError(f"refuse: board_revision={row[0]}，目标库已被写过")
            return "schema-only"
        except sqlite3.DatabaseError as e:
            # 垃圾/损坏文件：连 sqlite_master 都读不了——干净 refuse，不落 traceback
            raise MigrateError(f"refuse: {db_path} 不是可读的 SQLite 库（{e}）")
    finally:
        con.close()


def _apply_schema(con: sqlite3.Connection) -> None:
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    con.executescript(schema)   # 全部 IF NOT EXISTS / OR IGNORE，幂等


def run_migration(con: sqlite3.Connection, plans: list, run_ts: str) -> None:
    """按 topo 序 INSERT + 逐卡回读等价校验。调用方持有事务与 rollback。"""
    card_sql = ("INSERT INTO cards ({}) VALUES ({})".format(
        ", ".join(CARD_COLUMNS), ", ".join(":" + c for c in CARD_COLUMNS)))
    for p in plans:
        con.execute(card_sql, p["hot"])
        for row in p["sources"]:
            con.execute(
                "INSERT INTO sources (card_id, channel, who, date, ref, quote,"
                " origin_key, created_at) VALUES (?,?,?,?,?,?,NULL,?)",
                (p["hot"]["id"], row["channel"], row["who"], row["date"],
                 row["ref"], row["quote"], run_ts))
    con.execute("UPDATE board_revision SET value = 1 WHERE id = 1")

    # -- 逐卡 write-then-readback 等价校验 -------------------------------- #
    for p in plans:
        rid = p["hot"]["id"]
        row = con.execute(
            "SELECT {} FROM cards WHERE id = ?".format(", ".join(CARD_COLUMNS)),
            (rid,)).fetchone()
        if row is None:
            raise MigrateError(f"readback: {rid} 插入后查不到")
        got = dict(zip(CARD_COLUMNS, row))
        back = json.loads(got.pop("payload"))
        if back != p["norm"]:
            raise MigrateError(
                f"readback: {rid} payload 与 canonical dict 不等价"
                "（多为 JSON 无法承载的形态，如非 str 键/日期对象）")
        if dump_card_yaml(back) != dump_card_yaml(p["norm"]):
            raise MigrateError(f"readback: {rid} YAML dump 字节不等（键序漂移）")
        for col in got:
            if got[col] != p["hot"][col]:
                raise MigrateError(
                    f"readback: {rid} 热列 {col} 不一致 "
                    f"({got[col]!r} != {p['hot'][col]!r})")
        n = con.execute("SELECT COUNT(*) FROM sources WHERE card_id = ?",
                        (rid,)).fetchone()[0]
        if n != len(p["sources"]):
            raise MigrateError(f"readback: {rid} sources 行数 {n} != "
                               f"{len(p['sources'])}")


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
        plans, card_errors = [], []
        for rid in sorted(by_id):
            p = plan_card(rid, by_id[rid], allow_unknown=args.allow_unknown)
            if p["errors"]:
                card_errors += [f"{rid}: {e}" for e in p["errors"]]
            else:
                plans.append(p)
        if card_errors:
            for e in card_errors:
                say(f"migrate: ERROR {e}", err=True)
            raise MigrateError(f"{len(card_errors)} 张卡无法忠实入库，整体拒绝"
                               "（修复源文件或修 schema 后重来）")
        plans = _topo_order(plans)

        target_state = check_target(Path(args.db))
        run_ts = _iso(_dt.datetime.now(tz=_UTC))

        if args.dry_run:
            con = sqlite3.connect(":memory:")
        else:
            Path(args.db).parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(args.db)
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

        for msg in scan_notes:
            say(f"migrate: NOTE {msg}")
        if args.dry_run:
            for p in plans:
                h = p["hot"]
                extra = "".join(f"\n    WARN {w}" for w in p["warnings"])
                say(f"  {h['id']}: status={h['status']} tier={h['tier']} "
                    f"origin_trust={h['origin_trust']} "
                    f"created={h['created']}({p['created_from']}) "
                    f"sources={len(p['sources'])}{extra}")
        else:
            for p in plans:
                for w in p["warnings"]:
                    say(f"migrate: WARN {p['hot']['id']}: {w}")
        n_src = sum(len(p["sources"]) for p in plans)
        n_warn = sum(len(p["warnings"]) for p in plans)
        mode = "DRY-RUN（:memory: 排演通过，目标零接触）" if args.dry_run else "DONE"
        say(f"migrate: {mode} — {len(plans)} cards, {n_src} source rows, "
            f"{n_warn} warnings, target={args.db}({target_state}), "
            f"board_revision=1, readback 等价校验全过")
        return 0
    except MigrateError as e:
        say(f"migrate: REFUSED/FAILED — {e}", err=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
