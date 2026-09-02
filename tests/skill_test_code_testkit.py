"""test-code skill 判例共用件：把 skills/test-code/scripts 挂进 sys.path、造 fixture
mini-repo、造最小 detection dict、可脚本化的 FakeRunner（绝不真起子进程）。

法典指针：docs/CONTRACT.md §58（skill 只读项目阈值）；设计 vnext2-plan R2.8。
"""
import os
import sys

_SKILL_SCRIPTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "test-code", "scripts")
if _SKILL_SCRIPTS not in sys.path:
    sys.path.insert(0, _SKILL_SCRIPTS)

import ladder_common as lc  # noqa: E402

SKILL_SCRIPTS = _SKILL_SCRIPTS


def make_repo(root, files):
    """{relpath: text} → 落盘；返回 root。目录自动创建。"""
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return root


def fake_det(files, **overrides):
    """python/unittest 单栈项目的最小 detection dict；overrides 逐键覆盖顶层。"""
    tests_dir = "tests" if any(f.startswith("tests/") for f in files) else None
    layout = {
        "py_roots": sorted({f.split("/")[0] for f in files if f.endswith(".py") and "/" in f}),
        "py_src_roots": sorted({f.split("/")[0] for f in files
                                if f.endswith(".py") and "/" in f and not f.startswith("tests/")}),
        "tests_dir": tests_dir, "integration_dir": None, "py_runner": "unittest", "py_format": None,
        "property_tests": [], "requirements": [], "js_packages": [],
        "swift": {"package_dir": None, "schemes": []}, "importlinter": False, "mutmut": False,
        "benchmarks": False, "fuzz_dir": False, "qlty": False,
    }
    det = {
        "schemaVersion": 1, "repo": None, "is_git": True, "python": sys.executable,
        "files": sorted(files), "stacks": ["python"] if any(f.endswith(".py") for f in files) else [],
        "tools": {}, "pymods": {}, "layout": layout, "mutation_targets": [],
        "thresholds": {"complexity_max": 10, "crap_max": 30.0, "crap_tolerance": 0.5,
                       "max_function_lines": 100, "max_file_lines": 1000, "coverage": "no-drop",
                       "source": "skill-defaults", "note": "test"},
        "diff": {"base": "origin/main", "base_commit": "abc", "changed_files": [], "added": {},
                 "added_text": {}, "removed": {}, "untracked": []},
        "triggers": [], "recommendation": {"tier": 2, "reason": "test"},
    }
    det.update(overrides)
    return det


class FakeRunner(object):
    """脚本化 runner：rules = [(match, response)]。match = argv 子串或 predicate(argv)；
    response = (rc, stdout, stderr) / RunResult / callable(argv, cwd) → RunResult。
    没规则命中 → default。记录每次调用供断言。"""

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


def git_ok_rules(diff_text="", tracked=(), untracked=(), head="deadbeef" * 5):
    """让 FakeRunner 扮演一个干净的 git：ls-files / rev-parse / merge-base / diff / status。"""
    return [
        ("git ls-files --others", (0, "\n".join(untracked), "")),
        ("git ls-files", (0, "\n".join(tracked), "")),
        ("git rev-parse --verify --quiet origin/main", (0, "", "")),
        ("git rev-parse HEAD", (0, head, "")),
        ("git merge-base", (0, "cafebabe" * 5, "")),
        ("git diff --name-only", (0, "", "")),
        ("git diff -U0", (0, diff_text, "")),
        ("git status --porcelain", (0, "", "")),
        ("git --version", (0, "git version 2.0", "")),
    ]
