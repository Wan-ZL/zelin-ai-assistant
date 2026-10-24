"""doctor 探针家族：看板 server 与 UI 构建（CONTRACT §25 行目录；§54 看板 server；
§23 install report 的 ui 步）。

行：``board server`` / ``board ui build``。判决逻辑住 act/lib/board_server.py（plain-data
row），这里只包 CheckResult。v2.0.0：``store2`` / ``dashboard`` / ``actd heartbeat`` /
``auto-deploy`` 四行随 Act 管线退役（全文见 tag full）。
"""
from __future__ import annotations

import json
from typing import Optional

from act.lib import board_server, config, install_report, platform
from act.lib.checks.core import installer, row_from


# --------------------------------------------------------------------------- #
# §54 看板 server + §23 install report 的 ui 步（判决逻辑在 lib 的 row 函数里）
# --------------------------------------------------------------------------- #
def check_board_server(probes):
    """§54 看板 server 行：`GET /api/health` 答话才算活——`launchctl list` 里的
    pid 只说明进程起了，bind 成功没有它不知道。可达 + 托管 OK；可达但非托管
    （壳 spawn 的旧形状）WARN；不可达 + 托管 FAIL `board_server_down`（crash-loop /
    端口被占）；不可达 + 未托管 WARN。探针不可用（沙箱 / windows）→ 不出行。"""
    port = int(config.load_config().server_port)
    verdict = probes.board_health(port)
    if verdict.get("state") == "unavailable":
        return []
    try:
        listing = probes.launchctl_list()
    except Exception:  # noqa: BLE001 - 探针不许崩（宪法第 11 条）
        listing = ""
    darwin = platform.is_darwin()
    row = board_server.assess(verdict, board_server.hosted(listing, darwin), port,
                              darwin, installer())
    return row_from(row, "board server")


def install_report_step(name: str) -> Optional[dict]:
    """§23 install_report.json 里名为 ``name`` 的 step（最后一个同名者）；
    文件缺失 / 撕裂 / 形状不对 → None（宪法第 11 条：探针不许崩）。"""
    try:
        doc = json.loads(install_report.REPORT_PATH.read_text(encoding="utf-8"))
        steps = [st for st in doc.get("steps", []) if isinstance(st, dict) and st.get("name") == name]
    except (OSError, ValueError, AttributeError):
        return None
    return steps[-1] if steps else None


def check_ui_build(probes):
    """§23 install report `ui` 步的可见性：最近一次 install.sh 的 `ui` step 是 `skipped_tcc`
    （node 在 launchd 会话里缺 Full Disk Access——部署照常完成、web 看板却没
    重建）→ WARN `ui_build_tcc_blocked`；`fail`（只可能来自手动 install.sh）→
    WARN 指向 ui-build.log；其余不出行。"""
    row = board_server.ui_build_row(install_report_step("ui"), installer())
    if row is None:
        return []
    return row_from(row, "board ui build")
