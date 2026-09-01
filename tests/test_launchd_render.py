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
     /usr/bin/env —— TCC 按 binary 计权限，env 间接层会让授权漂移；
  4. 渲染进去的 repo 路径是 PHYSICAL 路径（symlink 全解开），解释器是
     **验证过能 import yaml** 的那个 —— 2026-08-31 live 部署的两个症状：
     ~/Projects -> /Volumes/… 这条便利 symlink 被渲进 PYTHONPATH，launchd
     会话经该形状被 TCC 拒绝；同一轮渲染又挑中 /opt/homebrew/bin/python3
     （3.14，没 PyYAML）。两条合起来 = 每个 agent 都 `ModuleNotFoundError`
     退出 1 + KeepAlive 空转。

本文件既钉模板形状（render() 是 install.sh 替换序的手工镜像），也**真的执行**
install.sh 里的 physical_path / pick_python / render_launchd_plist（见
InstallShRealRenderTestCase）。另外两个渲染方（mac/Sources/Doctor.swift
LaunchAgents.install、mac/Sources/SetupWizard.swift ActdAgent.renderAndLoad）
是同一替换序的 Swift 手工镜像——注释互相指认、由 code review 保持同步，物理
路径那一条另有 mac/LogicTests 的 AppPaths.physical 判例。
"""
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO / "act" / "launchd"

# 模板形状那一组是纯字符串 + plistlib，三个平台都跑；真执行 install.sh 的那
# 一组是 POSIX-only（见 InstallShRealRenderTestCase）。
_WIN = sys.platform.startswith("win")

# install.sh 里真正参与渲染的函数（顺序 = 依赖顺序）。测试执行的是从
# install.sh 抠出来的**原文**，所以脚本改了而测试没改会立刻炸。
_RENDER_FNS = ("physical_path", "py_imports_yaml", "pick_python",
               "pinned_python", "repo_outside_home",
               "daemon_python_candidates", "_sed_escape",
               "render_launchd_plist")

# 解释器挑选那一组（§55 双闸门：import yaml + launchd 可行性）。
_PICK_FNS = ("physical_path", "py_imports_yaml", "pick_python",
             "pinned_python", "repo_outside_home", "daemon_python_candidates",
             "_xml_escape", "py_launchd_can_import_act", "pick_daemon_python")


def install_sh_prelude(fns=_RENDER_FNS):
    """把 install.sh 的这些函数定义逐字抠出来的 bash 片段（eval 即可用）。
    每个定义都是 `name() {` 开头、行首 `}` 结尾的块。"""
    return "".join(
        "eval \"$(awk '/^%s\\(\\) \\{/,/^\\}/' \"$REPO/install.sh\")\"\n" % fn
        for fn in fns)

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
            # 登录 shell 的 claude 目录必须被替换进 PATH 且排头（2026-07-08
            # 双 claude 事故）——漏掉这条替换 = 渲染出不存在的 ~/.claude-bin
            self.assertTrue(
                env["PATH"].startswith(FAKE_CLAUDE_DIR + ":"),
                "%s: login-shell claude dir must lead PATH, got %s"
                % (path.name, env["PATH"]))

    def test_interpreter_is_absolute_and_never_env(self):
        for path, text, obj in self.rendered():
            argv0 = obj["ProgramArguments"][0]
            self.assertTrue(argv0.startswith("/"),
                            "%s: interpreter must be absolute" % path.name)
            self.assertNotEqual(argv0, "/usr/bin/env",
                                "%s: /usr/bin/env breaks per-binary TCC" % path.name)
            self.assertIn("python", argv0.rsplit("/", 1)[-1], path.name)
            self.assertNotIn("/usr/bin/env", text, path.name)

    def test_fd_soft_limit_is_raised_and_hard_limit_is_left_alone(self):
        # §55 资源上限：launchd gui domain 给 job 的默认是 soft 256 / hard
        # unlimited。只抬 soft（余量）；**不设 HardResourceLimits**——它只会把
        # unlimited 压低（2026-09-01 实测：Soft+Hard 8192 → [8192, 8192]；
        # 只 Soft → [8192, unlimited]）。8-31 的派发失败本身不是 fd 问题
        # （是 TCC，见 §55 第三幕），所以这里只是余量不是修复。
        for path, _, obj in self.rendered():
            self.assertIn("SoftResourceLimits", obj, "%s: soft limit missing" % path.name)
            self.assertGreaterEqual(
                int(obj["SoftResourceLimits"].get("NumberOfFiles") or 0), 4096,
                "%s: SoftResourceLimits.NumberOfFiles must be >= 4096" % path.name)
            self.assertNotIn("HardResourceLimits", obj,
                             "%s: HardResourceLimits lowers launchd's unlimited hard cap"
                             % path.name)

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


@unittest.skipIf(_WIN, "install.sh is POSIX-only; the Windows installer is install.ps1")
class InstallShRealRenderTestCase(unittest.TestCase):
    """真的跑 install.sh 的函数（不是镜像）——2026-08-31 live 部署判例。

    整个 class 在 Windows 上跳过：这里的每一条都要么直接 `bash -c` 跑 install.sh
    抠出来的函数、要么经 _render_from/_pick 间接跑，另有四条还要 os.symlink
    （Windows 上要特权）。install.sh 只在 macOS/Linux 上运行——Windows 侧的
    安装器是 install.ps1，它有自己的判例。上面那个 class 是纯字符串 + plistlib
    的模板形状检查，三个平台照跑，不在跳过范围内。

    症状：owner 的 repo 实体在 /Volumes/Storage/Server/Projects/…，另有便利
    symlink ~/Projects -> /Volumes/Storage/Server/Projects。从 symlink 那侧
    的 shell 跑 install.sh，渲染出的 PYTHONPATH / AIASSISTANT_HOME 是 symlink
    形状，launchd 起的进程经该形状被 TCC 拒绝，actd/radar 全部
    `ModuleNotFoundError: No module named 'act'` 退出 1、KeepAlive 空转。
    """

    PATH_KEYS = ("PYTHONPATH", "AIASSISTANT_HOME")

    def _tmpdir(self, prefix):
        d = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def _symlinked_repo(self):
        """<tmp>/phys/repo（实体）+ <tmp>/link -> <tmp>/phys（便利 symlink）。
        返回 (物理路径, 经 symlink 的等价路径)。"""
        tmp = self._tmpdir("symlinked-repo-")
        phys = tmp / "phys" / "repo"
        phys.mkdir(parents=True)
        (tmp / "link").symlink_to(tmp / "phys")
        # tmp 自己在 macOS 上就走 /var -> /private/var，所以物理侧也要 realpath
        return os.path.realpath(str(phys)), str(tmp / "link" / "repo")

    def _render_from(self, repo_root, py="/usr/bin/python3"):
        out = self._tmpdir("render-") / "out.plist"
        # 假 $HOME：render_launchd_plist 会 mkdir 日志目录，别落到真 home 上
        fake_home = self._tmpdir("render-home-")
        script = (
            "set -u\n"
            + install_sh_prelude()
            + 'REPO_ROOT="$1"\n'
            'RUNTIME_PY="$2"\n'
            'CLAUDE_LOGIN_BIN=/fake/claude-home/claude\n'
            'render_launchd_plist "$REPO/act/launchd/'
            'com.zelin.aiassistant.actd.plist" "$OUT"\n'
        )
        proc = subprocess.run(
            ["bash", "-c", script, "bash", repo_root, py],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "REPO": str(REPO), "OUT": str(out),
                 "HOME": str(fake_home)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        bare = re.sub(r"<!--.*?-->", "", out.read_text(encoding="utf-8"),
                      flags=re.S)
        return plistlib.loads(bare.encode("utf-8"))

    def test_render_from_a_symlinked_repo_bakes_the_physical_path(self):
        phys, linked = self._symlinked_repo()
        self.assertNotEqual(phys, linked)  # fixture sanity
        obj = self._render_from(linked)
        for key in self.PATH_KEYS:
            self.assertEqual(
                obj["EnvironmentVariables"][key], phys,
                "%s must carry the PHYSICAL repo path — a symlinked shape "
                "leaves the launchd session TCC-denied" % key)
        # 整份 plist 里都不许再出现 symlink 形状
        self.assertNotIn(linked, plistlib.dumps(obj).decode("utf-8"))

    def test_script_dir_resolves_the_invocation_symlink(self):
        # REPO_ROOT = SCRIPT_DIR 还喂着 home 指针（App 的 stateRoot）和 §18 的
        # cron 行，所以 `pwd -P` 这一条本身也要钉，不能只靠渲染方兜底。
        phys, linked = self._symlinked_repo()
        line = next(ln for ln in (REPO / "install.sh").read_text(
            encoding="utf-8").splitlines() if ln.startswith("SCRIPT_DIR="))
        probe = Path(phys) / "probe.sh"
        probe.write_text('set -u\n%s\nprintf %%s "$SCRIPT_DIR"\n' % line,
                         encoding="utf-8")
        proc = subprocess.run(
            ["bash", str(Path(linked) / "probe.sh")],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, phys,
                         "install.sh invoked through a symlink must still "
                         "resolve REPO_ROOT to the physical path")

    def test_render_from_the_physical_path_is_unchanged(self):
        phys, _ = self._symlinked_repo()
        obj = self._render_from(phys)
        for key in self.PATH_KEYS:
            self.assertEqual(obj["EnvironmentVariables"][key], phys)

    def _fake_python(self, name, imports_yaml):
        """假解释器：真 python 一个都不起（只是 exit 0 / exit 1 的 shell 壳）。"""
        p = self._tmpdir("fakepy-") / name
        p.write_text("#!/bin/sh\nexit %d\n" % (0 if imports_yaml else 1),
                     encoding="utf-8")
        p.chmod(0o755)
        return str(p)

    def _pick(self, *candidates):
        script = ("set -u\n" + install_sh_prelude(("py_imports_yaml",
                                                   "pick_python"))
                  + 'pick_python "$@" || printf ""\n')
        proc = subprocess.run(
            ["bash", "-c", script, "bash", *candidates],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "REPO": str(REPO)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_pick_python_rejects_an_interpreter_without_yaml(self):
        # 2026-08-31 第二症状：/opt/homebrew/bin/python3 = 3.14 无 PyYAML
        no_yaml = self._fake_python("python3", imports_yaml=False)
        with_yaml = self._fake_python("python3", imports_yaml=True)
        self.assertEqual(self._pick(no_yaml, with_yaml), with_yaml)

    def test_pick_python_rejects_relative_and_missing_candidates(self):
        with_yaml = self._fake_python("python3", imports_yaml=True)
        self.assertEqual(
            self._pick("python3", "/nonexistent/python3", with_yaml),
            with_yaml,
            "only absolute, executable, yaml-importing candidates qualify")

    def test_pick_python_prints_nothing_when_no_candidate_has_yaml(self):
        no_yaml = self._fake_python("python3", imports_yaml=False)
        self.assertEqual(self._pick(no_yaml), "")

    def test_render_falls_back_to_a_validated_interpreter(self):
        # RUNTIME_PY 没验过就不许进 plist：渲染方自己再挑一次
        phys, _ = self._symlinked_repo()
        no_yaml = self._fake_python("python3", imports_yaml=False)
        obj = self._render_from(phys, py=no_yaml)
        argv0 = obj["ProgramArguments"][0]
        self.assertNotEqual(argv0, no_yaml,
                            "an interpreter that cannot import yaml must never "
                            "reach the plist")
        self.assertTrue(argv0.startswith("/"), argv0)


@unittest.skipIf(_WIN, "install.sh is POSIX-only; the Windows installer is install.ps1")
class DaemonInterpreterSelectionTestCase(unittest.TestCase):
    """§55 双闸门解释器挑选——2026-08-31 live 部署的**最后**一幕判例。

    v0.48.2 修好路径之后，agent 依旧全部 `No module named 'act'`：渲染出的
    PYTHONPATH / AIASSISTANT_HOME 完全正确，plist 里那个
    /opt/homebrew/bin/python3 也确实装着 PyYAML（所以老的 yaml 单闸门放它过
    了），但 **macOS 按 binary 授文件权限**——它在 launchd 下读不了外置卷上的
    repo，而 /usr/bin/python3 读得了。两个解释器在交互 shell 里都读得好好的
    （shell 把自己的授权借给了子进程），差别只在 launchd 会话里存在。

    所以这里钉两件事：
      1. **候选次序**——repo 在 $HOME 之外时系统 python 排第一（它是那个已经
         带着用户授权的 binary）；在 $HOME 之内维持历史次序。
      2. **第二道闸门**——yaml 过了但 launchd 下 import 不到 act 的候选必须被
         跳过，且一个都不过时要退回第一个 yaml 候选（而不是什么都不 pin）。

    这里**绝不真的起 launchd**：真探针要么被 AIASSISTANT_LAUNCHD_PROBE=0 关掉、
    要么被脚本里重定义的同名函数替掉（注入缝，与 runner/triager 同款）。
    整个 class 在 Windows 上跳过，同 InstallShRealRenderTestCase。
    """

    def _tmpdir(self, prefix):
        d = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def _fake_python(self, name, imports_yaml=True):
        p = self._tmpdir("fakepy-") / name
        p.write_text("#!/bin/sh\nexit %d\n" % (0 if imports_yaml else 1),
                     encoding="utf-8")
        p.chmod(0o755)
        return str(p)

    def _run(self, body, repo_root, home, env=None, pinned="", args=()):
        """install.sh 的挑选函数原文 + 一段测试尾巴。绝不触碰真 launchd。
        body 里 $1 = REPO_ROOT 已 shift 掉，剩下的位置参数来自 args。"""
        if pinned:
            (Path(repo_root) / "config").mkdir(parents=True, exist_ok=True)
            (Path(repo_root) / "config" / "runtime.json").write_text(
                '{"python": "%s"}\n' % pinned, encoding="utf-8")
        script = ("set -u\n" + install_sh_prelude(_PICK_FNS)
                  + 'REPO_ROOT="$1"; shift\n' + body)
        base = {k: v for k, v in os.environ.items()
                if k != "AIASSISTANT_PYTHON"}
        proc = subprocess.run(
            ["bash", "-c", script, "bash", repo_root, *args],
            capture_output=True, text=True, timeout=60,
            env={**base, "REPO": str(REPO), "HOME": home,
                 # 默认关掉真探针：本 class 不许 bootstrap 任何 launchd job
                 "AIASSISTANT_LAUNCHD_PROBE": "0", **(env or {})})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def _candidates(self, repo_root, home, env=None, pinned=""):
        out = self._run("daemon_python_candidates\n", repo_root, home,
                        env=env, pinned=pinned)
        return [ln for ln in out.splitlines() if ln]

    # -- 1. 候选次序 --------------------------------------------------------- #

    def test_system_python_ranks_first_when_the_repo_is_outside_home(self):
        home = str(self._tmpdir("home-"))
        repo = str(self._tmpdir("repo-outside-"))  # 兄弟目录，不在 home 下
        cands = self._candidates(repo, home, env={"PY": "/fake/shell/python3"})
        self.assertEqual(
            cands[0], "/usr/bin/python3",
            "repo outside $HOME is the TCC-sensitive shape: the Apple-shipped "
            "system python (the binary that already carries the user's file "
            "grants) must be probed FIRST — a homebrew/miniconda python needs "
            "its own grant before a launchd job can read an external volume")

    def test_home_repo_keeps_the_historical_order(self):
        home = str(self._tmpdir("home-"))
        repo = os.path.join(home, "Projects", "zelin-ai-assistant")
        os.makedirs(repo)
        pin = self._fake_python("pinned-python3")
        cands = self._candidates(repo, home, env={"PY": "/fake/shell/python3"},
                                 pinned=pin)
        self.assertEqual(cands[0], pin,
                         "inside $HOME there is no TCC boundary to cross, so "
                         "the existing pin still wins")
        self.assertEqual(cands[-1], "/usr/bin/python3",
                         "system python stays the last resort inside $HOME")

    def test_explicit_override_outranks_the_system_python(self):
        home = str(self._tmpdir("home-"))
        repo = str(self._tmpdir("repo-outside-"))
        cands = self._candidates(repo, home,
                                 env={"AIASSISTANT_PYTHON": "/opt/mine/python3"})
        self.assertEqual(cands[0], "/opt/mine/python3",
                         "an explicit AIASSISTANT_PYTHON outranks every guess")

    def test_outside_home_is_decided_on_physical_paths(self):
        # 便利 symlink（事故的原始形状）不许把外置卷伪装成 $HOME 内
        tmp = self._tmpdir("phys-")
        home = tmp / "home"
        (home / "Projects").mkdir(parents=True)
        outside = tmp / "elsewhere" / "repo"
        outside.mkdir(parents=True)
        (home / "Projects" / "link").symlink_to(outside)
        cands = self._candidates(str(home / "Projects" / "link"), str(home))
        self.assertEqual(
            cands[0], "/usr/bin/python3",
            "a symlink from $HOME into an outside path is still outside — "
            "the comparison must resolve both sides physically")

    # -- 2. launchd 可行性闸门 ----------------------------------------------- #

    def _pick_with_stub(self, candidates, viable):
        """真的跑 pick_daemon_python，但把 launchd 探针换成注入的判决表
        （注入缝，与 runner/triager 同款——测试绝不真起 agent）。
        viable: {path: rc}，rc 同真探针（0 可行 / 1 不可行 / 2 测不出）。"""
        cases = "".join(
            '        %s) return %d ;;\n' % (c, rc) for c, rc in viable.items())
        body = (
            'py_launchd_can_import_act() {\n'
            '    LAUNCHD_PROBE_DETAIL="stub:$1"\n'
            '    case "$1" in\n' + cases +
            '        *) return 1 ;;\n'
            '    esac\n'
            '}\n'
            'pick_daemon_python "$@" || true\n'
            'printf "%s\\n%s" "$DAEMON_PY" "$DAEMON_PY_NOTE"\n')
        out = self._run(body, str(self._tmpdir("repo-")),
                        str(self._tmpdir("home-")), args=candidates)
        picked, _, note = out.partition("\n")
        return picked, note

    def test_a_yaml_capable_but_launchd_blind_interpreter_is_skipped(self):
        # 事故本体：两个都 import 得了 yaml，只有一个在 launchd 下看得见 repo
        blind = self._fake_python("blind-python3", imports_yaml=True)
        seeing = self._fake_python("seeing-python3", imports_yaml=True)
        picked, note = self._pick_with_stub([blind, seeing],
                                            {blind: 1, seeing: 0})
        self.assertEqual(picked, seeing,
                         "the yaml gate alone cannot tell these apart — the "
                         "launchd gate must reject the blind interpreter")
        self.assertEqual(note, "", "a clean pick carries no caveat")

    def test_the_yaml_gate_still_runs_first(self):
        no_yaml = self._fake_python("no-yaml-python3", imports_yaml=False)
        good = self._fake_python("good-python3", imports_yaml=True)
        # no_yaml 会通过 launchd stub，但它连第一道闸门都过不了
        picked, _ = self._pick_with_stub([no_yaml, good],
                                         {no_yaml: 0, good: 0})
        self.assertEqual(picked, good)

    def test_falls_back_to_the_first_yaml_candidate_when_none_are_viable(self):
        first = self._fake_python("first-python3", imports_yaml=True)
        second = self._fake_python("second-python3", imports_yaml=True)
        picked, note = self._pick_with_stub([first, second],
                                            {first: 1, second: 1})
        self.assertEqual(picked, first,
                         "pin the first yaml-capable candidate rather than "
                         "nothing — still strictly better than a PATH guess")
        self.assertIn("launchd probe", note,
                      "and say why, so the installer can warn instead of "
                      "reporting a clean pin")

    def test_an_unavailable_probe_degrades_to_the_yaml_gate(self):
        # 没有 launchd（Linux/CI）或探针被关：rc=2 = 测不出，绝不当成拒绝
        only = self._fake_python("only-python3", imports_yaml=True)
        picked, note = self._pick_with_stub([only], {only: 2})
        self.assertEqual(picked, only)
        self.assertIn("unverifiable", note)

    def test_the_real_probe_is_disabled_by_the_env_switch(self):
        # 判例本身的安全带：AIASSISTANT_LAUNCHD_PROBE=0 时**真**探针必须直接
        # 返回 2（测不出），一个 launchd job 都不许 bootstrap——上面每条用例
        # 都靠这个开关保持 hermetic。
        good = self._fake_python("good-python3", imports_yaml=True)
        out = self._run('py_launchd_can_import_act "$1" "$REPO_ROOT"\n'
                        'printf "%s" "$?"\n',
                        str(self._tmpdir("repo-")), str(self._tmpdir("home-")),
                        args=[good])
        self.assertEqual(out, "2")


if __name__ == "__main__":
    unittest.main()
