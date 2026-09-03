#!/usr/bin/env python3
"""test-ui skill · 判官：SUBJECT 清单 ⟷ REFERENCE 清单（+ tokens、几何、截图）→ 每条 PRESENT /
MISSING / CHANGED / WAIVED，账本套用（pending / waivers / aliases，只许缩），规则命中（WCAG +
token 规则表），opinion 隔离。这里决定，run_ui 只汇总。

配对（references/pairing.md）：aliases.txt（reference id → subject id）> data-parity-id pin >
元组 (screen family, role, slug) 同序号 > 无 → MISSING；同 screen+role 相似度 ≥ 0.8 只给
「建议」，绝不自动配对。pin 指到角色不同 / 名字不像的条目 = CHANGED `spoofed_pin`。
hidden（display:none / hidden / aria-hidden / 0×0 / 不可聚焦且离屏）= MISSING，永不 PRESENT。
TOPOLOGY：landmark / navigation / region / list / heading / tablist 比 parent（地标路径）、order、
side（相对 main）；GEOMETRY：`layout.*` 声明值 vs 运行时 bbox（config.geometry 映射）。

账本语义（docs/CONTRACT.md §58.4 三态 + §UI-parity.2 pending/waivers）：MISSING 且在 pending → 记账
不判红；PRESENT 却在 pending → STALE（划掉）；waivers 行必须带理由，`<rule>::<id>[::theme]`
或 `<id>` 或 `*`；pending 对 merge-base 只许缩（grew = FAIL）；aliases 悬空 = FAIL。
skill 不写任何账本；`--propose-pending` 落到 <report>/proposed/。

法典指针：§UI-parity（parity 契约 id 语法；本模块与 scripts/ui/parity_check.py 读同一本账；两者对
共有 id 的判决不一致 = run_ui 记 FAIL `parity_disagreement`，永不取平均）、§58（阈值只读）。
设计 = vnext2-plan R2.8 / D14。判例：tests/test_skill_test_ui_pair.py / _topology / _geometry /
_ledgers / _opinion_isolation（负控制齐全）。

用法：parity.py --subject INV --reference INV [--subject-tokens F] [--reference-tokens F]
                [--ledgers DIR] [--thresholds JSON] [--config config.json] --out DIR
"""

import argparse
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ladder_common_vendored as lc  # noqa: E402
import testui_common as tc  # noqa: E402

SIMILARITY_FLOOR = 0.8
SPOOF_FLOOR = 0.5
DEFAULT_THRESHOLDS = {
    "source": "skill-defaults", "note": "skill defaults — strict = WCAG 2.2 AA; visual strict = 0 %",
    "max_changed_pct": 0.0, "pixel_tolerance": 0, "max_mask_ratio": 0.2, "contrast_text": 4.5,
    "contrast_large": 3.0, "target_min_px": 24, "geometry_tolerance_px": 1.0, "token_required_families": ["layout"],
    "similarity_floor": SIMILARITY_FLOOR, "reruns": 3,
}
RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "references", "rules")
OPINION_ONLY_KEYS = ("opinion",)


# --------------------------------------------------------------------------- #
# 账本读取（parity 契约行形：`<id>  <reason…>  [<ref>]`）
# --------------------------------------------------------------------------- #

def parse_ledger(text):
    """→ {id: rest}（# 注释 / 空行忽略；同 scripts/ui/ui_common.parse_ledger）。"""
    entries = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        entries[parts[0]] = parts[1].strip() if len(parts) > 1 else ""
    return entries


def parse_aliases(text):
    """`<reference id>  <subject id>  [reason]` → {ref_id: {subject, reason}}。"""
    out = {}
    for ref_id, rest in parse_ledger(text).items():
        parts = rest.split(None, 1)
        out[ref_id] = {"subject": parts[0] if parts else "", "reason": parts[1] if len(parts) > 1 else ""}
    return out


def load_ledgers(ledger_dir):
    """ui/parity/{pending,waivers,aliases}.txt → dicts + 原文（缺席 = 空账）。"""
    def read(name):
        path = os.path.join(ledger_dir or "", name)
        return lc.read_text_or_empty(path) if ledger_dir and os.path.exists(path) else ""
    pending_text, waivers_text, aliases_text = read("pending.txt"), read("waivers.txt"), read("aliases.txt")
    return {"pending": parse_ledger(pending_text), "waivers": parse_ledger(waivers_text),
            "aliases": parse_aliases(aliases_text), "dir": ledger_dir,
            "texts": {"pending": pending_text, "waivers": waivers_text, "aliases": aliases_text}}


def waiver_for(waivers, item_id, rule_id=None, theme=None):
    """匹配顺序：`<rule>::<id>::<theme>` > `<rule>::<id>` > `<id>` > `*`；返回 (key, reason) 或 None。"""
    keys = []
    if rule_id and theme:
        keys.append("%s::%s::%s" % (rule_id, item_id, theme))
    if rule_id:
        keys.append("%s::%s" % (rule_id, item_id))
    keys += [item_id, "*"]
    for key in keys:
        if key in waivers:
            return key, waivers[key]
    return None


# --------------------------------------------------------------------------- #
# 配对
# --------------------------------------------------------------------------- #

def _key_tuple(item):
    key = item["key"]
    return (key["screen"], key["role"], key["slug"], _ordinal(item["id"]))


def _ordinal(item_id):
    match = re.search(r"#(\d+)$", item_id)
    return int(match.group(1)) if match else 1


def _is_dynamic(item):
    return "{dynamic}" in str(item["name"].get("raw", "")) or item.get("dynamic")


def index_subject(items):
    """by_key（家族内元组）· by_any（去掉 screen 的元组，首见者胜，供 union 回退）· families（被测有哪些 screen 家族）。"""
    by_any = {}
    for item in items:
        by_any.setdefault(_key_tuple(item)[1:], item)
    return {"by_key": {_key_tuple(i): i for i in items}, "by_id": {i["id"]: i for i in items},
            "by_pin": {i["pin"]: i for i in items if i.get("pin")}, "by_any": by_any,
            "families": {i["key"]["screen"] for i in items}}


def similarity(a, b):
    return round(difflib.SequenceMatcher(None, a or "", b or "").ratio(), 3)


def _visible(item):
    states = item.get("states") or {}
    return any(s.get("visible", True) for s in states.values()) if states else True


def _hidden_reason(item):
    for state in (item.get("states") or {}).values():
        if state.get("hidden_by"):
            return state["hidden_by"]
    return "hidden"


def _match_alias(ref, idx, ledgers, problems):
    alias = ledgers["aliases"].get(ref["id"])
    if not alias:
        return None
    subject = idx["by_id"].get(alias["subject"])
    if subject is None:
        problems.append({"kind": "dangling_alias", "line": "%s %s" % (ref["id"], alias["subject"])})
    return subject


def _match_pin(ref, idx):
    """→ (subject, spoofed)：pin 命中但角色不同或名字不像 → spoofed。"""
    subject = idx["by_pin"].get(ref["id"])
    if subject is None:
        return None, False
    same_role = subject["key"]["role"] == ref["key"]["role"]
    alike = similarity(subject["key"]["slug"], ref["key"]["slug"]) >= SPOOF_FLOOR
    return subject, not (same_role and alike)


def _suggest(ref, subject_items, floor):
    out = []
    for item in subject_items:
        if item["key"]["screen"] == ref["key"]["screen"] and item["key"]["role"] == ref["key"]["role"]:
            score = similarity(item["key"]["slug"], ref["key"]["slug"])
            if score >= floor and item["key"]["slug"] != ref["key"]["slug"]:
                out.append({"reference_id": ref["id"], "subject_id": item["id"], "similarity": score})
    return sorted(out, key=lambda s: -s["similarity"])[:3]


def _match_union(ref, idx):
    """参照的 screen 家族在被测侧根本不存在（原生独有的页：about / ask / deps / header…）→ 在全部家族的并集里
    找同 (role, slug, ordinal)（§66.2「web 尚无的页面在全部面的并集里找」）；家族存在则不回退——同名控件在别的页上
    不是 parity。"""
    if ref["key"]["screen"] in idx["families"]:
        return None
    return idx["by_any"].get(_key_tuple(ref)[1:])


def find_match(ref, idx, ledgers, problems):
    """→ (subject item | None, how, spoofed)；how ∈ alias | pin | tuple | tuple:union | none。"""
    via_alias = _match_alias(ref, idx, ledgers, problems)
    if via_alias is not None:
        return via_alias, "alias", False
    via_pin, spoofed = _match_pin(ref, idx)
    if via_pin is not None:
        return via_pin, "pin", spoofed
    tuple_hit = idx["by_key"].get(_key_tuple(ref))
    if tuple_hit is not None:
        return tuple_hit, "tuple", False
    union_hit = _match_union(ref, idx)
    return union_hit, "tuple:union" if union_hit is not None else "none", False


# --------------------------------------------------------------------------- #
# 字段比较（role / name / states / count / topology）
# --------------------------------------------------------------------------- #

def _name_changed(ref, sub):
    """地标（navigation / banner / main…）按角色 + 拓扑配对，名字只是辅助——原生侧栏没有 accessible name，web 给
    <nav> 加 aria-label 是更好的 a11y，不是 parity 破坏。"""
    if _is_dynamic(sub) or _is_dynamic(ref) or ref["key"]["role"] in tc.LANDMARK_ROLES:
        return False
    ref_name = ref["name"].get("en") or ref["name"].get("raw")
    sub_name = sub["name"].get("en") or sub["name"].get("raw")
    return tc.normalize_name(ref_name) != tc.normalize_name(sub_name)


def _focusable(item):
    states = list((item.get("states") or {}).values())
    return any(s.get("focusable", True) for s in states) if states else True


def _states_changed(ref, sub):
    """参照可聚焦而被测不可聚焦 = CHANGED states（两侧都禁用 / 都可聚焦不算）。"""
    if ref["kind"] != "interactive":
        return False
    return _focusable(ref) and not _focusable(sub)


TOPOLOGY_ROLES = tc.TOPOLOGY_KINDS | tc.LANDMARK_ROLES


def _topology_of(item):
    topo = item.get("topology")
    return topo if isinstance(topo, dict) else {}


def _both_differ(a, b):
    """两侧都有值且不等（缺一侧不算变化——源模式没有 side 就不裁）。"""
    return a is not None and b is not None and a != b


def _topology_changed(ref, sub):
    """→ 变化的子字段列表（side / parent / order）。TOPOLOGY 类角色比全部三项；普通控件不比拓扑（按钮换个父地标
    不算 CHANGED）——唯一例外是参照放在 navigation 地标里的条目：导航项离开导航 = 导航被重构（侧栏的 link 搬进
    header → `topology:parent` navigation → banner，owner (a) 的「挪到顶栏」）。"""
    rt, st = _topology_of(ref), _topology_of(sub)
    parent_differs = _parent_role(rt.get("parent")) != _parent_role(st.get("parent"))
    if ref["key"]["role"] not in TOPOLOGY_ROLES:
        return ["parent"] if _parent_role(rt.get("parent")) == "navigation" and parent_differs else []
    checks = (("side", _both_differ(rt.get("side"), st.get("side"))), ("parent", parent_differs),
              ("order", _both_differ(rt.get("order"), st.get("order"))))
    return [name for name, changed in checks if changed]


def _parent_role(path):
    """地标路径的最后一个地标角色（`window>banner:x>navigation:rail` → banner 之上 → 比较外层角色）。"""
    if not path or path == "window":
        return "window"
    last = path.split(">")[-1]
    return last.split(":")[0]


def _count_of(item):
    count = item.get("count")
    return count if count else 1


def fields_changed(ref, sub, spoofed):
    """count 只在被测比参照少时算变化——多出来的同名实例是 extras，永不是 parity（anti-gaming #4）。"""
    checks = (("spoofed_pin", spoofed), ("role", ref["key"]["role"] != sub["key"]["role"]),
              ("name", _name_changed(ref, sub)), ("states", _states_changed(ref, sub)),
              ("count", _count_of(sub) < _count_of(ref)))
    changed = [name for name, flag in checks if flag]
    return changed + ["topology:%s" % f for f in _topology_changed(ref, sub)]


def _topology_detail(ref, sub):
    return {"reference": ref.get("topology"), "subject": sub.get("topology")}


# --------------------------------------------------------------------------- #
# 条目判决
# --------------------------------------------------------------------------- #

def _row(ref, status, **extra):
    row = {"id": ref["id"], "location": ref["id"], "screen": ref.get("screen") or ref["key"]["screen"],
           "kind": ref["kind"], "role": ref["key"]["role"], "status": status, "fields_changed": [], "detail": {},
           "ledger": None, "subject_id": None, "matched_by": None, "gated": bool(ref.get("gated"))}
    row.update(extra)
    return row


def _na_owner(ref):
    return (ref.get("owner") or "web") in ("retired", "os", "shell")


def judge_item(ref, idx, ledgers, ctx):
    """一条 reference 条目 → row（PRESENT / MISSING / CHANGED / WAIVED），账本已套用。"""
    if _na_owner(ref):
        return _row(ref, "N-A", detail={"owner": ref.get("owner")})
    if ref.get("project_gated") is False:
        return _row(ref, "N-A", detail={"informational": True, "note": "listed, not gated by the project inventory (copy / help text)"})
    if _is_dynamic(ref):
        return _row(ref, "N-A", detail={"dynamic": True, "note": "runtime-named item has no source identity to pair"})
    subject, how, spoofed = find_match(ref, idx, ledgers, ctx["problems"])
    if subject is None:
        row = _row(ref, "MISSING", detail={"suggestions": _suggest(ref, ctx["subject_items"], ctx["floor"])})
    elif not _visible(subject):
        row = _row(ref, "MISSING", subject_id=subject["id"], matched_by=how,
                   detail={"hidden_by": _hidden_reason(subject)})
    else:
        row = _judge_matched(ref, subject, how, spoofed, ctx)
    return apply_ledger(row, ledgers, ctx["problems"])


def _display_name(item):
    name = item["name"]
    return name.get("en") if name.get("en") else name.get("raw")


def _unreachable(ref, subject, focus_walk):
    return focus_walk is not None and ref["kind"] == "interactive" and subject["id"] not in focus_walk


def _changed_detail(ref, subject, changed):
    detail = {}
    if any(f.startswith("topology") for f in changed):
        detail["topology"] = _topology_detail(ref, subject)
    if "name" in changed:
        detail["name"] = {"reference": _display_name(ref), "subject": _display_name(subject)}
    return detail


def _matched_changes(ref, subject, how, spoofed, focus_walk):
    """全部变化字段：alias 配对的名字差不算（人已声明是改名）；gated / unreachable 追加。"""
    changed = [f for f in fields_changed(ref, subject, spoofed) if not (how == "alias" and f == "name")]
    if ref.get("gated") is False and subject.get("gated"):
        changed.append("gated")
    if _unreachable(ref, subject, focus_walk):
        changed.append("unreachable")
    return changed


def _judge_matched(ref, subject, how, spoofed, ctx):
    changed = _matched_changes(ref, subject, how, spoofed, ctx.get("focus_walk"))
    return _row(ref, "CHANGED" if changed else "PRESENT", subject_id=subject["id"], matched_by=how, fields_changed=changed,
                detail=_changed_detail(ref, subject, changed))


def apply_ledger(row, ledgers, problems):
    """pending：MISSING / CHANGED → 记账（挂账的条目还没按规格落地——搬了位置的导航项仍算没到）；PRESENT 却挂账
    → stale（该划掉了）。waivers：MISSING/CHANGED → WAIVED（要理由）。"""
    pending, waivers = ledgers["pending"], ledgers["waivers"]
    if row["id"] in pending:
        if row["status"] in ("MISSING", "CHANGED"):
            row["ledger"] = "pending"
        else:
            problems.append({"kind": "stale_pending", "line": row["id"]})
            row["ledger"] = "stale"
    hit = waiver_for(waivers, row["id"]) if row["status"] in ("MISSING", "CHANGED") else None
    if hit:
        row = _waive(row, hit, problems)
    return row


def _waive(row, hit, problems):
    key, reason = hit
    if not reason.strip():
        problems.append({"kind": "reasonless_waiver", "line": key})
        row["detail"]["invalid_waiver"] = key
        return row
    row.update({"status": "WAIVED", "ledger": "waived", "detail": dict(row["detail"], waiver=key, reason=reason)})
    return row


def compare_items(subject, reference, ledgers, thresholds, focus_walk=None):
    """→ {rows, extras, suggestions, problems}。"""
    idx = index_subject(subject["items"])
    ctx = {"subject_items": subject["items"], "floor": float(thresholds.get("similarity_floor", SIMILARITY_FLOOR)),
           "problems": [], "focus_walk": focus_walk}
    rows = [judge_item(ref, idx, ledgers, ctx) for ref in reference["items"]]
    matched = {r["subject_id"] for r in rows if r["subject_id"]}
    extras = [i["id"] for i in subject["items"] if i["id"] not in matched and not _is_dynamic(i) and _visible(i)]
    suggestions = [s for r in rows for s in r["detail"].get("suggestions", [])]
    return {"rows": rows, "extras": extras, "suggestions": suggestions, "problems": ctx["problems"]}


# --------------------------------------------------------------------------- #
# a11y 树表达不了的 id 种类（快捷键字形、设置键名）：项目门在场就采它的判决，不在场就查源字符串（§66.2 的探针语义）
# --------------------------------------------------------------------------- #

STRING_PROBE_KINDS = ("shortcut", "setting")
_PROJECT_STATUS = {"PRESENT": ("PRESENT", None), "STALE": ("PRESENT", "stale"), "PENDING": ("MISSING", "pending"),
                   "MISSING": ("MISSING", None), "WAIVED": ("WAIVED", "waived")}


def _probe_kind(row):
    return row["id"].split(":", 1)[0]


def adopt_project_verdicts(rows, project_items):
    """`shortcut:*` / `setting:*` 行：项目门（scripts/ui/parity_check.py）判过的 id 采用它的判决——快捷键是字形在源码里、
    设置键是 "key" 在 server/web 源码里，都不是可达树能量的东西；matched_by 记 project:parity_check。"""
    items = project_items if project_items else {}
    for row in rows:
        verdict = items.get(row["id"]) if _probe_kind(row) in STRING_PROBE_KINDS else None
        if verdict in _PROJECT_STATUS:
            status, ledger = _PROJECT_STATUS[verdict]
            row.update(status=status, ledger=ledger, matched_by="project:parity_check", fields_changed=[],
                       detail=dict(row["detail"], project_verdict=verdict))
    return rows


def _needle(ref):
    if _probe_kind(ref) == "shortcut":
        return ref.get("shortcut")
    return '"%s"' % ref["id"].split(":")[-1].split("#")[0]


def string_probe(rows, reference_items, source_text, ledgers, problems):
    """无项目门时的替代探针：MISSING 的 shortcut / setting 行，字形或 "key" 出现在 subject 源码文本里 → PRESENT
    （matched_by source-string，evidence 记 needle）；账本重新套用。"""
    by_id = {i["id"]: i for i in reference_items}
    for row in rows:
        needle = _probe_needle(row, by_id)
        if needle and needle in source_text:
            row.update(status="PRESENT", ledger=None, matched_by="source-string", detail=dict(row["detail"], evidence=needle))
            apply_ledger(row, ledgers, problems)
    return rows


def _probe_needle(row, by_id):
    """MISSING 的 shortcut / setting 行 → 要在源码里找的字符串；其它行 → None。"""
    if row["status"] != "MISSING" or _probe_kind(row) not in STRING_PROBE_KINDS or row["id"] not in by_id:
        return None
    return _needle(by_id[row["id"]])


# --------------------------------------------------------------------------- #
# 账本纪律：只许缩（对 merge-base 文本比较）
# --------------------------------------------------------------------------- #

def ledger_shrink_check(ledgers, base_texts, acknowledged=()):
    """→ problems：pending 新增行 = grew；waivers 新增行没理由或没 acknowledged = grew。"""
    problems = []
    cur_pending, base_pending = set(ledgers["pending"]), set(parse_ledger(base_texts.get("pending", "")))
    problems += [{"kind": "pending_grew", "line": k} for k in sorted(cur_pending - base_pending)]
    base_waivers = parse_ledger(base_texts.get("waivers", ""))
    for key, reason in sorted(ledgers["waivers"].items()):
        if key in base_waivers:
            continue
        if not reason.strip() or key.split("::")[-1] not in acknowledged and key not in acknowledged:
            problems.append({"kind": "waiver_grew", "line": key})
    return problems


def ledger_lint(ledgers):
    """静态账本卫生：waivers 无理由、aliases 无 subject。"""
    problems = [{"kind": "reasonless_waiver", "line": k} for k, v in ledgers["waivers"].items() if not v.strip()]
    problems += [{"kind": "dangling_alias", "line": k} for k, v in ledgers["aliases"].items() if not v["subject"]]
    return problems


# --------------------------------------------------------------------------- #
# tokens / theme / geometry
# --------------------------------------------------------------------------- #

def _token_changed(ref_tok, sub_tok, tolerance):
    ref_v, sub_v = ref_tok.get("$value"), sub_tok.get("$value")
    if ref_tok.get("$type") == "dimension" or sub_tok.get("$type") == "dimension":
        a, b = tc.px_value(ref_v), tc.px_value(sub_v)
        return a is None or b is None or abs(a - b) > tolerance
    return tc.token_value_text(ref_tok) != tc.token_value_text(sub_tok)


def _themes_of(doc):
    themes = (doc if doc else {}).get("themes")
    return themes if isinstance(themes, dict) else {}


def _theme_table(doc, theme):
    table = _themes_of(doc).get(theme)
    return table if table else {}


BASE_THEME = "light"  # `:root` 作用域落在这一表（tokens._scope_theme）；其它主题只声明覆盖项，其余按 CSS 级联继承


def compare_tokens(subject_doc, reference_doc, thresholds):
    """逐主题逐路径：共有 → PRESENT/CHANGED；参照有、被测无且家族在 token_required_families → MISSING。被测主题表
    缺的路径先看 :root（BASE_THEME）——dark 没重声明的变量在 dark 下生效的就是 :root 值（row.inherited = true）；
    只有 :root 也没有才算 MISSING。"""
    rows, tolerance = [], float(thresholds.get("geometry_tolerance_px", 1.0))
    required = set(thresholds.get("token_required_families", []))
    base = _theme_table(subject_doc, BASE_THEME)
    for theme, ref_tokens in sorted(_themes_of(reference_doc).items()):
        sub_tokens = _theme_table(subject_doc, theme)
        for path, ref_tok in sorted(ref_tokens.items()):
            sub_tok, inherited = sub_tokens.get(path), False
            if sub_tok is None and theme != BASE_THEME and path in base:
                sub_tok, inherited = base[path], True
            rows += _token_row(theme, path, ref_tok, sub_tok, required, tolerance, inherited)
    return rows


def _token_row(theme, path, ref_tok, sub_tok, required, tolerance, inherited=False):
    base = {"id": "token:%s:%s" % (theme, path), "location": path, "theme": theme, "kind": "token",
            "reference": ref_tok.get("$value"), "subject": sub_tok.get("$value") if sub_tok else None, "inherited": inherited}
    if sub_tok is None:
        if path.split(".")[0] not in required:
            return []
        return [dict(base, status="MISSING")]
    changed = _token_changed(ref_tok, sub_tok, tolerance)
    return [dict(base, status="CHANGED" if changed else "PRESENT")]


def theme_parity(doc):
    """同一侧两主题的 token 集合差 → [{path, only_in}]（信息 → 规则 tokens.theme_parity）。"""
    themes = doc.get("themes") or {}
    if len(themes) < 2:
        return []
    names = sorted(themes)
    out = []
    for theme in names:
        others = set().union(*[set(themes[t]) for t in names if t != theme])
        out += [{"path": p, "only_in": theme} for p in sorted(set(themes[theme]) - others)]
    return out


def _declared_theme(doc):
    block = (doc if doc else {}).get("default_theme")
    declared = (block if isinstance(block, dict) else {}).get("declared")
    return declared if isinstance(declared, dict) else {}


def _observed_differs(observed, fallback):
    """首帧观察值（每种 emulation 一个）与参照默认值不同 → True；参照没声明 → 不裁。"""
    if not observed or not fallback:
        return False
    return any(value != fallback for value in observed.values())


def compare_default_theme(subject_doc, reference_doc, observed=None):
    """声明 vs 声明（fallback 与 mode）；有 observed 再比首帧观察值。参照声明了固定默认（mode fixed）而被测
    跟随系统（mode system）= CHANGED `mode`：无存储偏好时深色系统上首帧就是深色——owner (b) 的「默认成了深色」。"""
    ref, sub = _declared_theme(reference_doc), _declared_theme(subject_doc)
    changed = []
    if _both_differ(ref.get("fallback"), sub.get("fallback")):
        changed.append("declared")
    if _both_differ(ref.get("mode"), sub.get("mode")):
        changed.append("mode")
    if _observed_differs(observed, ref.get("fallback")):
        changed.append("observed")
    return {"id": "theme:default", "location": "theme:default", "kind": "theme", "reference": ref, "subject": sub,
            "observed": observed, "status": "CHANGED" if changed else "PRESENT", "fields_changed": changed}


def _token_px(table, path):
    token = table.get(path)
    return tc.px_value((token if token else {}).get("$value"))


def compare_geometry(geometry_map, reference_tokens, observed, thresholds, subject_tokens=None):
    """config.geometry {path: {screen, role|selector, measure}} → 声明值（参照 token）vs 观察 bbox。"""
    rows, tolerance = [], float(thresholds.get("geometry_tolerance_px", 1.0))
    ref_light, sub_light = _theme_table(reference_tokens, "light"), _theme_table(subject_tokens, "light")
    for path, spec in sorted((geometry_map if geometry_map else {}).items()):
        values = observed.get(path)
        rows.append(_geometry_row(path, spec, _token_px(ref_light, path), values if values else [], tolerance,
                                  _token_px(sub_light, path)))
    return rows


def _geometry_note(declared, sub_declared, tolerance):
    if sub_declared is not None and abs(sub_declared - declared) <= tolerance:
        return "declared matches (%spx), rendered does not" % sub_declared
    return None


def _geometry_row(path, spec, declared, values, tolerance, sub_declared):
    row = {"id": "geometry:%s" % path, "location": path, "kind": "geometry", "map": spec, "declared": declared,
           "observed": values, "status": "PRESENT", "note": None}
    if declared is None:
        return dict(row, status="N-A", note="reference declares no %s" % path)
    if not values:
        return dict(row, status="UNAVAILABLE", note="no runtime bbox for %s (needs tier 2 driver)" % path)
    if any(abs(v - declared) > tolerance for v in values):
        return dict(row, status="CHANGED", note=_geometry_note(declared, sub_declared, tolerance))
    return row


def geometry_source_substitute(geometry_map, css_texts):
    """源模式替代物：`var(--native-layout-…)` 在组件 CSS 里被消费过 → PRESENT(substituted)；否则 MISSING。
    永远标 substituted——它看不见渲染宽度。"""
    rows = []
    for path in sorted(geometry_map or {}):
        var_name = "--native-" + path.replace("_", "-").replace(".", "-")
        used = any(("var(%s)" % var_name) in text for text in css_texts)
        rows.append({"id": "geometry:%s" % path, "location": path, "kind": "geometry", "declared": None, "observed": [],
                     "status": "PRESENT" if used else "MISSING", "substituted": True,
                     "note": "source substitute: %s %s in CSS — cannot see rendered width" % (var_name, "consumed" if used else "not consumed")})
    return rows


# --------------------------------------------------------------------------- #
# 规则（references/rules/*.json；项目 config.rules 覆盖）
# --------------------------------------------------------------------------- #

def load_rules(overrides=None):
    rules = {}
    for name in ("wcag.json", "tokens.json"):
        path = os.path.join(RULES_DIR, name)
        if os.path.exists(path):
            rules.update(tc.read_json(path).get("rules") or {})
    for rule_id, patch in (overrides or {}).items():
        rules.setdefault(rule_id, {}).update(patch)
    return rules


def _hit(rule_id, rules, item_id, measured, threshold, theme=None, detail=None):
    rule = rules.get(rule_id) or {}
    return {"rule_id": rule_id, "id": item_id, "location": item_id, "measured": measured, "threshold": threshold,
            "severity": rule.get("severity", "serious"), "theme": theme, "detail": detail or {}, "status": "hit"}


def rule_name_interactive(inventory, rules, thresholds):
    return [_hit("wcag.name.interactive", rules, i["id"], "", "non-empty name")
            for i in inventory["items"] if i["kind"] == "interactive" and not str(i["name"].get("raw", "")).strip()
            and _visible(i)]


def _visible_states(item):
    """(theme, state) 对——只取可见状态（隐藏的控件不量尺寸也不量对比度）。"""
    states = item.get("states")
    for dim, state in (states if isinstance(states, dict) else {}).items():
        if state.get("visible"):
            yield dim.split("::")[0], state


def _small_target(state, floor):
    box = state.get("bbox")
    return box is not None and min(box[2], box[3]) < floor


def rule_target_size(inventory, rules, thresholds):
    floor = float(thresholds.get("target_min_px", 24))
    hits = []
    for item in [i for i in inventory["items"] if i["kind"] == "interactive"]:
        for theme, state in _visible_states(item):
            if _small_target(state, floor):
                hits.append(_hit("wcag.target.size", rules, item["id"], min(state["bbox"][2], state["bbox"][3]), floor, theme))
    return hits


def _contrast_floor(thresholds, large):
    return float(thresholds.get("contrast_large" if large else "contrast_text", 4.5))


def _low_contrast(state, thresholds):
    """(ratio, floor) 当该状态的对比度低于地板；否则 None。"""
    contrast = state.get("contrast")
    if not contrast or contrast.get("ratio") is None:
        return None
    floor = _contrast_floor(thresholds, contrast.get("large"))
    return (contrast["ratio"], floor) if contrast["ratio"] < floor else None


def rule_contrast_runtime(inventory, rules, thresholds):
    hits = []
    for item in inventory["items"]:
        for theme, state in _visible_states(item):
            low = _low_contrast(state, thresholds)
            if low:
                hits.append(_hit("wcag.contrast.text", rules, item["id"], low[0], low[1], theme))
    return hits


def _pair_colors(table, pair):
    return [tc.parse_color(str(_token_value(table, path))) for path in pair[:2]]


def _token_value(table, path):
    token = table.get(path)
    return (token if token else {}).get("$value")


def _pair_hit(theme, table, pair, rules, thresholds):
    fg, bg = _pair_colors(table, pair)
    if fg is None or bg is None:
        return None
    floor = _contrast_floor(thresholds, len(pair) > 2 and bool(pair[2]))
    ratio = tc.contrast_ratio(fg, bg)
    return _hit("wcag.contrast.text", rules, "%s/%s" % (pair[0], pair[1]), ratio, floor, theme) if ratio < floor else None


def rule_contrast_pairs(tokens_doc, rules, thresholds, pairs):
    """源模式对比度：config.tokens.contrast_pairs [[fg_path, bg_path, large?]] 逐主题算。"""
    hits = []
    for theme, table in sorted(_themes_of(tokens_doc).items()):
        for pair in (pairs if pairs else []):
            hit = _pair_hit(theme, table, pair, rules, thresholds)
            if hit:
                hits.append(hit)
    return hits


def _reached(inventory):
    walks = inventory.get("focus_walk")
    if not walks:
        return None
    return set().union(*[set(w) for w in walks.values()])


def rule_keyboard(inventory, rules, thresholds):
    reached = _reached(inventory)
    if reached is None:
        return []
    # 可见且在某个状态下可聚焦的交互项才算候选：disabled 的按钮（composer 的「捕获」在没输入时）本就不在 Tab 序里
    candidates = [i for i in inventory["items"] if i["kind"] == "interactive" and _visible(i) and _focusable(i)]
    return [_hit("wcag.keyboard", rules, i["id"], "unreachable", "in tab order") for i in candidates if i["id"] not in reached]


def rule_lang(inventory, rules, thresholds):
    lang = inventory.get("lang")
    if lang is None:
        return []
    return [] if str(lang).strip() else [_hit("wcag.lang", rules, "document", "", "html[lang]")]


def rule_heading_order(inventory, rules, thresholds):
    hits, last = [], {}
    for item in inventory["items"]:
        level = item.get("level")
        if item["key"]["role"] != "heading" or not level:
            continue
        prev = last.get(item.get("screen"))
        if prev is not None and level > prev + 1:
            hits.append(_hit("wcag.heading.order", rules, item["id"], "h%d after h%d" % (level, prev), "no skipped level"))
        last[item.get("screen")] = level
    return hits


def rule_theme_parity(tokens_doc, rules, thresholds):
    return [_hit("tokens.theme_parity", rules, "token:%s" % p["path"], "only in %s" % p["only_in"], "both themes")
            for p in theme_parity(tokens_doc)]


def rule_off_literals(tokens_doc, rules, thresholds):
    literals = tokens_doc.get("literals_outside")
    return [_hit("tokens.off_literal", rules, "%s:%s" % (lit["file"], lit["line"]), lit["value"], "var(--…)",
                 detail=lit) for lit in (literals if literals else [])]


INVENTORY_RULES = (rule_name_interactive, rule_target_size, rule_contrast_runtime, rule_keyboard, rule_lang,
                   rule_heading_order)
TOKEN_RULES = (rule_theme_parity, rule_off_literals)


def _apply_rules(fns, doc, rules, thresholds):
    if not doc:
        return []
    hits = []
    for fn in fns:
        hits += fn(doc, rules, thresholds)
    return hits


def run_rules(inventory, tokens_doc, thresholds, rules=None, contrast_pairs=None, waivers=None):
    """全部规则 → hits（waived 的标 WAIVED，理由随行）。"""
    if rules is None:
        rules = load_rules()
    hits = _apply_rules(INVENTORY_RULES, inventory, rules, thresholds) + _apply_rules(TOKEN_RULES, tokens_doc, rules, thresholds)
    if tokens_doc:
        hits += rule_contrast_pairs(tokens_doc, rules, thresholds, contrast_pairs)
    return [_waive_hit(h, waivers if waivers else {}) for h in hits]


def _waive_hit(hit, waivers):
    found = waiver_for(waivers, hit["id"], hit["rule_id"], hit.get("theme"))
    if found and found[1].strip():
        hit.update(status="WAIVED", waiver=found[0], reason=found[1])
    return hit


# --------------------------------------------------------------------------- #
# opinion 隔离（只能写 result.opinion；碰别的键 → 丢弃并记录）
# --------------------------------------------------------------------------- #

def apply_opinion(result, opinion):
    """opinion = {"text": …, 任意其它键}；只有 text/sections 进 result["opinion"]，其余键被丢弃并列在
    result["opinion"]["dropped_keys"]——设计质量意见永不改状态、条目或 fix-first。"""
    forbidden = sorted(k for k in (opinion or {}) if k in ("checks", "items", "fix_first", "status", "verdict", "rows"))
    text = (opinion or {}).get("text") or ""
    result["opinion"] = {"text": text, "dropped_keys": forbidden,
                         "banner": "Nothing below changes a status or a rank."}
    return result


# --------------------------------------------------------------------------- #
# 总装 + CLI
# --------------------------------------------------------------------------- #

def _counts(rows):
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


def _token_side(subject_tokens, reference_tokens, thresholds, observed_theme):
    """tokens + 默认主题两行；没有参照 tokens 就都空。"""
    if not reference_tokens:
        return [], None
    tokens = compare_tokens(subject_tokens, reference_tokens, thresholds) if subject_tokens else []
    return tokens, compare_default_theme(subject_tokens, reference_tokens, observed_theme)


def _geometry_side(geometry_map, reference_tokens, observed, thresholds, subject_tokens):
    if not geometry_map or observed is None:
        return []
    return compare_geometry(geometry_map, reference_tokens if reference_tokens else {}, observed, thresholds, subject_tokens)


def _problems(items, ledgers, base_texts, acknowledged):
    problems = items["problems"] + ledger_lint(ledgers)
    if base_texts is not None:
        problems += ledger_shrink_check(ledgers, base_texts, acknowledged)
    return _dedupe(problems)


def compare(subject, reference, ledgers, thresholds, subject_tokens=None, reference_tokens=None,
            geometry_map=None, geometry_observed=None, base_texts=None, acknowledged=(), focus_walk=None,
            observed_theme=None, rules=None, contrast_pairs=None):
    """一次完整比较 → 结果 dict（字段 add-only）。"""
    items = compare_items(subject, reference, ledgers, thresholds, focus_walk)
    tokens, theme_row = _token_side(subject_tokens, reference_tokens, thresholds, observed_theme)
    geometry = _geometry_side(geometry_map, reference_tokens, geometry_observed, thresholds, subject_tokens)
    hits = run_rules(subject, subject_tokens, thresholds, rules, contrast_pairs, ledgers["waivers"])
    return {"schemaVersion": tc.SCHEMA_VERSION, "items": items["rows"], "extras": items["extras"],
            "suggestions": items["suggestions"], "tokens": tokens, "theme_default": theme_row, "geometry": geometry,
            "rules": hits, "ledger_problems": _problems(items, ledgers, base_texts, acknowledged),
            "counts": {"items": _counts(items["rows"]), "tokens": _counts(tokens)}, "thresholds": thresholds}


def _dedupe(problems):
    seen, out = set(), []
    for problem in problems:
        key = (problem["kind"], problem["line"])
        if key not in seen:
            seen.add(key)
            out.append(problem)
    return out


def _item_red(row):
    return row["status"] in ("MISSING", "CHANGED") and row["ledger"] != "pending"


def _rule_red(hit):
    return hit["status"] == "hit" and hit["severity"] in ("critical", "serious")


def red_reasons(result):
    """→ 判红的理由列表（空 = 绿）。"""
    theme = result.get("theme_default")
    reasons = [("items", any(_item_red(r) for r in result["items"])),
               ("tokens", any(r["status"] in ("MISSING", "CHANGED") for r in result["tokens"])),
               ("theme", bool(theme) and theme["status"] == "CHANGED"),
               ("geometry", any(r["status"] == "CHANGED" for r in result["geometry"])),
               ("rules", any(_rule_red(h) for h in result["rules"])),
               ("ledger", bool(result["ledger_problems"]))]
    return [name for name, red in reasons if red]


def is_red(result):
    """任一非账本 MISSING/CHANGED、失效账本、非 WAIVED 的规则命中 → 红。"""
    return bool(red_reasons(result))


def render_md(result):
    lines = ["# parity — items %s · tokens %s" % (json.dumps(result["counts"]["items"]), json.dumps(result["counts"]["tokens"]))]
    for row in result["items"]:
        if row["status"] != "PRESENT":
            lines.append("- %s `%s` %s %s" % (row["status"], row["id"], ",".join(row["fields_changed"]), row["ledger"] or ""))
    lines += ["- ledger problem: %s %s" % (p["kind"], p["line"]) for p in result["ledger_problems"]]
    lines += ["- rule %s `%s` %s < %s" % (h["rule_id"], h["id"], h["measured"], h["threshold"]) for h in result["rules"]]
    return "\n".join(lines) + "\n"


def _load_optional(path):
    return tc.read_json(path) if path else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--subject-tokens")
    parser.add_argument("--reference-tokens")
    parser.add_argument("--ledgers", metavar="DIR")
    parser.add_argument("--thresholds", metavar="JSON")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    subject, reference = tc.read_json(args.subject), tc.read_json(args.reference)
    bad = tc.validate_inventory(subject) + tc.validate_inventory(reference)
    if bad:
        print("parity: inventory unreadable — fail closed: %s" % ", ".join(bad[:5]), file=sys.stderr)
        return 2
    thresholds = dict(DEFAULT_THRESHOLDS, **(_load_optional(args.thresholds) or {}))
    result = compare(subject, reference, load_ledgers(args.ledgers), thresholds, _load_optional(args.subject_tokens),
                     _load_optional(args.reference_tokens))
    tc.write_text(os.path.join(args.out, "parity.json"), tc.dump_json(result))
    tc.write_text(os.path.join(args.out, "parity.md"), render_md(result))
    print("parity: %s → %s" % (result["counts"], args.out))
    return 1 if is_red(result) else 0


if __name__ == "__main__":
    sys.exit(main())
