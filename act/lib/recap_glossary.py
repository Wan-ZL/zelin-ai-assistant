"""act/lib/recap_glossary.py — 会议纪要的术语表：转写进模型之前先把听错的词换回正确拼法（CONTRACT §63.14）。

issue #332 / #440 的实测：Whisper 把 SFT 听成 SRT / SLT、Nemotron 听成 new tron、
SageMaker 听成 stage maker、同一个人名三种拼法——而转写进模型时没有任何术语清单，
纪要就照着错的写。自此出稿前多一步，两条腿：

1. **确定性替换**（:func:`apply`）：术语表里每条 ``正确拼法: 听错1, 听错2`` 的听错形，
   在转写里按大小写不敏感 + 词边界（CJK 按子串）换成正确拼法，最长的先换；换了几处
   记在记录的 add-only ``glossary_hits`` 上（只有计数，宪法第 9 条）。owner 明确列出的
   听错形交给模型「再听一遍」就是又开一次赌局（§63.11 `prior=drop` 的同一条纪律）。
2. **术语清单进 prompt**（:func:`prompt_block`）：正确拼法的清单经 ``recap_text.build_prompt``
   的 ``glossary=`` 进 ``sanitize.fence_untrusted``——它是 owner 的一份文件，不是指令
   （宪法第 5 条：语气档走的同一条围栏）；「转写是机器听写、遇到读音相近的词用这份拼法」
   这条**指令**住围栏的标签上，模板 `PROMPT_HEADER` / `PROMPT_HEADER_SECTIONS` 一个字符没动。

术语表住两处、合并读（防腐 #4：有帽）：``state/recap-glossary.md``（owner 自己编辑，
与 ``state/voice-profile.md`` 同一性质；``#`` 行是注释，``- `` 列表符可有可无，冒号 /
全角冒号 / ``=`` 分开正确拼法与听错形，逗号 / 全角逗号 / 分号 / ``|`` 分开多个听错形）
与 config.yaml ``recap.glossary``（同一行格式的字符串列表）。上限 :data:`MAX_TERMS` 条、
每条 :data:`MIN_TERM_CHARS`..:data:`MAX_TERM_CHARS` 字符、每条最多
:data:`MAX_VARIANTS_PER_TERM` 个听错形，听错形短于 :data:`MIN_VARIANT_CHARS`（CJK 按
:data:`MIN_VARIANT_CHARS_CJK`）的丢掉（两个字母的形会在噪音上乱开火）；文件超过
:data:`MAX_FILE_BYTES` 不读（只剩 config 那一半）。读不动 / 坏行 = 那一行丢掉，永不抛。

stdlib-only；调用方 ``act/recap.py``（读表、替换、进 prompt），server 侧 ``server/recaps.py``
只读同一个文件报一个计数（`GET /api/settings/recap` 的 add-only ``glossary_terms``）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from act.lib import config

GLOSSARY_FILENAME = "recap-glossary.md"
MAX_TERMS = 200
MIN_TERM_CHARS = 2
MAX_TERM_CHARS = 64
MAX_VARIANTS_PER_TERM = 12
# 听错形的长度下限：拉丁 3（两个字母的形会在噪音上乱开火），CJK 2（两个字就是一个词 / 一个名字）
MIN_VARIANT_CHARS = 3
MIN_VARIANT_CHARS_CJK = 2
MAX_FILE_BYTES = 64 * 1024

# 一行的形：`正确拼法: 听错1, 听错2`（冒号 / 全角冒号 / 等号分开两半；逗号 / 全角逗号 / 分号 / 竖线分开多个）
_SPLIT_TERM = re.compile(r"[:：=]", re.UNICODE)
_SPLIT_VARIANTS = re.compile(r"[,，;；|]", re.UNICODE)
_BULLET = re.compile(r"^[-*•]\s+")
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_WS = re.compile(r"\s+")


def glossary_path() -> Path:
    """owner 自己编辑的那一份（``state/recap-glossary.md``；``server/recaps.glossary_path`` 镜像它）。"""
    return config.STATE_DIR / GLOSSARY_FILENAME


def _clean(value: str) -> str:
    return _WS.sub(" ", str(value)).strip()


def _variant_ok(variant: str) -> bool:
    floor = MIN_VARIANT_CHARS_CJK if _CJK.search(variant) else MIN_VARIANT_CHARS
    return floor <= len(variant) <= MAX_TERM_CHARS


def _variants(raw: str, term: str) -> list:
    """听错形表：去空白、按长度闸、与正确拼法同形的丢掉（换成自己没有意义）、去重（大小写不敏感）。"""
    out, seen = [], {term.lower()}
    for piece in _SPLIT_VARIANTS.split(raw):
        variant = _clean(piece)
        if not _variant_ok(variant) or variant.lower() in seen:
            continue
        seen.add(variant.lower())
        out.append(variant)
    return out[:MAX_VARIANTS_PER_TERM]


def parse_line(line) -> Optional[tuple]:
    """一行 → ``(正确拼法, [听错形])``，或 None（空行 / 注释 / 没有正确拼法 / 太长）。
    没写听错形的行也算一条（只进 prompt 的清单，不做替换）。"""
    text = _BULLET.sub("", str(line or "").strip())
    if not text or text.startswith("#"):
        return None
    m = _SPLIT_TERM.search(text)
    head, tail = (text[:m.start()], text[m.end():]) if m else (text, "")
    term = _clean(head)
    if not (MIN_TERM_CHARS <= len(term) <= MAX_TERM_CHARS):
        return None
    return term, _variants(tail, term)


def parse(lines) -> list:
    """多行 → 术语表（同一个正确拼法只认第一条，大小写不敏感；帽 :data:`MAX_TERMS`）。"""
    out, seen = [], set()
    for line in lines or []:
        entry = parse_line(line)
        if entry is None or entry[0].lower() in seen:
            continue
        seen.add(entry[0].lower())
        out.append(entry)
        if len(out) >= MAX_TERMS:
            break
    return out


def _file_lines(path: Path) -> list:
    """文件的行；缺席 / 读不动 / 超过 :data:`MAX_FILE_BYTES` = 空（永不抛）。"""
    try:
        if not path.exists() or path.stat().st_size > MAX_FILE_BYTES:
            return []
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def config_lines(cfg) -> list:
    """config.yaml ``recap.glossary``：字符串列表（同一行格式）；不是列表 / 非字符串项丢掉。"""
    raw = getattr(cfg, "raw", None)
    blk = raw.get("recap") if isinstance(raw, dict) else None
    items = blk.get("glossary") if isinstance(blk, dict) else None
    return [item for item in items if isinstance(item, str)] if isinstance(items, list) else []


def load(cfg=None, path: Optional[Path] = None) -> list:
    """两处合并读：文件在前（owner 的手笔优先占号），config 列表在后。"""
    return parse(_file_lines(path or glossary_path()) + config_lines(cfg))


def terms(glossary) -> list:
    """正确拼法的清单（进 prompt 的那一份）。"""
    return [term for term, _variants in glossary]


def _pattern(variant: str) -> re.Pattern:
    """一个听错形的正则：大小写不敏感；内部空白放宽成任意空白（Whisper 的分词不稳）；
    纬度边界——拉丁形两侧不许贴着字母数字（`tron` 不许咬掉 `Nemotron` 的尾巴），
    CJK 形按子串（中文没有词边界）。"""
    body = r"\s+".join(re.escape(piece) for piece in variant.split())
    if _CJK.search(variant):
        return re.compile(body, re.IGNORECASE)
    return re.compile(r"(?<![0-9A-Za-z_])%s(?![0-9A-Za-z_])" % body, re.IGNORECASE)


def _rules(glossary) -> list:
    """``[(正则, 正确拼法)]``，最长的听错形先换（`sage maker studio` 不许被 `sage maker` 抢先拆开）。"""
    pairs = [(variant, term) for term, variants in glossary for variant in variants]
    pairs.sort(key=lambda pair: (-len(pair[0]), pair[0].lower()))
    return [(_pattern(variant), term) for variant, term in pairs]


def _substitute(text: str, rules: list) -> tuple:
    out, hits = str(text or ""), 0
    for pattern, term in rules:
        out, n = pattern.subn(term, out)
        hits += n
    return out, hits


def apply(text: str, glossary) -> tuple:
    """``(替换后的转写, 换了几处)``——确定性、同输入恒同输出；空表 = 原文、0。"""
    return _substitute(text, _rules(glossary))


def apply_rows(rows: list, glossary) -> tuple:
    """``[(ts, text)]`` 逐行替换 → ``(rows, 换了几处)``。逐行而不是拼起来再换：听错形内部的
    空白放宽成了 ``\\s+``，拼起来换会让一个跨行的匹配把两段转写焊在一起（§63.13 的逐条锚
    要按行找原话，行不许动）。"""
    rules = _rules(glossary)
    if not rules:
        return list(rows), 0
    out, hits = [], 0
    for ts, text in rows:
        replaced, n = _substitute(text, rules)
        out.append((ts, replaced))
        hits += n
    return out, hits


def prompt_block(glossary) -> Optional[str]:
    """进围栏的那一份：一行一个正确拼法；空表 = None（那一块根本不进 prompt）。"""
    names = terms(glossary)
    return "\n".join(names) if names else None
