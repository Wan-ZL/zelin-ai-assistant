#!/usr/bin/env python3
"""全量场景表生成器：把「这个产品有多少个可验收场景」从人脑搬到一个确定性文件里。

法典：CONTRACT §58（QA 门与账本：阈值/基线是文件真源，判决靠脚本不靠眼睛）+ §66.2
（原生清单 ui/parity/native-inventory.json 是 UI 场景的真源）。本脚本只**清点**，
不判定在不在场——每行配一条 proof（证据 DSL），由 coverage_run.py 去执行。

五个源（每行 id 前缀即源）：
  contract:  docs/CONTRACT.md 的 `## N.` 节与 `### N.M` 小节（tombstone 节记 waived）
  parity:    ui/parity/native-inventory.json 里每个 gated 条目
  route:     server/app.py 的精确表/前缀表（含 #401 / #noauth / #404 / #409 分支行）
  setting:   server/settings_catalog.py 的 SECTIONS 逐字段 + 四个特殊模块
  shell:     shell/Sources/*.swift 的菜单表、全局快捷键、Dock 徽标、open_page、
             终端接管、通知转发、登录项开关

证据 DSL（executor 见 scripts/qa/coverage_run.py）：
  unittest:<模块,模块> · parity:<id> · swift:<Harness> · axprobe:<probe> ·
  http:<METHOD> <path> [noauth] expect=<code> [contains=<substr>] ·
  settings:<section>.<key> · flow:<name> · fixture:<slug>；多条用 " && " 串联（全中才算在）。

用法：
    python3 scripts/qa/coverage_inventory.py --write     # 重铸 qa/coverage_inventory.json
    python3 scripts/qa/coverage_inventory.py --check     # 陈旧即退 1（CI / 本地门）
    python3 scripts/qa/coverage_inventory.py --summary   # 一行摘要
"""

import argparse
import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
INVENTORY_PATH = os.path.join(REPO_ROOT, "qa", "coverage_inventory.json")
FIXTURES_B_PATH = os.path.join(REPO_ROOT, "qa", "coverage_fixtures_b.json")
SOURCES = ("contract", "parity", "routes", "settings", "shell")

# 一条 proof 里最多列几个 unittest 模块（证据行 ≤200 字符的预算；排序后取前 N，确定性）
MAX_MODULES = 4

WAIVE_TOMBSTONE = "tombstone"


def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def make_row(row_id, source, scenario, proof, tier="A", status="todo",
             waive_reason=None, note=None):
    """一行场景（键序固定 = 输出确定性的一半）。"""
    return {
        "id": row_id,
        "source": source,
        "scenario": scenario,
        "proof": proof,
        "tier": tier,
        "status": status,
        "waive_reason": waive_reason,
        "note": note,
    }


def join_proofs(*parts):
    """把若干 proof 片段串成 " && " 形式（空片段丢掉，顺序保留，去重）。"""
    out = []
    for part in parts:
        for piece in (part or "").split(" && "):
            piece = piece.strip()
            if piece and piece not in out:
                out.append(piece)
    return " && ".join(out)


# --------------------------------------------------------------------------- #
# 1. contract —— docs/CONTRACT.md 的节与小节
# --------------------------------------------------------------------------- #

_SECTION_RE = re.compile(r"^## (\d+)\.\s*(.*)$")
_SUB_RE = re.compile(r"^### (\d+)\.(\d+)\s*(.*)$")
# 「这一节自己退役了」的写法（CLAUDE.md 防腐 #6 的 tombstone 形制）。裸的 `tombstone` /
# `退役` 不算——正文里到处是「改 tombstone」「退役中 app」「sqlite tombstone」这类提及，
# 只有**说本节自己**退役/作废/并入的句子才判 waived。
_TOMBSTONE_HEADING_RE = re.compile(
    r"[（(]\s*(?:retired|tombstone)|已退役|整体退役|整节退役|本节退役|本节作废")
_TOMBSTONE_BODY_RE = re.compile(
    r"整体退役|整节退役|全节退役|本节退役|本节作废|本节已?并入\s*§")
_CITE_RE = re.compile(r"§\s*(\d+)(?:\.(\d+))?")


def parse_contract(text):
    """CONTRACT 正文 → [{id, level, num, scenario, tombstone}]（顺序 = 文件顺序）。

    tombstone 判定：标题 + 其后 3 行非空正文里出现 tombstone 形制。"""
    lines = text.split("\n")
    out = []
    for index, line in enumerate(lines):
        section = _SECTION_RE.match(line)
        sub = _SUB_RE.match(line)
        if section:
            num, heading = section.group(1), section.group(2).strip()
        elif sub:
            num = "%s.%s" % (sub.group(1), sub.group(2))
            heading = sub.group(3).strip()
        else:
            continue
        out.append({
            "id": "contract:§" + num,
            "num": num,
            "scenario": heading,
            "tombstone": bool(_TOMBSTONE_HEADING_RE.search(heading)
                              or _TOMBSTONE_BODY_RE.search(_head_body(lines, index))),
        })
    return out


def _head_body(lines, index):
    """标题之后的前 3 行非空正文（遇到下一个标题就停）。"""
    body = []
    for line in lines[index + 1:]:
        if line.startswith("#"):
            break
        if line.strip():
            body.append(line.strip())
        if len(body) == 3:
            break
    return "\n".join(body)


def test_texts(tests_dir):
    """tests/test_*.py → {模块路径: 正文}（§ 引用与路由字面量都从这一份读）。"""
    out = {}
    for name in sorted(os.listdir(tests_dir)):
        if name.startswith("test_") and name.endswith(".py"):
            out["tests." + name[:-3]] = read_text(os.path.join(tests_dir, name))
    return out


def cited_numbers(text):
    """一份正文里引到的 § 号：{"49", "49.2", …}（§N.M 同时算作引了 §N）。"""
    nums = set()
    for match in _CITE_RE.finditer(text):
        num, sub = match.group(1), match.group(2)
        nums.add(num)
        if sub:
            nums.add("%s.%s" % (num, sub))
    return nums


def citation_index(tests_dir):
    """tests/test_*.py → {"49": {模块…}, "49.2": {模块…}}（§N 的集合含其所有小节的引用）。"""
    index = {}
    for module, text in test_texts(tests_dir).items():
        for num in cited_numbers(text):
            index.setdefault(num, set()).add(module)
    return index


def unittest_proof(modules):
    if not modules:
        return ""
    return "unittest:" + ",".join(sorted(modules)[:MAX_MODULES])


_FLOW_RULES = (
    (("flow:card_lifecycle",),
     ("注册表", "卡片", "看板", "registry", "dashboard", "inbox", "card", "board", "提案")),
    (("flow:install_fresh", "flow:uninstall_reinstall"),
     ("安装", "install", "launchd", "卸载", "uninstall", "部署", "bootstrap", "agent 装")),
    (("flow:doctor_clean",), ("doctor", "体检", "诊断", "健康", "health")),
    (("flow:settings_roundtrip_all",), ("设置", "settings", "偏好", "旋钮")),
    (("flow:recaps",), ("recap", "纪要", "会议")),
    (("flow:pwa",), ("PWA", "pwa", "manifest", "装成 app")),
    (("flow:pages_controls",), ("web", "页面", "UI", "parity", "对齐", "前端")),
)
_INSTALL_SECTIONS = frozenset({"48", "56", "69", "74"})


def contract_flows(num, heading):
    """节号 + 标题 → 该节被哪些端到端 flow 走过（brief §3 的挂接表）。"""
    flows = []
    top = num.split(".")[0]
    haystack = heading.lower()
    for names, keywords in _FLOW_RULES:
        hit = any(word.lower() in haystack for word in keywords)
        if names[0] == "flow:install_fresh" and top in _INSTALL_SECTIONS:
            hit = True
        if hit:
            flows.extend(name for name in names if name not in flows)
    return flows


def _contract_modules(index, num):
    """(模块集合, note)：小节没人逐字引 → 退回父节的判例并记 parent-cite。"""
    modules = index.get(num, set())
    if modules or "." not in num:
        return modules, None
    parent = index.get(num.split(".")[0], set())
    return parent, "parent-cite" if parent else None


def contract_rows(root):
    text = read_text(os.path.join(root, "docs", "CONTRACT.md"))
    index = citation_index(os.path.join(root, "tests"))
    rows = []
    for entry in parse_contract(text):
        modules, note = _contract_modules(index, entry["num"])
        proof = join_proofs(unittest_proof(modules),
                            " && ".join(contract_flows(entry["num"], entry["scenario"])))
        waived = entry["tombstone"]
        rows.append(make_row(entry["id"], "contract", entry["scenario"], proof,
                             status="waived" if waived else "todo",
                             waive_reason=WAIVE_TOMBSTONE if waived else None, note=note))
    return rows


# --------------------------------------------------------------------------- #
# 2. parity —— ui/parity/native-inventory.json 的每个 gated 条目
# --------------------------------------------------------------------------- #

_HARNESS_FILES = ("BridgeHarness", "MenuHarness", "PolicyHarness", "LaunchHarness")


def harness_index(root):
    """shell/tests/*Harness.swift → {Harness 名: 正文}（缺席的 harness 不入表）。"""
    out = {}
    for name in _HARNESS_FILES:
        path = os.path.join(root, "shell", "tests", name + ".swift")
        if os.path.exists(path):
            out[name] = read_text(path)
    return out


def harness_pinning(harnesses, literal):
    """哪个 harness 的正文里逐字钉着这个字面量（按固定顺序取第一个）。"""
    for name in _HARNESS_FILES:
        if literal and literal in harnesses.get(name, ""):
            return name
    return ""


def catalog_sections(root):
    """server/settings_catalog.py 的 SECTIONS → [(section, key, field)]（import 真源，不抄）。"""
    if root not in sys.path:
        sys.path.insert(0, root)
    from server import settings_catalog  # noqa: PLC0415  (真源 import，§68)
    out = []
    for section in settings_catalog.SECTIONS:
        for field in section["fields"]:
            out.append((section["id"], field["key"], field))
    return out


def _parity_control_proof(item, harnesses):
    if item.get("owner") == "web":
        return "parity:" + item["id"], None
    if item.get("probe") == "notify_catalog":
        return "axprobe:notify_relay", "shell-owned system notice (server-owned catalog §66.2)"
    pinned = harness_pinning(harnesses, item.get("en") or "")
    if pinned:
        return "swift:" + pinned, None
    return "", "shell-owned control with no harness pin"


def _parity_setting_fallback(item, harnesses):
    """不在 server 设置目录里的键：壳持有的看 harness 有没有钉住它，其余留空（进 gaps）。"""
    if item.get("probe") == "shell_source":
        pinned = harness_pinning(harnesses, '"%s"' % item["key"])
        if pinned:
            return "swift:" + pinned, "shell-held UserDefaults key"
        return "", "shell-held UserDefaults key with no harness pin"
    if item.get("probe") == "server_source":
        return "", "server-side landing is not a settings-catalog key"
    return "", "web localStorage pref, no server/vitest probe"


def _parity_setting_proof(item, key_sections, harnesses):
    key = item["key"]
    if key in key_sections:
        return "settings:%s.%s" % (key_sections[key], key), None
    landing = (item.get("landing") or "").strip('"')
    if landing in key_sections:
        return "settings:%s.%s" % (key_sections[landing], landing), "landing key in the server catalog"
    return _parity_setting_fallback(item, harnesses)


def _parity_row(item, proof, scenario, note=None):
    return make_row("parity:" + item["id"], "parity", scenario, proof, note=note)


def _gated(items):
    return [item for item in items if item.get("gated")]


def _bilingual(item):
    return "%s / %s" % (item.get("zh", ""), item.get("en", ""))


def _control_rows(inventory, harnesses):
    rows = []
    for item in _gated(inventory["controls"]):
        proof, note = _parity_control_proof(item, harnesses)
        rows.append(_parity_row(item, proof, "native control %s (%s) is on the web page"
                                % (item["id"], _bilingual(item)), note))
    return rows


def _notification_rows(inventory):
    return [_parity_row(item, "axprobe:notify_relay",
                        "system notification kind %s is relayed" % (item.get("kind") or "general"))
            for item in _gated(inventory["notifications"])]


def _rail_rows(inventory):
    return [_parity_row(item, "flow:pages_controls",
                        "rail item %s (%s) opens its page" % (item["slug"], _bilingual(item)))
            for item in _gated(inventory["rail"]["items"])]


def _lane_rows(inventory):
    return [_parity_row(item, join_proofs("http:GET /api/lanes expect=200 contains=%s" % item["slug"],
                                          "flow:card_lifecycle"),
                        "board lane %s (%s) is served" % (item["slug"], _bilingual(item)))
            for item in _gated(inventory["lanes"]["items"])]


def _screen_rows(inventory):
    return [_parity_row(item, "flow:pages_controls",
                        "screen %s (%s) renders" % (item["id"], _bilingual(item)))
            for item in _gated(inventory["screens"])]


def _shortcut_rows(inventory):
    return [_parity_row(item, "flow:pages_controls",
                        "shortcut %s on %s fires" % (item.get("key", ""), item.get("screen", "")))
            for item in _gated(inventory["shortcuts"])]


def _theme_rows(inventory):
    return [_parity_row(item, "flow:pages_controls",
                        "theme/layout token %s is consumed" % (item.get("token") or item.get("value", "")))
            for item in _gated(inventory["theme_layout"])]


def _settings_key_rows(inventory, key_sections, harnesses):
    rows = []
    for item in _gated(inventory["settings_keys"]):
        proof, note = _parity_setting_proof(item, key_sections, harnesses)
        rows.append(_parity_row(item, proof, "settings key %s (%s store) is carried"
                                % (item["key"], item.get("store")), note))
    return rows


def parity_rows(root):
    """ui/parity/native-inventory.json 的每个 gated 条目一行（类别顺序固定；最终仍按 id 排序）。"""
    inventory = load_json(os.path.join(root, "ui", "parity", "native-inventory.json"))
    harnesses = harness_index(root)
    key_sections = {key: section for section, key, _field in catalog_sections(root)}
    rows = _control_rows(inventory, harnesses)
    rows += _notification_rows(inventory)
    rows += _rail_rows(inventory)
    rows += _lane_rows(inventory)
    rows += _screen_rows(inventory)
    rows += _settings_key_rows(inventory, key_sections, harnesses)
    rows += _shortcut_rows(inventory)
    rows += _theme_rows(inventory)
    return rows


# --------------------------------------------------------------------------- #
# 3. routes —— server/app.py 的路由表
# --------------------------------------------------------------------------- #

_TABLES = (
    ("_GET_JSON_ROUTES", "GET", False),
    ("_GET_PREFIX_ROUTES", "GET", True),
    ("_POST_JSON_ROUTES", "POST", False),
    ("_POST_PREFIX_ROUTES", "POST", True),
    ("_POST_RAW_ROUTES", "POST", False),
    ("_PUT_JSON_ROUTES", "PUT", False),
    ("_PUT_PREFIX_ROUTES", "PUT", True),
)
# do_GET 里手写的三条（不在表里）：路径 → (scenario, proof)
_SPECIAL_GETS = (
    ("/api/board", "the lane-shaped board projection", "http:GET /api/board expect=200"),
    ("/api/events", "the SSE event stream", ""),
    ("/api/search-index", "the conditional search index (ETag / 304)", "http:GET /api/search-index expect=200"),
)
# 前缀表的尾段占位（{id} 由 runner 从 demo 看板解析；其余是确定的 demo 值）
_PREFIX_TAIL = {
    "/api/cards/": "{id}",
    "/api/settings/": "{section}",
    "/api/logs/": "{log}",
    "/api/ingest/jobs/": "{job}",
    "/api/secrets/": "{name}",
}
_PREFIX_TAIL_POST = {"/api/secrets/": "{name}/verify"}


def _const_route_key(node, root):
    """表的键：字面量直接取；`attachments.ROUTE` 这样的到该模块里取常量。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return _module_constant(root, node.value.id, node.attr)
    return None


def _assigns_str(node, name):
    """`NAME = "字面量"` 形状的模块级赋值。"""
    if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
        return False
    return any(isinstance(target, ast.Name) and target.id == name for target in node.targets)


def _module_constant(root, module, name):
    path = os.path.join(root, "server", module + ".py")
    if not os.path.exists(path):
        return None
    for node in ast.parse(read_text(path)).body:
        if isinstance(node, ast.Assign) and _assigns_str(node, name):
            return node.value.value
    return None


def _handler_modules(node):
    """一条表项的 handler 里引用了哪些 server 子模块（用来找 409 的老家）。"""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
            names.add(sub.value.id)
        elif isinstance(sub, ast.Name):
            names.add(sub.id)
    return names


def _assign_names(node):
    return [target.id for target in node.targets if isinstance(target, ast.Name)]


def _dict_assignments(tree, wanted):
    """模块顶层 `NAME = {…}` → {NAME: ast.Dict}（只收 wanted 里的名字）。"""
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        for name in _assign_names(node):
            if name in wanted:
                out[name] = node.value
    return out


def parse_route_tables(root):
    """server/app.py → [(method, path, prefix?, handler 模块集合)]（表顺序 → 排序后输出）。"""
    tree = ast.parse(read_text(os.path.join(root, "server", "app.py")))
    tables = _dict_assignments(tree, frozenset(name for name, _m, _p in _TABLES))
    out = []
    for name, method, is_prefix in _TABLES:
        table = tables.get(name)
        for key, value in zip(table.keys, table.values) if table else ():
            path = _const_route_key(key, root)
            if path:
                out.append((method, path, is_prefix, _handler_modules(value)))
    return out


def conflict_modules(root):
    """server/*.py 里真的 `raise ConflictError(` 的模块名集合（409 分支行的依据）。"""
    out = set()
    server_dir = os.path.join(root, "server")
    for name in sorted(os.listdir(server_dir)):
        if name.endswith(".py"):
            if "raise ConflictError(" in read_text(os.path.join(server_dir, name)):
                out.add(name[:-3])
    return out


def modules_citing(texts, literal, extra=None):
    """哪些测试模块正文里有这个字面量（extra 里任一词也要在同一文件出现）。"""
    hits = []
    for module in sorted(texts):
        text = texts[module]
        if literal not in text:
            continue
        if extra and not any(word in text for word in extra):
            continue
        hits.append(module)
    return hits


def _shown_path(path, is_prefix):
    """前缀路由显示成带占位的形状（`/api/cards/{id}`）；精确路由原样。"""
    if not is_prefix:
        return path
    return path + _PREFIX_TAIL_POST.get(path, _PREFIX_TAIL.get(path, ""))


def _route_proof(method, path, shown, texts):
    """正路由的 proof：读面直接打；写面交给钉住这条路径的 unittest。

    写面不在 runner 里盲发（`/api/setup/reset`、`/api/uninstall/terminal` 这类会动真家伙），
    只有 401 分支走 http——四闸在读 body 之前就拒，安全。"""
    hits = unittest_proof(modules_citing(texts, path))
    if method == "GET":
        return join_proofs("http:GET %s expect=200" % shown, hits)
    return hits


def _branch_rows(method, shown, row_id, path, texts, conflicted):
    """一条路由的分支行：GET 的 token-light、写面的 401、以及会抛冲突的 409。"""
    out = []
    if method == "GET":
        out.append((("%s#noauth" % row_id), "GET %s stays token-light (§49)" % shown,
                    "http:GET %s noauth expect=200" % shown))
    else:
        out.append((("%s#401" % row_id), "%s %s without a token is 401" % (method, shown),
                    "http:%s %s noauth expect=401" % (method, shown)))
        if conflicted:
            hits = modules_citing(texts, path, extra=("409", "ConflictError", "CONFLICT"))
            out.append((("%s#409" % row_id), "%s %s reports a state conflict as 409" % (method, shown),
                        unittest_proof(hits)))
    return out


def route_rows(root):
    """server/app.py 的路由表 + 手写的三条 GET + 分支行（#noauth / #401 / #409 / #404）。"""
    conflicts = conflict_modules(root)
    texts = test_texts(os.path.join(root, "tests"))
    triples = []
    for path, what, proof in _SPECIAL_GETS:
        triples.append(("route:GET " + path, "GET %s serves %s" % (path, what),
                        join_proofs(proof, unittest_proof(modules_citing(texts, path)))))
        triples.append(("route:GET %s#noauth" % path,
                        "GET %s stays token-light (§49): no token still serves" % path,
                        "http:GET %s noauth expect=200" % path))
    for method, path, is_prefix, modules in parse_route_tables(root):
        shown = _shown_path(path, is_prefix)
        row_id = "route:%s %s" % (method, shown)
        triples.append((row_id, "%s %s is routed" % (method, shown),
                        _route_proof(method, path, shown, texts)))
        triples.extend(_branch_rows(method, shown, row_id, path, texts, bool(modules & conflicts)))
    triples.append(("route:GET /api/__unknown__#404", "an unknown /api path is 404",
                    "http:GET /api/__unknown__ expect=404"))
    seen, rows = set(), []
    for row_id, scenario, proof in triples:
        if row_id not in seen:
            seen.add(row_id)
            rows.append(make_row(row_id, "routes", scenario, proof))
    return rows


# --------------------------------------------------------------------------- #
# 4. settings —— server/settings_catalog.py 的 SECTIONS + 四个特殊模块
# --------------------------------------------------------------------------- #

_SPECIAL_SETTINGS = (
    ("models", "/api/settings/models", "§59 两把模型旋钮"),
    ("recap", "/api/settings/recap", "§63 会议 recap 三把旋钮"),
    ("daily-loop", "/api/settings/daily-loop", "§70 每日自我改进循环五把旋钮"),
    ("display", "/api/settings/display", "§54.1 显示偏好三把旋钮"),
)


def settings_rows(root):
    texts = test_texts(os.path.join(root, "tests"))
    rows = []
    for section, key, field in catalog_sections(root):
        label = (field.get("label") or {}).get("en") or key
        rows.append(make_row("setting:%s.%s" % (section, key), "settings",
                             "%s / %s round-trips (PUT → GET → effective → default)" % (section, label),
                             "settings:%s.%s" % (section, key)))
    for slug, path, what in _SPECIAL_SETTINGS:
        rows.append(make_row("setting:%s.snapshot" % slug, "settings",
                             "GET %s returns %s" % (path, what),
                             join_proofs("http:GET %s expect=200" % path,
                                         unittest_proof(modules_citing(texts, path)))))
        rows.append(make_row("setting:%s.update" % slug, "settings",
                             "PUT %s diff-writes %s" % (path, what),
                             unittest_proof(modules_citing(texts, path, extra=("PUT", "put")))))
    return rows


# --------------------------------------------------------------------------- #
# 5. shell —— shell/Sources/*.swift
# --------------------------------------------------------------------------- #

_MENU_ITEM_RE = re.compile(
    r'Item\(t\("([^"]+)",\s*"([^"]+)"\)(?:,\s*key:\s*"([^"]*)")?(?:,\s*option:\s*(true|false))?')


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"


def menu_items(source):
    """ShellSupport.swift 的 `MenuSpec.menus` 表 → [(zh, en, key, option)]（文件顺序）。"""
    start = source.find("static func menus(")
    if start < 0:
        return []
    end = source.find("\n    /// 把表逐项装成 NSMenu", start)
    body = source[start:end if end > 0 else start + 6000]
    out = []
    for match in _MENU_ITEM_RE.finditer(body):
        zh, en, key, option = match.group(1), match.group(2), match.group(3) or "", match.group(4)
        out.append((zh.replace(" \\(appName)", ""), en.replace(" \\(appName)", ""), key, option == "true"))
    return out


_SHELL_EXTRAS = (
    ("hotkey.quick-capture", "⌃⌥Space focuses the capture box (Carbon global hotkey)",
     "axprobe:hotkey_focus", "QuickCaptureHotkey"),
    ("bridge.quick-capture", "the `quick_capture` shell→web command is in the wire vocabulary",
     "swift:BridgeHarness", "quick_capture"),
    ("bridge.open-page", "the `open_page {page, anchor?}` command opens a board page",
     "axprobe:menu_open_page", "open_page"),
    ("dock.badge", "the Dock badge shows the pending count (nil at zero)",
     "axprobe:dock_badge", "dockTile"),
    ("terminal.takeover", "Apple Events hand a session over to the terminal",
     "axprobe:terminal_takeover", "TerminalLauncher"),
    ("notify.relay", "NotifyRelay drains the queue into system notifications",
     "axprobe:notify_relay", "NotifyRelay"),
    ("login-item.toggle", "the launch-at-login toggle registers / unregisters SMAppService",
     "swift:LaunchHarness", "LaunchAtLogin"),
)


def _menu_chord(key, option):
    """NSMenuItem 的 keyEquivalent → 人读的和弦（`""` = 无快捷键，option = ⌥⌘）。"""
    if not key:
        return ""
    return ("⌥⌘" if option else "⌘") + key.upper()


def _menu_rows(root, harnesses):
    """MenuSpec 的每一项（标题 + key equivalent）一行，判例 = 钉着它的 harness。"""
    support = read_text(os.path.join(root, "shell", "Sources", "ShellSupport.swift"))
    rows = []
    for zh, en, key, option in menu_items(support):
        chord = _menu_chord(key, option)
        slug = "menu." + slugify(en) + ("." + slugify(chord) if chord else "")
        pinned = harness_pinning(harnesses, '"%s"' % en) or "MenuHarness"
        rows.append(make_row("shell:" + slug, "shell",
                             "main-menu item %s / %s%s" % (zh, en, " (%s)" % chord if chord else ""),
                             "swift:" + pinned))
    return rows


def _rail_shortcut_row(item, retired):
    """⌘1–⌘8：MenuSpec 刻意不收（归 web NavRail），退役的侧栏项按归属表的 D 号记 waived。"""
    shortcut = item["shortcut"]
    slug = "shell:shortcut." + slugify(shortcut.replace("⌘", "cmd-"))
    scenario = "%s jumps to the %s page (rail shortcut, web-owned NavRail)" % (shortcut, item["slug"])
    if item.get("gated"):
        return make_row(slug, "shell", scenario, "flow:pages_controls",
                        note="MenuSpec deliberately omits ⌘1–⌘8 (web NavRail owns them)")
    design = re.search(r"\bD(\d+)\b", (retired.get(item["slug"]) or {}).get("reason", ""))
    return make_row(slug, "shell", scenario, "flow:pages_controls", status="waived",
                    waive_reason=("design-not-carried D%s" % design.group(1)) if design else None,
                    note="rail item retired")


def shell_rows(root):
    """壳的菜单表 / 全局快捷键 / Dock 徽标 / open_page / 终端接管 / 通知转发 / 登录项。"""
    harnesses = harness_index(root)
    inventory = load_json(os.path.join(root, "ui", "parity", "native-inventory.json"))
    retired = inventory["attribution"].get("rail_owner", {})
    rows = _menu_rows(root, harnesses)
    rows += [_rail_shortcut_row(item, retired)
             for item in inventory["rail"]["items"] if item.get("shortcut")]
    for slug, scenario, proof, literal in _SHELL_EXTRAS:
        pinned = harness_pinning(harnesses, literal)
        rows.append(make_row("shell:" + slug, "shell", scenario,
                             join_proofs(proof, ("swift:" + pinned) if pinned else "")))
    return rows


# --------------------------------------------------------------------------- #
# 合并 B 档 fixture 行 + 装订
# --------------------------------------------------------------------------- #

def fixtures_b_rows(path):
    """fixtures builder 的 qa/coverage_fixtures_b.json（缺席 = 空）。

    接受两种形状：裸数组，或 {"scenarios": [...]}（同一 schema）。缺字段按 make_row 补齐。"""
    if not os.path.exists(path):
        return []
    doc = load_json(path)
    raw = doc.get("scenarios", doc.get("rows", [])) if isinstance(doc, dict) else doc
    out = []
    for item in raw:
        out.append(make_row(item.get("id", ""), item.get("source", "fixtures"),
                            item.get("scenario", ""), item.get("proof", ""),
                            tier=item.get("tier", "B"), status=item.get("status", "todo"),
                            waive_reason=item.get("waive_reason"), note=item.get("note")))
    return [row for row in out if row["id"]]


def merge_rows(rows, extra):
    """两批行 → 按 id 排序、id 唯一（先到的赢，B 档不许悄悄改写清点出来的行）。"""
    merged = {}
    for row in list(rows) + list(extra):
        merged.setdefault(row["id"], row)
    return [merged[key] for key in sorted(merged)]


def build_rows(root=REPO_ROOT, fixtures_path=FIXTURES_B_PATH):
    rows = []
    rows.extend(contract_rows(root))
    rows.extend(parity_rows(root))
    rows.extend(route_rows(root))
    rows.extend(settings_rows(root))
    rows.extend(shell_rows(root))
    return merge_rows(rows, fixtures_b_rows(fixtures_path))


def document(rows):
    counts = {}
    for row in rows:
        counts[row["source"]] = counts.get(row["source"], 0) + 1
    return {
        "note": "generated by scripts/qa/coverage_inventory.py --write; do not hand-edit",
        "sources": list(SOURCES),
        "counts": dict(sorted(counts.items())),
        "scenarios": rows,
    }


def dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def summary_line(rows):
    missing = sum(1 for row in rows if not row["proof"])
    return "INVENTORY scenarios=%d missing_proof=%d sources=%s" % (
        len(rows), missing, ",".join(SOURCES))


def _write_inventory(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote %s" % path)


def _is_stale(path, text):
    current = read_text(path) if os.path.exists(path) else ""
    if current == text:
        return False
    print("qa/coverage_inventory.json is stale — run "
          "`python3 scripts/qa/coverage_inventory.py --write`")
    return True


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="重铸 qa/coverage_inventory.json")
    parser.add_argument("--check", action="store_true", help="陈旧 → 退 1")
    parser.add_argument("--summary", action="store_true", help="打一行摘要")
    parser.add_argument("--root", default=REPO_ROOT)
    parser.add_argument("--out", default=INVENTORY_PATH)
    parser.add_argument("--fixtures", default=FIXTURES_B_PATH)
    return parser


def _wants_summary(args):
    return args.summary or not (args.write or args.check)


def main(argv=None):
    args = build_parser().parse_args(argv)
    rows = build_rows(args.root, args.fixtures)
    text = dump(document(rows))
    if args.write:
        _write_inventory(args.out, text)
    if _wants_summary(args):
        print(summary_line(rows))
    return 1 if args.check and _is_stale(args.out, text) else 0


if __name__ == "__main__":
    sys.exit(main())
