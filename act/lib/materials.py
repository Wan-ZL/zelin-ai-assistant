"""素材库 — owner 主动扔进来的链接/备注：追加式台账 + 状态机 + 循环用的内容获取。

法典：docs/CONTRACT.md §62（owner 决策 D11；需求 R2.5.1–R2.5.4）。

三块，全部 stdlib：

1. **台账** ``state/materials/materials.jsonl``（``ledger_path(home)``）——
   append-only，一行一条完整记录 ``{id, ts, created_at, url, note, status,
   links}``；同一 id 的后一行覆盖前一行（fold），读侧按 id 折叠。超过
   ``LEDGER_MAX_BYTES`` 时自压缩：先折叠，再从最老的终态条目（done /
   dismissed）开始丢，直到装回上限；**开放条目永不被压缩丢掉**（宪法第 2
   条），台账的体量由 ``MAX_OPEN_ITEMS`` × 单条字段上限封顶。跨进程写者
   （server 线程 + 未来的每日循环）经 ``flock`` 串行（Windows 无 fcntl 时
   退化为无锁追加）。
2. **状态机** ``new → picked_up → proposal_created → pr_opened → done``，
   任何非终态可 → ``dismissed``；``dismissed → new`` 是回程票；表 =
   ``TRANSITIONS``。只有 ``transition()`` 改状态；``links`` 只增不删。
3. **内容获取** ``fetch(url)``：YouTube 走 yt-dlp 字幕（装了才用）否则
   oEmbed 标题；网页走 stdlib html→text，字节/字符双上限。网络与子进程都
   是**参数注入缝**（``fetcher`` / ``runner`` / ``which``），单元测试零出网。
   进 prompt 的唯一入口是 ``prompt_block()``：owner 备注与抓取内容各自过
   ``sanitize.fence_untrusted``（宪法第 5 条）。

本模块**永不铸卡**——素材是 owner 的意图（hand 级信任），成不成提案由每日
循环（P5）决定；§45 屏幕内容的裁决表与本模块无关。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from act.lib import sanitize

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows：无 flock，退化为无锁追加
    fcntl = None  # type: ignore[assignment]

# --------------------------------------------------------------------------- #
# 词表与上限（wire 侧逐字镜像：web/src/types.ts MaterialItem）
# --------------------------------------------------------------------------- #
STATUSES = ("new", "picked_up", "proposal_created", "pr_opened", "done", "dismissed")
OPEN_STATUSES = frozenset({"new", "picked_up", "proposal_created"})
TERMINAL_STATUSES = frozenset({"done", "dismissed"})
# 状态机（§62.3）。dismissed → new = 回程票（宪法第 2 条：放弃可撤销）；
# picked_up → new = 循环放回（读取后没能生成提案，下一轮再看）。
TRANSITIONS = {
    "new": frozenset({"picked_up", "dismissed"}),
    "picked_up": frozenset({"proposal_created", "new", "dismissed"}),
    "proposal_created": frozenset({"pr_opened", "done", "dismissed"}),
    "pr_opened": frozenset({"done", "dismissed"}),
    "done": frozenset(),
    "dismissed": frozenset({"new"}),
}
LINK_KEYS = ("proposal_id", "pr_url")
MAX_URL_CHARS = 2048
MAX_NOTE_CHARS = 2000
MAX_OPEN_ITEMS = 500
LEDGER_MAX_BYTES = 1 << 20  # 与 registry_writes.jsonl 同款 1MB 自压缩
ID_RE = re.compile(r"^m-[0-9a-f]{12}$")
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


class MaterialsError(ValueError):
    """台账/状态机拒绝：``code`` ∈ invalid | not_found | bad_transition | full。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def ledger_path(home) -> Path:
    return Path(home) / "state" / "materials" / "materials.jsonl"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime(_TS_FMT)


def _new_id() -> str:
    return "m-" + uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- #
# 字段归一
# --------------------------------------------------------------------------- #
def _is_http(parts) -> bool:
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def normalize_url(raw) -> str:
    """空串合法（纯备注）；否则必须是 http(s) 绝对地址且 ≤ MAX_URL_CHARS。"""
    url = str(raw or "").strip()
    if not url:
        return ""
    if not _is_http(urlsplit(url)) or len(url) > MAX_URL_CHARS:
        raise MaterialsError("invalid", "url must be an http(s) address of at most %d characters"
                             % MAX_URL_CHARS)
    return url


def normalize_note(raw) -> str:
    note = str(raw or "").strip()
    if "\x00" in note or len(note) > MAX_NOTE_CHARS:
        raise MaterialsError("invalid", "note must be at most %d characters" % MAX_NOTE_CHARS)
    return note


# --------------------------------------------------------------------------- #
# 台账读写
# --------------------------------------------------------------------------- #
@contextmanager
def _locked(path: Path):
    if fcntl is None:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _parse_line(line: str) -> Optional[dict]:
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    ok = isinstance(rec, dict) and isinstance(rec.get("id"), str) and rec.get("status") in STATUSES
    return rec if ok else None


def _read_lines(path: Path) -> list:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        rec = _parse_line(line)
        if rec is not None:
            out.append(rec)
    return out


def _fold(records: Iterable[dict]) -> dict:
    """同 id 后行覆盖前行；dict 保留首次出现顺序（= 创建顺序）。"""
    folded: dict = {}
    for rec in records:
        folded[rec["id"]] = rec
    return folded


def _encode(items: Iterable[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in items)


def _ts_key(rec: dict):
    return (str(rec.get("ts", "")), rec["id"])


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _trim_terminal(open_items: list, terminal: list) -> list:
    """丢最老的终态条目（terminal 已按 ts 升序）直到整份编码 ≤ 上限；开放条目永不丢。"""
    while terminal and len(_encode(open_items + terminal).encode("utf-8")) > LEDGER_MAX_BYTES:
        terminal = terminal[1:]
    return open_items + terminal


def _compact_locked(path: Path) -> None:
    folded = list(_fold(_read_lines(path)).values())
    open_items = [r for r in folded if r["status"] not in TERMINAL_STATUSES]
    terminal = sorted((r for r in folded if r["status"] in TERMINAL_STATUSES), key=_ts_key)
    survivors = {r["id"] for r in _trim_terminal(open_items, terminal)}
    # 重写保持折叠顺序（= 创建顺序）：读侧的同秒并列排序靠它
    _atomic_write(path, _encode(r for r in folded if r["id"] in survivors))


def compact(path: Path) -> None:
    """折叠 + 终态裁剪（append 超限时自动调用；也可手动）。"""
    with _locked(path):
        _compact_locked(path)


def _append(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    if path.stat().st_size > LEDGER_MAX_BYTES:
        _compact_locked(path)


def _status_filter(status: str) -> frozenset:
    if status == "open":
        return OPEN_STATUSES
    if status == "all":
        return frozenset(STATUSES)
    if status in STATUSES:
        return frozenset({status})
    raise MaterialsError("invalid", "unknown status filter %r" % (status,))


# --------------------------------------------------------------------------- #
# 公开 API（server 与每日循环共用）
# --------------------------------------------------------------------------- #
def list_items(path: Path, status: str = "open") -> list:
    """折叠后的条目，最新创建的在前。``status``：open（弹窗用：尚未开 PR /
    完成 / 放弃）| all | 单个状态名。"""
    keep = _status_filter(status)
    folded = list(_fold(_read_lines(path)).values())  # 折叠顺序 = 创建顺序
    picked = [(i, r) for i, r in enumerate(folded) if r["status"] in keep]
    # created_at 同秒并列时按台账顺序（后加的在前）——不靠随机 id 决胜
    picked.sort(key=lambda ir: (str(ir[1].get("created_at", "")), ir[0]), reverse=True)
    return [r for _i, r in picked]


def get(path: Path, item_id: str) -> Optional[dict]:
    return _fold(_read_lines(path)).get(item_id)


def open_count(path: Path) -> int:
    return sum(1 for r in _fold(_read_lines(path)).values() if r["status"] in OPEN_STATUSES)


def add(path: Path, *, url="", note="", clock: Callable[[], str] = _now_iso,
        ident: Callable[[], str] = _new_id) -> dict:
    """新条目（status=new）。url 或 note 至少一个非空；开放条目达上限时拒绝（full）。"""
    url = normalize_url(url)
    note = normalize_note(note)
    if not url and not note:
        raise MaterialsError("invalid", "url or note is required")
    with _locked(path):
        if open_count(path) >= MAX_OPEN_ITEMS:
            raise MaterialsError("full", "materials box is full (%d open items) — dismiss some first"
                                 % MAX_OPEN_ITEMS)
        ts = clock()
        rec = {"id": ident(), "ts": ts, "created_at": ts, "url": url, "note": note,
               "status": "new", "links": {}}
        _append(path, rec)
    return rec


def _merge_links(old, new) -> dict:
    merged = dict(old or {})
    merged.update({k: str(v) for k, v in (new or {}).items() if k in LINK_KEYS})
    return merged


def transition(path: Path, item_id: str, status: str, *, links=None,
               clock: Callable[[], str] = _now_iso) -> dict:
    """按 TRANSITIONS 表改状态并可附 links（proposal_id / pr_url，只增不删）。"""
    if status not in STATUSES:
        raise MaterialsError("invalid", "unknown status %r" % (status,))
    with _locked(path):
        current = get(path, item_id)
        if current is None:
            raise MaterialsError("not_found", "no material %r" % (item_id,))
        if status not in TRANSITIONS[current["status"]]:
            raise MaterialsError("bad_transition", "%s → %s is not allowed"
                                 % (current["status"], status))
        rec = dict(current)
        rec["ts"] = clock()
        rec["status"] = status
        rec["links"] = _merge_links(current.get("links"), links)
        _append(path, rec)
    return rec


# --------------------------------------------------------------------------- #
# 内容获取（每日循环消费；网络/子进程全部注入缝）
# --------------------------------------------------------------------------- #
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com",
                           "music.youtube.com", "youtu.be"})
OEMBED_ENDPOINT = "https://www.youtube.com/oembed"
SUB_LANGS = "en,en-orig,zh-Hans,zh-Hant,zh"
_LANG_PREF = ("en", "en-orig", "zh-Hans", "zh", "zh-Hant")
MAX_FETCH_BYTES = 2 << 20
MAX_TEXT_CHARS = 20_000
MAX_TITLE_CHARS = 300
FETCH_TIMEOUT = 60
USER_AGENT = "zelin-ai-assistant-materials/1"
_CHARSET_RE = re.compile(r'charset=["\']?([A-Za-z0-9_.:-]+)', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template", "iframe"})
_BLOCK_TAGS = frozenset({
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th",
    "table", "section", "article", "header", "footer", "nav", "main", "aside", "blockquote",
    "pre", "dd", "dt", "figure", "figcaption", "hr",
})


class _Deps:
    """fetch() 的注入缝打包：fetcher(url, timeout) → (content_type, bytes)；
    runner = subprocess.run 形状；which = shutil.which 形状。"""

    def __init__(self, fetcher, runner, which, timeout) -> None:
        self.fetcher = fetcher or _http_get
        self.runner = runner or _subprocess_run
        self.which = which or shutil.which
        self.timeout = timeout


def _subprocess_run(argv, **kwargs):
    # 调用时才查 subprocess.run——tests/__init__.py 的守卫壳照常拦截
    return subprocess.run(argv, **kwargs)  # argv 由 ytdlp_argv 构造，无 shell


def _http_get(url: str, timeout: float):
    req = Request(url, headers={"User-Agent": USER_AGENT,
                                "Accept": "text/html,text/plain;q=0.9,*/*;q=0.5"})
    with urlopen(req, timeout=timeout) as resp:  # classify() 已限 http(s)
        ctype = str(resp.headers.get("Content-Type", ""))
        body = resp.read(MAX_FETCH_BYTES + 1)
    return ctype, body


def classify(url) -> str:
    """youtube | web | unsupported（非 http(s) 绝对地址）。"""
    parts = urlsplit(str(url).strip())
    if not _is_http(parts):
        return "unsupported"
    return "youtube" if (parts.hostname or "").lower() in YOUTUBE_HOSTS else "web"


def _collapse_ws(text: str) -> str:
    """行内空白折成一个空格、空行全部吃掉——正文只进 prompt，不需要排版。"""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _set_text(result: dict, text: str, truncated_bytes: bool) -> None:
    clean = _collapse_ws(text)
    result["truncated"] = bool(truncated_bytes or len(clean) > MAX_TEXT_CHARS)
    result["text"] = clean[:MAX_TEXT_CHARS]


def _decode(body: bytes, ctype: str) -> str:
    m = _CHARSET_RE.search(ctype) or _CHARSET_RE.search(body[:4096].decode("ascii", "ignore"))
    enc = m.group(1) if m else "utf-8"
    try:
        return body.decode(enc, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list = []
        self.parts: list = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str):
    """(title, body_text)——stdlib HTMLParser；script/style 等整棵跳过，块级标签换行。"""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    title = _collapse_ws(" ".join(parser.title_parts))
    return title[:MAX_TITLE_CHARS], "".join(parser.parts)


def _fetch_web(result: dict, deps: _Deps) -> None:
    ctype, body = deps.fetcher(result["url"], deps.timeout)
    media = ctype.split(";")[0].strip().lower()
    text = _decode(body, ctype)
    if media in ("", "text/html", "application/xhtml+xml"):
        result["title"], text = html_to_text(text)
        result["source"] = "html"
    elif media.startswith("text/"):
        result["source"] = "text"
    else:
        result["error"] = "unsupported content-type %s" % media
        text = ""
    _set_text(result, text, len(body) > MAX_FETCH_BYTES)


def ytdlp_argv(bin_path: str, url: str, outdir) -> list:
    """字幕（人工 + 自动，en/zh）落 outdir，标题打到 stdout 第一行；不下载视频。"""
    return [bin_path, "--skip-download", "--no-simulate", "--print", "title",
            "--write-subs", "--write-auto-subs", "--sub-langs", SUB_LANGS,
            "--sub-format", "vtt", "--no-playlist", "--no-warnings", "--no-progress",
            "-o", "%(id)s.%(ext)s", "-P", str(outdir), url]


def _is_cue_text(line: str) -> bool:
    return bool(line) and "-->" not in line \
        and not line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE"))


def vtt_to_text(vtt: str) -> str:
    """WebVTT → 纯文本：去时间轴/头部/内联标签，相邻重复行（自动字幕的滚动回显）只留一份。"""
    out: list = []
    last = None
    for raw in vtt.splitlines():
        line = _TAG_RE.sub("", raw).strip()
        if _is_cue_text(line) and line != last:
            out.append(line)
            last = line
    return "\n".join(out)


def _lang_rank(path: Path):
    lang = path.name.split(".")[-2] if path.name.count(".") >= 2 else ""
    return (_LANG_PREF.index(lang) if lang in _LANG_PREF else len(_LANG_PREF), path.name)


def _pick_subtitles(outdir: Path) -> str:
    files = sorted(outdir.glob("*.vtt"), key=_lang_rank)
    return vtt_to_text(files[0].read_text(encoding="utf-8", errors="replace")) if files else ""


def _first_line(text) -> str:
    lines = str(text or "").strip().splitlines()
    return lines[0].strip()[:MAX_TITLE_CHARS] if lines else ""


def _tail(text) -> str:
    return str(text or "").strip()[-200:]


def _ytdlp(result: dict, deps: _Deps, bin_path: str) -> None:
    with tempfile.TemporaryDirectory(prefix="zai-materials-") as tmp:
        proc = deps.runner(ytdlp_argv(bin_path, result["url"], tmp), cwd=tmp,
                           timeout=deps.timeout, capture_output=True, text=True)
        result["title"] = _first_line(proc.stdout)
        text = _pick_subtitles(Path(tmp))
    result["source"] = "yt-dlp"
    _set_text(result, text, False)
    if not text and proc.returncode != 0:
        result["error"] = "yt-dlp exit %s: %s" % (proc.returncode, _tail(proc.stderr))


def _json_title(body: bytes) -> str:
    doc = json.loads(body.decode("utf-8", errors="replace"))
    title = doc.get("title") if isinstance(doc, dict) else None
    return str(title).strip()[:MAX_TITLE_CHARS] if isinstance(title, str) else ""


def _oembed_title(result: dict, deps: _Deps) -> None:
    query = urlencode({"url": result["url"], "format": "json"})
    _ctype, body = deps.fetcher(OEMBED_ENDPOINT + "?" + query, deps.timeout)
    result["title"] = _json_title(body)
    if not result["text"]:
        result["source"] = "oembed"


def _fetch_youtube(result: dict, deps: _Deps) -> None:
    bin_path = deps.which("yt-dlp")
    if bin_path:
        try:
            _ytdlp(result, deps, bin_path)
        except (OSError, subprocess.SubprocessError) as exc:
            result["error"] = ("yt-dlp: %s" % exc)[:200]
    if not result["title"]:
        _oembed_title(result, deps)


def _fetch_unsupported(result: dict, deps: _Deps) -> None:
    result["error"] = "unsupported url (http/https only)"


_FETCHERS = {"youtube": _fetch_youtube, "web": _fetch_web, "unsupported": _fetch_unsupported}


def fetch(url: str, *, fetcher=None, runner=None, which=None, timeout: float = FETCH_TIMEOUT) -> dict:
    """抓取一条素材的标题与正文。永不抛（宪法第 11 条）——失败写进 ``error``。

    返回 ``{url, kind, title, text, source, truncated, error}``（字段 add-only）。
    ``source`` ∈ yt-dlp | oembed | html | text | ""。这里的 title/text 是**原文**，
    进 prompt 必须经 :func:`prompt_block`。
    """
    deps = _Deps(fetcher, runner, which, timeout)
    result = {"url": str(url), "kind": classify(url), "title": "", "text": "",
              "source": "", "truncated": False, "error": None}
    try:
        _FETCHERS[result["kind"]](result, deps)
    except Exception as exc:  # noqa: BLE001 - 单条素材的失败只属于它自己
        result["error"] = ("%s: %s" % (type(exc).__name__, exc))[:200]
    return result


# --------------------------------------------------------------------------- #
# prompt 组装（循环侧唯一入口）
# --------------------------------------------------------------------------- #
def _s(doc: dict, key: str) -> str:
    return str(doc.get(key) or "")


def _fetched_lines(fetched: dict) -> list:
    flag = "，已截断" if fetched.get("truncated") else ""
    body = "标题：%s\n抓取错误：%s\n\n%s" % (_s(fetched, "title"), _s(fetched, "error"),
                                          _s(fetched, "text"))
    return ["抓取到的内容（来源 %s%s）：" % (_s(fetched, "source") or "无", flag),
            sanitize.fence_untrusted(body)]


def prompt_block(item: dict, fetched: Optional[dict] = None) -> str:
    """一条素材进 LLM prompt 的唯一形态：owner 的 URL/备注与抓取内容各自进围栏。"""
    fetched = dict(fetched or {})
    owner_block = "URL: %s\n备注：%s" % (_s(item, "url") or "(无)", _s(item, "note"))
    lines = ["素材 %s（加入于 %s）" % (_s(item, "id"), _s(item, "created_at")),
             "owner 扔进素材库的内容：", sanitize.fence_untrusted(owner_block)]
    if fetched:
        lines += _fetched_lines(fetched)
    return "\n".join(lines)
