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
  http:<METHOD> <path> [noauth] [ctype=<media>] expect=<code> [contains=<substr>] ·
  settings:<section>.<key> · flow:<name> · fixture:<slug>；多条用 " && " 串联（全中才算在）。

两条 route 侧的口径（demo 种子打不到的那一半，别把 http 行写成许不起的愿）：
  * **占位解析不出真对象**（`/api/settings/{section}` · `/api/logs/{log}` ·
    `/api/ingest/jobs/{job}`：demo 里没有这样的 section/log/job）→ 快乐路径交给钉着这条
    路径字面量的 unittest（与写面同一口径，见 _route_proof），http 行只留 demo 真打得到的
    那一支：`__absent__` → 404（`/api/logs/` 的占位带 `.log`，否则 LOG_NAME_RE 先给 400）。
  * **写面 401 分支**：Content-Type 闸排在 token 闸之前（server/app.py `_check_write_auth`），
    所以二进制体路由（`_POST_RAW_ROUTES`）的 401 行必须带 `ctype=<登记的 media type>`，
    否则先撞 415。media type 从 app.py 的表里读，不手抄。

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
# §66.2 的「有意不搬到 web」账本（shrink-only）：这些 id 的 parity 判卷面发的是 it.skip，
# 清单里必须记 waived，否则报告永远挂一条「vitest report has no it()」的假 MISSING。
WAIVERS_REL = os.path.join("ui", "parity", "waivers.txt")
_WAIVE_D_RE = re.compile(r"\bD(\d+)\b")
_LEDGER_COLS_RE = re.compile(r"\s{2,}")
_ISSUE_RE = re.compile(r"#(\d+)")


def waive_reason_for(reason):
    """账本理由原文 → 允许的 waive_reason（brief §2 的三选一），认不出来返回 None。

    先认 `D<nn>`（vnext2 台账 / 归属表的决策号）→ `design-not-carried D<nn>`；再认理由里
    点名的 CONTRACT tombstone → `tombstone`。**都没有就返回 None**——凭空发明一个 D 号
    比挂一条 MISSING 更坏（那会把「没人拍过板」写成「拍过板」）。"""
    text = reason or ""
    design = _WAIVE_D_RE.search(text)
    if design:
        return "design-not-carried D%s" % design.group(1)
    if "tombstone" in text:
        return WAIVE_TOMBSTONE
    return None


def parity_waivers(root):
    """ui/parity/waivers.txt → {inventory-id: 理由原文}（`#` 开头是注释；列以 2+ 空格分隔）。"""
    path = os.path.join(root, WAIVERS_REL)
    out = {}
    if not os.path.exists(path):
        return out
    for raw in read_text(path).split("\n"):
        line = raw.strip()
        if line and not line.startswith("#"):
            cols = _LEDGER_COLS_RE.split(line)
            out[cols[0]] = "  ".join(cols[1:])
    return out


def _issue_reasons(ledger):
    """{issue 号: 已经认出来的 waive_reason}（账本顺序，先到的赢 → 与文件一样确定）。"""
    out = {}
    for reason in ledger.values():
        mapped = waive_reason_for(reason)
        for issue in _ISSUE_RE.findall(reason) if mapped else ():
            out.setdefault(issue, mapped)
    return out


def _inherit_reason(reason, inherited):
    """理由本身认不出来时：按它点名的 issue 号借同一本账本里已经认出来的那条。"""
    for issue in _ISSUE_RE.findall(reason or ""):
        if issue in inherited:
            return inherited[issue]
    return None


def waiver_reasons(root):
    """ui/parity/waivers.txt → {id: waive_reason}；认不出理由的 id **不入表**（仍是 todo）。

    同一个退役决策常常有好几个面，而账本里只有第一行写全了出处（#119 那一批四行：第一行
    点名 `CONTRACT §39 tombstone`，其余三行只写「retired with #119」）。所以认不出来的行按
    它点名的 issue 号继承同一本账本里已经认出来的那条——不是发明 D 号，是把同一条决策的
    四个面记成同一个理由；账本里真的谁都没写出处的 id 照样留 todo，由报告诚实地挂 MISSING。"""
    ledger = parity_waivers(root)
    inherited = _issue_reasons(ledger)
    out = {}
    for row_id, reason in ledger.items():
        mapped = waive_reason_for(reason) or _inherit_reason(reason, inherited)
        if mapped:
            out[row_id] = mapped
    return out


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
        # 静态通知文案 = server-owned catalog，判例确定性钉死（§66.2 / §77.4）；
        # 活体 NotifyRelay 排空是项 [3] 的 axprobe:notify_relay，不混进确定性 A 档。
        return "unittest:tests.test_server_notify_catalog", "server-owned notice catalog (§66.2)"
    pinned = harness_pinning(harnesses, item.get("en") or "")
    if pinned:
        return "swift:" + pinned, None
    return "", "shell-owned control with no harness pin"


_PARITY_TEST_REL = os.path.join("web", "src", "parity.test.tsx")
_PARITY_IT_RE = re.compile(r'it\("(setting:prefs:[A-Za-z0-9_]+)"')
# probe=server_source 的键（概念搬到 server 侧的状态文件，§68.5）：落点文件 → 哪个快照露出它。
# runner 的 http executor 只认 `contains=<不含空格的子串>`，所以直接找 JSON 里的键名字面量。
_SERVER_LANDING_PROOF = {
    "setup_done.json": 'http:GET /api/setup expect=200 contains="done"',
}


def parity_vitest_ids(root):
    """web/src/parity.test.tsx 里以清单 id 为标题的 it()（`it("setting:prefs:<key>"` 形状）。

    这些 it 驱动真控件写 localStorage 键再读回来；runner 一次 vitest 就把它们判了
    （scripts/qa/coverage_run.py 的 `parity:` executor 复用 parity_check.control_presence）。"""
    path = os.path.join(root, _PARITY_TEST_REL)
    if not os.path.exists(path):
        return frozenset()
    return frozenset(_PARITY_IT_RE.findall(read_text(path)))


def _shell_pref_proof(item, harnesses):
    """壳持有的 UserDefaults 键：看哪个 harness 正文里逐字钉着它。"""
    pinned = harness_pinning(harnesses, '"%s"' % item["key"])
    if pinned:
        return "swift:" + pinned, "shell-held UserDefaults key"
    return "", "shell-held UserDefaults key with no harness pin"


def _server_pref_proof(item):
    """概念搬到 server 的键（probe=server_source）：落点文件 → 露出它的那个快照 GET。"""
    landing = (item.get("landing") or "").strip('"')
    proof = _SERVER_LANDING_PROOF.get(landing)
    if proof:
        return proof, "server-side landing %s, read back from the snapshot" % landing
    return "", "server-side landing is not a settings-catalog key"


def _web_pref_proof(item, vitest_ids):
    """web 自有的 localStorage 偏好键：parity.test.tsx 里有没有同名 it() 驱动真控件往返。"""
    if item["id"] in vitest_ids:
        return "parity:" + item["id"], "web localStorage pref pinned by a vitest it()"
    return "", "web localStorage pref, no server/vitest probe"


def _parity_setting_fallback(item, harnesses, vitest_ids):
    """不在 server 设置目录里的键：按清单的 probe 分派到三条兜底探针（都可能留空 → 进 gaps）。"""
    probe = item.get("probe")
    if probe == "shell_source":
        return _shell_pref_proof(item, harnesses)
    if probe == "server_source":
        return _server_pref_proof(item)
    return _web_pref_proof(item, vitest_ids)


def _parity_setting_proof(item, key_sections, harnesses, vitest_ids):
    key = item["key"]
    if key in key_sections:
        return "settings:%s.%s" % (key_sections[key], key), None
    landing = (item.get("landing") or "").strip('"')
    if landing in key_sections:
        return "settings:%s.%s" % (key_sections[landing], landing), "landing key in the server catalog"
    return _parity_setting_fallback(item, harnesses, vitest_ids)


def _parity_row(item, proof, scenario, note=None):
    return make_row("parity:" + item["id"], "parity", scenario, proof, note=note)


def _gated(items):
    return [item for item in items if item.get("gated")]


def _bilingual(item):
    return "%s / %s" % (item.get("zh", ""), item.get("en", ""))


def _apply_waiver(row, mapped):
    """账本里记了「有意不搬」的那一行 → waived（理由认不出来的不在表里，原样留 todo）。"""
    if mapped is None:
        return row
    row["status"] = "waived"
    row["waive_reason"] = mapped
    row["note"] = "ui/parity/waivers.txt (§66.2); parity 判卷面发 it.skip"
    return row


def _control_rows(inventory, harnesses, waivers):
    rows = []
    for item in _gated(inventory["controls"]):
        proof, note = _parity_control_proof(item, harnesses)
        row = _parity_row(item, proof, "native control %s (%s) is on the web page"
                          % (item["id"], _bilingual(item)), note)
        rows.append(_apply_waiver(row, waivers.get(item["id"])))
    return rows


def _notification_rows(inventory):
    return [_parity_row(item, "unittest:tests.test_server_notify_catalog",
                        "system notification kind %s is in the catalog" % (item.get("kind") or "general"))
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


def _settings_key_rows(inventory, key_sections, harnesses, vitest_ids):
    rows = []
    for item in _gated(inventory["settings_keys"]):
        proof, note = _parity_setting_proof(item, key_sections, harnesses, vitest_ids)
        rows.append(_parity_row(item, proof, "settings key %s (%s store) is carried"
                                % (item["key"], item.get("store")), note))
    return rows


def parity_rows(root):
    """ui/parity/native-inventory.json 的每个 gated 条目一行（类别顺序固定；最终仍按 id 排序）。"""
    inventory = load_json(os.path.join(root, "ui", "parity", "native-inventory.json"))
    harnesses = harness_index(root)
    key_sections = {key: section for section, key, _field in catalog_sections(root)}
    rows = _control_rows(inventory, harnesses, waiver_reasons(root))
    rows += _notification_rows(inventory)
    rows += _rail_rows(inventory)
    rows += _lane_rows(inventory)
    rows += _screen_rows(inventory)
    rows += _settings_key_rows(inventory, key_sections, harnesses, parity_vitest_ids(root))
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
# 占位解析不出真对象的前缀 GET（demo 种子里没有这样的 section/log/job）：http 行只留 demo
# 真打得到的那一支——尾段给一个绝不存在的名字，看它是不是 404。`/api/logs/` 的占位必须带
# `.log`：server/diagnostics.LOG_NAME_RE 先判形，形不对是 400 而不是 404。
_ABSENT_TAIL = {
    "/api/settings/": "__absent__",
    "/api/logs/": "__absent__.log",
    "/api/ingest/jobs/": "__absent__",
}
# 带必填 query 的精确 GET：§63.9 的 recap 历史必须点名 key（不点名 = 400）。`{recap}` 由
# runner 从 demo 种子解析，同 `{id}`。
_GET_QUERY = {"/api/recaps/history": "?key={recap}"}


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


def _raw_media(value, root):
    """`(attachments.CONTENT_TYPE, MAX_BYTES, fn)` 元组 → 第一位登记的 media type。"""
    if isinstance(value, ast.Tuple) and value.elts:
        return _const_route_key(value.elts[0], root)
    return None


def raw_media_types(root):
    """server/app.py 的 `_POST_RAW_ROUTES` → {path: media type}（二进制体路由，§10bis 贴图）。"""
    tree = ast.parse(read_text(os.path.join(root, "server", "app.py")))
    table = _dict_assignments(tree, frozenset({"_POST_RAW_ROUTES"})).get("_POST_RAW_ROUTES")
    out = {}
    for key, value in zip(table.keys, table.values) if table else ():
        path = _const_route_key(key, root)
        media = _raw_media(value, root)
        if path and media:
            out[path] = media
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


def _get_http_proof(path, shown):
    """一条 GET 的 http 片段：占位解析不出对象 → 打 `__absent__` 的 404 那一支；
    必填 query 的路由点名带上（§63.9 的 `?key=`）；其余照旧是 200。"""
    tail = _ABSENT_TAIL.get(path)
    if tail:
        return "http:GET %s%s expect=404" % (path, tail)
    return "http:GET %s%s expect=200" % (shown, _GET_QUERY.get(path, ""))


def _noauth(fragment):
    """同一条 http 片段的「不带 token」版：§49 的 GET 是 token-light，status 应当一模一样。"""
    return fragment.replace(" expect=", " noauth expect=", 1)


def _write_401_fragment(method, shown, media):
    """写面 401 行：二进制体路由要带上登记的 Content-Type，否则先撞 415（闸序见模块 docstring）。"""
    return "http:%s %s noauth%s expect=401" % (
        method, shown, (" ctype=%s" % media) if media else "")


def _route_proof(method, path, shown, texts):
    """正路由的 proof：读面直接打；写面（以及占位解析不出对象的读面）交给钉住这条路径的 unittest。

    写面不在 runner 里盲发（`/api/setup/reset`、`/api/uninstall/terminal` 这类会动真家伙），
    只有 401 分支走 http——四闸在读 body 之前就拒，安全。"""
    hits = unittest_proof(modules_citing(texts, path))
    if method == "GET":
        return join_proofs(_get_http_proof(path, shown), hits)
    return hits


def _branch_rows(method, shown, row_id, path, texts, conflicted, media=""):
    """一条路由的分支行：GET 的 token-light、写面的 401、以及会抛冲突的 409。"""
    out = []
    if method == "GET":
        out.append((("%s#noauth" % row_id), "GET %s stays token-light (§49)" % shown,
                    _noauth(_get_http_proof(path, shown))))
    else:
        out.append((("%s#401" % row_id), "%s %s without a token is 401" % (method, shown),
                    _write_401_fragment(method, shown, media)))
        if conflicted:
            hits = modules_citing(texts, path, extra=("409", "ConflictError", "CONFLICT"))
            out.append((("%s#409" % row_id), "%s %s reports a state conflict as 409" % (method, shown),
                        unittest_proof(hits)))
    return out


def route_rows(root):
    """server/app.py 的路由表 + 手写的三条 GET + 分支行（#noauth / #401 / #409 / #404）。"""
    conflicts = conflict_modules(root)
    media_types = raw_media_types(root)
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
        triples.extend(_branch_rows(method, shown, row_id, path, texts,
                                    bool(modules & conflicts), media_types.get(path, "")))
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
    return make_row(slug, "shell", scenario, "flow:pages_controls", status="waived",
                    waive_reason=waive_reason_for((retired.get(item["slug"]) or {}).get("reason", "")),
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
        if pinned:
            # 确定性覆盖 = 钉着它的 harness；活体 axprobe 是项 [3]（§77.4）的现场确认
            rows.append(make_row("shell:" + slug, "shell", scenario, "swift:" + pinned,
                                 note=("live probe %s = 项[3] shell_ui_probe（§77.4）" % proof)
                                 if proof.startswith("axprobe:") else None))
        elif proof.startswith("axprobe:"):
            # 纯活体壳探针，无确定性 harness：A 档不承载，记 waived（现场跑在项 [3]，需 owner FDA）
            rows.append(make_row("shell:" + slug, "shell", scenario, proof,
                                 status="waived", waive_reason="design-not-carried D79",
                                 note="live-shell probe = 项[3]（§77.4，需 owner FDA 现场确认）"))
        else:
            rows.append(make_row("shell:" + slug, "shell", scenario, proof))
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
