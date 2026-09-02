"""store2 export — 全库 -> YAML 快照目录（git-diff 友好；BUILD-CONTRACT §3）。

用法（repo 根目录运行）::

    python3 -m act.lib.store2.export_yaml --db state/store2.db --out /tmp/snapshot [--prune]

设计要点：
- **payload 即真源**：migrate_yaml 把每张卡的 canonical ``to_dict()`` 全文 JSON 化
  存进 ``cards.payload``（热列只是查询投影，见 migrate_yaml 模块注释）。因此导出
  = ``json.loads(payload)``（Python dict 保插入序）→ 按 registry 的 dump 参数落
  YAML —— key 顺序、省略语义与 live registry.save() 逐字节一致，git diff 干净。
- 目录布局镜像 registry：``<out>/<id>.yaml``；archived 卡落 ``<out>/archive/``。
  文件名由 DB 侧 id 推导且过白名单正则——绝不让 id 里的路径片段逃出 out 目录
  （对齐 server/files.py 的路径纪律）。
- tombstone 卡（payload 已清空）跳过并计数——快照只含仍有内容的卡。
- 幂等：内容未变不重写（保 mtime 稳定）；``--prune`` 才删除 DB 里已不存在的
  旧快照文件（默认不删，保守）。

本模块同时是 card 序列化形状的**单一落点**（migrate_yaml 从这里 import）：
字段词表直接 ``from ..registry import CORE_ORDER, OPTIONAL_ORDER``——真源只有
一份，live 加字段时迁移/导出自动跟上（v0.48 前这里手抄了整套词表，因为当时的
worktree 基线 registry 缺 §37/§38/§44 字段；merge 后理由失效，已去重）。
``FIELD_DEFAULTS`` 与 from_dict/to_dict 语义仍是逐字复刻（dataclass 默认值不
是可 import 的表），由 ``tests/test_store2_field_parity.py`` 逐字段钉住。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import yaml

from ..registry import CORE_ORDER, OPTIONAL_ORDER

# --------------------------------------------------------------------------- #
# card shape — 词表 import 自 live act/lib/registry.py（单一真源）：
# CORE_ORDER 永远序列化（哪怕 null），顺序即 YAML 顺序；OPTIONAL_ORDER 的值
# in (None, "", [], False) 时整键跳过（0 == False 也被跳过——silent_merge_count: 0
# 不落盘，registry 明示这是有意的）。下面的默认值/归一语义仍是复刻，加字段时
# 必须同步补 FIELD_DEFAULTS——parity test 会红。
# --------------------------------------------------------------------------- #

# dataclass 默认值（sources 的 list 工厂在 normalize_card 里现做，防共享引用）
FIELD_DEFAULTS = {
    "id": "", "title": "", "type": "", "tier": "T1", "status": "detected",
    "hardness": "soft", "deadline": None, "repeated_mentions": 1,
    "green_sign_required": False, "disagreement": None,
    "cost_estimate_usd": None, "sources": None, "plan": None,
    "summary": "", "definition_of_done": None, "outputs": None, "card": None,
    "execution": None, "improvement_of": None, "merged_into": None,
    "target_repo": None, "target_kind": None, "delivery_mode": "repo",
    "notes": "", "trashed_at": None, "prev_status": None, "trash_reason": None,
    "permanent": False, "origin_trust": None,
    "thread_id": None, "thread_key": None,
    "archived_at": None, "archive_reason": None, "split_from": None,
    "silent_merge_count": 0, "display_title": None, "user_titled": False,
    "former_titles": None, "preset": None,
    "work_id": None,          # §60（D21）工作编号；None = 未批准/legacy
}

# import 期 fail-fast：registry 加了字段而这里漏补默认值 = 迁移静默丢字段。
# 用显式 raise 而非 `assert`——`python -O` 会把 assert 整条蒸发，守卫就没了。
_MISSING_DEFAULTS = (set(CORE_ORDER) | set(OPTIONAL_ORDER)) ^ set(FIELD_DEFAULTS)
if _MISSING_DEFAULTS:
    raise AssertionError(
        "FIELD_DEFAULTS 与 registry 字段词表不一致（缺/多）: "
        + ", ".join(sorted(_MISSING_DEFAULTS)))


def say(msg: str, *, err: bool = False) -> None:
    """store2 CLI 输出的 Windows 安全形：cp1252 等非 UTF-8 stdout/stderr 印
    不出中文时降级 backslashreplace——绝不让一条进度行的 UnicodeEncodeError
    崩掉迁移/导出本体（live CI 事故 2026-08-31：py3.9 Windows 管道默认
    cp1252，migrate 的 NOTE/WARN 中文全数炸 setUpClass）。migrate_yaml 复用。"""
    stream = sys.stderr if err else sys.stdout
    try:
        print(msg, file=stream)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "ascii"
        print(msg.encode(enc, "backslashreplace").decode(enc, "replace"),
              file=stream)


def dropped_keys(raw: dict) -> list:
    """from_dict 会静默丢弃的未知顶层键（`repo` 只在被当 target_repo 别名消费
    时不算丢弃）——migrate 的 dry-run 用它把丢弃报出来。"""
    out = []
    for k in raw:
        if k in FIELD_DEFAULTS:
            continue
        if k == "repo" and "target_repo" not in raw:
            continue  # 别名被消费，不算丢
        out.append(k)
    return out


def normalize_card(raw: dict) -> dict:
    """raw YAML dict → canonical card dict（= live registry 的 from_dict∘to_dict）。

    逐条复刻：未知键丢弃；`repo` 读侧别名；delivery_mode 白名单归 repo；
    id/title/tier 非 str 一律 str() 归一（数字 title、`id: 4` 真实出现过）；
    to_dict 的 core 恒序列化 + optional 省略语义。status/deadline 等**不校验
    不归一**（legacy `merged_into:<id>` 状态串原样保留——热列归一是 migrate
    的事，payload 真源永远 verbatim）。
    """
    d = dict(raw or {})
    vals = {}
    for k, default in FIELD_DEFAULTS.items():
        if k in d:
            vals[k] = d[k]
        elif k == "sources":
            vals[k] = []
        else:
            vals[k] = default
    if "target_repo" not in d and "repo" in d:
        vals["target_repo"] = d["repo"]
    dm = str(vals.get("delivery_mode") or "").strip().lower()
    vals["delivery_mode"] = dm if dm in ("chat", "repo") else "repo"
    for k in ("id", "title", "tier", "work_id"):
        v = vals[k]
        if v is not None and not isinstance(v, str):
            vals[k] = str(v)
    out: dict = {}
    for k in CORE_ORDER:
        out[k] = vals[k]
    for k in OPTIONAL_ORDER:
        v = vals[k]
        if v in (None, "", [], False):
            continue
        if k == "delivery_mode" and v == "repo":
            continue
        out[k] = v
    return out


def dump_card_yaml(obj) -> str:
    """与 live registry._dump_yaml 同参 dump——字节级对齐的关键。"""
    return yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, width=100)


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #

# 文件名白名单：id 只许字母数字 . _ -（首字符不许 '.'）——路径穿越零容忍
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*\Z")


def export_db(db_path: Path, out_dir: Path, prune: bool = False) -> int:
    """全库导出。返回进程退出码（0=干净，2=有跳过/异常行）。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT id, status, tombstone, payload FROM cards ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    written = unchanged = tombstones = 0
    problems: list = []
    expected: set = set()
    for card_id, status, tombstone, payload in rows:
        if tombstone:
            tombstones += 1
            continue
        if not isinstance(card_id, str) or not _SAFE_ID_RE.match(card_id):
            problems.append(f"skip {card_id!r}: id 不符合文件名白名单")
            continue
        try:
            obj = json.loads(payload)
        except ValueError as e:
            problems.append(f"skip {card_id}: payload 不是合法 JSON: {e}")
            continue
        if not isinstance(obj, dict) or not obj.get("id"):
            problems.append(f"skip {card_id}: payload 缺 id（疑似半截行）")
            continue
        rel = Path("archive") / f"{card_id}.yaml" if status == "archived" \
            else Path(f"{card_id}.yaml")
        path = out_dir / rel
        expected.add(path)
        text = dump_card_yaml(obj)
        if path.exists() and path.read_text(encoding="utf-8") == text:
            unchanged += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written += 1

    pruned = 0
    if prune:
        candidates = list(out_dir.glob("*.yaml"))
        if (out_dir / "archive").exists():
            candidates += list((out_dir / "archive").glob("*.yaml"))
        for p in candidates:
            if p.name == "R-000-example.yaml":  # 文档样例永不动（对齐 registry）
                continue
            if p not in expected:
                p.unlink()
                pruned += 1

    say(f"export: {written} written, {unchanged} unchanged, "
        f"{tombstones} tombstones skipped, {pruned} pruned -> {out_dir}")
    for msg in problems:
        say(f"export: WARN {msg}", err=True)
    return 2 if problems else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m act.lib.store2.export_yaml",
        description="store2 SQLite -> YAML 快照目录（deterministic，git-diff 友好）")
    ap.add_argument("--db", required=True, help="store2 SQLite 路径")
    ap.add_argument("--out", required=True, help="快照输出目录")
    ap.add_argument("--prune", action="store_true",
                    help="删除 DB 中已不存在的旧快照 *.yaml（默认保留）")
    args = ap.parse_args(argv)
    db = Path(args.db)
    if not db.exists():
        say(f"export: DB 不存在: {db}", err=True)
        return 1
    return export_db(db, Path(args.out), prune=args.prune)


if __name__ == "__main__":
    raise SystemExit(main())
