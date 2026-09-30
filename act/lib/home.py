"""运行时 home 的判据：这个进程允许往哪棵树里写（CONTRACT §82；解析顺序见 §19）。

`AIASSISTANT_HOME` 是 `act/lib/config.py` 全部路径常量的唯一输入，而它的回落值
`~/Projects/zelin-ai-assistant` **就是 owner 的 live 安装**。于是「忘了设这个变量」
与「继承了守护进程的那一份」两种情形下，一次测试或一次无人值守的门跑就会把
真账本当成自己的沙箱——2026-09-18 的看板被抹（registry 清空、`state/vault-mirror`
删除、27 个 tracked 文件消失、夹具卡 `R-8150` / `R-960` 被工号分配收养）正是这条路
（issue #452）。

**法条一句话**：跑测试的进程永远不许把一棵 **git 工作树**当成 home。判据与 §74.1
的 `.pkg` 目的地守卫逐字同源——「路径解析掉所有符号链接之后，它自己或它的某个
祖先有没有 `.git`」——因为那条判据在这台机器上已经被判例验证过一次：`~/Projects`
是一条指向 `/Volumes/Storage/Server/Projects` 的符号链接，只看字面路径的守卫在真实
形状上等于没装。

**只在测试面执法**（§82.2 的射程）：生产入口一律显式携带 `AIASSISTANT_HOME`
（launchd plist、crontab 行、`ingest/*.sh`、install.sh、壳注入的子进程 env——§19
「daemon 不读指针」那一条的另一面），所以「不是测试跑者」= 生产，本模块一个字节
都不碰它；`act.doctor` 的 home 行、`--print-path` CLI 的「永不 traceback」承诺
因此原样成立。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

#: Python 侧 home 的回落值（§19 第三层）。server/ 侧另有一份镜像常量
#: （`server/paths.DEFAULT_HOME`，漂移由 tests/test_server_paths_mirror.py 钉住）——
#: §54.3 允许壳在不注入任何 env 的情况下起 `python -m server`，那一侧不受本模块管。
DEFAULT_HOME = "~/Projects/zelin-ai-assistant"

#: 认得出「我正跑在测试里」的模块名。`python3 -m unittest` / `pytest` 都由 runpy
#: 先把跑者模块装进 `sys.modules`，**之后**才 import 任何测试模块——所以这个判据
#: 与 import 顺序无关（`tests/__init__.py` 的沙箱恰恰依赖顺序，那是 #452 的病根）。
#: 生产侧零误伤：act/ 与 server/ 全树没有一行 `import unittest|pytest|doctest`，
#: 十二个生产入口 import 完 `sys.modules` 里一个都不在（判例钉住）。
TEST_RUNNER_MODULES = ("unittest", "pytest")

#: 逃生门：显式认领「我知道这是 live 树，照写」。仓库里没有任何一处设它——
#: 设了就是人手按下的，出事有名有姓（fail-closed 的守卫需要一个有记录的出口，
#: 否则下一个被它挡住的人会把整条守卫删掉）。
ALLOW_LIVE_ENV = "AIASSISTANT_ALLOW_LIVE_HOME"

_FALSEY = frozenset({"", "0", "false", "no", "off"})


class HomeNotIsolated(BaseException):
    """测试跑者把一棵 git 工作树当成了 `AIASSISTANT_HOME`（§82.2）。

    故意在 `act.lib.config` **import 期**抛：那一刻 11 个路径常量还没有一个被下游
    的 34 处模块级常量抄走，进程里也还没有任何一次写盘（全树 import 期零文件副作用，
    判例钉住）。抛得越早，能被写坏的东西越少。

    **故意继承 BaseException**，与 `tests/__init__.RealSubprocessBanned` 同款同理由：
    生产代码遍地是 `except Exception` 的 best-effort 兜底（宪法第 11 条——一条坏记录
    不许崩 pass），守卫要是能被吞掉就等于没建。实测过一次：`server/settings.py` 那句
    `except Exception: skill_store = None` 会把这条拒绝咽下去，进程带着 live 路径
    若无其事地跑完（判例 `SwallowedGuardTestCase`）。unittest 的 testPartExecutor
    用裸 `except:` 兜，所以照样记成该条测试的 ERROR，不会把整轮跑飞。
    """


def checkout_root(path) -> Optional[Path]:
    """`path` 落在哪棵 git 工作树里；`None` = **证明得了**它在任何工作树之外。

    先把每一级符号链接解开再判（§74.1 同款：字面路径骗得过守卫）。`.git` 是目录
    （普通 clone）还是文件（worktree / submodule）都算，一路往上走到 `/`——事故里
    那棵 checkout 根本不在 `$HOME` 底下。

    **解析不动 = 不算证明**（fail-closed）：`resolve()` 抛了就拿原路径继续走，宁可
    多拦一次，也不放过一棵其实住在工作树里的 home。
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        resolved = Path(path).expanduser()
    for candidate in (resolved, *resolved.parents):
        try:
            if (candidate / ".git").exists():
                return candidate
        except OSError:      # 权限 000 的中间目录：判不了这一级，往上继续
            continue
    return None


def under_test(modules=None) -> bool:
    """这个进程是不是一个测试跑者（`TEST_RUNNER_MODULES` 任一已装进 sys.modules）。"""
    loaded = sys.modules if modules is None else modules
    return any(name in loaded for name in TEST_RUNNER_MODULES)


def _allowed(env) -> bool:
    return str(env.get(ALLOW_LIVE_ENV, "")).strip().lower() not in _FALSEY


def explain(home, root) -> str:
    """守卫拒绝时给人看的那段话——点名两棵树，并给出两条修法（§82.2）。"""
    return (
        "AIASSISTANT_HOME 指向一棵 git 工作树，而这个进程是测试跑者：\n"
        "  home     = %s\n"
        "  checkout = %s\n"
        "测试与无人值守的门跑绝不许写进 live 安装（CONTRACT §82，issue #452："
        "2026-09-18 的看板被抹就是这条路）。修法两条：\n"
        "  1. 给这一次跑一个沙箱：AIASSISTANT_HOME=$(mktemp -d) "
        "python3 -m unittest discover -s tests\n"
        "  2. 真要写进这棵树（几乎从不）：%s=1\n"
        % (home, root, ALLOW_LIVE_ENV)
    )


def guard(home, *, modules=None, env=None) -> None:
    """测试跑者 × home 在工作树里 → 抛 `HomeNotIsolated`；其余一切情形静默放行。

    `modules` / `env` 是注入缝（判例用；`tests/__init__.py` 的反向哨兵靠
    `modules=("unittest",)` 以跑者身份补问一次），默认读真进程状态。
    """
    environ = os.environ if env is None else env
    if not under_test(modules) or _allowed(environ):
        return
    root = checkout_root(home)
    if root is not None:
        raise HomeNotIsolated(explain(home, root))
