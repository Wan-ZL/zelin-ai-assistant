"""`systemctl --user list-units` 这张表的解析合同（CONTRACT §25 / §49 / §55）。

tests/test_doctor.py 的 `SystemdDoctorTestCase` 喂的 fixture 全是「六列齐全、
描述非空」的那一行（`_systemctl` 拼出来的就是这一种），tests/
test_doctor_service_orphans.py 也一样——夜间变异（§57）因此在
`_systemd_table` 的那一句列数闸门与 `_unit_row` 的短名推导上留了一串存活体：
`len(parts) >= 4` 改成 `> 4` / `>= 3` / `>= 5`、`and` 改成 `or`、
`rsplit(".", 1)` 改成 `rsplit(".", 2)`，判例照样全绿。

这里钉的是那张表的三句合同：

* **四列就是一条记录**：UNIT / LOAD / ACTIVE / SUB 齐了就够——描述列可以为空
  （`systemctl --user list-units --plain --no-legend` 加一个空 `Description=`
  就是这个形状）。因为少了描述就把 unit 判成「没注册」，会在一台健康的 Linux
  机器上凭空印出一条 FAIL 和一句「跑 install-linux.sh」。
* **不足四列的残行只能被跳过**：`_systemd_table` **没有** try 兜底（不像
  `core.launchctl_table`），一个 `parts[3]` 的 IndexError 会把整个 doctor run
  炸成一行 `diagnostic crashed`——探针不许崩（宪法第 11 条）。
* **短名只剥最后一段类型后缀**：leaf 里的点属于 canonical slug（防腐 #9，
  模块名/unit 名/label/task leaf 同源），多剥一层会让看板上的行名和 fix 里的
  `systemctl --user enable --now <unit>` 指着两个不同的名字。

**两个体判为等价（可达输入上无可观察差异，不强杀）**：`_task_row` 与
`_task_retire_fix` 里的 `full.rsplit("\\\\", 1)[-1]`（maxsplit `1 → 2`）——对任何
字符串与任何 `maxsplit >= 1`，`rsplit(sep, n)[-1]` 恒等于「最后一个分隔符之后
的那一段」，两个算子逐字同解。（`_unit_row` 的 `rsplit(".", 1)[0]` 取的是
**第一段**，那个不等价，上面用带点的 leaf 钉住了。）
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import

from act import doctor
from act.lib import config, taskscheduler
from act.lib.checks import services

# 六列齐全的正常行（UNIT LOAD ACTIVE SUB + 描述），与 test_doctor.py 同形。
FULL = "  %-28s loaded %-9s %-8s Zelin AI Assistant\n"
# 四列：描述为空（--plain --no-legend + 空 Description=）。
BARE = "  %s loaded %s %s\n"


def _probes(listing: str, units):
    return doctor.Probes(launchctl_list=lambda: listing, systemd_units=list(units))


def _task_probes(listing: str, tasks):
    return doctor.Probes(launchctl_list=lambda: listing,
                         scheduled_tasks=list(tasks))


def _schtasks(rows) -> str:
    """`schtasks /query /fo LIST /v` 文本；值为 None 的字段整条不出现。"""
    out = []
    for task, fields in rows.items():
        block = ["Folder: \\ZelinAIAssistant", "TaskName: %s" % task]
        block += ["%s: %s" % (k, v) for k, v in fields.items() if v is not None]
        out.append("\n".join(block) + "\n\n")
    return "".join(out)


def _empty_home():
    """一个连 act/ 都没有的 AIASSISTANT_HOME（期望集合 = 空集）。"""
    tmp = tempfile.mkdtemp(prefix="zai-no-templates-")
    return mock.patch.object(config, "HOME", Path(tmp))


class _UnreadableHome:
    """扫不动的 HOME：模板目录存在与否都读不出来（权限 / 竞态 / 卸载的卷）。

    `iterdir` 与 `glob` 两条路都抛 —— 两个 `_templated_*` 各走一条。
    """

    def __truediv__(self, other):
        return self

    def __str__(self):
        return "<unreadable home>"

    def iterdir(self):
        raise OSError("permission denied")

    def glob(self, pattern):
        raise OSError("permission denied")


def _by_name(rows):
    return {r.name: r for r in rows}


class UnitTableColumnsTestCase(unittest.TestCase):
    """列数闸门：四列成行，残行跳过。"""

    UNITS = ["zelin-actd.service", "zelin-gmail-radar.timer"]

    def test_a_unit_line_without_a_description_still_parses(self):
        # 描述列为空 = 四列。健康的 unit 不许因为「没有描述」被读成没注册。
        text = (BARE % ("zelin-actd.service", "active", "running")
                + BARE % ("zelin-gmail-radar.timer", "active", "waiting"))
        by = _by_name(services.check_systemd(_probes(text, self.UNITS)))
        self.assertEqual(by["actd"].status, doctor.OK)
        self.assertIn("active (running)", by["actd"].detail)
        self.assertEqual(by["gmail-radar"].status, doctor.OK)

    def test_a_truncated_line_is_skipped_and_never_breaks_the_table(self):
        # 半行（管道被提前关掉 / 输出被截断）只能被跳过：这张表的解析没有
        # try 兜底，一个 IndexError 会把整个 doctor run 变成 `diagnostic crashed`。
        # 而且后面那些完整的行必须照样读出来，不许被前面的残行打断。
        text = ("  zelin-actd.service loaded\n"                     # 两列
                "  zelin-gmail-radar.timer loaded active\n"         # 三列
                + FULL % ("zelin-actd.service", "active", "running"))
        by = _by_name(services.check_systemd(_probes(text, self.UNITS)))
        self.assertEqual(by["actd"].status, doctor.OK)
        # 三列的那一行没有 SUB 列 → 不成记录 → 这个 timer 读作「没注册」
        self.assertEqual(by["gmail-radar"].status, doctor.WARN)
        self.assertIn("not registered", by["gmail-radar"].detail)

    def test_only_service_and_timer_rows_become_records(self):
        # 表头 / legend / 别的 unit 类型（.socket、.mount）都不是我们的行。
        text = ("UNIT LOAD ACTIVE SUB DESCRIPTION\n"
                + FULL % ("dbus.socket", "active", "running")
                + FULL % ("zelin-actd.service", "active", "running")
                + "2 loaded units listed. Pass --all to see loaded but inactive units.\n")
        by = _by_name(services.check_systemd(_probes(text, self.UNITS)))
        self.assertEqual(by["actd"].status, doctor.OK)
        self.assertEqual(by["gmail-radar"].status, doctor.WARN)


class UnitShortNameTestCase(unittest.TestCase):
    """短名 = unit 名剥掉 `zelin-` 前缀与**最后一段**类型后缀。"""

    def test_a_dotted_leaf_keeps_its_dots(self):
        # leaf 是 canonical slug（防腐 #9）：`.timer` 是类型后缀，leaf 里的点
        # 不是。多剥一层 = 看板上那一行叫 `daily`，而 fix 里让 owner
        # `systemctl --user enable --now zelin-daily.loop.timer`——两个名字。
        unit = "zelin-daily.loop.timer"
        text = FULL % (unit, "inactive", "dead")
        rows = services.check_systemd(_probes(text, [unit]))
        self.assertEqual([r.name for r in rows], ["daily.loop"])
        self.assertIn("systemctl --user enable --now %s" % unit, rows[0].fix)

    def test_the_shipped_units_keep_their_slug(self):
        rows = services.check_systemd(
            _probes("", ["zelin-actd.service", "zelin-weekly-digest.timer"]))
        self.assertEqual([r.name for r in rows], ["actd", "weekly-digest"])


class TaskStatusVocabularyTestCase(unittest.TestCase):
    """schtasks 的 `Status` 只有三个词是好消息，其余一律**原样报出来**。"""

    ACTD = "\\ZelinAIAssistant\\actd"

    def test_an_unrecognised_status_is_reported_verbatim(self):
        # docs/WINDOWS.md：schtasks 不暴露「registered 但在崩循环」。所以凡是
        # 不认识的状态都必须把原词印给 owner + 给一条能自己去看的命令——
        # 吞掉它等于把一台不工作的机器报成健康。
        text = _schtasks({self.ACTD: {"Status": "Could not start",
                                      "Scheduled Task State": "Enabled"}})
        row = services.check_scheduled_tasks(_task_probes(text, [self.ACTD]))[0]
        self.assertEqual(row.status, doctor.FAIL)
        self.assertIn("Could not start", row.detail)
        self.assertIn("not ready/running", row.detail)
        self.assertIn("schtasks /Query", row.fix)
        self.assertEqual(row.failure_id, "agent_unloaded")

    def test_a_missing_status_field_reads_as_unknown(self):
        # `Status:` 整条不见（LIST 输出被裁 / schtasks 版本差异）：detail 里
        # 必须出现 'unknown' 这个词，而不是一对空引号——空引号看起来像
        # 「状态是空字符串」，那是另一件事。
        text = _schtasks({self.ACTD: {"Status": None,
                                      "Scheduled Task State": "Enabled"}})
        row = services.check_scheduled_tasks(_task_probes(text, [self.ACTD]))[0]
        self.assertEqual(row.status, doctor.FAIL)
        self.assertIn("'unknown'", row.detail)

    def test_no_task_templates_is_one_warn_row_not_a_missing_row(self):
        # 期望集合空 = checkout 不完整。这一行必须存在（而且只有一行），
        # 否则 Windows 上整个任务家族会从报告里静默消失。
        row = services.check_scheduled_tasks(_task_probes("", []))
        self.assertEqual(row.name, "scheduled tasks")
        self.assertEqual(row.status, doctor.WARN)
        self.assertIn("act/tasksched", row.fix)


class OrphanFileFaceTestCase(unittest.TestCase):
    """§55 孤儿探测的文件面：读不到 = 空清单，绝不是 None、绝不抛。"""

    def test_only_prefixed_unit_files_are_listed_and_sorted(self):
        tmp = tempfile.mkdtemp(prefix="zai-xdg-")
        unit_dir = Path(tmp) / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        for name in ("zelin-server.service", "zelin-actd.service",
                     "other-vendor.service"):
            (unit_dir / name).write_text("[Unit]\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            self.assertEqual(services.installed_user_units(),
                             ["zelin-actd.service", "zelin-server.service"])

    def test_an_unreadable_unit_dir_is_an_empty_list(self):
        # 目录不可读（权限 / 竞态）= 这一面没有证据，不是崩，也不是 None
        # （None 会在 `u not in known` 之前就把孤儿行炸掉）。
        with mock.patch.object(services, "user_unit_dir",
                               side_effect=OSError("permission denied")):
            self.assertEqual(services.installed_user_units(), [])


class NoTemplateDirTestCase(unittest.TestCase):
    """模板目录整个不在（不完整 checkout）：期望集合是**空集**，于是机器上
    还在跑的 job 全部读作孤儿——§55 要的正是这个方向的失明保护。"""

    def test_every_running_zelin_unit_is_an_orphan_without_templates(self):
        text = FULL % ("zelin-ghost.service", "active", "running")
        with _empty_home():
            row = services.check_systemd_orphans(
                doctor.Probes(launchctl_list=lambda: text,
                              installed_user_units=lambda: []))
        self.assertEqual(row.status, doctor.FAIL)
        self.assertIn("zelin-ghost.service", row.detail)

    def test_every_running_zelin_task_is_an_orphan_without_templates(self):
        ghost = taskscheduler.TASK_PATH_PREFIX + "ghost"
        text = _schtasks({ghost: {"Status": "Running"}})
        with _empty_home():
            row = services.check_task_orphans(
                doctor.Probes(launchctl_list=lambda: text))
        self.assertEqual(row.status, doctor.FAIL)
        self.assertIn(ghost, row.detail)

    def test_an_unreadable_template_dir_reads_as_an_empty_expected_set(self):
        # 模板目录扫不动（权限 / 卷被卸载）时，两个 `_templated_*` 都必须答
        # 一个**空集**：孤儿行于是把机器上还在跑的 job 全报出来（宁可多报，
        # 也不要因为读不到模板就静默放过——§55 的整条法就是这个方向）。
        # 答 None 会在 `not in known` 那一步把这一行炸掉（宪法第 11 条）。
        ghost_task = taskscheduler.TASK_PATH_PREFIX + "ghost"
        with mock.patch.object(config, "HOME", _UnreadableHome()):
            task_row = services.check_task_orphans(
                doctor.Probes(launchctl_list=lambda: _schtasks(
                    {ghost_task: {"Status": "Running"}})))
            unit_row = services.check_systemd_orphans(
                doctor.Probes(launchctl_list=lambda: FULL % (
                    "zelin-ghost.service", "active", "running"),
                    installed_user_units=lambda: []))
        self.assertEqual(task_row.status, doctor.FAIL)
        self.assertIn(ghost_task, task_row.detail)
        self.assertEqual(unit_row.status, doctor.FAIL)
        self.assertIn("zelin-ghost.service", unit_row.detail)


if __name__ == "__main__":
    unittest.main()
