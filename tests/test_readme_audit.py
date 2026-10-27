"""README 主张审计器的判据（docs/CONTRACT.md §58 质量仪表 + §77.5 README 真实性审计）。

钉四件事：抽取（bullet / prose / table / code / mermaid 各归各类）、路径判据
（不存在 = stale，运行时路径放行）、退役面判据（问问助手 / iMessage / 菜单栏
app / mac 构建指令 / 非当前 tag 的版本字面量）、UI 标签判据（§66 owner=web
清单 + web/src 文案源里查不到的引号文案 = stale）。以及 `--summary` 的逐字格式
`README claims=<n> stale=<m> images=<k>`——它是 goal 的验收行，改一个字符就是
改契约。
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

_QA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import readme_audit  # noqa: E402


def _fake_repo(readme, extra_files=()):
    """最小仓库：README + web/src 一个文案源（§66 清单 v2.0.0 退役，不再是标签源）。"""
    root = tempfile.mkdtemp(prefix="readme-audit-")
    os.makedirs(os.path.join(root, "web", "src"))
    os.makedirs(os.path.join(root, "docs", "images"))
    with open(os.path.join(root, "web", "src", "i18n.ts"), "w") as fh:
        fh.write('export const strings = { review: "In review" };\n')
    with open(os.path.join(root, "README.md"), "w") as fh:
        fh.write(readme)
    for rel in extra_files:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("x")
    return root


class ExtractClaimsTest(unittest.TestCase):
    def test_kinds(self):
        claims = readme_audit.extract_claims(
            "# Title\n\nOne sentence. Two sentences.\n\n- a bullet\n\n"
            "| col | col |\n|---|---|\n| cell | cell |\n\n"
            "```bash\nbash install.sh\n```\n\n"
            "```mermaid\nA[\"node\"] --> B\n```\n")
        kinds = [c.kind for c in claims]
        self.assertEqual(kinds.count("prose"), 2)
        self.assertEqual(kinds.count("bullet"), 1)
        self.assertEqual(kinds.count("table"), 2)  # 表头 + 数据行；分隔行是噪声
        self.assertEqual(kinds.count("code"), 1)
        self.assertEqual(kinds.count("diagram"), 1)

    def test_badges_and_html_shell_are_not_claims(self):
        claims = readme_audit.extract_claims(
            "[![CI](https://x/badge.svg)](https://x)\n<p align=\"center\">\n</p>\n")
        self.assertEqual(claims, [])

    def test_claim_index_is_1_based_and_dense(self):
        claims = readme_audit.extract_claims("- one\n- two\n")
        self.assertEqual([c.index for c in claims], [1, 2])


class PathClaimTest(unittest.TestCase):
    def test_missing_path_is_stale(self):
        root = _fake_repo("- see [the guide](docs/NOPE.md)\n")
        report = readme_audit.audit(root, tag="v1.0.114")
        self.assertEqual(len(report.stale), 1)
        self.assertIn("docs/NOPE.md", report.stale[0].reasons[0])

    def test_existing_path_is_ok(self):
        root = _fake_repo("- see [the guide](docs/INSTALL.md)\n",
                          extra_files=("docs/INSTALL.md",))
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_runtime_paths_are_not_stale(self):
        root = _fake_repo("- the board reads `state/dashboard.json` from `web/dist`\n")
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_home_path_with_a_space_is_one_token(self):
        """`~/Library/Application Support/…` 不被切成 `Support/…` 这条假路径。"""
        self.assertEqual(readme_audit._candidate_paths(
            "a dir under `~/Library/Application Support/Example/claude-config/`"), [])

    def test_prose_slash_is_not_a_path(self):
        root = _fake_repo("- launchd/cron scheduling with export/cleanup scripts\n")
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_command_line_inside_a_fence_is_path_checked(self):
        root = _fake_repo("```bash\nbash scripts/ghost.sh\n```\n")
        report = readme_audit.audit(root, tag="v1.0.114")
        self.assertEqual([c.kind for c in report.stale], ["code"])

    def test_mermaid_node_text_is_not_a_path_claim(self):
        root = _fake_repo("```mermaid\nA[\"recaps / three of them\"] --> B\n```\n")
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_glob_resolves(self):
        root = _fake_repo("- the recap helpers live in `act/lib/recap_*.py`\n",
                          extra_files=("act/lib/recap_text.py",))
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])


class RetiredSurfaceTest(unittest.TestCase):
    def _reasons(self, text):
        root = _fake_repo(text)
        return [r for c in readme_audit.audit(root, tag="v1.0.114").stale
                for r in c.reasons]

    def test_ask_page(self):
        self.assertIn("Ask page", self._reasons("- open the Ask page to chat\n")[0])

    def test_imessage(self):
        self.assertIn("iMessage", self._reasons("- approve from iMessage\n")[0])

    def test_menu_bar_app(self):
        self.assertIn("menu-bar", self._reasons("- click the menu bar icon\n")[0])

    def test_mac_build_instructions(self):
        self.assertIn("mac/", self._reasons("- run `mac/scripts/build.sh`\n")[0])

    def test_stale_version_literal(self):
        reasons = self._reasons("- removed in v0.21 as announced\n")
        self.assertIn("v0.21", reasons[0])
        self.assertIn("v1.0.114", reasons[0])

    def test_current_tag_literal_is_ok(self):
        self.assertEqual(self._reasons("- this page describes v1.0.114\n"), [])

    def test_unknown_tag_disables_the_version_judgement(self):
        """浅 clone / 没有 tag 的 checkout 上不判版本——宁可少判，不可乱判。"""
        root = _fake_repo("- removed in v0.21 as announced\n")
        self.assertEqual(readme_audit.audit(root, tag="").stale, [])

    def test_os_versions_are_not_version_literals(self):
        self.assertEqual(self._reasons("- needs macOS 14+ and Python 3.9+\n"), [])

    def test_lite_2_0_retired_surfaces(self):
        """v2.0.0 退役的面在 Removed 块之外出现 = stale（§77.5 追记）。"""
        cases = {
            "- turn on Live captions in the header\n": "live captions",
            "- the Slack radar mints a card\n": "radars",
            "- drag a card across the kanban\n": "board",
            "- double-click the .pkg to install\n": ".pkg",
            "- every merge auto-deploys to your Mac\n": "auto-deploy",
            "- actd dispatches approved work\n": "Act daemon",
        }
        for text, needle in cases.items():
            with self.subTest(text=text):
                reasons = self._reasons(text)
                self.assertTrue(reasons and needle in reasons[0], reasons)

    def test_lite_vocabulary_is_not_retired(self):
        self.assertEqual(self._reasons(
            "- record the screen, ingest notes into Obsidian, get meeting recaps\n"), [])


class RemovedBlockTest(unittest.TestCase):
    """一个围栏「Removed in 2.0」块免判（§77.5 追记）；第二个块 stale。"""

    BODY = ("menu-bar app and Live captions\n"
            "`mac/build.sh`, `install-linux.sh`, the .pkg installer\n"
            "last 1.x = v1.0.117 (tag full)\n")

    def _stale(self, readme):
        return readme_audit.audit(_fake_repo(readme), tag="v2.0.0").stale

    def test_removed_language_fence_is_exempt(self):
        self.assertEqual(self._stale("```removed\n" + self.BODY + "```\n"), [])

    def test_removed_heading_line_fence_is_exempt(self):
        self.assertEqual(self._stale("```text\nRemoved in 2.0\n" + self.BODY + "```\n"), [])

    def test_chinese_heading_line_is_recognised(self):
        self.assertEqual(self._stale("```\n2.0 删除的东西\n" + self.BODY + "```\n"), [])

    def test_claims_are_kind_removed(self):
        claims = readme_audit.extract_claims("```removed\nmenu-bar app\n```\n- after\n")
        self.assertEqual([c.kind for c in claims], ["removed", "bullet"])

    def test_heading_only_counts_on_the_first_line(self):
        """块中间才出现的 Removed in 2.0 不把整块变成免判块。"""
        stale = self._stale("```text\nmenu-bar app\nRemoved in 2.0\n```\n")
        self.assertEqual(len(stale), 1)

    def test_removed_fence_with_a_removed_heading_is_one_block(self):
        """```removed 围栏的第一行再写 Removed in 2.0：还是第一个块，不被当成第二个。"""
        self.assertEqual(self._stale("```removed\nRemoved in 2.0\n" + self.BODY + "```\n"), [])

    def test_second_block_is_stale(self):
        readme = ("```removed\nmenu-bar app\n```\n"
                  "text between\n"
                  "```removed\nLive captions\n```\n")
        stale = self._stale(readme)
        self.assertEqual(len(stale), 1)
        self.assertIn("only one 'Removed in 2.0' block", stale[0].reasons[0])
        self.assertEqual(stale[0].text, "Live captions")

    def test_outside_the_block_is_still_judged(self):
        stale = self._stale("```removed\nmenu-bar app\n```\n- click the menu bar icon\n")
        self.assertEqual([c.kind for c in stale], ["bullet"])


def _git(root, *args):
    subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
                   + list(args), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class FullRefTest(unittest.TestCase):
    """`full` 标签 / 分支：提到它的主张里，本树没有的路径到 full 那棵树上查（§77.5 追记）。"""

    def _repo_with_full(self, readme):
        root = _fake_repo(readme, extra_files=("docs/OLD.md",))
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "1.x")
        _git(root, "tag", "full")
        _git(root, "rm", "-q", "docs/OLD.md")
        _git(root, "commit", "-q", "-m", "2.0")
        return root

    def _stale(self, root):
        return readme_audit.audit(root, tag="v2.0.0").stale

    def test_path_at_full_is_ok_when_the_claim_names_full(self):
        root = self._repo_with_full("- the old guide is at tag `full`: `docs/OLD.md`\n")
        self.assertEqual(self._stale(root), [])

    def test_full_object_syntax(self):
        root = self._repo_with_full("- `git show full:docs/OLD.md`\n")
        self.assertEqual(self._stale(root), [])

    def test_missing_at_full_is_stale(self):
        root = self._repo_with_full("- `git show full:docs/NOPE.md`\n")
        stale = self._stale(root)
        self.assertEqual(len(stale), 1)
        self.assertIn("nor at tag full", stale[0].reasons[0])

    def test_claim_without_full_is_judged_on_this_tree(self):
        root = self._repo_with_full("- read `docs/OLD.md`\n")
        self.assertEqual(len(self._stale(root)), 1)

    def test_no_full_ref_means_unjudgeable(self):
        """本地还没有 full（S2 之前、浅 clone）= 放行，不乱判。"""
        root = _fake_repo("- the old guide is at tag `full`: `docs/GONE.md`\n")
        self.assertEqual(readme_audit.audit(root, tag="v2.0.0").stale, [])

    def test_plain_full_literal_is_not_a_path(self):
        root = self._repo_with_full("```\ngit checkout full && bash install.sh\n```\n")
        _git(root, "commit", "-q", "--allow-empty", "-m", "noop")
        with open(os.path.join(root, "install.sh"), "w") as fh:
            fh.write("x")
        self.assertEqual(self._stale(root), [])


class LabelClaimTest(unittest.TestCase):
    def test_a_leftover_parity_inventory_is_not_a_label_source(self):
        root = _fake_repo('- click the "Approve" button on the card\n',
                          extra_files=("ui/parity/native-inventory.json",))
        with open(os.path.join(root, "ui", "parity", "native-inventory.json"), "w") as fh:
            fh.write('{"controls": [{"owner": "web", "en": "Approve"}]}')
        self.assertEqual(len(readme_audit.audit(root, tag="v1.0.114").stale), 1)

    def test_label_from_web_sources_is_ok(self):
        root = _fake_repo('- the "In review" lane holds finished work\n')
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_unknown_label_is_stale(self):
        root = _fake_repo('- press the "Teleport" button on the board\n')
        stale = readme_audit.audit(root, tag="v1.0.114").stale
        self.assertEqual(len(stale), 1)
        self.assertIn("Teleport", stale[0].reasons[0])

    def test_retired_owner_label_is_stale(self):
        """退役面的文案（Ask）不算「web app 渲染得出来」。"""
        root = _fake_repo('- the "Ask" tab answers questions\n')
        self.assertTrue(readme_audit.audit(root, tag="v1.0.114").stale)

    def test_html_attribute_values_are_not_labels(self):
        root = _fake_repo('<p align="center"><sub>the board page</sub></p>\n')
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_quotes_without_ui_context_are_not_labels(self):
        root = _fake_repo('The music is "Voxel Revolution" by Kevin MacLeod.\n')
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_render_mode_requires_the_label_on_screen(self):
        root = _fake_repo('- the "In review" lane holds finished work\n')
        clean = readme_audit.audit(root, tag="v1.0.114",
                                   rendered="In review / Done")
        self.assertEqual(clean.stale, [])
        missing = readme_audit.audit(root, tag="v1.0.114", rendered="Done only")
        self.assertIn("not rendered by the running web app",
                      missing.stale[0].reasons[0])


class SummaryAndOutputTest(unittest.TestCase):
    def test_summary_line_is_verbatim(self):
        root = _fake_repo("- a bullet about `docs/NOPE.md`\n"
                          "![shot](docs/images/board-light.png)\n",
                          extra_files=("docs/images/board-light.png",))
        report = readme_audit.audit(root, tag="v1.0.114")
        self.assertEqual(report.summary(), "README claims=2 stale=1 images=1")

    def test_images_are_deduped(self):
        root = _fake_repo("![a](docs/images/x.png) and again docs/images/x.png\n",
                          extra_files=("docs/images/x.png",))
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").images,
                         ["docs/images/x.png"])

    def test_markdown_table_has_one_row_per_claim(self):
        root = _fake_repo("- one bullet\n- two bullets\n")
        report = readme_audit.audit(root, tag="v1.0.114")
        body = readme_audit.render_markdown(report, "v1.0.114")
        self.assertIn("| # | line | kind | verdict | reason | claim |", body)
        self.assertEqual(body.count("| ok |"), 2)
        self.assertIn("README claims=2 stale=0 images=0", body)

    def test_main_summary_prints_exactly_one_line(self):
        root = _fake_repo("- a clean bullet\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = readme_audit.main(["--repo", root, "--tag", "v1.0.114",
                                      "--summary"])
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue(), "README claims=1 stale=0 images=0\n")

    def test_check_exits_1_when_stale(self):
        root = _fake_repo("- broken `docs/NOPE.md`\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = readme_audit.main(["--repo", root, "--tag", "v1.0.114",
                                      "--summary", "--check"])
        self.assertEqual(code, 1)

    def test_out_writes_the_default_report_path(self):
        root = _fake_repo("- a clean bullet\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            readme_audit.main(["--repo", root, "--tag", "v1.0.114", "--out"])
        self.assertTrue(os.path.exists(
            os.path.join(root, "qa", "coverage-report", "readme-audit.md")))


if __name__ == "__main__":
    unittest.main()
