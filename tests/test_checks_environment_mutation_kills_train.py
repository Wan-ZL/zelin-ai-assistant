"""doctor 工具链/配置/凭证/目录家族的**非健康分支**（CONTRACT §25；§18 录制依赖；
§19 凭证顺序；§71.1 电源探针可读性）。

tests/test_doctor.py 的健康基线（`test_healthy_setup_has_no_fails_and_exits_zero`）
把这一族每一行的 **OK 分支**都走了一遍，剩下的 WARN / FAIL 分支大多没有任何断言——
夜间变异（§57）因此在这一个文件里留下了本轮最大的一片存活体：三十多个
`return X → return None`（行本身消失，doctor 的报告里那一行静默不见）、一串超时
常量与字符串截断长度、以及三处 `or`/`and` 的缺省构造。

这里钉的是这一族的四句合同：

* **每一行都必须是一行**：`run_checks` 把返回值当 `CheckResult`（或它的列表）
  收；一个返回 `None` 的分支 = 报告里那一行**消失**，而 §25 的合同是「每个检查
  一行，症状在前、修法在后」。行不见 = 那台机器的那个毛病没人说得出口。
* **外来文本进 detail 必须截断到成文的长度**：`claude --version` 的 stderr、
  YAML 解析器的异常、活探针的输出都是不可信长度的外部文本；detail 是**一行**。
* **超时阶梯是 owner 要等的秒数**（同 tests/test_checks_core_mutation_kills_train.py
  的那一条）：登录 shell 探测 15 s、`claude --version` 15 s、守护 python 的
  `import yaml` 20 s、`gh auth status` 15 s、一次真模型调用 90 s。
* **缺省构造是 fail-loud 的**：`py or "empty"`、`classify(out) or
  "claude_auth_failed"` 都是「宁可说一个笨答案，也不留一个空洞」。

**一个体判为等价（可达输入上无可观察差异，不强杀）**：`_too_old` 的
`return False`（`→ return None`）——私名谓词，唯一消费者是
`check_runtime_python` 里的 `if _too_old(ver)`，None 与 False 同为假值。
（`_runtime_pin` 的 `return ""` 不在此列：它的返回值会被 `% (py or "empty")`
读进 detail，而且 tests/test_doctor_default_probes.py 已经把它当公开合同断言。）
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act import
from tests.scratch_testkit import scratch_dir

from act import doctor
from act.lib import config, platform, secrets
from act.lib.checks import environment

# 一段长度可数、逐位不同的外来文本（截断长度的判据要能分辨第 79/80/81 个字符）。
NOISE = "".join(str(i % 10) for i in range(300))


def _tmpdir(case, prefix: str) -> Path:
    return Path(scratch_dir(case, prefix=prefix))


class _Run:
    """`Probes.run` 的假实现：记账每一次调用（argv / env / timeout）。"""

    def __init__(self, result=(0, "")):
        self.result = result
        self.calls = []

    def __call__(self, cmd, env=None, timeout=None):
        self.calls.append({"cmd": cmd, "env": env, "timeout": timeout})
        return self.result


def _probes(**kw):
    """默认全 hermetic：不问真 PATH、不读真登录 shell、不碰真 plist。"""
    kw.setdefault("which", lambda name: None)
    kw.setdefault("run", _Run())
    kw.setdefault("daemon_path_env", lambda: None)
    kw.setdefault("login_shell_claude", lambda: None)
    return doctor.Probes(**kw)


class HomeRowTestCase(unittest.TestCase):
    def test_a_home_without_install_sh_is_a_fail_row_that_names_itself(self):
        # 下面每一条路径都由 HOME 推导——它错了，报告里其余每一行都在说谎。
        with mock.patch.object(config, "HOME", _tmpdir(self, "zai-nohome-")):
            row = environment.check_home(_probes())
        self.assertEqual(row.name, "AIASSISTANT_HOME")
        self.assertEqual(row.status, doctor.FAIL)
        self.assertIn("install.sh", row.detail + row.fix)


class LoginShellProbeTestCase(unittest.TestCase):
    def test_the_login_shell_probe_is_capped_at_fifteen_seconds(self):
        # 登录 shell 会 source owner 的 rc 文件（nvm / conda / 公司脚本）——
        # 它可以慢，但不许把 doctor 挂住。
        run = _Run((0, "/opt/homebrew/bin/claude\n"))
        with mock.patch.dict(os.environ, {"SHELL": "/bin/zsh"}):
            self.assertEqual(environment.login_shell_claude(run),
                             "/opt/homebrew/bin/claude")
        self.assertEqual(run.calls[0]["timeout"], 15)
        self.assertEqual(run.calls[0]["cmd"][:2], ["/bin/zsh", "-lc"])


class ClaudeCliRowTestCase(unittest.TestCase):
    """`claude CLI` 行：版本号取第一行、失败时把 stderr 截到一行。"""

    def _row(self, result):
        run = _Run(result)
        row = environment.check_claude(
            _probes(which=lambda name: "/fake/bin/claude", run=run))
        return row, run

    def test_the_version_probe_is_capped_at_fifteen_seconds(self):
        _, run = self._row((0, "2.1.252 (Claude Code)\n"))
        self.assertEqual(run.calls[0]["timeout"], 15)

    def test_the_version_is_the_first_line_capped_at_sixty_chars(self):
        # `claude --version` 把版本印在第一行，后面可能跟着更新提示 / 警告。
        # detail 是一行：版本号超过 60 字符（未来的长后缀）就截断。
        row, _ = self._row((0, NOISE[:40] + "\nupdate available: run `claude update`\n"))
        self.assertEqual(row.status, doctor.OK)
        self.assertIn(NOISE[:40], row.detail)
        self.assertNotIn("update available", row.detail)
        row, _ = self._row((0, NOISE + "\n"))
        self.assertIn(NOISE[:60], row.detail)
        self.assertNotIn(NOISE[:61], row.detail)

    def test_a_failing_version_probe_is_a_warn_row_with_a_capped_tail(self):
        # 行必须在（探针失败也是一个症状）；外来 stderr 截到 80 字符。
        row, _ = self._row((1, NOISE))
        self.assertEqual(row.name, "claude CLI")
        self.assertEqual(row.status, doctor.WARN)
        self.assertIn("/fake/bin/claude", row.detail)
        self.assertIn(NOISE[:80], row.detail)
        self.assertNotIn(NOISE[:81], row.detail)


class DaemonPythonRowTestCase(unittest.TestCase):
    """`daemon python` 行：pin 不可用 = FAIL，版本取探针输出的最后一行。"""

    def setUp(self):
        self.home = _tmpdir(self, "zai-runtime-")
        (self.home / "config").mkdir()
        self.rj = self.home / "config" / "runtime.json"
        p = mock.patch.object(config, "HOME", self.home)
        p.start()
        self.addCleanup(p.stop)

    def _pin(self, payload: str):
        self.rj.write_text(payload, encoding="utf-8")

    def test_a_pinned_but_non_executable_interpreter_is_a_fail_row(self):
        # 文件在、却不可执行：`not py` 是假、`not os.access(...)` 是真——两条
        # 判据是**或**的关系，任一成立就是「这个 pin 用不了」。合成「与」
        # 会让 launchd 拿着一个跑不起来的解释器，而 doctor 说一切正常。
        dud = self.home / "python-not-executable"
        dud.write_text("#!/bin/sh\n", encoding="utf-8")
        dud.chmod(0o644)
        self._pin(json.dumps({"python": str(dud)}))
        row = environment.check_runtime_python(_probes())
        self.assertEqual(row.name, "daemon python")
        self.assertEqual(row.status, doctor.FAIL)
        self.assertIn(str(dud), row.detail)
        self.assertIn("install.sh", row.fix)

    def test_an_empty_pin_says_empty_rather_than_nothing(self):
        # runtime.json 在、`python` 是空串：detail 里必须出现一个词，不能是
        # 一对空括号——owner 要能从这一行看出「pin 是空的」。
        self._pin(json.dumps({"python": ""}))
        row = environment.check_runtime_python(_probes())
        self.assertEqual(row.status, doctor.FAIL)
        self.assertIn("empty", row.detail)

    def test_a_malformed_runtime_json_reads_as_no_pin(self):
        # 坏文件只是另一个症状（宪法第 11 条）：读成空串，不是崩、不是 None。
        self._pin("{not json")
        self.assertEqual(environment._runtime_pin(self.rj), "")

    def test_the_interpreter_probe_is_capped_at_twenty_seconds(self):
        self._pin(json.dumps({"python": _executable(self.home)}))
        run = _Run((0, "3.12\n"))
        environment.check_runtime_python(_probes(run=run))
        self.assertEqual(run.calls[0]["timeout"], 20)

    def test_the_version_is_the_last_line_of_the_probe_output(self):
        # 解释器可能先吐 DeprecationWarning / site 提示，版本永远是**最后**
        # 一行（`print` 是这个脚本做的最后一件事）。取错行 = 版本判据失效。
        self._pin(json.dumps({"python": _executable(self.home)}))
        row = environment.check_runtime_python(
            _probes(run=_Run((0, "DeprecationWarning: nope\n3.12\n"))))
        self.assertEqual(row.status, doctor.OK)
        self.assertIn("Python 3.12", row.detail)

    def test_an_old_interpreter_is_a_fail_row_that_names_both_versions(self):
        self._pin(json.dumps({"python": _executable(self.home)}))
        row = environment.check_runtime_python(_probes(run=_Run((0, "3.8\n"))))
        self.assertEqual(row.status, doctor.FAIL)
        self.assertIn("3.8", row.detail)
        self.assertIn("AIASSISTANT_PYTHON", row.fix)


def _executable(home: Path) -> str:
    """A real, executable file to satisfy the `os.access(py, X_OK)` gate."""
    py = home / "python-exec"
    py.write_text("#!/bin/sh\n", encoding="utf-8")
    py.chmod(0o755)
    return str(py)


class ConfigRowTestCase(unittest.TestCase):
    """`config.yaml` 行：三种坏法各有一行，YAML 报错截到一行。"""

    def setUp(self):
        self.home = _tmpdir(self, "zai-config-")
        self.path = self.home / "config.yaml"
        p = mock.patch.object(config, "CONFIG_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)

    def test_a_missing_config_is_a_warn_row_with_the_copy_command(self):
        row = environment.check_config(_probes())
        self.assertEqual(row.name, "config.yaml")
        self.assertEqual(row.status, doctor.WARN)
        self.assertIn("cp config.example.yaml", row.fix)

    def test_a_python_without_pyyaml_is_a_fail_row(self):
        self.path.write_text("sources: {}\n", encoding="utf-8")
        with mock.patch.object(config, "yaml", None):
            row = environment.check_config(_probes())
        self.assertEqual(row.status, doctor.FAIL)
        self.assertIn("pip install", row.fix)

    def test_invalid_yaml_reports_the_first_line_capped_at_eighty(self):
        # 解析器的异常是多行的（"while parsing ... in \"<unicode string>\",
        # line 3, column 5"）；detail 是一行，所以只取**第一行**并截断。
        self.path.write_text("sources: {}\n", encoding="utf-8")
        boom = ValueError(NOISE + "\nline 2 of the parser message\ncontext here")
        with mock.patch.object(config.yaml, "safe_load", side_effect=boom):
            row = environment.check_config(_probes())
        self.assertEqual(row.status, doctor.FAIL)
        self.assertEqual(row.failure_id, "config_invalid")
        self.assertIn(NOISE[:80], row.detail)
        self.assertNotIn(NOISE[:81], row.detail)
        self.assertNotIn("line 2 of the parser message", row.detail)
        self.assertNotIn("context here", row.detail)


class AnthropicKeyRowTestCase(unittest.TestCase):
    """`anthropic key` 行：POSIX 上逐位查 group/other，Windows 上不查。"""

    def setUp(self):
        self.dir = _tmpdir(self, "zai-secrets-")
        p = mock.patch.object(secrets, "SECRETS_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)
        self.key = self.dir / secrets.ANTHROPIC_API_KEY_FILE
        self.key.write_text("sk-ant-test\n", encoding="utf-8")

    def _row(self):
        return environment.check_anthropic_key(
            _probes(legacy_key_path=self.dir / "no-such-legacy.txt"))

    @unittest.skipIf(os.name != "posix", "POSIX mode bits only")
    def test_any_group_or_other_bit_counts_as_readable_by_others(self):
        # `chmod 600` 是逐位的：连 other 的执行位（0o001）都不许留下，
        # 掩码漏掉最低那一位就会把一个可被别人摸到的 key 文件报成 OK。
        self.key.chmod(0o601)
        row = self._row()
        self.assertEqual(row.status, doctor.WARN)
        self.assertIn("chmod 600", row.fix)
        self.key.chmod(0o600)
        self.assertEqual(self._row().status, doctor.OK)

    def test_windows_skips_the_mode_check_and_says_why(self):
        # NTFS 没有 POSIX mode 位，chmod 600 在那里是 no-op——查了就是恒 WARN。
        with mock.patch.object(platform, "is_windows", return_value=True):
            row = self._row()
        self.assertEqual(row.name, "anthropic key")
        self.assertEqual(row.status, doctor.OK)
        self.assertIn("NTFS", row.detail)


class StateDirsRowTestCase(unittest.TestCase):
    """`state dirs` 行：缺目录和不可写是两个不同的 FAIL，都必须有行。"""

    def setUp(self):
        self.root = _tmpdir(self, "zai-state-")
        self.dirs = (self.root / "state", self.root / "state" / "inbox",
                     self.root / "state" / "logs")
        for name, value in zip(("STATE_DIR", "INBOX_DIR", "LOG_DIR"), self.dirs):
            p = mock.patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_missing_dirs_are_named_in_the_fail_row(self):
        row = environment.check_state_dirs(_probes())
        self.assertEqual(row.name, "state dirs")
        self.assertEqual(row.status, doctor.FAIL)
        for d in self.dirs:
            self.assertIn(str(d), row.detail)
        self.assertIn("install.sh", row.fix)

    def test_present_but_unwritable_dirs_are_their_own_fail_row(self):
        # 目录在、写不进去（chown 错了 / 迁过盘）是另一个毛病，修法也不同：
        # 装机脚本救不了它，chown 才行。
        for d in self.dirs:
            d.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(environment.os, "access", return_value=False):
            row = environment.check_state_dirs(_probes())
        self.assertEqual(row.status, doctor.FAIL)
        self.assertIn("chown", row.fix)
        self.assertEqual(environment.check_state_dirs(_probes()).status, doctor.OK)


class ObsidianRowTestCase(unittest.TestCase):
    """`obsidian vault` 行：未配置 / 目录不在 / 收件箱不在 / 都在，四条各一行。"""

    def setUp(self):
        self.root = _tmpdir(self, "zai-vault-")

    def _row(self, raw=None, unprocessed=None):
        cfg = config.Config(obsidian_raw=raw, obsidian_unprocessed=unprocessed)
        with mock.patch.object(config, "load_config", return_value=cfg):
            return environment.check_obsidian(_probes())

    def test_an_unset_vault_says_unset_not_missing(self):
        # 「没配」和「配了但不在」是两个毛病，修法也不同（去 config.yaml 填一行
        # vs 去建目录）。把 None 走成路径会让报告说错话。
        for raw in (None, "", "   "):
            row = self._row(raw=raw)
            self.assertEqual(row.status, doctor.WARN, repr(raw))
            self.assertIn("sources.obsidian_raw", row.detail)
            self.assertNotIn("None", row.detail)

    def test_a_configured_but_missing_vault_is_its_own_warn_row(self):
        ghost = self.root / "no-such-vault"
        row = self._row(raw=str(ghost))
        self.assertEqual(row.status, doctor.WARN)
        self.assertIn(str(ghost), row.detail)

    def test_a_missing_ingest_inbox_is_its_own_warn_row(self):
        raw = self.root / "2 - raw"
        raw.mkdir(parents=True)
        inbox = self.root / "1 - unprocessed"
        row = self._row(raw=str(raw), unprocessed=str(inbox))
        self.assertEqual(row.status, doctor.WARN)
        self.assertIn(str(inbox), row.detail)
        self.assertIn("mkdir -p", row.fix)

    def test_both_present_is_the_ok_row(self):
        raw = self.root / "2 - raw"
        inbox = self.root / "1 - unprocessed"
        raw.mkdir(parents=True)
        inbox.mkdir(parents=True)
        row = self._row(raw=str(raw), unprocessed=str(inbox))
        self.assertEqual(row.name, "obsidian vault")
        self.assertEqual(row.status, doctor.OK)
        self.assertIn(str(raw), row.detail)


class ScreenpipeRowTestCase(unittest.TestCase):
    """`screenpipe db` 行：两小时整是门槛，时长是向下取整的真实时间。"""

    def setUp(self):
        self.db = _tmpdir(self, "zai-screenpipe-") / "db.sqlite"
        self.db.write_text("not-a-real-db", encoding="utf-8")
        self.mtime = self.db.stat().st_mtime

    def _row(self, age: float):
        return environment.check_screenpipe(
            _probes(screenpipe_db=self.db, now=lambda: self.mtime + age))

    def test_a_db_that_was_never_written_is_its_own_warn_row(self):
        # 「从没录过」和「录过但停了」是两个不同的状态，修法也不同（去开录制
        # vs 引擎死了）。这一行必须在——不然一台从没开过录制的机器上，
        # `screenpipe db` 这一行整个消失，owner 无从知道录制没在跑。
        row = environment.check_screenpipe(
            _probes(screenpipe_db=self.db.parent / "never-written.sqlite",
                    now=lambda: self.mtime))
        self.assertEqual(row.name, "screenpipe db")
        self.assertEqual(row.status, doctor.WARN)
        self.assertEqual(row.failure_id, "")          # 没录过不是故障分类
        self.assertIn("never-written.sqlite", row.detail)

    def test_exactly_two_hours_is_still_fresh(self):
        # 导出 cron 每 30 分钟落一次盘；「两小时没写」才叫停了。门槛是闭的：
        # 正好两小时那一刻还不算停，多一秒才算（差一秒就在健康机器上报警）。
        row = self._row(2 * 3600)
        self.assertEqual(row.status, doctor.OK)
        self.assertIn("120 min ago", row.detail)
        self.assertEqual(self._row(2 * 3600 + 1).status, doctor.WARN)

    def test_a_stale_db_is_a_warn_row_with_the_engine_dead_classification(self):
        row = self._row(3 * 3600)
        self.assertEqual(row.name, "screenpipe db")
        self.assertEqual(row.status, doctor.WARN)
        self.assertEqual(row.failure_id, "engine_dead")
        self.assertIn("3h ago", row.detail)

    def test_the_hours_are_floored_at_the_hour_boundary(self):
        # 9 小时 59 分 55 秒还是 `9h`，10 小时零 5 秒才是 `10h`——owner 看到的
        # 数字是真实经过的整小时，不是四舍五入、也不是别的除数。
        self.assertIn("9h ago", self._row(10 * 3600 - 5).detail)
        self.assertIn("10h ago", self._row(10 * 3600 + 5).detail)


class OptionalToolRowsTestCase(unittest.TestCase):
    """`node/npx` 与 `gh CLI`：可选依赖缺席也必须**有一行**。"""

    def test_missing_npx_is_a_classified_warn_row(self):
        row = environment.check_npx(_probes())
        self.assertEqual(row.name, "node/npx")
        self.assertEqual(row.status, doctor.WARN)
        self.assertEqual(row.failure_id, "node_missing")

    def test_the_gh_auth_probe_is_capped_at_fifteen_seconds(self):
        run = _Run((0, ""))
        environment.check_gh(_probes(which=lambda n: "/fake/bin/gh", run=run))
        self.assertEqual(run.calls[0]["cmd"], ["/fake/bin/gh", "auth", "status"])
        self.assertEqual(run.calls[0]["timeout"], 15)

    def test_an_unauthenticated_gh_is_a_warn_row_that_names_the_binary(self):
        row = environment.check_gh(
            _probes(which=lambda n: "/fake/bin/gh", run=_Run((1, "not logged in"))))
        self.assertEqual(row.name, "gh CLI")
        self.assertEqual(row.status, doctor.WARN)
        self.assertIn("/fake/bin/gh", row.detail)
        self.assertIn("gh auth login", row.fix)


class ClaudeAuthRowTestCase(unittest.TestCase):
    """`claude auth` 行（唯一花钱的活探针）：凭证解析与失败归类。"""

    def setUp(self):
        self.dir = _tmpdir(self, "zai-auth-secrets-")
        p = mock.patch.object(secrets, "SECRETS_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)

    def _probes_for(self, result):
        return _probes(which=lambda n: "/fake/bin/claude", run=_Run(result),
                       legacy_key_path=self.dir / "no-such-legacy.txt")

    def test_without_a_key_the_probe_runs_on_subscription_auth_and_says_so(self):
        # 没有 key 文件时活探针走 CLI 的存储凭证——GUI 会话里能过，
        # cron/launchd 里常常过不去。那句提醒必须在。
        probes = self._probes_for((0, "ok"))
        row = environment.check_claude_auth(probes)
        self.assertEqual(row.name, "claude auth")
        self.assertEqual(row.status, doctor.OK)
        self.assertIn("subscription auth", row.detail)
        self.assertIn("headless", row.detail)
        self.assertNotIn("ANTHROPIC_API_KEY", probes.run.calls[0]["env"])

    def test_a_failed_live_call_keeps_the_last_120_chars_of_the_output(self):
        # 模型/网关的错误正文长度不可信；detail 是一行，留**尾部**（尾巴上
        # 才是原因，开头是重复的样板）。
        row = environment.check_claude_auth(self._probes_for((1, NOISE)))
        self.assertEqual(row.status, doctor.FAIL)
        self.assertIn(NOISE[-120:], row.detail)
        self.assertNotIn(NOISE[-121:], row.detail)

    def test_an_unclassifiable_failure_still_gets_the_generic_id(self):
        # 分类器认不出来 ≠ 没毛病：这一行必须带一个 failure id，
        # 否则 App 上它连一个「怎么修」的按钮都没有（§25 分类目录）。
        row = environment.check_claude_auth(self._probes_for((1, NOISE)))
        self.assertEqual(row.failure_id, "claude_auth_failed")

    def test_without_a_claude_binary_the_row_is_an_honest_skip(self):
        row = environment.check_claude_auth(_probes())
        self.assertEqual(row.name, "claude auth")
        self.assertEqual(row.status, doctor.WARN)


if __name__ == "__main__":
    unittest.main()
