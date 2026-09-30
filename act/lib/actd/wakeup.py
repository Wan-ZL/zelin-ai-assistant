"""早醒：排着 owner 动作时主循环不睡满一个 pass 间隔（CONTRACT §80.1）。

**病灶**（issue #450，owner 原话「前端来看每一步都有很多等待」）：主循环历来
结在 `time.sleep(interval)`（出厂 10s），而 owner 的每一个动作都要先落
`state/inbox/`、等**下一个** pass 才被 drain。于是点一下批准到卡片动起来，
平均白等 interval/2、最坏 interval——而且这笔等待挂在**每一个**动词上
（批准 / 停止 / 验收 / 打回），一条卡走完 running → review 要交这笔过路费
好几次。

**单写者不破**（宪法第 1 条）：本模块改的只是主循环**睡多久**，不是谁写卡。
registry 的写者仍然只有 actd 主循环；server 照旧只写 `state/inbox/`（它本来
就写这个目录）+ 回执。「有新工作」的信号就是 inbox 目录本身多了一个文件——
于是零新文件、零新写者、零新契约面，syncd / boardctl / 手写进去的文件全部
自动享受同一条早醒。

**为什么轮询而不是 FSEvents/inotify**：运行时依赖白名单 = stdlib + PyYAML
（宪法第 7 条），而 `server/watcher.py` 早有同款先例并写明了理由（300ms
mtime 轮询 dashboard.json）。空闲时的代价 = 每秒 4 次 `os.scandir`。

**不会空转**：基线 = **pass 开始那一刻**的文件名集合，只有出现基线之外的名字
才早醒。`inbox.process_inbox` 是全路径 ack+unlink 的（连毒文件都删），所以
正常情况下 inbox 总会被抽干；但万一 `safe_unlink` 失败留下一个删不掉的文件，
它在基线里，于是不会让循环 250ms 空转一整天。基线取在 pass **之前**还有第二
个好处：pass 中途（drain 跑完之后）才落地的动作不在基线里，于是那一笔不用再
等一整个 interval。
"""
from __future__ import annotations

import os
import time
from typing import Callable, Iterable, Optional

from act.lib import config

# 轮询粒度：owner 感知不到 250ms，而空闲代价是每秒 4 次 scandir。
POLL_SECONDS = 0.25


def inbox_names(inbox_dir=None) -> frozenset:
    """当前 inbox 里的决策文件名集合。

    目录不存在 / 读不动 = 空集——探测失败绝不崩主循环（宪法第 11 条），
    最坏结果只是这一轮睡满，退化成本模块之前的老行为。
    """
    d = config.INBOX_DIR if inbox_dir is None else inbox_dir
    try:
        # `with`：这个函数每秒跑 4 次、跑在常驻守护进程里，scandir 的 fd 必须
        # 当场还回去，不能指望 GC（否则 ResourceWarning 之外还漏 fd）。
        with os.scandir(d) as it:
            return frozenset(e.name for e in it if e.name.endswith(".json"))
    except OSError:
        return frozenset()


def wait_for_work(
    interval: float,
    baseline: Optional[Iterable[str]] = None,
    *,
    names: Callable[[], frozenset] = inbox_names,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    poll_s: float = POLL_SECONDS,
) -> bool:
    """睡最多 ``interval`` 秒，inbox 里一出现 ``baseline`` 之外的文件就早醒。

    ``baseline`` = pass 开始那一刻的 :func:`inbox_names`（见模块 docstring 的
    「不会空转」）。四个 seam（names/sleeper/clock/poll_s）全部可注入，判例
    不睡真觉、不碰真目录。

    Returns True 当且仅当**被新工作叫醒**（含一进来就发现有活、一秒没睡）；
    睡满 ``interval`` 返回 False。``interval <= 0`` 保持老语义（不睡）。
    """
    deadline = clock() + max(float(interval), 0.0)
    base = frozenset(baseline or ())
    while True:
        if names() - base:
            return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        sleeper(min(poll_s, remaining))
