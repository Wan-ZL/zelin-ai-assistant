"""test-ui skill · `--against` 判例：七种写法各得什么 side 记录与仪器模式；别名来自 ui/parity/config.json 或内置
`native`；解析不出 → ReferenceError（列候选）；git: 只解析 sha、worktree 懒建、清理；marker 探针只打回环地址、
fetch 可注入；坏 config.json fail closed。零真子进程。

法典：docs/CONTRACT.md §62（native 别名 = 冻结源）；设计 vnext2-plan R2.8。
"""
import os
import tempfile
import unittest

from tests import skill_test_ui_testkit as kit

import reference as ref  # noqa: E402


class ConfigTestCase(unittest.TestCase):
    def test_builtin_native_alias_when_inventory_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ref.load_config(tmp), ({"references": {}}, None))
            kit.make_repo(tmp, {"ui/parity/native-inventory.json": "{}"})
            cfg, source = ref.load_config(tmp)
            self.assertEqual(sorted(cfg["references"]), ["native"])
            self.assertEqual(cfg["references"]["native"]["mode"], "frozen")
            self.assertIsNone(source)
            kit.make_repo(tmp, {"ui/parity/config.json": '{"references": {"v1": {"inventory": "x.json", "mode": "frozen"}}}'})
            cfg, source = ref.load_config(tmp)
            self.assertEqual(sorted(cfg["references"]), ["native", "v1"])
            self.assertEqual(source, "ui/parity/config.json")
            kit.make_repo(tmp, {"ui/parity/config.json": "{broken"})
            with self.assertRaises(ref.ReferenceError):
                ref.load_config(tmp)

    def test_default_against_order(self):
        runner = kit.FakeRunner(kit.git_ok_rules())
        self.assertEqual(ref.default_against({"references": {"native": {}}}, runner, "/r"), "native")
        self.assertEqual(ref.default_against({"references": {}}, runner, "/r"), "git:origin/main")
        no_git = kit.FakeRunner(default=(1, "", ""))
        self.assertEqual(ref.default_against({"references": {}}, no_git, "/r"), "design-system")


class ParseTestCase(unittest.TestCase):
    def test_forms(self):
        cfg = {"references": {"native": {}}}
        self.assertEqual(ref.parse_against("design-system", cfg)["kind"], "design-system")
        self.assertEqual(ref.parse_against("native", cfg), {"kind": "alias", "locator": "native"})
        self.assertEqual(ref.parse_against("git:origin/main", cfg), {"kind": "git", "locator": "origin/main"})
        self.assertEqual(ref.parse_against("url:http://127.0.0.1:1/", cfg)["kind"], "url")
        with self.assertRaises(ref.ReferenceError) as caught:
            ref.parse_against("nope", cfg)
        self.assertIn("candidates: native, design-system", str(caught.exception))


class SidesTestCase(unittest.TestCase):
    def test_alias_inventory_dir_design_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit.make_repo(tmp, {"ui/parity/native-inventory.json": "{}", "ui/tokens/native-tokens.json": "{}", "other/x.html": "<main></main>"})
            cfg, _ = ref.load_config(tmp)
            alias = ref.resolve_side(tmp, "native", cfg)
            self.assertEqual(alias["mode"], {"structure": "frozen", "tokens": "frozen", "visual": "na"})
            self.assertTrue(alias["resolved"].startswith("sha256:"))
            self.assertIsNone(alias["hint"])
            inventory = ref.resolve_side(tmp, "inventory:%s" % os.path.join(tmp, "ui/parity/native-inventory.json"), cfg)
            self.assertEqual(inventory["mode"]["structure"], "frozen")
            directory = ref.resolve_side(tmp, "dir:%s" % os.path.join(tmp, "other"), cfg)
            self.assertEqual(directory["mode"], {"structure": "source", "tokens": "source", "visual": "na"})
            design = ref.resolve_side(tmp, "design-system", cfg)
            self.assertEqual(design["mode"], {"structure": "na", "tokens": "source", "visual": "na"})
            with self.assertRaises(ref.ReferenceError):
                ref.resolve_side(tmp, "inventory:/nonexistent.json", cfg)
            with self.assertRaises(ref.ReferenceError):
                ref.resolve_side(tmp, "dir:/nonexistent", cfg)

    def test_alias_missing_inventory_gets_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {"references": {"v1": {"inventory": "gone.json", "produced_by": ["scripts/ui/x.py"], "mode": "frozen"}}}
            side = ref.resolve_side(tmp, "v1", cfg)
            self.assertEqual(side["mode"]["structure"], "na")
            self.assertIn("scripts/ui/x.py --out", side["hint"])

    def test_url_and_app(self):
        url = ref.resolve_side("/r", "url:http://127.0.0.1:4711/", {"references": {}})
        self.assertEqual(url["mode"], {"structure": "runtime", "tokens": "runtime", "visual": "na"})
        self.assertIn("VISUAL refused", url["hint"])
        with self.assertRaises(ref.ReferenceError):
            ref.resolve_side("/r", "url:ftp://x", {"references": {}})
        app = ref.resolve_side("/r", "app:python3 -m server --port 1", {"references": {}})
        self.assertEqual(app["launch"]["argv"], ["python3", "-m", "server", "--port", "1"])
        with self.assertRaises(ref.ReferenceError):
            ref.resolve_side("/r", "app:", {"references": {}})

    def test_git_side_lazy_worktree(self):
        runner = kit.FakeRunner(kit.git_ok_rules())
        side = ref.resolve_side("/r", "git:origin/main", {"references": {}}, runner, cache_dir="/tmp/tu-cache")
        self.assertEqual(side["resolved"], "sha:" + "90ceb713" * 5)
        self.assertEqual(side["worktree"], "/tmp/tu-cache/ref-" + ("90ceb713" * 5)[:12])
        self.assertFalse(side["worktree_ready"])
        self.assertFalse(any("worktree add" in c for c in runner.commands()))
        self.assertFalse(ref.remove_git_side("/r", side, runner))  # nothing to remove yet
        with tempfile.TemporaryDirectory() as tmp:
            side["worktree"] = os.path.join(tmp, "ref-x")
            adder = kit.FakeRunner([("worktree add", lambda argv, cwd: (os.makedirs(argv[-2]), kit.lc.RunResult(0, "", ""))[1])])
            self.assertEqual(ref.ensure_worktree("/r", side, adder), side["worktree"])
            self.assertTrue(side["worktree_ready"])
            self.assertEqual(ref.ensure_worktree("/r", side, kit.FakeRunner(default=(1, "", "no"))), side["worktree"])  # reuse, no call
            self.assertTrue(ref.remove_git_side("/r", side, kit.FakeRunner(default=(0, "", ""))))
        failing = kit.FakeRunner(default=(1, "", "fatal"))
        with self.assertRaises(ref.ReferenceError):
            ref.ensure_worktree("/r", {"worktree": "/tmp/tu-cache/nope", "sha": "x"}, failing)
        with self.assertRaises(ref.ReferenceError):
            ref.resolve_side("/r", "git:nope", {"references": {}}, kit.FakeRunner(default=(1, "", "")))


class MarkerTestCase(unittest.TestCase):
    def test_probe_marker(self):
        marker = {"path": "/api/health", "expr": ".demo == true"}
        self.assertTrue(ref.probe_marker("http://127.0.0.1:1", marker, fetch=lambda url: '{"demo": true, "ok": 1}'))
        self.assertFalse(ref.probe_marker("http://127.0.0.1:1", marker, fetch=lambda url: '{"demo": false}'))
        self.assertFalse(ref.probe_marker("http://127.0.0.1:1", marker, fetch=lambda url: "not json"))
        self.assertFalse(ref.probe_marker("http://example.com", marker, fetch=lambda url: '{"demo": true}'))  # non-loopback refused
        self.assertFalse(ref.probe_marker("http://127.0.0.1:1", None, fetch=lambda url: '{"demo": true}'))
        nested = {"path": "/h", "expr": ".seed.demo == \"yes\""}
        self.assertTrue(ref.probe_marker("http://localhost:9", nested, fetch=lambda url: '{"seed": {"demo": "yes"}}'))

    def test_subject_side(self):
        side = ref.subject_side("/r", "web-dom", True, {"server": ["x"], "seed": ["s"], "marker": {"path": "/h"}}, "abc", True)
        self.assertEqual(side["mode"], {"structure": "runtime", "tokens": "runtime", "visual": "runtime"})
        self.assertEqual(side["seed"]["recipe"], ["s"])
        self.assertFalse(side["seed"]["seeded_by_skill"])
        offline = ref.subject_side("/r", "web-dom", False, None)
        self.assertEqual(offline["mode"], {"structure": "source", "tokens": "source", "visual": "na"})


if __name__ == "__main__":
    unittest.main()
