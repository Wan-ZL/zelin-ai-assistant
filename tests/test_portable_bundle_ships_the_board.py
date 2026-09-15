"""CONTRACT §49 追记 (2026-09-14, owner 决策 D67) + §56.2 的发布资产：便携包必须
自带那块看板。

`install-linux.sh` / `install.ps1` 把 `python -m server` 注册成常驻 UI，所以
`scripts/package-portable.sh` 的文件集里少了 `server/` 或 `web/`，
docs/WINDOWS.md「Option A — download the release bundle」（文档推荐的主装路径）
装完就是一个只会 `ModuleNotFoundError` 的任务 + 零 UI，而 §73.5 的
「三平台发的是同一个 server/、同一份 web/dist」会变成谎话。

钉三件事：
  * `COMMON` 里有 `act` / `ingest` / `server` / `web`（`webui` 留作手跑回落）；
  * `scrub` 把 `web/node_modules` 与测试材料删掉——live checkout 的 npm 树有几百
    MB，而 `web/src/parity.test.tsx` 读的是仓库根下的 `ui/parity/`（包里没有）；
  * `.github/workflows/release.yml` 在跑 `package-portable.sh` **之前**
    `npm ci && npm run build`，所以包里那份 `web/dist` 是真的。

不真跑打包脚本（cp -R 整棵树 + tar 在单测里太贵）：这条判例钉的是文件集本身。
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGER = REPO / "scripts" / "package-portable.sh"
RELEASE = REPO / ".github" / "workflows" / "release.yml"


def _bash_array(text: str, name: str) -> list:
    """Items of a `NAME=(\n  a\n  b\n)` bash array, comments stripped."""
    body = text.split(name + "=(", 1)[1].split("\n)", 1)[0]
    items = []
    for raw in body.splitlines():
        line = re.sub(r"#.*$", "", raw).strip()
        if line:
            items.append(line)
    return items


class PortableBundleFileSetTestCase(unittest.TestCase):
    def setUp(self):
        self.text = PACKAGER.read_text(encoding="utf-8")
        self.common = _bash_array(self.text, "COMMON")

    def test_the_bundle_carries_the_board_server_and_the_web_sources(self):
        for needed in ("act", "ingest", "server", "web"):
            self.assertIn(needed, self.common)

    def test_every_bundled_path_exists_in_the_repo(self):
        for item in self.common:
            self.assertTrue((REPO / item).exists(),
                            "package-portable.sh copies a missing path: %s" % item)

    def test_the_npm_tree_and_the_web_tests_never_ship(self):
        self.assertIn("tar -cf - --exclude 'node_modules' web", self.text)
        self.assertIn('rm -rf "$dir/web/node_modules"', self.text)
        self.assertIn('"$dir/web/e2e"', self.text)
        self.assertIn("-name '*.test.tsx'", self.text)

    def test_the_swift_app_and_the_suite_stay_out(self):
        for excluded in ("mac", "ios", "shared", "tests", "supabase"):
            self.assertNotIn(excluded, self.common)


class ReleaseWorkflowBuildsTheBoardTestCase(unittest.TestCase):
    """The prebuilt `web/dist` in the bundle only exists if CI builds it first."""

    def setUp(self):
        self.text = RELEASE.read_text(encoding="utf-8")

    def test_the_board_is_built_before_the_bundles_are_packaged(self):
        build = self.text.index("npm run build")
        package = self.text.index("bash scripts/package-portable.sh")
        self.assertLess(build, package)
        self.assertIn("npm ci", self.text[:build])
        self.assertIn("working-directory: web", self.text[:build])
