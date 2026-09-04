#!/usr/bin/env python3
"""UI 对齐门 `[ui-parity]`：原生清单 ui/parity/native-inventory.json ⟷ web 实现。

法典：docs/CONTRACT.md §66.2。owner 2026-09-02：「你不能依靠一个个去看，而是要通过
一些硬指标、硬代码、硬文档来进行保证」——所以每一条清单 id 都有一个机器探针：
  control:*     web/src/parity.test.tsx（vitest，由清单驱动、不手写列表）用 demo
                fixture 渲染每个页面/卡片，按 accessible name / 文本找每个双语标签；
                本脚本以 `--reporter=json` 跑它并读每条 it() 的 pass/fail
  screen:*      zh + en 标题字面量都出现在 web/src 源码（剥注释、排除 *.test.*）
  rail:*        web/src 有 `data-rail-item="<slug>"` + 双语标题；rail:order 要求
                data-rail-item 的出现顺序 = 原生顺序（只数清单里仍 gated 的项——归属表
                RAIL_OWNER 标 retired 的 ask / deps 不在栏上，D29 / D30），且容器带 `data-rail="left"`
  lane:*        双语列名出现在 web/src 或 server/lanes.py；lanes:order 要求 server/lanes.py
                LANES 的 slug 顺序 = 原生顺序；lanes:rail-left/right 要求 BoardLanes.tsx
                的 BacklogStrip 在所有 Lane 之前、ArchiveStrip 在之后
  setting:overrides:<k>   server/settings*.py 出现字面量 "<k>"（server 是 overrides 的写者，§59）
  setting:prefs:<k>       web/src 出现字面量 "<k>"（localStorage 同名键）；清单 probe=shell_source 的
                键（壳持有的 UserDefaults）→ shell/Sources 出现 "<k>"；probe=server_source 的键
                （概念搬到 server）→ 清单 `landing` 字面量出现在 server/*.py
  notification:<kind>     server/notify_catalog.py 的 kind 词表登记了它（general = 无 kind）
  control:notifications:* （probe=notify_catalog，壳直发的系统通知句）→ zh 与 en 都登记在
                server/notify_catalog.py（server-owned 目录），且 shell/Sources 有同一对 L()
  shortcut:*    快捷键字形（如 ⌘F）出现在 web/src 源码
  theme:default web/index.html 首帧脚本写着 `dataset.theme = "light"` 兜底
  layout:*      web/src/**/*.css 某处 `var(--native-layout-…)` 消费该 token

判决 = qa_common.compare_with_ledger 的三态（§58.4 同一语义）：MISSING 且不在
ui/parity/pending.txt、也不在 ui/parity/waivers.txt → NEW → FAIL；在 pending 上但
已 PRESENT → STALE → FAIL（划掉那行）。两本账本只许缩（ledger_diff 同门管）。
非 web 负责的条目（shell / os / retired）与说明性文案（copy / help）只列不判——
例外是清单带 `probe` 的条目（上面三条 shell / server 探针，§66.2 追记）。
报告：ui/parity/report.json + report.md（PRESENT / MISSING / PENDING / WAIVED 计数）。

用法：
    python3 scripts/ui/parity_check.py --check [--report DIR]      # 门（跑 vitest）
    python3 scripts/ui/parity_check.py --check --vitest-json F     # 用现成的 vitest 报告
    python3 scripts/ui/parity_check.py --write-pending             # 重铸 pending（出生 / 清账轮）
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "qa"))
import qa_common  # noqa: E402
import ui_common as uc  # noqa: E402

WEB_DIR = os.path.join(uc.REPO_ROOT, "web")
REPORT_JSON = os.path.join(uc.PARITY_DIR, "report.json")
REPORT_MD = os.path.join(uc.PARITY_DIR, "report.md")
PARITY_TEST = "src/parity.test.tsx"

_TS_COMMENT = re.compile(r"//[^\n]*|/\*[\s\S]*?\*/")
_STRUCTURAL_IDS = ("rail:order", "lanes:order", "lanes:rail-left", "lanes:rail-right")


# --------------------------------------------------------------------------- #
# web / server 源码快照（探针只读这份，一次读完）
# --------------------------------------------------------------------------- #

_SKIP_DIRS = frozenset({"node_modules", "dist", "__pycache__"})


def _wanted(fn, suffixes, skip_tests):
    return fn.endswith(suffixes) and not (skip_tests and ".test." in fn)


def _walk(root, sub, suffixes, skip_tests):
    """root/sub 下的文件 → {repo 相对路径: 文本}（跳过 node_modules/dist 与 *.test.*）。"""
    out = {}
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, sub)):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in sorted(fn for fn in filenames if _wanted(fn, suffixes, skip_tests)):
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
            out[rel] = uc.read_text(os.path.join(dirpath, fn))
    return out


def _load_notify_catalog(root):
    """<root>/server/notify_catalog.py 作为独立模块加载（探针只用 kind_names / has_sentence）；
    缺席 = None（该类探针一律「不在」）。"""
    path = os.path.join(root, "server", "notify_catalog.py")
    if not os.path.exists(path):
        return None
    if uc.REPO_ROOT not in sys.path:
        sys.path.insert(0, uc.REPO_ROOT)   # 目录的 body 引用 act.lib.failures（§25 单源）
    spec = importlib.util.spec_from_file_location("_zai_notify_catalog", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shell_l_pairs(shell_sources):
    pairs = set()
    for text in shell_sources.values():
        stripped, masked = uc.scan_views(text)
        pairs.update((zh, en) for _off, zh, en in uc.find_l_calls(stripped, masked))
    return pairs


class WebSnapshot(object):
    """探针读的一切：web/src 源码（剥注释）、CSS、server 源、index.html、lanes 目录、
    shell/Sources（壳持有的偏好键与 L() 对）、通知目录模块。"""

    def __init__(self, root=uc.REPO_ROOT):
        self.root = root
        web_src = os.path.join("web", "src")
        self.ts = {p: _TS_COMMENT.sub("", t) for p, t in _walk(root, web_src, (".ts", ".tsx"), True).items()}
        self.css = _walk(root, web_src, (".css",), True)
        self.server = _walk(root, "server", (".py",), False)
        shell = _walk(root, os.path.join("shell", "Sources"), (".swift",), False)
        index = os.path.join(root, "web", "index.html")
        self.index_html = uc.read_text(index) if os.path.exists(index) else ""
        self.ts_all = "\n".join(self.ts[p] for p in sorted(self.ts))
        self.server_all = "\n".join(self.server[p] for p in sorted(self.server))
        self.settings_py = "\n".join(t for p, t in sorted(self.server.items())
                                     if os.path.basename(p).startswith("settings"))
        self.lanes_py = self.server.get("server/lanes.py", "")
        self.board_lanes = self.ts.get("web/src/components/board/BoardLanes.tsx", "")
        self.shell_all = "\n".join(shell[p] for p in sorted(shell))
        self.shell_pairs = _shell_l_pairs(shell)
        self.notify = _load_notify_catalog(root)

    def ts_has(self, literal):
        return bool(literal) and literal in self.ts_all

    def css_has(self, needle):
        return any(needle in text for path, text in self.css.items()
                   if not path.endswith("tokens.css"))

    def shell_posts(self, zh, en):
        """壳源码里有同一对 L()（插值只差占位名）。"""
        same = self.notify.same_template if self.notify else (lambda a, b: a == b)
        return any(same(zh, pzh) and same(en, pen) for pzh, pen in self.shell_pairs)


# --------------------------------------------------------------------------- #
# 静态探针（非 control 条目）
# --------------------------------------------------------------------------- #

def _both_labels(snap, item):
    return snap.ts_has(item.get("zh", "")) and snap.ts_has(item.get("en", ""))


def probe_screen(snap, item):
    return _both_labels(snap, item)


def probe_rail_item(snap, item):
    return _both_labels(snap, item) and snap.ts_has('data-rail-item="%s"' % item["slug"])


def probe_lane(snap, item):
    corpus = snap.ts_all + "\n" + snap.lanes_py
    return item["zh"] in corpus and item["en"] in corpus


def probe_setting(snap, item):
    literal = '"%s"' % item["key"]
    if item["store"] == "overrides":
        return literal in snap.settings_py
    probe = item.get("probe")
    if probe == "shell_source":          # 壳自己持有同名 UserDefaults 键
        return literal in snap.shell_all
    if probe == "server_source":         # 概念搬到 server：清单点名的落点字面量
        return bool(item.get("landing")) and item["landing"] in snap.server_all
    return snap.ts_has(literal)


def probe_notification(snap, item):
    """notification:<kind> → server/notify_catalog.py 的 kind 词表（general = 无 kind）。"""
    return snap.notify is not None and (item.get("kind") or "general") in snap.notify.kind_names()


def probe_notice_control(snap, item):
    """壳直发的系统通知句：server-owned 目录登记了这一对 zh / en，且壳源码真的发它。"""
    return (snap.notify is not None and snap.notify.has_sentence(item["zh"], item["en"])
            and snap.shell_posts(item["zh"], item["en"]))


def probe_shortcut(snap, item):
    return snap.ts_has(item["key"])


def probe_theme(snap, item):
    return 'dataset.theme = "%s"' % item["value"] in snap.index_html


def probe_layout(snap, item):
    return snap.css_has("var(--native-%s)" % item["token"].replace(".", "-").replace("_", "-"))


_STATIC_PROBES = {
    "screen": probe_screen, "rail": probe_rail_item, "lane": probe_lane, "setting": probe_setting,
    "shortcut": probe_shortcut, "theme": probe_theme, "layout": probe_layout,
    "notification": probe_notification,
}


def _rail_order_ok(snap, inventory):
    found = re.findall(r'data-rail-item="([\w-]+)"', snap.ts_all)
    expected = [r["slug"] for r in inventory["rail"]["items"] if r.get("gated")]   # 退役的侧栏项不在期望里
    return found == expected and 'data-rail="left"' in snap.ts_all


def _lanes_order_ok(snap, inventory):
    found = re.findall(r'"slug":\s*"(\w+)"', snap.lanes_py)
    return found == inventory["lanes"]["order"]


def _strip_ok(snap, component, before):
    src = snap.board_lanes
    lanes = [m.start() for m in re.finditer(r"<Lane\b", src)]
    pos = src.find("<" + component)
    if pos < 0 or not lanes:
        return False
    return pos < min(lanes) if before else pos > max(lanes)


def structural_items(snap, inventory):
    """rail / lanes 的结构条目（顺序、左右书立条）：id → present。"""
    return {
        "rail:order": _rail_order_ok(snap, inventory),
        "lanes:order": _lanes_order_ok(snap, inventory),
        "lanes:rail-left": _strip_ok(snap, "BacklogStrip", True),
        "lanes:rail-right": _strip_ok(snap, "ArchiveStrip", False),
    }


def _static_probe_for(item):
    """gated 条目的静态探针；web 的 control 走 vitest（None）。"""
    kind = item["id"].split(":", 1)[0]
    if kind != "control":
        return _STATIC_PROBES[kind]
    return probe_notice_control if item.get("probe") == "notify_catalog" else None


def static_presence(snap, inventory):
    """所有静态判的 gated 条目：id → present（bool）。web 的 control 由 vitest 判，不在这里。"""
    presence = {}
    for item in _iter_items(inventory):
        probe = _static_probe_for(item) if item.get("gated") else None
        if probe is not None:
            presence[item["id"]] = bool(probe(snap, item))
    presence.update(structural_items(snap, inventory))
    return presence


def _iter_items(inventory):
    for c in inventory["controls"]:
        yield c
    for group in ("rail", "lanes"):
        for item in inventory[group]["items"]:
            yield item
    for key in ("screens", "settings_keys", "shortcuts", "notifications", "theme_layout"):
        for item in inventory[key]:
            yield item


# --------------------------------------------------------------------------- #
# vitest 探针（control 条目）
# --------------------------------------------------------------------------- #

def default_vitest_runner(web_dir, out_path):
    """真跑 `npx vitest run src/parity.test.tsx --reporter=json`；返回退出码。
    单元测试永不走这里（注入 fake runner）。"""
    cmd = ["npx", "vitest", "run", PARITY_TEST, "--reporter=json", "--outputFile=" + out_path]
    try:
        return subprocess.call(cmd, cwd=web_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return 127


def parse_vitest_report(text):
    """vitest JSON 报告 → {title: status}（title = 清单 id，可带 ` [pending]` 后缀）。"""
    doc = json.loads(text)
    out = {}
    for suite in doc.get("testResults", []):
        for case in suite.get("assertionResults", []):
            out[case.get("title", "")] = case.get("status", "")
    return out


def control_presence(results, pending):
    """it() 结果 → id → present。普通 it 断言「在」：passed = 在；`[pending]` it
    断言「不在」：failed = 其实在（→ STALE）。"""
    presence = {}
    for title, status in results.items():
        if title.endswith(" [pending]"):
            presence[title[:-len(" [pending]")]] = status == "failed"
        elif title.startswith("control:"):
            presence[title] = status == "passed"
    return presence


def vitest_presence(web_dir, pending, vitest_json=None, runner=None):
    """(presence, error)。报告缺席/跑不起来 = error 字符串（门 FAIL，不软化）。"""
    if vitest_json is None:
        vitest_json = os.path.join(web_dir, ".parity-vitest.json")
        rc = (runner or default_vitest_runner)(web_dir, vitest_json)
        if not os.path.exists(vitest_json):
            return {}, "vitest produced no report (rc=%s): node + `npm ci` in web/ are required" % rc
    try:
        results = parse_vitest_report(uc.read_text(vitest_json))
    except (OSError, ValueError) as exc:
        return {}, "unreadable vitest report %s: %s" % (vitest_json, exc)
    return control_presence(results, pending), None


# --------------------------------------------------------------------------- #
# 判决 + 报告
# --------------------------------------------------------------------------- #

def gated_ids(inventory):
    ids = [item["id"] for item in _iter_items(inventory) if item.get("gated")]
    return ids + list(_STRUCTURAL_IDS)


def judge(presence, gated, waivers, pending):
    """scores：缺席 = 1.0（违例）、在场 = 0.0；waived 不入分。返回三态判决。"""
    scores = {}
    for item_id in gated:
        if item_id in waivers:
            continue
        scores[item_id] = 0.0 if presence.get(item_id, False) else 1.0
    ledger = {k: 1.0 for k in pending}
    return qa_common.compare_with_ledger(scores, ledger, threshold=0.0), scores


def _owner_counts(inventory):
    """不判的条目按原因计数：informational（copy/help 文案）或 owner（shell / os / retired）。"""
    counts = {}
    for item in _iter_items(inventory):
        if item.get("gated"):
            continue
        owner = item.get("owner", "web")
        key = "informational" if owner == "web" else owner
        counts[key] = counts.get(key, 0) + 1
    return counts


def _status_of(score, listed):
    """(分数, 是否在 pending) → 四态之一。"""
    if score == 0.0:
        return "STALE" if listed else "PRESENT"
    return "PENDING" if listed else "MISSING"


def build_report(inventory, presence, scores, result, waivers, pending):
    statuses = {item_id: _status_of(score, item_id in pending) for item_id, score in scores.items()}
    statuses.update({item_id: "WAIVED" for item_id in waivers})
    counts = {}
    for status in statuses.values():
        counts[status] = counts.get(status, 0) + 1
    return {
        "inventory_sha256": inventory["source"]["sha256"],
        "counts": counts,
        "not_gated": _owner_counts(inventory),
        "new": result["new"], "stale": result["stale"],
        "items": {k: statuses[k] for k in sorted(statuses)},
        "ok": result["ok"],
    }


def _group_counts(report):
    groups = {}
    for item_id, status in report["items"].items():
        kind = item_id.split(":", 1)[0]
        bucket = groups.setdefault(kind, {})
        bucket[status] = bucket.get(status, 0) + 1
    return groups


def render_markdown(report):
    c = report["counts"]
    lines = ["# UI parity report (CONTRACT §66)", "",
             "Inventory `ui/parity/native-inventory.json` sha256 `%s`." % report["inventory_sha256"][:12], "",
             "| status | count |", "|---|---|"]
    lines += ["| %s | %d |" % (k, c.get(k, 0)) for k in ("PRESENT", "PENDING", "MISSING", "STALE", "WAIVED")]
    lines += ["", "Not gated: " + ", ".join("%s %d" % kv for kv in sorted(report["not_gated"].items())), "",
              "| kind | " + " | ".join(("PRESENT", "PENDING", "MISSING", "STALE", "WAIVED")) + " |",
              "|---|---|---|---|---|---|"]
    for kind, bucket in sorted(_group_counts(report).items()):
        lines.append("| %s | %s |" % (kind, " | ".join(
            str(bucket.get(s, 0)) for s in ("PRESENT", "PENDING", "MISSING", "STALE", "WAIVED"))))
    lines += ["", "Verdict: **%s**" % ("OK" if report["ok"] else "FAIL")]
    lines += _list_block("NEW (missing, not in pending.txt — implement, never enroll)", report["new"])
    lines += _list_block("STALE (present but still in pending.txt — strike the line)", report["stale"])
    return "\n".join(lines) + "\n"


def _list_block(title, ids):
    if not ids:
        return []
    return ["", "## " + title, ""] + ["- `%s`" % i for i in ids]


def write_pending(path, presence, gated, waivers):
    """出生 / 清账：当前所有 MISSING（不含 waived）→ pending.txt。"""
    missing = sorted(i for i in gated if i not in waivers and not presence.get(i, False))
    header = ("# ui/parity/pending.txt —— 尚未搬到 web 的原生 UI 条目（shrink-only，CONTRACT §66.2）。\n"
              "# 只许缩：新缺项 FAIL（补实现，不许记账）；已补齐仍挂账 FAIL（划掉这行）。\n"
              "# 每行：<inventory-id>。重铸：python3 scripts/ui/parity_check.py --write-pending（清账轮以外别用）。\n")
    uc.write_text(path, header + "".join(i + "\n" for i in missing))
    return len(missing)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def collect_presence(inventory, snap, pending, web_dir, vitest_json=None, runner=None):
    presence = static_presence(snap, inventory)
    controls, error = vitest_presence(web_dir, pending, vitest_json, runner)
    presence.update(controls)
    return presence, error


def _print_verdict(result, report, error):
    print("[ui-parity] gated %d: PRESENT %d, PENDING %d, MISSING(new) %d, STALE %d, WAIVED %d" % (
        len(report["items"]), report["counts"].get("PRESENT", 0), report["counts"].get("PENDING", 0),
        report["counts"].get("MISSING", 0), report["counts"].get("STALE", 0), report["counts"].get("WAIVED", 0)))
    for item_id in result["new"]:
        print("  NEW: %s" % item_id)
    for item_id in result["stale"]:
        print("  STALE: %s" % item_id)
    if error:
        print("  ERROR: %s" % error)
    print("[ui-parity] %s" % ("OK" if result["ok"] and not error else "FAIL"))


def run(args, runner=None):
    inventory = uc.load_json(args.inventory)
    waivers = uc.load_ledger(args.waivers)
    pending = uc.load_ledger(args.pending)
    snap = WebSnapshot(args.root)
    presence, error = collect_presence(inventory, snap, pending, args.web_dir, args.vitest_json, runner)
    gated = gated_ids(inventory)
    if args.write_pending:
        print("wrote %d pending item(s) to %s" % (write_pending(args.pending, presence, gated, waivers),
                                                   uc.display_path(args.pending)))
        pending = uc.load_ledger(args.pending)
    result, scores = judge(presence, gated, waivers, pending)
    report = build_report(inventory, presence, scores, result, waivers, pending)
    uc.write_text(args.report_json, uc.dump_json(report))
    uc.write_text(args.report_md, render_markdown(report))
    if args.report:
        qa_common.write_report(args.report, "ui_parity_verdict.txt", render_markdown(report))
    _print_verdict(result, report, error)
    return 0 if result["ok"] and not error else 1


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-pending", action="store_true")
    parser.add_argument("--vitest-json", default=None, help="现成的 vitest JSON 报告（不再跑 vitest）")
    parser.add_argument("--report", metavar="DIR")
    parser.add_argument("--inventory", default=uc.INVENTORY_PATH)
    parser.add_argument("--waivers", default=uc.WAIVERS_PATH)
    parser.add_argument("--pending", default=uc.PENDING_PATH)
    parser.add_argument("--web-dir", default=WEB_DIR)
    parser.add_argument("--root", default=uc.REPO_ROOT, help="仓库根（探针读 web/ server/ 的位置）")
    parser.add_argument("--report-json", default=REPORT_JSON)
    parser.add_argument("--report-md", default=REPORT_MD)
    return parser


def main(argv=None, runner=None):
    args = build_parser().parse_args(argv)
    return run(args, runner)


if __name__ == "__main__":
    sys.exit(main())
