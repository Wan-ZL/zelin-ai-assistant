"""重点人物账本（people ledger）的纯逻辑层 — CONTRACT §17（issue #23 追记）。

每个配置的人一本滚动账（``state/people_ledger/people/<slug>.json``）：
「我答应对方的」（owner_owes）与「对方答应我的」（person_owes），每条带来源
引文；新提及并入既有账本（按归一化文本去重），后续笔记显示已完成 → 标 done。
渲染稿 ``<落点>/people_ledger/<slug>.md`` 每次更新重写，落点沿用 §17 v0.14
的守卫（``execution.default_target_repo`` 显式配置才写工作台，否则
``state/``）。

这里没有 LLM 调用、没有通知、没有文件扫描——那些都在入口 ``act/people_ledger.py``；
本模块只管人物解析与关键词护栏、提及检测、账本 JSON 的读写与合并、markdown
渲染、游标。判例：tests/test_people_ledger_guard.py（护栏 / 配置）、
tests/test_people_ledger_merge.py（合并 / 渲染 / 落点 / 游标）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from act.lib import config, failures

# §17 关键词护栏（2026-07-08 风暴的教训）：示例占位、退化 token、停用词一律不扫。
PLACEHOLDER_PEOPLE = frozenset({"your.manager", "your-manager", "your manager"})
STOPWORDS = frozenset({"your", "the", "my", "our", "you", "and", "for", "manager",
                       "boss", "team", "lead"})
MIN_TOKEN_ASCII = 3        # 派生 token 短于此（ASCII）= 退化，弃用
MIN_TOKEN_OTHER = 2        # 非 ASCII（中文名等）允许两字

DIRECTIONS = ("owner_owes", "person_owes")
TEXT_MAX = 300
QUOTE_MAX = 500
DONE_RETENTION = 100       # 每人保留的已完成条目上限（最旧的先掉）——「日志必有帽」

_ITEM_ID_RE = re.compile(r"^L-\d+$")


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def ledger_dir() -> Path:
    return config.STATE_DIR / "people_ledger"


def people_dir() -> Path:
    return ledger_dir() / "people"


def cursor_path() -> Path:
    return ledger_dir() / "cursor.json"


def lock_path() -> Path:
    return ledger_dir() / "lock"


def log_path() -> Path:
    return ledger_dir() / "ledger.log"


def ensure_dirs() -> None:
    people_dir().mkdir(parents=True, exist_ok=True)


def output_root(cfg: config.Config) -> Path:
    """渲染稿落点：显式配置的工作台，否则 state/（§17 v0.14 落点守卫——绝不
    创建示例占位路径）。"""
    if cfg.default_target_repo_configured:
        return cfg.target_repo_path
    return config.STATE_DIR


def rendered_path(cfg: config.Config, slug: str) -> Path:
    return output_root(cfg) / "people_ledger" / f"{slug}.md"


# --------------------------------------------------------------------------- #
# json IO (atomic write; unreadable = default)
# --------------------------------------------------------------------------- #
def _read_json(path: Path, default):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return doc if isinstance(doc, dict) else default


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# people: config → guarded Person list
# --------------------------------------------------------------------------- #
class Person:
    """一个受关注的人：配置原文 ``handle``、文件名 ``slug``、显示名与匹配 token。"""

    def __init__(self, handle: str, tokens: list) -> None:
        self.handle = handle
        self.slug = slugify(handle)
        self.display = display_name(handle)
        self.tokens = tokens
        self._re = re.compile(
            "|".join(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(t) for t in tokens),
            re.IGNORECASE)

    def mentioned_in(self, text: str) -> bool:
        return bool(self._re.search(text or ""))


def slugify(handle: str) -> str:
    """文件名用 slug：ASCII 小写 + 连字符；全非 ASCII（中文名）→ ``p-<sha1[:8]>``。"""
    ascii_part = re.sub(r"[^a-z0-9]+", "-", str(handle).lower()).strip("-")
    if ascii_part:
        return ascii_part
    return "p-" + hashlib.sha1(str(handle).encode("utf-8")).hexdigest()[:8]


def display_name(handle: str) -> str:
    """``arash.khoshbakht`` → ``Arash``；非 ASCII 原样。"""
    head = str(handle).strip().split(".")[0].split("@")[-1]
    return head.title() if head.isascii() else head


def _token_ok(tok: str) -> bool:
    if not tok or tok.lower() in STOPWORDS:
        return False
    return len(tok) >= (MIN_TOKEN_ASCII if tok.isascii() else MIN_TOKEN_OTHER)


def tokens_for(handle: str) -> list:
    """一个 handle 派生的匹配 token（去重、经护栏）：整串 + 按 ``.``/``_``/``-``/
    空格拆出的每段。占位 ``your.manager`` 派生的 ``your`` 会命中几乎每篇英文
    笔记——护栏在这里把它砍成空表（→ 该人物停用）。"""
    raw = str(handle or "").strip().lstrip("@")
    if not raw or raw.lower() in PLACEHOLDER_PEOPLE:
        return []
    return _unique_ok_tokens([raw] + re.split(r"[._\-\s]+", raw))


def _unique_ok_tokens(parts: list) -> list:
    out: list = []
    seen: set = set()
    for p in parts:
        if _token_ok(p) and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def _dedupe_handles(handles: list) -> list:
    seen: set = set()
    out: list = []
    for h in handles:
        key = str(h or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(str(h).strip())
    return out


def resolve_people(cfg: config.Config) -> "tuple[list, list]":
    """``(people, dropped)``：``people_ledger.people`` 非空用它，否则回落
    ``sources.watch_people``；每个人经护栏，被砍光 token 的进 ``dropped``
    （入口打一行日志，绝不用退化关键词扫描）。"""
    handles = _dedupe_handles(list(cfg.people_ledger_people or []) or list(cfg.watch_people or []))
    return _classify(handles)


def _classify(handles: list) -> "tuple[list, list]":
    people: list = []
    dropped: list = []
    for h in handles:
        toks = tokens_for(h)
        if toks:
            people.append(Person(h, toks))
        else:
            dropped.append(h)
    return people, dropped


# --------------------------------------------------------------------------- #
# ledger documents
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_ledger(person: Person) -> dict:
    return {"person": person.handle, "display": person.display, "slug": person.slug,
            "next_id": 1, "items": [], "updated_at": None}


def ledger_path(slug: str) -> Path:
    return people_dir() / f"{slug}.json"


def load_ledger(person: Person) -> dict:
    doc = _read_json(ledger_path(person.slug), None) or empty_ledger(person)
    doc.setdefault("items", [])
    doc.setdefault("next_id", 1)
    doc["display"] = person.display
    return doc


def save_ledger(doc: dict) -> None:
    doc["updated_at"] = now_iso()
    _write_json(ledger_path(doc["slug"]), doc)


def open_items(doc: dict) -> list:
    return [it for it in doc.get("items", []) if it.get("status") == "open"]


def normalize_text(text: str) -> str:
    """去重键：NFKC、小写、去标点、折叠空白。"""
    s = unicodedata.normalize("NFKC", str(text or "")).lower()
    s = re.sub(r"[^\w\s]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- #
# LLM 输出消毒（宪法第 11 条：类型逐字段，坏形状丢条不崩 pass）
# --------------------------------------------------------------------------- #
def clean_item(raw) -> Optional[dict]:
    """一条模型输出 → ``{direction, text, quote}`` 或 None（丢弃）。
    speaker 为 assistant / system 的条目一律丢——账本记的是人对人的承诺，
    屏幕上 AI 对话里的「我会去做」不是任何人的承诺（§45 回声环同源）。"""
    if not isinstance(raw, dict) or _blocked_speaker(raw):
        return None
    direction, text, quote = _item_fields(raw)
    if direction not in DIRECTIONS or not text:
        return None
    return {"direction": direction, "text": text[:TEXT_MAX], "quote": quote[:QUOTE_MAX]}


def _blocked_speaker(raw: dict) -> bool:
    return str(raw.get("speaker") or "").strip().lower() in ("assistant", "system")


def _item_fields(raw: dict) -> "tuple[str, str, str]":
    direction = str(raw.get("direction") or "").strip().lower()
    text = " ".join(str(raw.get("text") or "").split())
    quote = " ".join(str(raw.get("quote") or "").split())
    return direction, text, quote


def clean_done_ids(raw) -> list:
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if isinstance(x, (str, int)) and _ITEM_ID_RE.match(str(x).strip())]


# --------------------------------------------------------------------------- #
# merge: new items dedupe against open ones; done ids close open ones
# --------------------------------------------------------------------------- #
def _is_duplicate(item: dict, opened: list) -> bool:
    key_t, key_q = normalize_text(item["text"]), normalize_text(item["quote"])
    for o in opened:
        if o.get("direction") != item["direction"]:
            continue
        if normalize_text(o.get("text")) == key_t:
            return True
        if key_q and normalize_text(o.get("quote")) == key_q:
            return True
    return False


def _add_item(doc: dict, item: dict, note_name: str, date: str) -> None:
    item_id = "L-%d" % int(doc.get("next_id", 1))
    doc["next_id"] = int(doc.get("next_id", 1)) + 1
    doc["items"].append({"id": item_id, "direction": item["direction"], "text": item["text"],
                         "quote": item["quote"], "note": note_name, "date": date,
                         "status": "open", "done_at": None, "done_note": None})


def _close_items(doc: dict, done_ids: list, note_name: str, date: str) -> int:
    closed = 0
    wanted = set(done_ids)
    for it in doc.get("items", []):
        if it.get("id") in wanted and it.get("status") == "open":
            it.update({"status": "done", "done_at": date, "done_note": note_name})
            closed += 1
    return closed


def _id_num(item: dict) -> int:
    """``L-12`` → 12；手改坏掉的 id 当 0（排序用，绝不因一条坏 id 崩整轮）。"""
    try:
        return int(str(item.get("id", ""))[2:])
    except ValueError:
        return 0


def _trim_done(doc: dict) -> None:
    """已完成条目只留最近 DONE_RETENTION 条（按完成时间 + 编号排序，旧的掉）。"""
    done = [it for it in doc["items"] if it.get("status") == "done"]
    if len(done) <= DONE_RETENTION:
        return
    done.sort(key=lambda it: (str(it.get("done_at") or ""), _id_num(it)))
    drop = {it["id"] for it in done[:len(done) - DONE_RETENTION]}
    doc["items"] = [it for it in doc["items"] if it.get("id") not in drop]


def merge(doc: dict, new_items, done_ids, note_name: str, date: str) -> "tuple[int, int]":
    """并入一篇笔记的抽取结果；返回 ``(added, closed)``。同批内重复也去重
    （逐条并入后再比对下一条）。``new`` / ``done`` 不是 list（模型给了 dict /
    数字 / 字串）= 空表——宪法第 11 条，坏形状丢，不崩 pass。"""
    added = 0
    for raw in (new_items if isinstance(new_items, list) else []):
        item = clean_item(raw)
        if item is None or _is_duplicate(item, open_items(doc)):
            continue
        _add_item(doc, item, note_name, date)
        added += 1
    closed = _close_items(doc, clean_done_ids(done_ids), note_name, date)
    _trim_done(doc)
    return added, closed


# --------------------------------------------------------------------------- #
# render (markdown, UI language)
# --------------------------------------------------------------------------- #
def _fmt_open(it: dict) -> str:
    quote = f" 「{it['quote']}」" if it.get("quote") else ""
    return f"- [ ] {it['id']} · {it['text']}（{it.get('date') or '?'} · {it.get('note') or '?'}）{quote}"


def _fmt_done(it: dict) -> str:
    return (f"- [x] {it['id']} · {it['text']}"
            f"（{failures.pick('完成于', 'done')} {it.get('done_at') or '?'} · {it.get('done_note') or '?'}）")


def _section(header: str, lines: list) -> list:
    return [header] + (lines or [failures.pick("- （无）", "- (none)")]) + [""]


def _open_lines(items: list, direction: str) -> list:
    return [_fmt_open(it) for it in open_items({"items": items}) if it.get("direction") == direction]


def _done_lines(items: list, last: int = 20) -> list:
    return [_fmt_done(it) for it in items if it.get("status") == "done"][-last:]


def _head_lines(doc: dict, who: str) -> list:
    updated = doc.get("updated_at") or "?"
    return [failures.pick(f"# 重点人物账本 · {who}", f"# People ledger · {who}"),
            failures.pick(f"更新于 {updated}", f"Updated {updated}"), ""]


def render(doc: dict, owner: str) -> str:
    who = doc.get("display") or doc.get("person") or "?"
    items = doc.get("items", [])
    out = _head_lines(doc, who)
    out += _section(failures.pick(f"## {owner} 答应 {who} 的", f"## {owner} owes {who}"),
                    _open_lines(items, "owner_owes"))
    out += _section(failures.pick(f"## {who} 答应 {owner} 的", f"## {who} owes {owner}"),
                    _open_lines(items, "person_owes"))
    out += _section(failures.pick("## 已完成（最近 20 条）", "## Done (last 20)"), _done_lines(items))
    return "\n".join(out)


def write_rendered(cfg: config.Config, doc: dict) -> Path:
    path = rendered_path(cfg, doc["slug"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(render(doc, cfg.owner_name or "Owner"), encoding="utf-8")
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- #
# cursor (水位：首跑 = now，不回填)
# --------------------------------------------------------------------------- #
def load_cursor() -> Optional[dict]:
    doc = _read_json(cursor_path(), None)
    if doc is None or not isinstance(doc.get("marker"), (int, float)):
        return None
    return doc


def save_cursor(marker: float, first_run_at: Optional[str] = None) -> None:
    prev = load_cursor() or {}
    _write_json(cursor_path(), {"marker": float(marker),
                                "first_run_at": first_run_at or prev.get("first_run_at") or now_iso(),
                                "updated_at": now_iso()})
