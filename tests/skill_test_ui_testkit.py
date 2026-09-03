"""test-ui skill 判例共用件：把 skills/test-ui/scripts 挂进 sys.path、造 fixture mini-repo、造最小
detection dict、可脚本化的 FakeRunner（绝不真起子进程）、最小清单 / tokens 文档 / 合成 PNG。

法典指针：docs/CONTRACT.md §58（skill 只读项目阈值）、§62（parity 契约 id 语法）；设计 vnext2-plan R2.8 / D14。
"""
import os
import shutil
import sys

_SKILL_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "test-ui", "scripts")
if _SKILL_SCRIPTS not in sys.path:
    sys.path.insert(0, _SKILL_SCRIPTS)

import ladder_common_vendored as lc  # noqa: E402
import testui_common as tc  # noqa: E402

SKILL_SCRIPTS = _SKILL_SCRIPTS
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "test_ui")


def make_repo(root, files):
    """{relpath: text} → 落盘；返回 root。目录自动创建。"""
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return root


def copy_fixture(name, dest):
    """tests/fixtures/test_ui/<name>/ → dest（整目录拷贝）。"""
    shutil.copytree(os.path.join(FIXTURES, name), dest, dirs_exist_ok=True)
    return dest


def side(role="subject", kind="dir", locator="/r", mode=None, **extra):
    record = {"role": role, "kind": kind, "locator": locator, "resolved": "path:/r", "stack": "web-dom",
              "mode": mode or {"structure": "source", "tokens": "source", "visual": "na"}, "inventory": None,
              "tokens": None, "goldens": None, "launch": None, "produced_by": [], "hint": None, "commit": "abc", "dirty": False}
    record.update(extra)
    return record


def fake_det(files, repo="/r", surfaces=None, reference=None, **overrides):
    """static-html 单面项目的最小 detection dict；overrides 逐键覆盖顶层。"""
    html = [f for f in files if f.endswith(".html")]
    det = {
        "schemaVersion": 1, "skill": {"name": "test-ui", "version": tc.SKILL_VERSION}, "repo": repo, "is_git": False,
        "python": sys.executable, "files": sorted(files),
        "surfaces": surfaces if surfaces is not None else ([{"kind": "static-html", "root": ".", "files": len(html)}] if html else []),
        "web_dir": None, "lang": "zh",
        "tokens_files": {"css": [f for f in files if f.endswith("tokens.css")], "index_html": None, "type_scale": None,
                         "component_dirs": []},
        "tools": {"node": None, "npx": None, "playwright": None, "playwright_bin": False, "axe": None, "odiff": None, "git": None},
        "adapters": {}, "config": {}, "config_source": None, "config_base": None,
        "ledgers": {"dir": None, "parsed": {"pending": {}, "waivers": {}, "aliases": {}, "dir": None, "texts": {}}, "base_texts": None},
        "goldens": {"dir": None, "machine_key": "test-chromium-dpr1", "machine_dir": None}, "dims": {},
        "thresholds": dict(__import__("parity").DEFAULT_THRESHOLDS), "thresholds_base": None,
        "sides": {"subject": side(), "reference": reference or side("reference", "dir", "/ref")},
        "against": "dir:/ref", "candidates": [], "runtime_hint": "runtime UNAVAILABLE — missing: playwright module (test)",
        "diff": {"base": None, "base_commit": None, "changed_files": [], "added_text": {}, "untracked": []},
        "triggers": [], "recommendation": {"tier": 1, "reason": "test", "screens": []}, "menu": [],
    }
    det.update(overrides)
    return det


def make_item(screen, role, name, kind=None, visible=True, hidden_by=None, focusable=None, pin=None, count=1,
              parent="window", order=0, side_=None, gated=False, owner="web", ordinal=1, level=None):
    """schemaVersion 1 条目（id 用 parity 语法）。"""
    slug = tc.slugify(name) if name else ("unnamed" if role in tc.INTERACTIVE_ROLES else role)
    kind = kind or ("interactive" if role in tc.INTERACTIVE_ROLES else "landmark" if role in tc.LANDMARK_ROLES
                    else "heading" if role == "heading" else "static")
    id_kind = "landmark" if role in tc.LANDMARK_ROLES else "heading" if role == "heading" else "control"
    item_id = tc.make_id(id_kind, screen, role, slug) + ("#%d" % ordinal if ordinal > 1 else "")
    return {"id": item_id, "key": {"screen": tc.screen_family(screen), "role": role, "slug": slug}, "kind": kind,
            "name": {"raw": name, "zh": None, "en": None, "alt": []}, "name_source": "text", "pin": pin, "owner": owner,
            "gated": gated, "shortcut": None, "count": count,
            "topology": {"parent": parent, "order": order, "side": side_},
            "states": {"source": {"visible": visible, "hidden_by": hidden_by,
                                  "focusable": (role in tc.INTERACTIVE_ROLES and visible) if focusable is None else focusable}},
            "screen": screen, "source": {"file": "x.html", "line": 1}, "evidence": "source", "level": level}


def make_inventory(items, mode="source", role="subject", **extra):
    inv = tc.empty_inventory("test", mode, "testkit", {"role": role, "kind": "dir", "locator": "/x"})
    inv["items"] = list(items)
    inv["names"] = sorted({i["name"]["raw"] for i in items if i["name"]["raw"]})
    inv.update(extra)
    return inv


def make_tokens(themes, declared=None, literals=None):
    """{theme: {path: value}} → tokens 文档；颜色/尺寸按值形状自动定 $type。"""
    doc = {"schemaVersion": 1, "producer": tc.producer("test", "source", "testkit"),
           "default_theme": {"declared": declared or {"mode": "system", "fallback": "light", "evidence": []}, "observed": None},
           "themes": {}, "families": {}, "geometry": {}, "literals_outside": list(literals or []), "type_scale": {}}
    for theme, table in themes.items():
        doc["themes"][theme] = {}
        for path, value in table.items():
            kind = "color" if tc.parse_color(str(value)) else ("dimension" if str(value).endswith("px") else "string")
            doc["themes"][theme][path] = {"$type": kind, "$value": tc.canonical_color(value) if kind == "color" else value,
                                          "var": "--" + path.split(".", 1)[-1].replace(".", "-")}
    return doc


def make_png(width, height, color=(200, 200, 200), blocks=()):
    """纯色 PNG + 若干 [x, y, w, h, (r,g,b)] 色块 → bytes。"""
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            pixel = color
            for bx, by, bw, bh, bc in blocks:
                if bx <= x < bx + bw and by <= y < by + bh:
                    pixel = bc
            row += bytes(pixel)
        rows.append(bytes(row))
    return tc.encode_png(width, height, rows, 3)


class FakeRunner(object):
    """脚本化 runner：rules = [(match, response)]。match = argv 子串或 predicate(argv)；
    response = (rc, stdout, stderr) / RunResult / callable(argv, cwd) → RunResult。"""

    def __init__(self, rules=None, default=(0, "", "")):
        self.rules = list(rules or [])
        self.default = default
        self.calls = []

    @staticmethod
    def _matches(match, argv):
        if callable(match):
            return match(argv)
        return match in " ".join(str(a) for a in argv)

    @staticmethod
    def _respond(resp, argv, cwd):
        if callable(resp):
            return resp(argv, cwd)
        if isinstance(resp, lc.RunResult):
            return resp
        return lc.RunResult(*resp)

    def __call__(self, argv, cwd=None, timeout=None, env=None):
        self.calls.append({"argv": [str(a) for a in argv], "cwd": cwd, "timeout": timeout})
        for match, resp in self.rules:
            if self._matches(match, argv):
                return self._respond(resp, argv, cwd)
        return self._respond(self.default, argv, cwd)

    def commands(self):
        return [" ".join(c["argv"]) for c in self.calls]


def git_ok_rules(tracked=(), untracked=(), head="deadbeef" * 5, diff_text="", names=""):
    """让 FakeRunner 扮演一个干净的 git。"""
    return [
        ("git ls-files --others", (0, "\n".join(untracked), "")),
        ("git ls-files", (0, "\n".join(tracked), "")),
        ("git rev-parse --verify --quiet origin/main", (0, "90ceb713" * 5, "")),
        ("git rev-parse HEAD", (0, head, "")),
        ("git merge-base", (0, "cafebabe" * 5, "")),
        ("git diff --name-only", (0, names, "")),
        ("git diff -U0", (0, diff_text, "")),
        ("git status --porcelain", (0, "", "")),
        ("git show", (1, "", "not found")),
        ("git --version", (0, "git version 2.0", "")),
        ("node -e", (1, "", "Cannot find module")),
    ]
