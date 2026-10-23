"""scripts/auto-deploy.sh 大版本边界（CONTRACT §56.3 第 2b 步，2026-10-23 v1.0.117 bridge）——真 bash + 真 git。

lite 2.0 是另一棵产品树（没有 actd）：1.x 的部署脚本若把它 ff 进来，就绪等待 / doctor
闸门会用旧脚本的标准判新树、回滚、标记 sha——每台公网 1.x 机器每 10 分钟一次。法条：
origin/main 上可达的最高 `v*` tag 的 major 大于本 checkout 的 major → 不 ff、不装、不问
CI，写 `major_boundary`（detail 点名 tag + 手动命令），**每个 tag 只通知一次**（镜像私账
`major_notified_tag`），exit 0；`--force` 同样不跨。

夹具复用 tests/integration/test_auto_deploy_script.py 的 ``AutoDeployFixture``（基线
checkout = v0.48.3，所以 v1.0.0 就是一次大版本跨越）。
"""
import time
import unittest

import tests.integration.test_auto_deploy_script as base
from act.lib import deploy_state

BUDGET_SECONDS = 90  # 7 runs of real bash+git (~1-2 s each standalone)
_T0 = [0.0]


def setUpModule():
    _T0[0] = time.monotonic()


def tearDownModule():
    elapsed = time.monotonic() - _T0[0]
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_auto_deploy_major_boundary.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))


@unittest.skipIf(base._WIN, "bash + install.sh are POSIX-only")
class MajorBoundaryTestCase(base.AutoDeployFixture):

    def test_new_major_on_main_is_never_fast_forwarded_and_notifies_once_per_tag(self):
        self.push("1.0.0")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "HEAD must not cross a major version")
        self.assertEqual(self.installs(), [], "install.sh never ran")
        self.assertEqual(self.ci_queries(), [], "refused before the CI gate: no API call")
        self.assertEqual(self.roster_queries(), [], "…and before the session gate")
        st = self.state()
        self.assertEqual(st["status"], "major_boundary")
        self.assertEqual(st["head"], self.base_sha)
        self.assertEqual(st["version"], "0.48.3")
        self.assertIn("v1.0.0", st["detail"])
        self.assertIn("git checkout v1.0.0 && bash install.sh", st["detail"])
        self.assertNotIn(str(self.live), st["detail"], "no local paths in the projected detail")
        self.assertEqual(self.mirror()["major_notified_tag"], "v1.0.0")
        self.assertNotIn("major_notified_tag", deploy_state.read(self.live / "state" / "deploy_state.json"),
                         "private bookkeeping is not projected")
        notes = self.notifications()
        self.assertEqual(len(notes), 1)
        self.assertIn("v1.0.0", notes[0])
        self.assertIn("git checkout v1.0.0 && bash install.sh", notes[0])
        self.assertIn("never crosses a major version", self.log_text())

        # every later 10-min pass: same verdict, no second notification
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.state()["status"], "major_boundary")
        self.assertEqual(len(self.notifications()), 1, "notify once, not every pass")
        self.assertEqual(self.head(), self.base_sha)

        # a newer tag of the new major is a new fact: one more notification
        self.push("1.0.1")
        self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(self.state()["status"], "major_boundary")
        self.assertIn("v1.0.1", self.state()["detail"])
        self.assertEqual(len(self.notifications()), 2)
        self.assertIn("v1.0.1", self.notifications()[1])
        self.assertEqual(self.installs(), [])

    def test_untagged_commits_behind_a_new_major_tag_are_refused_too(self):
        self.push("1.0.0")
        # a later merge not yet tagged still sits on top of the v1.0.0 tree
        (self.dev / "README.md").write_text("after the tag\n", encoding="utf-8")
        base._git(self.dev, "add", "-A")
        base._git(self.dev, "commit", "-q", "-m", "untagged")
        base._git(self.dev, "push", "-q", "origin", "main")
        self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(self.state()["status"], "major_boundary")
        self.assertIn("v1.0.0", self.state()["detail"])
        self.assertEqual(self.head(), self.base_sha)

    def test_force_does_not_cross_a_major_version(self):
        self.push("1.0.0")
        proc = self.run_script("--force", doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        self.assertEqual(self.installs(), [])
        self.assertEqual(self.state()["status"], "major_boundary")

    def test_same_major_still_deploys(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertNotIn("major_notified_tag", self.mirror())

    def test_doctor_row_warns_with_the_manual_upgrade_fix(self):
        self.push("1.0.0")
        self.run_script(doctor_plan=["-", "-"])
        row = deploy_state.auto_deploy_row(deploy_state.read(self.live / "state" / "deploy_state.json"))
        self.assertEqual(row["status"], "warn", row)
        self.assertIn("major_boundary", row["detail"])
        self.assertEqual(row["fix"], deploy_state.auto_deploy_fix(deploy_state.MAJOR_BOUNDARY))
        self.assertIn("bash install.sh", row["fix"])


if __name__ == "__main__":
    unittest.main()
