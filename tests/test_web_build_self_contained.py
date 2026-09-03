"""web/ 的构建在仓库之外也成立（CONTRACT §56.5 ui 步、§66.2、§69.4）。

install.sh 的 ui 步把 web/（除 node_modules / dist）镜像到 $HOME 下的构建目录里
`npm run build`——仓库根不在那里。2026-09-03 首次 fresh-install 验收（run
33714777638）死在这一点：`tsc --noEmit` 连测试文件一起查，而 src/parity.test.tsx
静态 import 了 `../../ui/parity/*.json`，镜像里解析不到 → ui=fail → 装机报 1 个失败步。
本文件钉的形状（不起 node，纯文本判例；真 `npm run build` 的复现住
tests/integration/test_web_build_outside_repo.py）：

  - `npm run build` 的类型检查走 web/tsconfig.build.json，它 extends tsconfig.json、
    排除 `src/**/*.test.ts(x)` 与 e2e；全量检查（含测试）= `npm run typecheck`；
  - CI「Web tests」job 两个都跑（build 瘦了，测试文件的类型覆盖不许因此丢）；
  - web/src 下**任何**文件（测试也算）都没有逃出 web/ 的静态 import——仓库根的
    fixture 只许经 import.meta.glob 读（vite 在转换期解析、tsc 不看文件系统），
    找不到时判例自己抛错而不是静默空转；
  - install.sh 的镜像排除集不变（node_modules / dist / .zai-*），tsconfig.build.json
    因此随镜像走。
"""
import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"
SRC = WEB / "src"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
INSTALL_SH = REPO_ROOT / "install.sh"

# `import x from "…"` / `import "…"` / `export … from "…"` / `import("…")` 的说明符
_SPECIFIER = re.compile(
    r"""(?:^|\n)\s*(?:import|export)\b[^;'"\n]*?\bfrom\s*["']([^"']+)["']"""
    r"""|(?:^|\n)\s*import\s*["']([^"']+)["']"""
    r"""|\bimport\(\s*["']([^"']+)["']\s*\)""",
)
# 行注释 / 块注释里的示例不算 import
_COMMENTS = re.compile(r"//[^\n]*|/\*[\s\S]*?\*/")


def _strip_jsonc_comments(text):
    """字符串之外的 // 与 /* */ 注释换成空——glob 里的 `/**/` 在引号内，不能当注释剥。"""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\":
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif text.startswith("//", i):
            i = text.find("\n", i)
            if i < 0:
                break
            continue
        elif text.startswith("/*", i):
            i = text.index("*/", i) + 2
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _jsonc(path):
    """tsconfig 是 JSONC：剥注释后再 json.loads。"""
    return json.loads(_strip_jsonc_comments(path.read_text(encoding="utf-8")))


def _source_files():
    return sorted(p for p in SRC.rglob("*") if p.suffix in (".ts", ".tsx") and p.is_file())


def _escaping_imports(path):
    """本文件里解析后落在 web/ 之外的说明符（相对路径经 path 归一化后判）。"""
    text = _COMMENTS.sub("", path.read_text(encoding="utf-8"))
    out = []
    for m in _SPECIFIER.finditer(text):
        spec = next(g for g in m.groups() if g)
        if not spec.startswith("."):
            continue                       # 裸模块名 / 别名 —— 由 node_modules 解析
        target = (path.parent / spec.split("?", 1)[0]).resolve()
        if WEB.resolve() not in target.parents and target != WEB.resolve():
            out.append(spec)
    return out


class BuildTsconfigTestCase(unittest.TestCase):
    def test_build_script_type_checks_with_the_build_tsconfig_only(self):
        scripts = json.loads((WEB / "package.json").read_text(encoding="utf-8"))["scripts"]
        self.assertEqual(scripts["build"], "tsc --noEmit -p tsconfig.build.json && vite build")
        self.assertEqual(scripts["typecheck"], "tsc --noEmit")

    def test_build_tsconfig_extends_the_main_one_and_excludes_tests(self):
        cfg = _jsonc(WEB / "tsconfig.build.json")
        self.assertEqual(cfg["extends"], "./tsconfig.json")
        self.assertEqual(cfg["include"], _jsonc(WEB / "tsconfig.json")["include"])
        for pattern in ("src/**/*.test.ts", "src/**/*.test.tsx", "e2e"):
            self.assertIn(pattern, cfg["exclude"])
        # nothing but include/exclude/extends: compiler options stay single-sourced
        self.assertNotIn("compilerOptions", cfg)

    def test_every_test_file_matches_an_exclude_pattern(self):
        tests = [p for p in _source_files() if ".test." in p.name]
        self.assertGreater(len(tests), 10)
        for p in tests:
            self.assertRegex(p.name, r"\.test\.tsx?$", "%s would still be type-checked by the build" % p)

    def test_ci_web_job_runs_the_full_typecheck_and_the_build(self):
        text = CI_YML.read_text(encoding="utf-8")
        job = text[text.index("name: Web tests (build + vitest)"):text.index("qa-gates:")]
        self.assertIn("run: npm run typecheck", job)
        self.assertIn("run: npm run build", job)
        self.assertIn("run: npx vitest run", job)


class NoEscapingImportTestCase(unittest.TestCase):
    def test_no_web_src_file_imports_from_outside_web(self):
        offenders = {str(p.relative_to(REPO_ROOT)): _escaping_imports(p)
                     for p in _source_files() if _escaping_imports(p)}
        self.assertEqual(offenders, {}, "static imports that escape web/ break the out-of-repo build "
                                        "(install.sh ui step); read repo fixtures via import.meta.glob")

    def test_parity_suite_reads_repo_fixtures_via_glob_and_fails_loud(self):
        text = (SRC / "parity.test.tsx").read_text(encoding="utf-8")
        self.assertIn('import.meta.glob(["../../ui/parity/native-inventory.json", "../../ui/parity/fixtures/*.json"]', text)
        self.assertIn('import.meta.glob("../../ui/parity/*.txt"', text)
        self.assertIn('query: "?raw"', text)
        self.assertIn("throw new Error(`ui/parity/${rel} not found", text)
        self.assertNotIn('from "../../ui/parity/', text)

    def test_detector_sees_an_escaping_import(self):
        # the detector itself must not be a no-op: plant an escaping import in a temp file under src
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".test.ts", dir=str(SRC), delete=False,
                                         encoding="utf-8") as fh:
            fh.write('import x from "../../ui/parity/native-inventory.json";\n'
                     'import { y } from "./types";\n'
                     '// import z from "../../not-an-import";\n'
                     'const w = await import("../../ui/parity/fixtures/lanes.json");\n')
            planted = Path(fh.name)
        try:
            self.assertEqual(_escaping_imports(planted),
                             ["../../ui/parity/native-inventory.json", "../../ui/parity/fixtures/lanes.json"])
        finally:
            planted.unlink()


class InstallMirrorTestCase(unittest.TestCase):
    def test_install_sh_mirror_keeps_the_build_tsconfig(self):
        # the rsync exclude set is node_modules / dist / .zai-* only — tsconfig.build.json rides along
        text = INSTALL_SH.read_text(encoding="utf-8")
        m = re.search(r"rsync -a --checksum --delete((?: --exclude \S+)+) ", text)
        self.assertIsNotNone(m, "install.sh lost the web/ mirror rsync")
        excludes = set(re.findall(r"--exclude (\S+)", m.group(1)))
        self.assertEqual(excludes, {"node_modules", "dist", "'.zai-*'"})


if __name__ == "__main__":
    unittest.main()
