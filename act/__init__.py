"""Zelin's AI Assistant — act (approve / execute / deliver) pipeline package.

版本真源是 main 上的 git tag（CONTRACT §56.1；宪法第 8 条），**不是这个文件**：
下面那一行只是烘焙回落值（没有 act/_version.py 也没有 git 的副本才用它）。
运行时 act.lib.version.resolve 按 act/_version.py → git describe → 回落值解析。
PR 永不改那一行来「bump」——CI「Version pins untouched」把关；它只在过渡期
（§56.1 过渡条款）或 chore PR 里刷新到当前最新 tag。
"""
from act.lib import version as _version

__version__ = "0.48.21"
__version__ = _version.resolve(__version__)
