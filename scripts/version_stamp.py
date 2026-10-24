#!/usr/bin/env python3
"""版本盖章 CLI（CONTRACT §56.1）：把 git tag 真源盖进不被提交的地方。

    python3 scripts/version_stamp.py                 # 打印这个 checkout 应带的版本
    python3 scripts/version_stamp.py --write         # 同上，并写 act/_version.py
    python3 scripts/version_stamp.py --version 0.48.16 --write   # 指定版本（release.yml 从 tag 名取）
    python3 scripts/version_stamp.py --check-pins    # 提交的 pin 必须是占位 0.0.0-dev（v2.0.0 起
                                                     # version.PIN_FILES 为空，恒 rc 0）
    python3 scripts/version_stamp.py --stamp-into DIR  # 往 DIR/act/_version.py 写（打包 stage）
    python3 scripts/version_stamp.py --runtime       # 打印运行时 act.__version__（stamp 优先）

默认版本 = act.lib.version.stamp_decision：git 答得上 → tag（领先 = tag+N，
过渡条款见 lib）；git 答不上但已有 stamp → 保留；都没有 → 烘焙回落常量。
stdout 只有版本串一行（shell 直接 `VERSION="$(…)"`），过程说明走 stderr。
``--ios``（把 iOS 两处 MARKETING_VERSION pin 盖成版本）随 ios/ 在 v2.0.0 退役。
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


def pin_values(text: str) -> list:
    return [m.group(2).strip() for m in _PIN_RE.finditer(text)]


def _check_one_pin_file(repo_root: Path, rel: str) -> int:
    """一份 pin 文件：全部 == 占位 → 0；有别的值或一处都没有 → 1（::error:: 注解）。"""
    values = pin_values((repo_root / rel).read_text(encoding="utf-8"))
    bad = [v for v in values if v != ver.PIN_PLACEHOLDER]
    if values and not bad:
        _say("%s: %d pin(s) == %s" % (rel, len(values), ver.PIN_PLACEHOLDER))
        return 0
    _say("::error file=%s::MARKETING_VERSION must stay the placeholder %s (found %s) — "
         "versions come from git tags, never from PR edits (CONTRACT §56.1)"
         % (rel, ver.PIN_PLACEHOLDER, bad or "nothing"))
    return 1


def do_check_pins(repo_root: Path, files=None) -> int:
    """提交的 pin 全部 == 占位（否则有人手 bump 了）。files 缺省 = version.PIN_FILES（v2.0.0 起为空）。"""
    rels = ver.PIN_FILES if files is None else files
    return 1 if any([_check_one_pin_file(repo_root, rel) for rel in rels]) else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", help="use this version instead of deriving it")
    parser.add_argument("--write", action="store_true", help="write act/_version.py")
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
    if args.write:
        ver.write_stamp(version, root / "act" / "_version.py")
        _say("stamped act/_version.py = %s (%s)" % (version, source))
    if args.stamp_into:
        target = Path(args.stamp_into) / "act" / "_version.py"
        ver.write_stamp(version, target)
        _say("stamped %s = %s (%s)" % (target, version, source))
    print(version)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
