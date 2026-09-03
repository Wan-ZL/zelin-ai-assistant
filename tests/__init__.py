"""Test bootstrap: point AIASSISTANT_HOME at a throwaway tmp dir BEFORE any
``act.*`` import, so module-level path constants (config.HOME, STATE_DIR,
REGISTRY_DIR, secrets.SECRETS_DIR, analytics dirs, ...) all resolve inside the
sandbox and no test ever touches the real repo/state or Zelin's real keys.

Also installs the fail-loud subprocess guard (see below): no test may fire a
real model call or reach the network.

Run the suite from the repo root:
    python3 -m unittest discover -s tests -v
"""
import os
import shlex
import subprocess
import sys
import tempfile

TMP_HOME = tempfile.mkdtemp(prefix="aiassistant-test-home-")
os.environ["AIASSISTANT_HOME"] = TMP_HOME
# §55 launchd probes (install.sh's interpreter viability probe, doctor's
# `launchd claude` row) bootstrap a real throwaway launchd job when switched
# on. The suite injects fakes everywhere; this is the belt-and-braces so a
# forgotten seam can never touch the developer's launchd from a test.
os.environ.setdefault("AIASSISTANT_LAUNCHD_PROBE", "0")
# §55 第五幕 stable daemon copy of claude: config.resolve_claude_bin prefers it
# whenever the file exists, so a developer machine that has one would flip
# every argv[0] pin in the suite. Point it into the sandbox (absent by
# default; tests that want one create it there). Unconditional: a stray value
# in the developer's shell must not aim the suite at a real binary either.
os.environ["AIASSISTANT_STABLE_CLAUDE"] = os.path.join(TMP_HOME, "stable-claude", "claude")
# §54 doctor `board server` row: the default probe GETs 127.0.0.1:<port>/api/health.
# A developer machine usually has a real board server on 47820 — the suite must
# never read it. Off = the row is omitted unless a test injects Probes.board_health.
os.environ.setdefault("AIASSISTANT_HTTP_PROBE", "0")
# §53 数据层后端：套件默认强制 YAML——测试沙箱里 ensure()/tick() 绝不偷偷迁移
# （activate.py 只在 auto 下激活）。store2 侧的行为测试自己显式切 sqlite
# （改 env + registry.reset_store_cache()，用完复原）。
os.environ.setdefault("ZAI_REGISTRY_BACKEND", "yaml")
# §65 每日循环的 launchd 日志读取器默认读 ~/Library/Logs/zelin-ai-assistant/——
# 开发者机器上有真日志，读了就是不确定的测试输入；指进沙箱（目录可以不存在）。
os.environ.setdefault("ZAI_LAUNCHD_LOG_DIR", os.path.join(TMP_HOME, "launchd-logs"))
# §65 每日循环挂在 actd.run_once 里：沙箱里没有 state/daily_loop.json，任何一条走
# 真 run_once 的判例都会在本地时间 ≥ 03:30 时把整轮循环跑起来（真 gh、真 doctor
# 子进程）。默认关掉；循环自己的判例显式打开（AIASSISTANT_DAILY_LOOP=1）。
os.environ.setdefault("AIASSISTANT_DAILY_LOOP", "0")


# --------------------------------------------------------------------------- #
# fail-loud subprocess guard（测试卫生 rule 7 的执法机制）
# --------------------------------------------------------------------------- #
# 纪律：测试**绝不真的起 agent**、绝不出网——LLM 调用一律走注入缝（runner /
# triager / extractor / silent_merge.JUDGE_RUNNER 的 fake），网络一律 mock。
# 此前这条纪律只靠自觉，代价是真实事故：radar 的 §44.2 fold 判官没接注入缝，
# 每跑一次全量测试就真花钱起一次 `claude -p`（本守卫落地当天抓出两处）。
#
# 拦什么（两条，都只盯"真花钱/真出网"的形状）：
#   A. 带 prompt 起跑的 claude —— `-p/--print/--resume`，或 `--bg` + 裸参数
#      （dispatch/resume/rework 三个发射点的形状，见 executor._bg_base_cmd）。
#   B. 出网工具（curl/wget/ssh/...），argv[0] 与 `bash -c "…"` 的脚本体都查。
#
# 故意不拦：本地能力探针（`claude --version/--help/--bg`、`claude agents
# --json --all`、`claude mcp list`、`claude stop <id>`）——doctor/ask 的若干
# 集成测试有意探真装的 CLI，零成本零网络，且在没装 claude 的 CI 上本就不发生。
# 也拦不住 detached 子进程里的调用（守卫只活在测试进程内，见 silent_merge
# 的 `python3 -m act.lib.silent_merge` 判官）——那条路要靠注入缝自觉。
_PROMPT_FLAGS = frozenset({"-p", "--print", "--resume"})
_NETWORK_PROGRAMS = frozenset({
    "curl", "wget", "nc", "ncat", "netcat", "telnet", "ssh", "scp", "sftp",
})
# 待办（§65 审查）：`gh` 也该进这份名单——§65 循环与 §57 pinned issue 都经注入缝——
# 但 test_ask / test_telemetry_level 仍经 doctor 真跑 `gh auth status`；先把那两处
# 探针改成可注入，再收编。
# 这些只是外壳，真正要看的是它们后面那条命令（`bash -c "curl …"`）
_SHELL_WRAPPERS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "env", "xargs"})


class RealSubprocessBanned(BaseException):
    """故意继承 BaseException：生产代码遍地是 `except Exception` 的 best-effort
    兜底（宪法第 11 条——一条坏记录不许崩 pass），守卫要是能被吞掉就等于没建。
    unittest 的 testPartExecutor 用裸 `except:` 兜，所以照样记成该条测试的
    ERROR，不会把整轮跑飞。"""


def _split(text: str) -> list:
    """尽力 tokenize；引号不配对等畸形串退回空白切分（守卫自己绝不崩）。"""
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _tokens(args, shell: bool) -> list:
    """argv → 字符串 token 列表（str/bytes/PathLike 都归一；失败给空表，畸形
    argv 交给 Popen 自己报错）。"""
    if isinstance(args, (str, bytes, os.PathLike)):
        text = os.fsdecode(args)
        return _split(text) if shell else [text]
    try:
        return [os.fsdecode(a) for a in args]
    except TypeError:
        return []


def _model_call(tokens: list) -> bool:
    """这条 argv 是否"带着 prompt 起 agent"（= 真花钱、真出网）。"""
    if not tokens or os.path.basename(tokens[0]) != "claude":
        return False
    rest = tokens[1:]
    if _PROMPT_FLAGS & set(rest):
        return True
    # dispatch 形：claude --bg [--dangerously-skip-permissions] [--name X] <prompt>
    return "--bg" in rest and any(not t.startswith("-") for t in rest)


def _network_hits(tokens: list) -> list:
    """出网工具名（外壳命令连脚本体一起查）。"""
    if tokens and os.path.basename(tokens[0]) in _SHELL_WRAPPERS:
        tokens = tokens[:1] + [t for arg in tokens[1:] for t in _split(arg)]
    return sorted({os.path.basename(t) for t in tokens if t
                   and os.path.basename(t) in _NETWORK_PROGRAMS})


class _GuardedPopen(subprocess.Popen):
    """Popen 的守卫壳。subprocess.run/check_output/check_call 都经模块全局
    ``Popen`` 这个名字创建进程，所以换掉这一个名字即覆盖全部入口。"""

    def __init__(self, args, *rest, **kwargs):
        tokens = _tokens(args, bool(kwargs.get("shell")))
        why = None
        if _model_call(tokens):
            why = ("带 prompt 的真 claude 调用（真花钱/真出网）——判官与 runner "
                   "必须走注入缝：merge_review/executor/ask 的 runner 参数、"
                   "silent_merge.JUDGE_RUNNER、radar 的 extractor")
        else:
            hits = _network_hits(tokens)
            if hits:
                why = f"出网工具 {', '.join(hits)} —— 网络一律 mock"
        if why:
            msg = f"测试禁止的 subprocess: {why}。argv={str(args)[:200]!r}"
            # 先落 stderr：即便调用点把异常吞了，痕迹仍在（见 tests/__init__.py）
            print("SUBPROCESS GUARD: " + msg, file=sys.stderr)
            raise RealSubprocessBanned(msg)
        super().__init__(args, *rest, **kwargs)


subprocess.Popen = _GuardedPopen  # type: ignore[misc]
