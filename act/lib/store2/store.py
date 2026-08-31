"""store2 存取层 — SQLite 地基的唯一写入口（BUILD-CONTRACT §3；PR2 不接线，actd 不 import）。

职责（B2，与 schema.md「给 B2/B3/B4 的接口约定」逐条对应）：
* 连接管理：WAL + busy_timeout=5000 + foreign_keys=ON（per-connection！）、每线程一连接
* 写事务 helper：BEGIN IMMEDIATE → board_revision +1 → 新值盖到被触碰卡的 board_rev → COMMIT
  （子表 notes/sources/dispatches 变更也 bump 所属卡；no-op 不写不 bump）
* CAS 三件套（抄 dashi database.mjs:2181-2211 / #requireVersion / #throwMissingOrConflict 模式）：
  ①预检 409 语义（expected/actual 随错误带出）②UPDATE ... WHERE id=? AND version=?
  ③changes!=1 → 重查分 404（卡没了）/ 409（版本被别人推走）
* 类型化转移 API：transition(card_id, verb, actor_type, expected_version) —— verb 表只负责
  算目标状态与随行字段（prev_status 回程票 / merged_into_id 父指针），合法性完全交给
  schema 的 transition_whitelist trigger 执法（fail-closed：查不到 = ILLEGAL_TRANSITION）
* activities：每笔真实变更追加 [{field,before,after}] 审计行（dashi task_activities 同型）
* 增量读：changes_since(cursor) 按 board_rev 拉变更，含 tombstone 行 → 客户端学到删除

actor 语义（schema.md）：actor = 动作的发起者，不是写库进程 —— inbox 动作记 'user'，
radar/triage/digest 等自主管线记 'system'，headless session 及旁路进程记 'agent'。
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple, Optional

SCHEMA_VERSION = 1
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# 卡片热列全集（schema.sql cards 表）——行↔dict 转换与 create_card 白名单的共同真源
_CARD_COLUMNS = (
    "id", "status", "prev_status", "tier", "type", "title", "origin_trust",
    "target_repo", "deadline", "created", "updated", "version",
    "merged_into_id", "board_rev", "tombstone", "last_actor_type", "payload",
)
# create_card 允许显式传入的键（migration 需要完整控制出生形态；version/board_rev 由 store 管）
_CREATE_KEYS = frozenset(_CARD_COLUMNS) - {"version", "board_rev", "tombstone", "last_actor_type"}
# agent 出生面再收紧：prev_status 是 restore 的目的地，agent 铸卡带票 = 预埋
# 「trashed→approved」组合旁路弹药；cards_agent_insert_wall trigger 兜底
_AGENT_CREATE_KEYS = _CREATE_KEYS - {"prev_status"}
_CREATE_REQUIRED = ("id", "status", "title")
# update_card_fields 可改热列：title 是 FROZEN 身份锚（§37）不收；
# status/prev_status/merged_into_id 只许走 transition（状态机口径唯一）
_MUTABLE_FIELDS = frozenset({"tier", "type", "deadline", "target_repo", "origin_trust", "payload"})

_ACTOR_TYPES = ("user", "agent", "system")
_DISPATCH_END_STATES = ("completed", "failed", "stopped")

# schema trigger 的 RAISE 码 → 归类（消息即码，见 schema.sql 各 trigger）
_TRANSITION_CODES = ("AGENT_TRANSITION_FORBIDDEN", "AGENT_FIELD_FORBIDDEN",
                     "ILLEGAL_TRANSITION")
_INTEGRITY_CODES = (
    "TOMBSTONE_FROZEN", "USE_TOMBSTONE", "CARD_ID_IMMUTABLE",
    "NOTES_APPEND_ONLY", "NOTES_RECEIPT_SET_ONCE",
    "ACTIVITIES_APPEND_ONLY", "REVISION_MONOTONIC",
)


# --------------------------------------------------------------------------- #
# 错误族 — code 语义对齐 server error envelope（NOT_FOUND/409 分层）
# --------------------------------------------------------------------------- #
class StoreError(Exception):
    """store2 错误基类；code 机器可读，details 供上层组装 envelope。"""

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class NotFound(StoreError):
    """404 语义：卡/行不存在（tombstone 卡的内容已清空，对写路径同样按不存在处理）。"""

    def __init__(self, kind: str, key: Any):
        super().__init__("NOT_FOUND", f"{kind} '{key}' does not exist", {"kind": kind, "key": key})


class VersionConflict(StoreError):
    """409 语义：CAS 预检/写后重查发现版本被别的写者推走（dashi #requireVersion 同型）。"""

    def __init__(self, card_id: str, expected: int, actual: int):
        super().__init__(
            "VERSION_CONFLICT",
            f"card '{card_id}' was changed by another writer",
            {"expected_version": expected, "actual_version": actual},
        )


class TransitionDenied(StoreError):
    """状态机 trigger 拒绝：ILLEGAL_TRANSITION / AGENT_TRANSITION_FORBIDDEN（D3 权限墙）。"""


class IntegrityViolation(StoreError):
    """其余 schema 执法：append-only、tombstone 冻结、去重键、one_active……"""


def _translate_integrity(e: sqlite3.IntegrityError) -> StoreError:
    """trigger RAISE 消息即错误码；UNIQUE 约束按索引名细分（fail-closed 兜底 INTEGRITY_ERROR）。"""
    msg = str(e)
    for code in _TRANSITION_CODES:
        if code in msg:
            return TransitionDenied(code, msg)
    for code in _INTEGRITY_CODES:
        if code in msg:
            return IntegrityViolation(code, msg)
    if "sources_dedup" in msg or "sources.channel" in msg:
        return IntegrityViolation("SOURCE_DUPLICATE", msg)
    if "dispatches_one_active" in msg or "dispatches.card_id" in msg:
        return IntegrityViolation("DISPATCH_ACTIVE", msg)
    return IntegrityViolation("INTEGRITY_ERROR", msg)


# --------------------------------------------------------------------------- #
# verb 表 — 动词只算「去哪 + 带什么」，合法性（老状态×新状态×actor）由 whitelist trigger 执法。
# 用户动词与 live inbox 动作同名（actd.py handlers）；管线动词按 CONTRACT 法条命名。
# --------------------------------------------------------------------------- #
class _Verb(NamedTuple):
    to: Optional[str] = None      # 固定目标状态
    to_prev: bool = False         # 动态目标 = prev_status（restore/unarchive 回程票）
    fallback: str = ""            # 回程票缺失时的兜底（对齐 live registry.restore/unarchive）
    stash_prev: bool = False      # 进站时把当前状态存进 prev_status（trash/archive）
    needs_parent: bool = False    # 必须携带 merged_into_id（merge）


VERBS: dict[str, _Verb] = {
    # —— 用户动词（inbox 动作同名；actor 由调用方传，trigger 按 (old,new,actor) 判） ——
    "approve":         _Verb(to="approved"),                       # §3 批准（user 独占，墙在 DB）
    "reject":          _Verb(to="trashed", stash_prev=True),       # §9 现行 reject = 入回收站
    "raise":           _Verb(to="raising"),                        # §8 研究并提议
    "defer":           _Verb(to="detected"),                       # §10 暂缓存备选
    "accept":          _Verb(to="delivered"),                      # 验收（user 独占，墙在 DB）
    "rework":          _Verb(to="executing"),                      # 打回重做
    "done_external":   _Verb(to="delivered"),                      # §10 系统外完成
    "abort_execution": _Verb(to="card_sent"),                      # §10 停止并退回提案
    "stop_to_review":  _Verb(to="review"),                         # §10 停下来收成果
    "revert_review":   _Verb(to="review"),                         # §10 退回待验收
    "trash":           _Verb(to="trashed", stash_prev=True),       # any → 回收站（契约 header）
    "restore":         _Verb(to_prev=True, fallback="detected"),   # §9 精确复位回程票
    "archive":         _Verb(to="archived", stash_prev=True),      # §10 封存（system=auto-archive）
    "unarchive":       _Verb(to_prev=True, fallback="delivered"),  # §10 解封回 prev_status
    "merge":           _Verb(to="merged", needs_parent=True),      # §21 并入主卡（不可逆）
    # —— 管线动词（system actor） ——
    "dispatch":        _Verb(to="executing"),                      # §4 approved → 起跑
    "reconcile_done":  _Verb(to="review"),                         # 自然完成收割
    "expand_done":     _Verb(to="card_sent"),                      # §8 扩写完成/失败兜底落提案；
                                                                   # user actor = comment 折回重审批（§32.2）
    "promote":         _Verb(to="card_sent"),                      # §10 雷达 act-now 命中提升
    "re_raise":        _Verb(to="card_sent"),                      # §10 已交付线程回锅
    "digest_revert":   _Verb(to="review"),                         # §24 digest 卡刷新拉回待验收
    "session_active":  _Verb(to="executing"),                      # §30 同调翻回运行中
}


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _parse_payload(text: Any) -> dict:
    """payload 读侧容忍：写侧有 json_valid CHECK，但读侧仍不许崩 pass（宪法第 11 条）。"""
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


class Store:
    """每实例一个 DB 文件；连接按线程隔离（sqlite3 连接不跨线程共享）。

    ``now_fn`` 为测试注入缝（repo 测试风格：注入 seam，不 monkeypatch 时间）。
    """

    def __init__(self, db_path, now_fn=_iso_now):
        self._db_path = str(db_path)
        self._now = now_fn
        self._local = threading.local()
        self._ensure_schema()

    # ----------------------------------------------------------------- #
    # 连接与事务
    # ----------------------------------------------------------------- #
    def _ensure_schema(self) -> None:
        conn = self._conn()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            # schema.sql 全程 IF NOT EXISTS / OR IGNORE，幂等；末尾把 user_version 钉到 1
            conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        elif version != SCHEMA_VERSION:
            # 未来版本的库 fail-closed：绝不带着不认识的 schema 盲写
            raise StoreError(
                "SCHEMA_VERSION_MISMATCH",
                f"db user_version={version}, store2 supports {SCHEMA_VERSION}",
                {"db_version": version, "supported": SCHEMA_VERSION},
            )

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # isolation_level=None = 手动事务（BEGIN IMMEDIATE 由 _write 显式发）
            conn = sqlite3.connect(self._db_path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")     # per-connection！（schema.md 约定）
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        """关闭当前线程的连接（其余线程的连接由各自线程 close 或随进程回收）。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @contextmanager
    def _write(self):
        """写事务 helper：BEGIN IMMEDIATE 抢写锁；IntegrityError 翻译成类型化错误后回滚。"""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except sqlite3.IntegrityError as e:
            conn.execute("ROLLBACK")
            raise _translate_integrity(e) from e
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    @staticmethod
    def _bump_revision(conn: sqlite3.Connection) -> int:
        """全局游标 +1 并返回新值（monotonic trigger 保证只进不退）。"""
        conn.execute("UPDATE board_revision SET value = value + 1 WHERE id = 1")
        return conn.execute("SELECT value FROM board_revision WHERE id = 1").fetchone()[0]

    def _append_activity(self, conn, card_id, actor_type, actor_id, changes) -> None:
        """审计行（dashi #recordTaskActivity 同型）：changes 为空 = no-op，不落行。"""
        if not changes:
            return
        conn.execute(
            "INSERT INTO activities (card_id, actor_type, actor_id, changes, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (card_id, actor_type, actor_id, _dump_json(changes), self._now()),
        )

    @staticmethod
    def _require_actor(actor_type: str) -> None:
        if actor_type not in _ACTOR_TYPES:
            raise StoreError("INVALID_FIELD", f"unknown actor_type '{actor_type}'",
                             {"actor_type": actor_type})

    # ----------------------------------------------------------------- #
    # 读
    # ----------------------------------------------------------------- #
    @staticmethod
    def _row_to_card(row: sqlite3.Row) -> dict:
        card = {k: row[k] for k in _CARD_COLUMNS}
        card["payload"] = _parse_payload(card["payload"])
        return card

    def _get_row(self, conn, card_id: str) -> Optional[sqlite3.Row]:
        return conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()

    def get_card(self, card_id: str) -> dict:
        """按 id 取卡（含 tombstone 骨架行——调用方看 tombstone 字段自行分流）。"""
        row = self._get_row(self._conn(), card_id)
        if row is None:
            raise NotFound("card", card_id)
        return self._row_to_card(row)

    def list_cards(self, status: Optional[str] = None, include_tombstones: bool = False) -> list[dict]:
        sql = "SELECT * FROM cards"
        where, args = [], []
        if not include_tombstones:
            where.append("tombstone = 0")
        if status is not None:
            where.append("status = ?")
            args.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id"
        return [self._row_to_card(r) for r in self._conn().execute(sql, args)]

    def current_revision(self) -> int:
        return self._conn().execute("SELECT value FROM board_revision WHERE id = 1").fetchone()[0]

    def changes_since(self, cursor: int) -> dict:
        """增量读：board_rev > cursor 的全部卡（含 tombstone 行 → 客户端学到删除）。

        返回 ``{"revision": 当前游标, "cards": [...]}``；客户端下次带 revision 回来。
        cursor=0 = 全量首拉。行按 (board_rev, id) 稳定排序。
        """
        conn = self._conn()
        rev = conn.execute("SELECT value FROM board_revision WHERE id = 1").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM cards WHERE board_rev > ? ORDER BY board_rev, id", (int(cursor),)
        ).fetchall()
        return {"revision": rev, "cards": [self._row_to_card(r) for r in rows]}

    # ----------------------------------------------------------------- #
    # 写 — 卡片
    # ----------------------------------------------------------------- #
    def create_card(self, card: dict, actor_type: str = "system",
                    actor_id: Optional[str] = None) -> dict:
        """铸卡（migration 的整库 INSERT 也走这里）。

        出生状态不设限（schema.md：合法出生点很多，出生资格是应用层判断），
        但 agent 铸批准后各态的卡、或带 prev_status 回程票（restore 组合旁路的
        弹药）在此拒收——cards_agent_insert_wall trigger 兜底同一道墙的 SQL 面。
        未知键 fail-closed 拒收（对齐 server 的 zero-tolerance 纪律）。
        """
        self._require_actor(actor_type)
        allowed = _AGENT_CREATE_KEYS if actor_type == "agent" else _CREATE_KEYS
        unknown = set(card) - allowed
        if unknown:
            raise StoreError("UNKNOWN_FIELD", f"unknown card fields: {sorted(unknown)}",
                             {"fields": sorted(unknown)})
        missing = [k for k in _CREATE_REQUIRED if not card.get(k)]
        if missing:
            raise StoreError("INVALID_FIELD", f"missing required fields: {missing}",
                             {"fields": missing})
        now = self._now()
        payload = card.get("payload") or {}
        if not isinstance(payload, dict):
            raise StoreError("INVALID_FIELD", "payload must be a dict", {"field": "payload"})
        with self._write() as conn:
            rev = self._bump_revision(conn)
            conn.execute(
                "INSERT INTO cards (id, status, prev_status, tier, type, title,"
                " origin_trust, target_repo, deadline, created, updated, version,"
                " merged_into_id, board_rev, tombstone, last_actor_type, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 0, ?, ?)",
                (
                    card["id"], card["status"], card.get("prev_status"),
                    card.get("tier", "T1"), card.get("type", ""), card["title"],
                    # origin_trust 缺省 external = fail-closed（出身不明一律走审批）
                    card.get("origin_trust", "external"),
                    card.get("target_repo"), card.get("deadline"),
                    card.get("created") or now, card.get("updated") or now,
                    card.get("merged_into_id"), rev, actor_type, _dump_json(payload),
                ),
            )
            self._append_activity(conn, card["id"], actor_type, actor_id or "create",
                                  [{"field": "status", "before": None, "after": card["status"]}])
            row = self._get_row(conn, card["id"])
        return self._row_to_card(row)

    def _cas_precheck(self, conn, card_id: str, expected_version: Optional[int]) -> sqlite3.Row:
        """CAS 件一：预检。缺卡/tombstone → 404；版本不符 → 409（带 expected/actual）。

        expected_version=None = 免预检（单写者管线路径），后续 UPDATE 用行内版本，
        在 BEGIN IMMEDIATE 写锁下等价于「以刚读到的为准」。
        """
        row = self._get_row(conn, card_id)
        if row is None or row["tombstone"]:
            raise NotFound("card", card_id)
        if expected_version is not None and row["version"] != expected_version:
            raise VersionConflict(card_id, expected_version, row["version"])
        return row

    def _cas_update(self, conn, card_id: str, used_version: int, sql: str, args: tuple) -> None:
        """CAS 件二/三：WHERE id AND version；changes!=1 → 重查分 404/409
        （dashi #throwMissingOrConflict 逐式移植）。"""
        cur = conn.execute(sql, args)
        if cur.rowcount != 1:
            row = self._get_row(conn, card_id)
            if row is None:
                raise NotFound("card", card_id)
            raise VersionConflict(card_id, used_version, row["version"])

    def transition(self, card_id: str, verb: str, actor_type: str,
                   expected_version: Optional[int], *,
                   merged_into_id: Optional[str] = None,
                   actor_id: Optional[str] = None) -> dict:
        """类型化状态转移。verb → 目标状态 + 随行字段；(old,new,actor) 合法性由
        transition_whitelist trigger 执法 —— store 层不复刻法条，fail-closed 在 DB。

        同状态且无字段变化 = no-op：不 bump、不写 activity、原样返回
        （幂等重放无害，例如对已在 review 的卡再点 stop_to_review）。
        """
        self._require_actor(actor_type)
        spec = VERBS.get(verb)
        if spec is None:
            raise StoreError("UNKNOWN_VERB", f"unknown transition verb '{verb}'", {"verb": verb})
        if spec.needs_parent and not merged_into_id:
            raise StoreError("INVALID_FIELD", f"verb '{verb}' requires merged_into_id",
                             {"verb": verb})
        with self._write() as conn:
            row = self._cas_precheck(conn, card_id, expected_version)
            old_status, old_prev = row["status"], row["prev_status"]

            # —— 算目标状态与随行字段 ——
            if spec.to_prev:
                new_status = old_prev or spec.fallback
                # 回程票用掉即清空（对齐 live registry.restore/unarchive）；
                # 唯 restore 回 archived 时必须补一张（schema CHECK 要求封存卡带票）。
                # TODO(contract): trashed→archived 复位后的 prev_status 语义未入宪，
                # 此处取 unarchive 兜底值 'delivered'（最保守：解封后落已验收）。
                new_prev = "delivered" if new_status == "archived" else None
            else:
                new_status = spec.to
                new_prev = old_status if (spec.stash_prev and old_status != new_status) else old_prev
            new_minto = merged_into_id if spec.needs_parent else row["merged_into_id"]

            # —— no-op 过滤 ——
            if (new_status, new_prev, new_minto) == (old_status, old_prev, row["merged_into_id"]):
                return self._row_to_card(row)

            used_version = row["version"]
            rev = self._bump_revision(conn)
            # status UPDATE 必须同语句 SET last_actor_type（trigger 读 NEW.last_actor_type 执法）
            self._cas_update(
                conn, card_id, used_version,
                "UPDATE cards SET status = ?, prev_status = ?, merged_into_id = ?,"
                " last_actor_type = ?, updated = ?, version = version + 1, board_rev = ?"
                " WHERE id = ? AND version = ?",
                (new_status, new_prev, new_minto, actor_type, self._now(), rev,
                 card_id, used_version),
            )
            changes = [{"field": "status", "before": old_status, "after": new_status}]
            if new_prev != old_prev:
                changes.append({"field": "prev_status", "before": old_prev, "after": new_prev})
            if new_minto != row["merged_into_id"]:
                changes.append({"field": "merged_into_id",
                                "before": row["merged_into_id"], "after": new_minto})
            self._append_activity(conn, card_id, actor_type, actor_id or verb, changes)
            fresh = self._get_row(conn, card_id)
        return self._row_to_card(fresh)

    def update_card_fields(self, card_id: str, expected_version: Optional[int],
                           fields: dict, actor_type: str,
                           actor_id: Optional[str] = None) -> dict:
        """非状态热列 + payload 的 CAS 更新。status 族只许走 transition；title 是
        FROZEN 身份锚（§37）不收。payload 传入即整体替换（真源在调用方内存）。"""
        self._require_actor(actor_type)
        bad = set(fields) - _MUTABLE_FIELDS
        if bad:
            raise StoreError("UNKNOWN_FIELD",
                             f"fields not updatable here: {sorted(bad)}", {"fields": sorted(bad)})
        with self._write() as conn:
            row = self._cas_precheck(conn, card_id, expected_version)
            changes, assigns, args = [], [], []
            for key in sorted(fields):
                value = fields[key]
                if key == "payload":
                    if not isinstance(value, dict):
                        raise StoreError("INVALID_FIELD", "payload must be a dict",
                                         {"field": "payload"})
                    old_payload = _parse_payload(row["payload"])
                    if value == old_payload:
                        continue
                    # payload 变更按顶层键 diff 进审计（值可能很大，诚实优先不截断）
                    for k in sorted(set(old_payload) | set(value)):
                        if old_payload.get(k) != value.get(k):
                            changes.append({"field": f"payload.{k}",
                                            "before": old_payload.get(k),
                                            "after": value.get(k)})
                    assigns.append("payload = ?")
                    args.append(_dump_json(value))
                else:
                    if value == row[key]:
                        continue
                    changes.append({"field": key, "before": row[key], "after": value})
                    assigns.append(f"{key} = ?")
                    args.append(value)
            if not assigns:                       # no-op：不 bump、不写 activity
                return self._row_to_card(row)
            used_version = row["version"]
            rev = self._bump_revision(conn)
            assigns += ["last_actor_type = ?", "updated = ?",
                        "version = version + 1", "board_rev = ?"]
            args += [actor_type, self._now(), rev, card_id, used_version]
            self._cas_update(conn, card_id, used_version,
                             f"UPDATE cards SET {', '.join(assigns)} WHERE id = ? AND version = ?",
                             tuple(args))
            self._append_activity(conn, card_id, actor_type, actor_id or "update", changes)
            fresh = self._get_row(conn, card_id)
        return self._row_to_card(fresh)

    def purge_trashed(self, card_id: str, actor_id: Optional[str] = None) -> dict:
        """§9 回收站保留期硬删的 store2 形态：tombstone=1 + payload='{}' + bump board_rev
        —— 删除因此进 revision 流，增量客户端能学到。已 tombstone = 幂等 no-op。
        只有 trashed 卡可 purge（schema CHECK backstop；archived NEVER purge，§10）。
        ``permanent`` 钉住判断是 retention pass 的业务，不在 store 层。"""
        with self._write() as conn:
            row = self._get_row(conn, card_id)
            if row is None:
                raise NotFound("card", card_id)
            if row["tombstone"]:
                return self._row_to_card(row)
            rev = self._bump_revision(conn)
            # 不走 CAS：purge 是 retention 的单方面动作，语义上无并发编辑者
            conn.execute(
                "UPDATE cards SET tombstone = 1, payload = '{}', last_actor_type = 'system',"
                " updated = ?, board_rev = ? WHERE id = ?",
                (self._now(), rev, card_id),
            )
            self._append_activity(conn, card_id, "system", actor_id or "purge",
                                  [{"field": "tombstone", "before": 0, "after": 1}])
            fresh = self._get_row(conn, card_id)
        return self._row_to_card(fresh)

    # ----------------------------------------------------------------- #
    # 写 — 子表（每笔都 bump 所属卡的 board_rev + updated；不动 version——
    # CAS 只护卡片行本身的编辑，子表追加不该把并发编辑者顶成 409）
    # ----------------------------------------------------------------- #
    def _touch_card(self, conn, card_id: str, rev: int) -> None:
        """子表变更盖章所属卡：board_rev 推新让增量同步看见（tombstone 卡会被
        trigger 拦下 TOMBSTONE_FROZEN，调用方已预检）。"""
        conn.execute("UPDATE cards SET board_rev = ?, updated = ? WHERE id = ?",
                     (rev, self._now(), card_id))

    def _require_live_card(self, conn, card_id: str) -> sqlite3.Row:
        row = self._get_row(conn, card_id)
        if row is None or row["tombstone"]:
            raise NotFound("card", card_id)
        return row

    def add_note(self, card_id: str, kind: str, body: str, actor_type: str,
                 actor_id: Optional[str] = None) -> int:
        """追加 comment/steer/fold 回执行（append-only，trigger 执法）。返回 note id。"""
        self._require_actor(actor_type)
        now = self._now()
        with self._write() as conn:
            self._require_live_card(conn, card_id)
            rev = self._bump_revision(conn)
            cur = conn.execute(
                "INSERT INTO notes (card_id, kind, body, actor_type, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (card_id, kind, body, actor_type, now),
            )
            note_id = cur.lastrowid
            self._touch_card(conn, card_id, rev)
            self._append_activity(conn, card_id, actor_type, actor_id or f"note:{kind}",
                                  [{"field": "notes", "before": None,
                                    "after": {"id": note_id, "kind": kind}}])
        return note_id

    def _mark_note(self, note_id: int, column: str, actor_id: Optional[str]) -> None:
        """回执列 set-once（trigger backstop）；已设同值 = 幂等 no-op 不 bump。"""
        with self._write() as conn:
            row = conn.execute("SELECT card_id, delivered_at, acked_at FROM notes WHERE id = ?",
                               (note_id,)).fetchone()
            if row is None:
                raise NotFound("note", note_id)
            if row[column] is not None:
                return
            rev = self._bump_revision(conn)
            now = self._now()
            conn.execute(f"UPDATE notes SET {column} = ? WHERE id = ?", (now, note_id))
            self._touch_card(conn, row["card_id"], rev)
            self._append_activity(conn, row["card_id"], "system",
                                  actor_id or f"note.{column}",
                                  [{"field": f"note.{column}", "before": None,
                                    "after": {"id": note_id, "at": now}}])

    def mark_note_delivered(self, note_id: int, actor_id: Optional[str] = None) -> None:
        """comment 注入运行中 session 的时刻（§32.2 真实回执，set-once）。"""
        self._mark_note(note_id, "delivered_at", actor_id)

    def mark_note_acked(self, note_id: int, actor_id: Optional[str] = None) -> None:
        """session/流程确认消费的时刻（set-once）。"""
        self._mark_note(note_id, "acked_at", actor_id)

    def get_notes(self, card_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM notes WHERE card_id = ? ORDER BY created_at, id", (card_id,))
        return [dict(r) for r in rows]

    def add_source(self, card_id: str, channel: str, who: Optional[str] = None,
                   date: Optional[str] = None, ref: Optional[str] = None,
                   quote: Optional[str] = None, origin_key: Optional[str] = None,
                   actor_type: str = "system", actor_id: Optional[str] = None) -> int:
        """追加来源引文。origin_key 只收外部强信号（slack:<ts>/gmail:<message_id>），
        无强信号传 None——绝不 fuzzy（§10 thread_key 纪律）。撞 (channel,origin_key)
        全局去重键 → IntegrityViolation('SOURCE_DUPLICATE')：同一条外部消息永远只属一张卡，
        调用方该走 fold/并入而不是重复插引文。"""
        self._require_actor(actor_type)
        with self._write() as conn:
            self._require_live_card(conn, card_id)
            rev = self._bump_revision(conn)
            cur = conn.execute(
                "INSERT INTO sources (card_id, channel, who, date, ref, quote, origin_key,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (card_id, channel, who, date, ref, quote, origin_key, self._now()),
            )
            source_id = cur.lastrowid
            self._touch_card(conn, card_id, rev)
            self._append_activity(conn, card_id, actor_type, actor_id or "source",
                                  [{"field": "sources", "before": None,
                                    "after": {"id": source_id, "channel": channel}}])
        return source_id

    def get_sources(self, card_id: str) -> list[dict]:
        # id 序 = 插入序 = 到达序（sources[0] 驱动 thread_key 推导，保序是硬要求）
        rows = self._conn().execute(
            "SELECT * FROM sources WHERE card_id = ? ORDER BY id", (card_id,))
        return [dict(r) for r in rows]

    def open_dispatch(self, card_id: str, runtime: str = "claude",
                      session_id: Optional[str] = None,
                      worktree_path: Optional[str] = None,
                      branch: Optional[str] = None,
                      actor_id: Optional[str] = None) -> int:
        """起一轮派发（status='running'）。一卡至多一个活 session：撞 one_active
        partial unique → IntegrityViolation('DISPATCH_ACTIVE')（§46 语义的数据库层）。"""
        with self._write() as conn:
            self._require_live_card(conn, card_id)
            rev = self._bump_revision(conn)
            cur = conn.execute(
                "INSERT INTO dispatches (card_id, runtime, session_id, worktree_path,"
                " branch, status, started_at) VALUES (?, ?, ?, ?, ?, 'running', ?)",
                (card_id, runtime, session_id, worktree_path, branch, self._now()),
            )
            dispatch_id = cur.lastrowid
            self._touch_card(conn, card_id, rev)
            self._append_activity(conn, card_id, "system", actor_id or "dispatch",
                                  [{"field": "dispatches", "before": None,
                                    "after": {"id": dispatch_id, "status": "running"}}])
        return dispatch_id

    def close_dispatch(self, dispatch_id: int, status: str,
                       exit_code: Optional[int] = None,
                       session_id: Optional[str] = None,
                       actor_id: Optional[str] = None) -> None:
        """收尾一轮派发：running → completed/failed/stopped + finished_at
        （CHECK 钉死 running↔未收尾 的耦合，绝不留悬挂账——§21 纪律）。
        session_id 派发后才拿到时在收尾一并回填。非 running 行重复收尾 → DISPATCH_NOT_RUNNING。"""
        if status not in _DISPATCH_END_STATES:
            raise StoreError("INVALID_FIELD",
                             f"dispatch end status must be one of {_DISPATCH_END_STATES}",
                             {"status": status})
        with self._write() as conn:
            row = conn.execute("SELECT card_id, status FROM dispatches WHERE id = ?",
                               (dispatch_id,)).fetchone()
            if row is None:
                raise NotFound("dispatch", dispatch_id)
            if row["status"] != "running":
                raise StoreError("DISPATCH_NOT_RUNNING",
                                 f"dispatch {dispatch_id} already ended as '{row['status']}'",
                                 {"status": row["status"]})
            rev = self._bump_revision(conn)
            conn.execute(
                "UPDATE dispatches SET status = ?, exit_code = ?,"
                " session_id = COALESCE(?, session_id), finished_at = ? WHERE id = ?",
                (status, exit_code, session_id, self._now(), dispatch_id),
            )
            self._touch_card(conn, row["card_id"], rev)
            self._append_activity(conn, row["card_id"], "system", actor_id or "dispatch",
                                  [{"field": "dispatches", "before": "running",
                                    "after": {"id": dispatch_id, "status": status,
                                              "exit_code": exit_code}}])

    def get_dispatches(self, card_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM dispatches WHERE card_id = ? ORDER BY started_at, id", (card_id,))
        return [dict(r) for r in rows]

    def get_activities(self, card_id: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM activities WHERE card_id = ? ORDER BY created_at, id", (card_id,))
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["changes"] = json.loads(d["changes"])
            except (TypeError, ValueError):    # 读侧不崩（写侧有 json_valid CHECK）
                d["changes"] = []
            out.append(d)
        return out
