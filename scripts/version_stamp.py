#!/usr/bin/env python3
"""版本盖章 CLI（CONTRACT §56.1）：把 git tag 真源盖进不被提交的地方。

    python3 scripts/version_stamp.py                 # 打印这个 checkout 应带的版本
    python3 scripts/version_stamp.py --write         # 同上，并写 act/_version.py
    python3 scripts/version_stamp.py --version 0.48.16 --write   # 指定版本（release.yml 从 tag 名取）
    python3 scripts/version_stamp.py --ios           # 把 iOS 两处 MARKETING_VERSION pin 由占位 sed 成版本
                                                     # （只在 CI runner / 构建机上做，绝不提交）
    python3 scripts/version_stamp.py --check-pins    # 提交的 pin 必须是占位 0.0.0-dev（CI 门）
    python3 scripts/version_stamp.py --stamp-into DIR  # 往 DIR/act/_version.py 写（打包 stage）
    python3 scripts/version_stamp.py --runtime       # 打印运行时 act.__version__（stamp 优先）

默认版本 = act.lib.version.stamp_decision：git 答得上 → tag（领先 = tag+N，
过渡条款见 lib）；git 答不上但已有 stamp → 保留；都没有 → 烘焙回落常量。
stdout 只有版本串一行（shell 直接 `VERSION="$(…)"`），过程说明走 stderr。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from act.lib import version as ver  # noqa: E402

_PIN_RE = re.compile(r"(MARKETING_VERSION[^\S\n]*[:=][^\S\n]*\"?)([^\";\n]+)(\"?)")


_QUIET = [False]


def _say(msg: str) -> None:
    if not _QUIET[0]:
        sys.stderr.write(msg + "\n")


def decide(args, repo_root: Path) -> tuple:
    """(版本, 来源)——命令行 --version 优先，否则 stamp_decision。"""
    if args.version:
        if ver.parse_tag(args.version) is None and "+" not in args.version:
            _say("warning: %r is not X.Y.Z" % args.version)
        return args.version.lstrip("v"), "argv"
    fallback = ver.read_fallback(repo_root / "act" / "__init__.py")
    return ver.stamp_decision(fallback, repo_root / "act" / "_version.py", repo_root)


def stamp_pins(text: str, version: str) -> tuple:
    """pin 文件文本里每处 MARKETING_VERSION 的值换成 version：(新文本, 替换数)。"""
    return _PIN_RE.subn(lambda m: m.group(1) + version + m.group(3), text)


def pin_values(text: str) -> list:
    return [m.group(2).strip() for m in _PIN_RE.finditer(text)]


def do_ios(version: str, repo_root: Path) -> int:
    """把 iOS pins 从占位 sed 成 version（工作树里，永不提交）。"""
    for rel in ver.PIN_FILES:
        path = repo_root / rel
        text = path.read_text(encoding="utf-8")
        new, n = stamp_pins(text, version)
        if n == 0:
            _say("error: no MARKETING_VERSION in %s — project layout changed" % rel)
            return 1
        path.write_text(new, encoding="utf-8")
        _say("stamped %d MARKETING_VERSION pin(s) in %s -> %s" % (n, rel, version))
    return 0


def do_check_pins(repo_root: Path) -> int:
    """提交的 pin 全部 == 占位（否则有人手 bump 了）。"""
    rc = 0
    for rel in ver.PIN_FILES:
        values = pin_values((repo_root / rel).read_text(encoding="utf-8"))
        bad = [v for v in values if v != ver.PIN_PLACEHOLDER]
        if not values or bad:
            _say("::error file=%s::MARKETING_VERSION must stay the placeholder %s (found %s) — "
                 "versions come from git tags, never from PR edits (CONTRACT §56.1)"
                 % (rel, ver.PIN_PLACEHOLDER, bad or "nothing"))
            rc = 1
        else:
            _say("%s: %d pin(s) == %s" % (rel, len(values), ver.PIN_PLACEHOLDER))
    return rc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", help="use this version instead of deriving it")
    parser.add_argument("--write", action="store_true", help="write act/_version.py")
    parser.add_argument("--ios", action="store_true", help="stamp the iOS MARKETING_VERSION pins (runner-only)")
    parser.add_argument("--check-pins", action="store_true", help="fail unless the committed pins are the placeholder")
    parser.add_argument("--stamp-into", metavar="DIR", help="write DIR/act/_version.py (packaging stage)")
    parser.add_argument("--runtime", action="store_true", help="print the runtime act.__version__ (stamp first)")
    parser.add_argument("--quiet", action="store_true", help="no stderr notes (PowerShell treats native stderr as errors)")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    _QUIET[0] = bool(args.quiet)
    root = Path(args.repo_root).resolve()

    if args.check_pins:
        return do_check_pins(root)
    if args.runtime:
        fallback = ver.read_fallback(root / "act" / "__init__.py")
        print(ver.resolve(fallback, root / "act" / "_version.py", root))
        return 0

    version, source = decide(args, root)
    rc = 0
    if args.write:
        ver.write_stamp(version, root / "act" / "_version.py")
        _say("stamped act/_version.py = %s (%s)" % (version, source))
    if args.stamp_into:
        target = Path(args.stamp_into) / "act" / "_version.py"
        ver.write_stamp(version, target)
        _say("stamped %s = %s (%s)" % (target, version, source))
    if args.ios:
        rc = do_ios(version, root)
    print(version)
    return rc


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
