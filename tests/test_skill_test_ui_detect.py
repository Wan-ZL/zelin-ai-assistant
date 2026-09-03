"""test-ui skill · 探测判例：UI 面（web-react > static-html；swift 并列）、token 文件、工具探针（node require.resolve
经 FakeRunner）、项目适配器、内置 config（本 repo 形状：screens / launch / geometry / dims）、阈值来源
（gates.toml [ui] → config.thresholds → 默认）、diff → 触发器（docs-only 不点火、.md 里的 aria-label 不点火）、
tier 推荐、两侧仪器模式、菜单形状。零真子进程（git / node 全 FakeRunner）。

法典：docs/CONTRACT.md §58 / §62；设计 vnext2-plan R2.8。
"""
import os
import tempfile
import unittest

from tests import skill_test_ui_testkit as kit

import detect_ui  # noqa: E402

WEB_FILES = ["web/package.json", "web/index.html", "web/src/pages/BoardPage.tsx", "web/src/pages/SettingsPage.tsx",
             "web/src/components/board/Lane.tsx", "web/src/components/shell/HeaderBar.tsx", "web/src/styles/tokens.css",
             "web/src/components/board/board.css", "web/src/styles/typeScale.ts", "scripts/demo_seed.py", "server/__main__.py",
             "docs/x.md", "mac/Sources/App.swift"]


def _repo(tmp):
    return kit.make_repo(tmp, {f: ('{"name": "web"}' if f.endswith("package.json") else ":root { --bg: #fff; color-scheme: light; }"
                                   if f.endswith("tokens.css") else '<html lang="en"></html>' if f.endswith("index.html") else "// x")
                               for f in WEB_FILES})


class SurfacesAndFilesTestCase(unittest.TestCase):
    def test_surfaces(self):
        surfaces = detect_ui.detect_surfaces(WEB_FILES)
        self.assertEqual([(s["kind"], s["root"]) for s in surfaces], [("web-react", "web/src"), ("swift-source", "mac/Sources")])
        self.assertEqual(detect_ui.detect_surfaces(["a/index.html", "a/b.html"])[0]["kind"], "static-html")
        self.assertEqual(detect_ui.detect_surfaces(["x.py"]), [])

    def test_tokens_files_and_lang(self):
        with tempfile.TemporaryDirectory() as tmp:
            _repo(tmp)
            found = detect_ui.detect_tokens_files(tmp, WEB_FILES, detect_ui.detect_surfaces(WEB_FILES))
            self.assertEqual(found["css"], ["web/src/styles/tokens.css"])
            self.assertEqual(found["component_dirs"], ["web/src/components/board"])
            self.assertEqual(found["index_html"], "web/index.html")
            self.assertEqual(detect_ui.detect_lang(tmp, "web/index.html"), "en")
            self.assertIsNone(detect_ui.detect_lang(tmp, None))

    def test_adapters_and_builtin_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            _repo(tmp)
            adapters = detect_ui.detect_adapters(tmp)
            self.assertEqual(adapters["demo_seed"], "scripts/demo_seed.py")
            self.assertIsNone(adapters["parity_check"])
            cfg, source = detect_ui.build_config(tmp, WEB_FILES, adapters)
            self.assertEqual([s["id"] for s in cfg["screens"]], ["board", "settings", "shell"])
            self.assertEqual(cfg["screens"][0]["route"], "")
            self.assertEqual(cfg["launch"]["server"], ["{py}", "-m", "server"])
            self.assertNotIn("geometry", cfg)  # no ui/tokens → no geometry defaults
            self.assertIn("built-in adapter defaults", source)
            kit.make_repo(tmp, {"ui/parity/config.json": '{"screens": [{"id": "only", "route": "", "source": ["x"]}], "thresholds": {"max_changed_pct": 0.02}}'})
            cfg, source = detect_ui.build_config(tmp, WEB_FILES, adapters)
            self.assertEqual([s["id"] for s in cfg["screens"]], ["only"])
            self.assertEqual(source, "ui/parity/config.json")


class ThresholdsTestCase(unittest.TestCase):
    def test_sources_in_order(self):
        runner = kit.FakeRunner(kit.git_ok_rules())
        with tempfile.TemporaryDirectory() as tmp:
            current, base, _cfg = detect_ui.detect_thresholds(runner, tmp, {}, None)
            self.assertEqual((current["source"], base), ("skill-defaults", None))
            current, _b, _c = detect_ui.detect_thresholds(runner, tmp, {"thresholds": {"max_changed_pct": 0.02}}, None)
            self.assertEqual((current["source"], current["max_changed_pct"]), ("ui/parity/config.json .thresholds", 0.02))
            kit.make_repo(tmp, {"qa/gates.toml": "[complexity]\nmax = 6\n[ui]\nmax_changed_pct = 0.01\ncontrast_text = 4.5\n"})
            current, base, _c = detect_ui.detect_thresholds(runner, tmp, {"thresholds": {"max_changed_pct": 0.5}}, "cafebabe")
            self.assertEqual((current["source"], current["max_changed_pct"]), ("qa/gates.toml [ui]", 0.01))
            self.assertEqual(base["source"], "skill-defaults")  # git show fails in the fake → base = defaults


class DiffAndTriggersTestCase(unittest.TestCase):
    def test_docs_only_diff_fires_nothing(self):
        """负控制：.md 里写着 aria-label / @media / data-theme 也不点火。"""
        diff = {"changed_files": ["docs/a.md"], "added_text": {"docs/a.md": [(1, 'aria-label="x" @media data-theme')]}, "base": "origin/main", "base_commit": "c", "untracked": []}
        self.assertEqual(detect_ui.detect_triggers(diff), [])
        det = {"diff": diff, "triggers": [], "config": {}}
        self.assertEqual(detect_ui.recommend(det)["tier"], 1)

    def test_ui_diff_fires_and_recommends(self):
        diff = {"changed_files": ["web/src/components/shell/HeaderBar.tsx", "web/src/styles/tokens.css", "act/x.py"],
                "added_text": {"web/src/components/shell/HeaderBar.tsx": [(3, '<a aria-label="x">')], "act/x.py": [(1, "aria-label")],
                               "web/src/styles/tokens.css": [(1, "--native-layout-lane-width: 300px;"), (2, "color-scheme: dark;")]},
                "base": "origin/main", "base_commit": "c", "untracked": []}
        fired = {t["id"]: t for t in detect_ui.detect_triggers(diff)}
        self.assertEqual(sorted(fired), ["a11y_attr_changed", "layout_changed", "screen_changed", "theme_changed", "tokens_changed"])
        self.assertEqual(fired["a11y_attr_changed"]["hits"], 1)  # the .py line did not count
        cfg = {"screens": [{"id": "shell", "source": ["web/src/components/shell/*"]}]}
        rec = detect_ui.recommend({"diff": diff, "triggers": list(fired.values()), "config": cfg})
        self.assertEqual((rec["tier"], rec["screens"]), (3, ["shell"]))
        screen_only = {"diff": dict(diff, changed_files=["web/src/pages/BoardPage.tsx"]), "triggers": [{"id": "screen_changed"}], "config": cfg}
        self.assertEqual(detect_ui.recommend(screen_only)["tier"], 2)
        many = {"diff": diff, "triggers": [{"id": "tokens_changed"}], "config": {"screens": [{"id": str(i), "source": ["web/src/components/shell/*"]} for i in range(4)]}}
        self.assertEqual(detect_ui.recommend(many)["tier"], 4)
        self.assertEqual(detect_ui.recommend({"diff": dict(diff, changed_files=[]), "triggers": [], "config": {}})["tier"], 2)

    def test_diff_parser_and_untracked(self):
        text = "diff --git a/x.tsx b/x.tsx\n--- a/x.tsx\n+++ b/x.tsx\n@@ -1,0 +2,2 @@\n+<nav>\n+</nav>\n-old\n"
        runner = kit.FakeRunner(kit.git_ok_rules(diff_text=text, names="x.tsx"))
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"new.html": "<main></main>\n"})
            diff = detect_ui.detect_diff(runner, tmp, None, ["new.html"])
        self.assertEqual(diff["changed_files"], ["new.html", "x.tsx"])
        self.assertEqual(diff["added_text"]["x.tsx"], [(2, "<nav>"), (3, "</nav>")])
        self.assertEqual(diff["added_text"]["new.html"], [(1, "<main></main>")])
        self.assertEqual(diff["base_commit"], "cafebabe" * 5)


class DetectEndToEndTestCase(unittest.TestCase):
    def test_detect_fake_git_and_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            _repo(tmp)
            rules = [("require.resolve(\"playwright\")", (0, "/w/node_modules/playwright/index.js\n", ""))] + kit.git_ok_rules(tracked=WEB_FILES)
            runner = kit.FakeRunner(rules)
            det = detect_ui.detect(tmp, runner=runner, which=lambda name: "/bin/" + name if name in ("node", "git", "npx") else None)
        self.assertEqual([s["kind"] for s in det["surfaces"]], ["web-react", "swift-source"])
        self.assertEqual(det["tools"]["playwright"], "/w/node_modules/playwright/index.js")
        self.assertEqual(det["against"], "git:origin/main")
        self.assertEqual(det["sides"]["reference"]["kind"], "git")
        self.assertFalse(det["sides"]["reference"].get("worktree_ready"))  # detect never creates the worktree
        self.assertIn("web/dist", det["runtime_hint"])                    # dist missing → runtime unavailable
        self.assertEqual(det["sides"]["subject"]["mode"]["structure"], "source")
        menu = {m["id"]: m for m in det["menu"]}
        self.assertEqual(menu["structure_source"]["kind"], "internal")
        self.assertEqual(menu["structure_runtime"]["kind"], "unavailable")
        self.assertEqual(menu["project_parity"]["kind"], "na")
        self.assertEqual(menu["opinion"]["circle"], "extended")
        self.assertEqual(det["thresholds"]["source"], "skill-defaults")
        self.assertTrue(all("git worktree add" not in c for c in runner.commands()))

    def test_detect_cli_exit_codes(self):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(detect_ui.main(["--repo", "/nonexistent/x"]), 2)
            with tempfile.TemporaryDirectory() as tmp:
                kit.make_repo(tmp, {"a.html": "<main></main>"})
                self.assertEqual(detect_ui.main(["--repo", tmp, "--against", "nope"]), 2)
                out = os.path.join(tmp, "d.json")
                self.assertEqual(detect_ui.main(["--repo", tmp, "--against", "design-system", "--out", out]), 0)
                self.assertTrue(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
