#!/usr/bin/env python3
"""README 真实性审计：把 README.md 的每条主张判成 ok / stale（CONTRACT §58 + §77.5）。

法典：docs/CONTRACT.md §58（质量仪表与合并硬门——本脚本是同一族的一道观测门）、
§77.5（本门的判据）。v2.0.0：§66 的 UI 对齐清单（`ui/parity/native-inventory.json`，
标签的第一真源）随 mac/ 退役，标签只对 web/src 与设置目录的文案源判。
判据三类（缺一不可，全部机械判定）：
  1. **路径**——主张里出现的仓库路径必须存在（运行时生成的路径按前缀白名单放行）。
  2. **退役面**——问问助手 / Ask 页、iMessage 通道、原生菜单栏 app UI、被当成
     「现在就这么做」的 mac/ 构建指令、lite 2.0 退役的面（实时字幕、雷达、看板卡片 /
     kanban、.pkg、自动部署、actd、`claude --bg` 派发），以及非当前 tag 的版本字面量
     （§56.1：版本真源是 main 上的 git tag，README 里写死旧版本 = 过期主张）。
  3. **UI 标签**——主张里引号括起来的界面文案必须真的被 web app 渲染：查
     web/src 与 server/settings_catalog.py 的文案源；`--render` 时改用 Playwright
     真渲染出来的文本兜底判定。

v2.0.0 的两条放行（§77.5 追记）：
  - **一个**围栏「Removed in 2.0」块（围栏语言写 `removed`，或块内第一行以
    `Removed in 2.0` / `2.0 删除` 开头）：块里列的是已经退役的东西，所以路径轴、退役轴
    （含版本字面量）与标签轴都不判它；第二个这样的块 = stale（只许一处说「删了什么」）。
  - **`full` 标签 / 分支**（1.x 的归档点）：提到 `full` 的主张里，本树不存在的路径改到
    `full` 那棵树上查（`full:<path>` 写法同理）；本地没有 `full` 这个 ref 时视为无法判、
    放行（与 tag 拿不到时整条版本轴不判同理）。

用法：
    python3 scripts/qa/readme_audit.py --summary
    python3 scripts/qa/readme_audit.py --out qa/coverage-report/readme-audit.md
    python3 scripts/qa/readme_audit.py --check          # stale > 0 → exit 1
    python3 scripts/qa/readme_audit.py --summary --render   # 额外跑真渲染

stdlib-only（跑在 owner 机器的 /usr/bin/python3 3.9 与 CI 上）。判例：
tests/test_readme_audit.py。
"""

import argparse
import glob
import json
import os
import re
import shlex
import subprocess
import sys

REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
DEFAULT_OUT = os.path.join("qa", "coverage-report", "readme-audit.md")
IMAGE_DIR = "docs/images"

# 运行时才出生的路径（安装/构建/守护进程写的），README 提到它们不算过期主张。
RUNTIME_PREFIXES = (
    "state/", "act/registry/", "config/secrets/", "config/runtime.json",
    "config.yaml", "web/dist", "web/node_modules", "node_modules/",
    "ingest/vault/", "qa/coverage-report/", "shell/build", "mac/build",
)
# 路径形状：带扩展名或带斜杠；这些扩展名之外的裸词（`actd`、`npm ci`）不当路径。
_PATH_EXTS = (
    "py", "sh", "ts", "tsx", "js", "md", "json", "yaml", "yml", "toml", "ps1",
    "swift", "png", "jpg", "gif", "mp4", "plist", "webmanifest", "txt", "css",
)
_EXT_RE = re.compile(r"\.(%s)$" % "|".join(_PATH_EXTS), re.I)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\]\(([^)\s]+)")
_HTML_REF_RE = re.compile(r'(?:src|href)="([^"]+)"')
_IMAGE_RE = re.compile(r"docs/images/[\w.\-]+\.png")
_VERSION_RE = re.compile(r"\bv\d+\.\d+(?:\.\d+)?(?:\.x)?\b")

# 1.x 的归档 ref（branch + annotated tag，lite2-plan L16）。
FULL_REF = "full"
# 「Removed in 2.0」块的认法：围栏语言 = removed，或块内第一行以这两种开头。
_REMOVED_FENCE_LANG = "removed"
_REMOVED_HEAD_RE = re.compile(r"^(?:Removed in 2\.0|2\.0 删除)", re.I)
# 主张提到 `full` 这个 ref 的写法：反引号 `full`、tag/branch full、`full:<path>`。
_FULL_REF_RE = re.compile(r"`full`|\b(?:tag|branch)\s+`?full\b|标签\s*`?full\b|\bfull:[\w.]", re.I)

# 退役面（§27 tombstone / D3 / §56.1 / lite 2.0 的 tombstone）。每条 = (正则, 判词)。
RETIRED_PATTERNS = (
    (re.compile(r"问问助手|\bAsk page\b|\?page=ask", re.I),
     "retired surface: 问问助手 / Ask page (CONTRACT §27 tombstone, D29)"),
    (re.compile(r"iMessage", re.I),
     "retired surface: iMessage radar / transport (removed in v0.21)"),
    (re.compile(r"menu[- ]bar|menubar|菜单栏|MenuBarExtra", re.I),
     "retired surface: native Mac menu-bar app UI (D3 — the board is the UI)"),
    (re.compile(r"mac/(?:Sources|scripts|package\.sh|build)"),
     "retired surface: mac/ native app build instructions (D3, app retiring)"),
    (re.compile(r"\bSwiftUI\b.*(?:app|window|kanban)", re.I),
     "retired surface: SwiftUI app presented as current (D3)"),
    (re.compile(r"live[- ]captions?|实时字幕", re.I),
     "retired surface: live captions (v2.0.0, §36 tombstone, lite2-plan L2)"),
    (re.compile(r"\b(?:slack|gmail|obsidian|claude[- ]session)s?\s+radars?\b|\bradars?\b|雷达", re.I),
     "retired surface: the Act radars (v2.0.0, §48 tombstone)"),
    (re.compile(r"\bkanban\b|看板卡片|卡片详情|approval card|审批卡|\bcard lanes?\b", re.I),
     "retired surface: the Act board and its cards (v2.0.0, §1/§37 tombstones)"),
    (re.compile(r"\.pkg\b|\bpkg installer\b", re.I),
     "retired surface: the .pkg installer (v2.0.0, §74.1/§74.2 tombstones)"),
    (re.compile(r"auto[- ]deploy|自动部署", re.I),
     "retired surface: auto-deploy (v2.0.0, §56.3 tombstone; upgrades are §79)"),
    (re.compile(r"\bactd\b|claude --bg", re.I),
     "retired surface: the Act daemon / claude --bg dispatch (v2.0.0, §3/§5 tombstones)"),
)
# UI 语境词：只有在这些词旁边的引号文案才当成 UI 标签来判。
_UI_CONTEXT_RE = re.compile(
    r"button|toggle|switch|lane|column|page|tab|click|check\b|checkbox|menu|"
    r"panel|section|label|screen|rail|card|chip|badge|settings|按钮|开关|列|页|"
    r"点击|标签|卡片",
    re.I,
)
_QUOTED_RE = re.compile(r'"([^"\n]{1,40})"|“([^”\n]{1,40})”|「([^」\n]{1,40})」')
# 引号里的这些东西不是 UI 标签（配置键 `telemetry.enabled`、路径、URL 片段）：
# 带点 / 斜杠 / 下划线的连写标识符一律不判——UI 文案是人读的词，不是键名。
_NOT_A_LABEL_RE = re.compile(r"^\s*$|://|^[\w./\-]*[./_][\w./\-]*$")


class Claim(object):
    """README 的一条主张（一句话 / 一个 bullet / 一行命令 / 一行表格）。"""

    def __init__(self, index, kind, line, text):
        self.index = index
        self.kind = kind
        self.line = line
        self.text = text
        self.verdict = "ok"
        self.reasons = []
        self.block = 0      # 第几个 Removed in 2.0 块（0 = 不在块里）

    def mark(self, reason):
        self.verdict = "stale"
        if reason not in self.reasons:
            self.reasons.append(reason)

    def as_dict(self):
        return {"index": self.index, "kind": self.kind, "line": self.line,
                "text": self.text, "verdict": self.verdict,
                "reason": "; ".join(self.reasons)}


# --------------------------------------------------------------------------- #
# 抽取：README → claims
# --------------------------------------------------------------------------- #

def _split_sentences(text):
    """按句号/问号/感叹号切句；不切 `3.9`、`v1.0.114`、`e.g.` 这类点。"""
    parts = re.split(r"(?<=[.!?。！？])\s+(?=[A-Z(\[`*\u4e00-\u9fff])", text)
    return [p.strip() for p in parts if p.strip()]


def _is_noise(line):
    """徽章、锚点、纯 HTML 壳、注释——没有可判的主张。"""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return True
    if stripped.startswith("[![") or stripped.startswith("<!--"):
        return True
    if re.match(r"^</?(p|br|sub|div|center)\b[^>]*>$", stripped, re.I):
        return True
    return re.match(r"^\|[\s\-:|]+\|$", stripped) is not None


def _fence_language(stripped, fence):
    """```开合：返回新的围栏状态（None = 已经出了围栏）。"""
    if fence is not None:
        return None
    return stripped[3:].strip() or "sh"


def _fenced_kind(fence):
    """围栏的 claim kind：mermaid 是画（diagram），Removed in 2.0 块是 removed，其余当命令（code）。"""
    if fence == "mermaid":
        return "diagram"
    if fence == _REMOVED_FENCE_LANG:
        return "removed"
    return "code"


def _fenced_claims(stripped, fence):
    """围栏内一行 → [(kind, text)]。"""
    if not stripped or stripped.startswith("#"):
        return []
    return [(_fenced_kind(fence), stripped)]


def _plain_claims(stripped):
    """围栏外一行 → [(kind, text)]：bullet / table / media 各算一条，散文按句切。"""
    if _is_noise(stripped):
        return []
    if re.match(r"^[-*]\s+", stripped):
        return [("bullet", stripped)]
    if stripped.startswith("|"):
        return [("table", stripped)]
    if "<img" in stripped or stripped.startswith("!["):
        return [("media", stripped)]
    return [("prose", sentence) for sentence in _split_sentences(stripped)]


class _FenceState(object):
    """extract_claims 的围栏状态：当前围栏语言、是否刚开围栏、见过几个 Removed in 2.0 块。"""

    def __init__(self):
        self.fence = None
        self.fresh = False      # 刚开了一个围栏、还没见到第一行内容
        self.blocks = 0

    def toggle(self, stripped):
        """```开合。"""
        self.fence = _fence_language(stripped, self.fence)
        self.fresh = self.fence is not None
        if self.fence == _REMOVED_FENCE_LANG:
            self.blocks += 1

    def see(self, stripped):
        """围栏内第一行以 Removed in 2.0 开头 → 整块按 removed 判（§77.5 追记）。"""
        if self.fence is None or not stripped:
            return
        if self.fresh and _REMOVED_HEAD_RE.match(stripped):
            self.fence = _REMOVED_FENCE_LANG
            self.blocks += 1
        self.fresh = False

    def pairs(self, stripped):
        if self.fence is not None:
            return _fenced_claims(stripped, self.fence)
        return _plain_claims(stripped)


def extract_claims(text):
    """README 正文 → Claim 列表（bullet / table / code / diagram / media / prose / removed）。

    removed = 「Removed in 2.0」围栏块里的行（§77.5 追记）；每行带上它属于第几个这样的块
    （``Claim.block``），:func:`check_removed_blocks` 据此只放行第一个。"""
    claims = []
    state = _FenceState()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            state.toggle(stripped)
            continue
        state.see(stripped)
        for kind, body in state.pairs(stripped):
            claim = Claim(len(claims) + 1, kind, lineno, body)
            claim.block = state.blocks if kind == "removed" else 0
            claims.append(claim)
    return claims


# --------------------------------------------------------------------------- #
# 判据 1：路径
# --------------------------------------------------------------------------- #

def _shell_tokens(text):
    """一行命令切成 token（带引号的整段算一个，切不动就退回空白切分）。"""
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def _raw_path_tokens(text, kind):
    """可能是路径的原始片段：反引号里的命令 token、md 链接、html src|href。"""
    raw = []
    for match in _INLINE_CODE_RE.finditer(text):
        raw.extend(_shell_tokens(match.group(1)))
    raw.extend(_LINK_RE.findall(text))
    raw.extend(_HTML_REF_RE.findall(text))
    if kind == "code":
        raw.extend(_shell_tokens(text))
    return raw


def _looks_like_path(token):
    """URL / 家目录 / 绝对路径不判；带斜杠或带已知扩展名的才当仓库路径。"""
    if re.match(r"^(https?:|mailto:|#|\?|~|/|\$|\.\.)", token) or "://" in token:
        return False
    if "/" in token:
        return True
    return bool(_EXT_RE.search(token))


def _candidate_paths(text, kind="prose"):
    """主张里长得像仓库路径的片段。

    自由散文里的 `a/b` 不当路径——「launchd/cron」「Slack/Gmail」是英文顿挫，
    不是文件；真要断言一条路径，README 的写法一律是反引号或链接。"""
    out = []
    for item in _raw_path_tokens(text, kind):
        token = item.strip().strip("\"'`,;:。，、()[]").rstrip(".")
        if token not in out and _looks_like_path(token):
            out.append(token)
    return out


_BASENAMES = {}
_SKIP_DIRS = {".git", "node_modules", "dist", ".venv", "__pycache__", "build"}


def _basenames(repo_root):
    """仓库里所有文件名（裸文件名引用如 `install.sh` 用它兜底）。"""
    cached = _BASENAMES.get(repo_root)
    if cached is not None:
        return cached
    names = set()
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        names.update(files)
    _BASENAMES[repo_root] = names
    return names


def _path_exists(repo_root, token):
    target = token.rstrip("/")
    if any(token.startswith(prefix) for prefix in RUNTIME_PREFIXES):
        return True
    if "*" in target:
        return bool(glob.glob(os.path.join(repo_root, target)))
    if os.path.exists(os.path.join(repo_root, target)):
        return True
    return "/" not in target and target in _basenames(repo_root)


_FULL_TREE = {}


def _git_ok(repo_root, *args):
    try:
        proc = subprocess.run(["git", "-C", repo_root] + list(args),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              check=False)
    except OSError:
        return False
    return proc.returncode == 0


def _is_git_toplevel(repo_root):
    """repo_root 自己就是一个 git checkout 的根（不向上找到别人的仓库）。"""
    try:
        proc = subprocess.run(["git", "-C", repo_root, "rev-parse", "--show-toplevel"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    except OSError:
        return False
    top = proc.stdout.decode("utf-8", "replace").strip()
    return proc.returncode == 0 and os.path.realpath(top) == os.path.realpath(repo_root)


def full_ref_exists(repo_root):
    """本地有没有 `full` 这个 ref（tag 或 branch）；没有 = full 那棵树无法查。"""
    cached = _FULL_TREE.get(repo_root)
    if cached is None:
        cached = _is_git_toplevel(repo_root) and _git_ok(
            repo_root, "rev-parse", "--verify", "--quiet", FULL_REF + "^{commit}")
        _FULL_TREE[repo_root] = cached
    return cached


def _path_in_full(repo_root, path):
    """路径在 `full` 那棵树里在不在；本地没有 full ref = 无法判，放行。"""
    if not full_ref_exists(repo_root):
        return True
    return _git_ok(repo_root, "cat-file", "-e", "%s:%s" % (FULL_REF, path.rstrip("/")))


def _full_object_path(token):
    """`full:<path>` 写法 → path；别的 token → None。"""
    if token.startswith(FULL_REF + ":"):
        return token[len(FULL_REF) + 1:]
    return None


def _token_ok(repo_root, token, mentions_full):
    """一条路径 token 成不成立：`full:<path>` 只查 full；提到 full 的主张里本树没有的也查 full。"""
    full_path = _full_object_path(token)
    if full_path is not None:
        return _path_in_full(repo_root, full_path)
    if _path_exists(repo_root, token):
        return True
    return mentions_full and _path_in_full(repo_root, token)


def check_paths(repo_root, claim):
    if claim.kind in ("diagram", "removed"):
        return
    mentions_full = bool(_FULL_REF_RE.search(claim.text))
    for token in _candidate_paths(claim.text, claim.kind):
        if not _token_ok(repo_root, token, mentions_full):
            where = " (nor at tag %s)" % FULL_REF if mentions_full or _full_object_path(token) else ""
            claim.mark("path does not exist: %s%s" % (token, where))


def check_removed_blocks(claims):
    """只许一个「Removed in 2.0」块（§77.5 追记）：第二个及以后的块里每一行都 stale。"""
    for claim in claims:
        if claim.kind == "removed" and claim.block > 1:
            claim.mark("only one 'Removed in 2.0' block is allowed (§77.5); this is block %d"
                       % claim.block)


# --------------------------------------------------------------------------- #
# 判据 2：退役面与版本字面量
# --------------------------------------------------------------------------- #

def current_tag(repo_root):
    """当前版本真源 = 仓库里最新的 git tag（§56.1）；拿不到就不判版本。"""
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "tag", "--list", "v*", "--sort=-v:refname"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    except OSError:
        return None
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        if line.strip():
            return line.strip()
    return None


def _check_version_literals(claim, tag):
    """版本字面量只许等于当前 tag；tag 拿不到（浅 clone / 无 tag）就不判。"""
    if not tag:
        return
    for match in _VERSION_RE.finditer(claim.text):
        literal = match.group(0)
        if literal == tag:
            continue
        claim.mark("version literal %s is not the current tag %s (§56.1: the tag "
                   "is the only version truth)" % (literal, tag))


def check_retired(claim, tag):
    if claim.kind == "removed":
        return
    for pattern, reason in RETIRED_PATTERNS:
        if pattern.search(claim.text):
            claim.mark(reason)
    _check_version_literals(claim, tag)


# --------------------------------------------------------------------------- #
# 判据 3：UI 标签
# --------------------------------------------------------------------------- #

def _source_corpus(repo_root):
    chunks = []
    for rel in ("web/index.html", "server/settings_catalog.py"):
        path = os.path.join(repo_root, rel)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                chunks.append(handle.read())
    web_src = os.path.join(repo_root, "web", "src")
    for root, _dirs, files in os.walk(web_src):
        for name in files:
            if name.endswith((".ts", ".tsx", ".css")):
                with open(os.path.join(root, name), "r", encoding="utf-8",
                          errors="replace") as handle:
                    chunks.append(handle.read())
    return "\n".join(chunks)


def label_corpus(repo_root):
    """web/src + 设置目录的文案，拼成一个查找面（§66 清单那一半随 v2.0.0 退役）。"""
    return _source_corpus(repo_root)


_HTML_ATTR_RE = re.compile(r'\s[\w:-]+="[^"]*"')


def _quoted_text(match):
    for group in match.groups():
        if group:
            return group.strip()
    return ""


def _is_ui_label(label):
    return bool(label) and not _NOT_A_LABEL_RE.search(label)


def candidate_labels(text):
    """主张里引号括起来、且处在 UI 语境里的文案（= 要去比对界面的标签）。"""
    if not _UI_CONTEXT_RE.search(text):
        return []
    # HTML 属性值（align="center"、width="760"）不是 UI 文案，先摘掉再找引号。
    text = _HTML_ATTR_RE.sub(" ", text)
    out = []
    for match in _QUOTED_RE.finditer(text):
        label = _quoted_text(match)
        if _is_ui_label(label) and label not in out:
            out.append(label)
    return out


def check_labels(claim, corpus_lower, rendered_lower=None):
    if claim.kind in ("code", "diagram", "removed"):
        return
    for label in candidate_labels(claim.text):
        needle = label.lower()
        if needle in corpus_lower:
            if rendered_lower is not None and needle not in rendered_lower:
                claim.mark("UI label %r is in the sources but was not rendered "
                           "by the running web app" % label)
            continue
        claim.mark("UI label %r is not rendered by the web app (not in web/src "
                   "nor in the settings catalog)" % label)


# --------------------------------------------------------------------------- #
# 真渲染（--render）
# --------------------------------------------------------------------------- #

def rendered_text(repo_root, pages=("", "?page=ingest", "?page=settings",
                                    "?page=about", "?page=permissions")):
    """Playwright 打开 demo server 的每一页，返回可见文本（失败 = 抛 RuntimeError）。"""
    script = os.path.join(repo_root, "scripts", "qa", "readme_render.mjs")
    if not os.path.exists(script):
        raise RuntimeError("renderer helper missing: %s" % script)
    proc = subprocess.run(["node", script, ",".join(pages)], cwd=repo_root,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=False)
    if proc.returncode != 0:
        raise RuntimeError("render failed (%s): %s" % (
            proc.returncode, proc.stderr.decode("utf-8", "replace")[-800:]))
    return proc.stdout.decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# 审计与输出
# --------------------------------------------------------------------------- #

class Report(object):
    def __init__(self, claims, images):
        self.claims = claims
        self.images = images

    @property
    def stale(self):
        return [c for c in self.claims if c.verdict == "stale"]

    def summary(self):
        return "README claims=%d stale=%d images=%d" % (
            len(self.claims), len(self.stale), len(self.images))


def referenced_images(text):
    """README 引用到的 docs/images/*.png（去重、排序）。"""
    return sorted(set(_IMAGE_RE.findall(text)))


def audit(repo_root, readme_text=None, tag=None, rendered=None):
    if readme_text is None:
        with open(os.path.join(repo_root, "README.md"), "r",
                  encoding="utf-8") as handle:
            readme_text = handle.read()
    if tag is None:
        tag = current_tag(repo_root)
    corpus_lower = label_corpus(repo_root).lower()
    rendered_lower = rendered.lower() if rendered is not None else None
    claims = extract_claims(readme_text)
    for claim in claims:
        check_paths(repo_root, claim)
        check_retired(claim, tag)
        check_labels(claim, corpus_lower, rendered_lower)
    check_removed_blocks(claims)
    return Report(claims, referenced_images(readme_text))


def _cell(text):
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(report, tag):
    lines = [
        "# README audit",
        "",
        "Generated by `scripts/qa/readme_audit.py` (CONTRACT §58 / §77.5). "
        "Version truth = git tag `%s`." % (tag or "<unknown>"),
        "",
        report.summary(),
        "",
        "| # | line | kind | verdict | reason | claim |",
        "|---|---|---|---|---|---|",
    ]
    for claim in report.claims:
        text = claim.text if len(claim.text) <= 160 else claim.text[:157] + "…"
        lines.append("| %d | %d | %s | %s | %s | %s |" % (
            claim.index, claim.line, claim.kind, claim.verdict,
            _cell("; ".join(claim.reasons)) or "-", _cell(text)))
    lines.append("")
    return "\n".join(lines)


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=REPO_ROOT)
    parser.add_argument("--summary", action="store_true",
                        help="print only the one-line summary (no stale detail)")
    parser.add_argument("--out", nargs="?", const=DEFAULT_OUT, default=None,
                        help="write the markdown table (default %s)" % DEFAULT_OUT)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when any claim is stale")
    parser.add_argument("--render", action="store_true",
                        help="also verify UI labels against a Playwright render")
    parser.add_argument("--tag", default=None, help="override the version truth")
    parser.add_argument("--json", action="store_true", help="dump claims as JSON")
    return parser


def _write_report(report, out, repo_root, tag):
    out_path = out if os.path.isabs(out) else os.path.join(repo_root, out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report, tag))


def _print_stale(report):
    for claim in report.stale:
        print("STALE line %d: %s\n    %s" % (
            claim.line, "; ".join(claim.reasons), claim.text[:160]),
            file=sys.stderr)


def _emit(report, args):
    """stdout：--json 给 JSON，否则恒是那一行 summary；stale 明细走 stderr。"""
    if args.json:
        print(json.dumps([c.as_dict() for c in report.claims],
                         ensure_ascii=False, indent=2))
        return
    print(report.summary())
    if not args.summary:
        _print_stale(report)


def main(argv=None):
    args = _build_parser().parse_args(argv)
    repo_root = os.path.abspath(args.repo)
    rendered = rendered_text(repo_root) if args.render else None
    tag = args.tag if args.tag is not None else current_tag(repo_root)
    report = audit(repo_root, tag=tag, rendered=rendered)
    if args.out:
        _write_report(report, args.out, repo_root, tag)
    _emit(report, args)
    return _exit_code(args, report)


def _exit_code(args, report):
    if args.check and report.stale:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
