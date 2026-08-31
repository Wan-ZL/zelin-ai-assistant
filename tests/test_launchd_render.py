"""launchd plist 模板路径纪律（v0.48.x）——渲染形状判例。

2026-08-31 事故：「一键修复」按模板重渲染 plist，把 StandardOut/ErrorPath 拉回
$REPO/state/*.log —— repo 在外置卷（TCC-gated volume）上时，launchd 在 exec 前
就要打开日志路径并 chdir 到 WorkingDirectory，任一失败整个 agent 以
EX_CONFIG(78) 拒绝 spawn，把手工修好的 plist 一键打回故障态。

本文件把渲染后的 plist 形状钉死：

  1. StandardOut/ErrorPath 永在 ~/Library/Logs/zelin-ai-assistant/ 下，
     永不指向 repo（外置卷也一样）；
  2. WorkingDirectory = $HOME，模块解析改走 EnvironmentVariables.PYTHONPATH；
  3. 解释器是渲染注入的绝对路径（CONTRACT §19 runtime 指针），永不
     /usr/bin/env —— TCC 按 binary 计权限，env 间接层会让授权漂移。

三个渲染方（install.sh render_launchd_plist、mac/Sources/Doctor.swift
LaunchAgents.install、mac/Sources/SetupWizard.swift ActdAgent.renderAndLoad）
共享同一批模板与同一占位符替换序，所以钉「模板 + 替换序」= 钉全部三方。
"""
import plistlib
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO / "act" / "launchd"

# 故意用外置卷路径当 repo root：这正是事故场景 —— 渲染结果里 launchd
# 需要在 spawn 前触碰的路径（workdir、日志）一个都不许指向它。
FAKE_HOME = "/Users/testuser"
FAKE_REPO = "/Volumes/External/zelin-ai-assistant"
FAKE_PY = "/Users/testuser/miniconda3/bin/python3"
FAKE_CLAUDE_DIR = "/Users/testuser/.claude-bin-real"
LOG_DIR = FAKE_HOME + "/Library/Logs/zelin-ai-assistant"


def render(text):
    """install.sh render_launchd_plist 替换序的逐字镜像（次序有意义：
    长占位符先换，剩下的裸 /Users/YOURUSERNAME 才轮到 $HOME）。"""
    pydir = FAKE_PY.rsplit("/", 1)[0]
    return (
        text.replace("/Users/YOURUSERNAME/.claude-bin", FAKE_CLAUDE_DIR)
        .replace("/Users/YOURUSERNAME/miniconda3/bin/python3", FAKE_PY)
        .replace("/Users/YOURUSERNAME/Projects/zelin-ai-assistant", FAKE_REPO)
        .replace("/Users/YOURUSERNAME/miniconda3/bin", pydir)
        .replace("/Users/YOURUSERNAME", FAKE_HOME)
    )


class LaunchdTemplateShapeTestCase(unittest.TestCase):
    def setUp(self):
        self.templates = sorted(TEMPLATE_DIR.glob("*.plist"))
        self.assertTrue(self.templates, "no launchd templates found")

    def rendered(self):
        for path in self.templates:
            text = render(path.read_text(encoding="utf-8"))
            self.assertNotIn("YOURUSERNAME", text,
                             "%s: unrendered placeholder left over" % path.name)
            # 注释先剥掉再喂 plistlib：模板注释里有 `--bg` 这类双连字符，
            # Apple 的 plist 解析器容忍、严格 XML (expat) 不容忍——launchd
            # 真正消费的键值形状才是这里要钉的东西。
            bare = re.sub(r"<!--.*?-->", "", text, flags=re.S)
            yield path, text, plistlib.loads(bare.encode("utf-8"))

    def test_templates_parse_after_render(self):
        # plutil -lint 的可移植等价：渲染结果必须是合法 plist
        for path, _, obj in self.rendered():
            self.assertIsInstance(obj, dict, path.name)
            self.assertEqual(obj["Label"], path.stem, path.name)

    def test_log_paths_live_under_user_logs_never_the_repo(self):
        for path, _, obj in self.rendered():
            short = path.stem.rsplit(".", 1)[-1]
            for key in ("StandardOutPath", "StandardErrorPath"):
                log = obj[key]
                self.assertEqual(
                    log, "%s/%s.launchd.log" % (LOG_DIR, short),
                    "%s %s: launchd-touched log path must live under "
                    "~/Library/Logs/zelin-ai-assistant/" % (path.name, key))
                self.assertNotIn(FAKE_REPO, log)

    def test_workdir_is_home_and_pythonpath_carries_the_repo(self):
        for path, _, obj in self.rendered():
            self.assertEqual(obj["WorkingDirectory"], FAKE_HOME,
                             "%s: WorkingDirectory must be $HOME (launchd "
                             "chdirs pre-exec)" % path.name)
            env = obj["EnvironmentVariables"]
            self.assertEqual(env["PYTHONPATH"], FAKE_REPO, path.name)
            self.assertEqual(env["AIASSISTANT_HOME"], FAKE_REPO, path.name)

    def test_interpreter_is_absolute_and_never_env(self):
        for path, text, obj in self.rendered():
            argv0 = obj["ProgramArguments"][0]
            self.assertTrue(argv0.startswith("/"),
                            "%s: interpreter must be absolute" % path.name)
            self.assertNotEqual(argv0, "/usr/bin/env",
                                "%s: /usr/bin/env breaks per-binary TCC" % path.name)
            self.assertIn("python", argv0.rsplit("/", 1)[-1], path.name)
            self.assertNotIn("/usr/bin/env", text, path.name)

    def test_repo_appears_only_in_env_vars(self):
        # spawn 前 launchd 触碰的每个键都不许携带 repo 路径；repo 只允许
        # 作为环境变量值（进程起来之后才被 python 读取）。
        for path, _, obj in self.rendered():
            for key in ("WorkingDirectory", "StandardOutPath",
                        "StandardErrorPath"):
                self.assertNotIn(FAKE_REPO, obj[key],
                                 "%s: %s must not touch the repo" % (path.name, key))
            for arg in obj["ProgramArguments"]:
                self.assertNotIn(FAKE_REPO, arg,
                                 "%s: ProgramArguments must not carry the repo "
                                 "path (module resolution rides PYTHONPATH)"
                                 % path.name)


if __name__ == "__main__":
    unittest.main()
