"""README 主张审计器的判据（docs/CONTRACT.md §58 质量仪表 + §66 UI 对齐清单）。

钉四件事：抽取（bullet / prose / table / code / mermaid 各归各类）、路径判据
（不存在 = stale，运行时路径放行）、退役面判据（问问助手 / iMessage / 菜单栏
app / mac 构建指令 / 非当前 tag 的版本字面量）、UI 标签判据（§66 owner=web
清单 + web/src 文案源里查不到的引号文案 = stale）。以及 `--summary` 的逐字格式
`README claims=<n> stale=<m> images=<k>`——它是 goal 的验收行，改一个字符就是
改契约。
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from tests.scratch_testkit import scratch_dir

_QA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa")
if _QA_DIR not in sys.path:
    sys.path.insert(0, _QA_DIR)

import readme_audit  # noqa: E402


def _fake_repo(case, readme, extra_files=()):
    """最小仓库：README + ui/parity 清单 + web/src 一个文案源。"""
    root = scratch_dir(case, prefix="readme-audit-")
    os.makedirs(os.path.join(root, "ui", "parity"))
    os.makedirs(os.path.join(root, "web", "src"))
    os.makedirs(os.path.join(root, "docs", "images"))
    with open(os.path.join(root, "ui", "parity", "native-inventory.json"), "w") as fh:
        fh.write('{"controls": [{"id": "control:board:button:approve", '
                 '"owner": "web", "en": "Approve", "zh": "\\u6279\\u51c6"},'
                 '{"id": "control:x", "owner": "retired", "en": "Ask"}]}')
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
        root = _fake_repo(self, "- see [the guide](docs/NOPE.md)\n")
        report = readme_audit.audit(root, tag="v1.0.114")
        self.assertEqual(len(report.stale), 1)
        self.assertIn("docs/NOPE.md", report.stale[0].reasons[0])

    def test_existing_path_is_ok(self):
        root = _fake_repo(self, "- see [the guide](docs/INSTALL.md)\n",
                          extra_files=("docs/INSTALL.md",))
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_runtime_paths_are_not_stale(self):
        root = _fake_repo(self, "- the board reads `state/dashboard.json` from `web/dist`\n")
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_prose_slash_is_not_a_path(self):
        root = _fake_repo(self, "- launchd/cron scheduling with Slack/Gmail radars\n")
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_command_line_inside_a_fence_is_path_checked(self):
        root = _fake_repo(self, "```bash\nbash scripts/ghost.sh\n```\n")
        report = readme_audit.audit(root, tag="v1.0.114")
        self.assertEqual([c.kind for c in report.stale], ["code"])

    def test_mermaid_node_text_is_not_a_path_claim(self):
        root = _fake_repo(self, "```mermaid\nA[\"radars / three of them\"] --> B\n```\n")
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_glob_resolves(self):
        root = _fake_repo(self, "- three radars live in `act/radar*.py`\n",
                          extra_files=("act/radar_slack.py",))
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])


class RetiredSurfaceTest(unittest.TestCase):
    def _reasons(self, text):
        root = _fake_repo(self, text)
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
        root = _fake_repo(self, "- removed in v0.21 as announced\n")
        self.assertEqual(readme_audit.audit(root, tag="").stale, [])

    def test_os_versions_are_not_version_literals(self):
        self.assertEqual(self._reasons("- needs macOS 14+ and Python 3.9+\n"), [])


class LabelClaimTest(unittest.TestCase):
    def test_label_in_inventory_is_ok(self):
        root = _fake_repo(self, '- click the "Approve" button on the card\n')
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_label_from_web_sources_is_ok(self):
        root = _fake_repo(self, '- the "In review" lane holds finished work\n')
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_unknown_label_is_stale(self):
        root = _fake_repo(self, '- press the "Teleport" button on the board\n')
        stale = readme_audit.audit(root, tag="v1.0.114").stale
        self.assertEqual(len(stale), 1)
        self.assertIn("Teleport", stale[0].reasons[0])

    def test_retired_owner_label_is_stale(self):
        """清单里 owner=retired 的文案（Ask）不算「web app 渲染得出来」。"""
        root = _fake_repo(self, '- the "Ask" tab answers questions\n')
        self.assertTrue(readme_audit.audit(root, tag="v1.0.114").stale)

    def test_html_attribute_values_are_not_labels(self):
        root = _fake_repo(self, '<p align="center"><sub>the board page</sub></p>\n')
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_quotes_without_ui_context_are_not_labels(self):
        root = _fake_repo(self, 'The music is "Voxel Revolution" by Kevin MacLeod.\n')
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").stale, [])

    def test_render_mode_requires_the_label_on_screen(self):
        root = _fake_repo(self, '- click the "Approve" button on the card\n')
        clean = readme_audit.audit(root, tag="v1.0.114",
                                   rendered="Approve Reject")
        self.assertEqual(clean.stale, [])
        missing = readme_audit.audit(root, tag="v1.0.114", rendered="Reject only")
        self.assertIn("not rendered by the running web app",
                      missing.stale[0].reasons[0])


class SummaryAndOutputTest(unittest.TestCase):
    def test_summary_line_is_verbatim(self):
        root = _fake_repo(self, "- a bullet about `docs/NOPE.md`\n"
                          "![shot](docs/images/board-light.png)\n",
                          extra_files=("docs/images/board-light.png",))
        report = readme_audit.audit(root, tag="v1.0.114")
        self.assertEqual(report.summary(), "README claims=2 stale=1 images=1")

    def test_images_are_deduped(self):
        root = _fake_repo(self, "![a](docs/images/x.png) and again docs/images/x.png\n",
                          extra_files=("docs/images/x.png",))
        self.assertEqual(readme_audit.audit(root, tag="v1.0.114").images,
                         ["docs/images/x.png"])

    def test_markdown_table_has_one_row_per_claim(self):
        root = _fake_repo(self, "- one bullet\n- two bullets\n")
        report = readme_audit.audit(root, tag="v1.0.114")
        body = readme_audit.render_markdown(report, "v1.0.114")
        self.assertIn("| # | line | kind | verdict | reason | claim |", body)
        self.assertEqual(body.count("| ok |"), 2)
        self.assertIn("README claims=2 stale=0 images=0", body)

    def test_main_summary_prints_exactly_one_line(self):
        root = _fake_repo(self, "- a clean bullet\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = readme_audit.main(["--repo", root, "--tag", "v1.0.114",
                                      "--summary"])
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue(), "README claims=1 stale=0 images=0\n")

    def test_check_exits_1_when_stale(self):
        root = _fake_repo(self, "- broken `docs/NOPE.md`\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = readme_audit.main(["--repo", root, "--tag", "v1.0.114",
                                      "--summary", "--check"])
        self.assertEqual(code, 1)

    def test_out_writes_the_default_report_path(self):
        root = _fake_repo(self, "- a clean bullet\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            readme_audit.main(["--repo", root, "--tag", "v1.0.114", "--out"])
        self.assertTrue(os.path.exists(
            os.path.join(root, "qa", "coverage-report", "readme-audit.md")))


if __name__ == "__main__":
    unittest.main()
