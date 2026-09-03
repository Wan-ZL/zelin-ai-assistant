"""doctor 探针家族：管线活性与数据层（CONTRACT §25 行目录；§53.6 store2 体检；
§47.4 心跳看门狗；§54 看板 server；§56 自动部署；§56.5 ui 步）。

行：``store2`` / ``dashboard`` / ``actd heartbeat``（进程活着 + 心跳过期 =
卡住不是在循环，FAIL ``actd_stalled``）/ ``board server`` / ``board ui build`` /
``auto-deploy``。后三行的判决逻辑住 act/lib/board_server.py 与
act/lib/deploy_state.py（plain-data row），这里只包 CheckResult。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Optional

from act.lib import board_server, config, deploy_state, heartbeat, install_report, platform
from act.lib.checks.core import (ACTD_LABEL, ACTD_TASK, ACTD_UNIT, FAIL, OK, WARN,
                                 CheckResult, installer, launchctl_table, pick,
                                 row_from)

# actd rewrites dashboard.json every ~10s pass; anything older than this means
# the daemon is not writing (same threshold as the app's staleness banner).
DASHBOARD_FRESH_SECONDS = 90


# --------------------------------------------------------------------------- #
# store2 (§53.6)
# --------------------------------------------------------------------------- #
def _store2_active_row(st: dict) -> CheckResult:
    marker = st.get("marker") or {}
    late = st.get("late_yaml_writes") or []
    if late:
        shown = ", ".join(late[:5]) + ("…" if len(late) > 5 else "")
        return CheckResult(
            "store2", WARN,
            pick("SQLite 是真源，但激活后仍有进程往 YAML 目录写：%s"
                 "——那些卡不在真源里" % shown,
                 "SQLite is the truth, but YAML files were written after"
                 " activation: %s — those cards are NOT in the truth" % shown),
            pick("确认写者已升级/重启（旧雷达进程），再手动核对这些文件是否"
                 "需要重新录入（重新触发一次对应捕获）",
                 "restart the stale writer processes, then re-enter those"
                 " cards through a normal capture"))
    return CheckResult(
        "store2", OK,
        "SQLite is the registry truth (%s cards at activation; backup %s;"
        " daily export last_run=%s)" % (
            marker.get("cards", "?"), marker.get("backup_dir", "?"),
            st.get("export_last_run") or "never"))


def _store2_db_missing_row() -> CheckResult:
    from act.lib import registry
    return CheckResult(
        "store2", FAIL,
        pick("激活标记在，但 %s 不见了——数据层处于故障半态，管线读写会"
             "响亮失败" % registry.store2_db_path(),
             "truth marker present but %s is missing — the data layer is"
             " in a broken half-state" % registry.store2_db_path()),
        pick("按 docs/TROUBLESHOOTING.md「store2 回滚」：停守护 → 恢复 "
             "state/backups/registry-<ts>/ → config registry.backend: yaml"
             " → 重启",
             "follow docs/TROUBLESHOOTING.md (store2 rollback): stop the"
             " daemons, restore state/backups/registry-<ts>/, set"
             " registry.backend: yaml, restart"),
    ).with_failure("store2_db_missing")


def _store2_refused_row(st: dict) -> CheckResult:
    act_info = st.get("activation") or {}
    reason = str(act_info.get("reason") or "?")
    n = act_info.get("diff_total") or 0
    extra = (pick("；差异 %s 条，明细在 state/store2_activation.json" % n,
                  "; %s field diff(s), details in"
                  " state/store2_activation.json" % n) if n else "")
    return CheckResult(
        "store2", FAIL,
        pick("store2 激活被拒，YAML 仍是真源：%s%s" % (reason, extra),
             "store2 activation refused — YAML stays the truth: %s%s"
             % (reason, extra)),
        pick("修复点名的卡文件后等重试（或删 state/store2_activation.json"
             " 立即重试）；备份完好在 %s" % act_info.get("backup_dir"),
             "fix the named card files and wait for the retry (or delete"
             " state/store2_activation.json to retry now); the backup is"
             " intact at %s" % act_info.get("backup_dir")),
    ).with_failure("store2_refused")


def _store2_yaml_forced_row(st: dict) -> CheckResult:
    note = "（store2 标记在，回滚开关生效）" if st.get("marker_present") else ""
    return CheckResult(
        "store2", OK,
        pick("YAML 后端（registry.backend/env 强制）%s" % note,
             "YAML backend (forced by registry.backend/env)%s" % note))


def _store2_pending_row() -> CheckResult:
    # pending：还没激活过（全新安装 / 升级后第一个 actd pass 会做）
    return CheckResult(
        "store2", OK,
        pick("尚未激活（YAML 是真源）——actd 下一个 pass 将自动「备份→迁移→"
             "逐字段比对」，零差异才切换",
             "not yet activated (YAML is the truth) — actd's next pass runs"
             " backup → migrate → field-by-field parity, and only a zero diff"
             " flips the truth"))


def check_store2(probes):
    """§53.6 数据层真源体检：激活状态 / 拒绝原因 / 每日导出 / 迟到 YAML 写。

    数据源 = act/lib/store2/activate.status()（doctor 与 --report 同一真相）。
    FAIL 两形：refused（迁移比对有差异——YAML 仍是真源，diff 摘要在
    state/store2_activation.json）与 db_missing（标记在、库没了——按
    TROUBLESHOOTING「store2 回滚」处置）。"""
    from act.lib.store2 import activate
    st = activate.status()
    state = st.get("state")
    if state == "yaml_forced":
        return _store2_yaml_forced_row(st)
    if state == "active":
        return _store2_active_row(st)
    if state == "db_missing":
        return _store2_db_missing_row()
    if state in ("refused", "cooldown"):
        return _store2_refused_row(st)
    return _store2_pending_row()


# --------------------------------------------------------------------------- #
# dashboard freshness
# --------------------------------------------------------------------------- #
def check_dashboard(probes):
    path = config.DASHBOARD_PATH
    if not path.exists():
        return CheckResult(
            "dashboard", FAIL,
            pick("state/dashboard.json 缺失——App 会一直显示「missing」",
                 "state/dashboard.json missing - the app shows 'missing' forever"),
            pick("启动 actd（bash install.sh），或手动生成一次：python3 -m act.lib.dashboard",
                 "start actd (bash install.sh), or seed once: python3 -m act.lib.dashboard"))
    try:
        gen = json.loads(path.read_text(encoding="utf-8")).get("generated_at", "")
        ts = _dt.datetime.strptime(gen, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
    except Exception:  # noqa: BLE001 - torn/malformed file is the symptom
        return CheckResult(
            "dashboard", FAIL,
            pick("state/dashboard.json 读不出来或没有合法的 generated_at",
                 "state/dashboard.json is unreadable or has no valid generated_at"),
            pick("删掉它并重启 actd（它会原子重写）",
                 "delete it and restart actd (it rewrites atomically)"))
    age = probes.now() - ts
    if age <= DASHBOARD_FRESH_SECONDS:
        return CheckResult("dashboard", OK, "fresh (generated %ds ago)" % max(int(age), 0))
    return CheckResult(
        "dashboard", FAIL,
        "stale (generated %d min ago) - actd is not writing; the app renders old data" % int(age // 60),
        "launchctl list | grep aiassistant; "
        "tail -20 ~/Library/Logs/zelin-ai-assistant/actd.launchd.log"
        " (pre-v0.48 installs: state/actd.launchd.log)",
    ).with_failure("dashboard_stale")


# --------------------------------------------------------------------------- #
# §47.4 heartbeat
# --------------------------------------------------------------------------- #
def pid_alive(pid: int) -> Optional[bool]:
    """进程是否存在；None = 本平台判不了（Windows 的 os.kill(pid, 0) 会
    TerminateProcess，绝不能拿来探活）。"""
    if platform.is_windows():
        return None
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # 存在但不是我们的——对「活着」的判断足够
    except (OSError, TypeError, ValueError):
        return None


def actd_restart_cmd() -> str:
    """The hard-restart command for the resident daemon on this OS — a stalled
    process needs a kill+respawn, not a reload."""
    if platform.is_darwin():
        return "launchctl kickstart -k gui/$(id -u)/%s" % ACTD_LABEL
    if platform.is_windows():
        return 'schtasks /End /TN "%s" & schtasks /Run /TN "%s"' % (ACTD_TASK, ACTD_TASK)
    return "systemctl --user restart %s" % ACTD_UNIT


def _heartbeat_pid(hb: Optional[dict]) -> Optional[int]:
    """The writer's pid when it is a real positive int (bool is an int — reject)."""
    pid = (hb or {}).get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        return pid
    return None


def actd_alive(probes, hb: Optional[dict]) -> Optional[bool]:
    """Is the resident daemon process alive? darwin asks launchd (the pid
    column); elsewhere the heartbeat's own pid is probed. None = cannot tell."""
    if platform.is_darwin():
        row = launchctl_table(probes).get(ACTD_LABEL)
        if row is not None:
            return row[0] != "-"
    pid = _heartbeat_pid(hb)
    if pid is not None:
        return probes.pid_alive(pid)
    return None


def _never_beat_row(alive: Optional[bool], restart: str):
    if alive:
        return CheckResult(
            "actd heartbeat", WARN,
            "actd is running but has never written state/actd.heartbeat - the "
            "daemon predates v0.48.4 or just started; without it a silent stall "
            "is invisible",
            restart + "  # restart so the upgraded daemon starts beating")
    return []   # not running: the actd row already carries the fix


def _stale_beat_row(hb: dict, alive: Optional[bool], restart: str) -> CheckResult:
    mins = int(float(hb.get("age_s") or 0) // 60)
    phase = str(hb.get("phase") or "?")
    if alive is False:
        return CheckResult(
            "actd heartbeat", WARN,
            "no heartbeat for %d min and actd is not running - see the actd row"
            % mins, restart)
    who = "alive (pid %s)" % hb.get("pid") if alive else "process state unknown"
    return CheckResult(
        "actd heartbeat", FAIL,
        "%s but no heartbeat for %d min (last seen in phase '%s') - the loop is "
        "stuck, not looping; cards will not move and the board goes stale"
        % (who, mins, phase),
        restart,
    ).with_failure("actd_stalled")


def check_heartbeat(probes):
    """§47.4 stall watchdog: process alive + heartbeat stale = the loop is stuck.

    2026-08-31 22:31: actd kept its pid for 2.5 h with no children, parked in
    time.sleep, dashboard frozen — `launchctl list` said running, loop_health
    counted zero crashes, doctor said healthy. The heartbeat's mtime (touched
    at every phase boundary of every pass) is the only signal that separates
    "alive" from "looping"; ``stale_after_s`` comes from the writer
    (3 × interval, floor 90 s) so the threshold has exactly one owner.
    """
    hb = probes.heartbeat_read()
    alive = actd_alive(probes, hb)
    restart = actd_restart_cmd()
    if hb is None:
        return _never_beat_row(alive, restart)
    if not heartbeat.is_stale(hb):
        return CheckResult(
            "actd heartbeat", OK,
            "beating (phase=%s, %ds ago%s)" % (
                str(hb.get("phase") or "?"), int(float(hb.get("age_s") or 0)),
                ", pid %s" % hb["pid"] if hb.get("pid") else ""))
    return _stale_beat_row(hb, alive, restart)


# --------------------------------------------------------------------------- #
# §54 看板 server + §56.5 ui 步 + §56 auto-deploy（判决逻辑在 lib 的 row 函数里）
# --------------------------------------------------------------------------- #
def check_board_server(probes):
    """§54 看板 server 行：`GET /api/health` 答话才算活——`launchctl list` 里的
    pid 只说明进程起了，bind 成功没有它不知道。可达 + 托管 OK；可达但非托管
    （壳 spawn 的旧形状）WARN；不可达 + 托管 FAIL `board_server_down`（crash-loop /
    端口被占）；不可达 + 未托管 WARN。探针不可用（沙箱 / windows）→ 不出行。"""
    if platform.is_windows():
        return []
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
    """§56.5 `ui` 步的可见性：最近一次 install.sh 的 `ui` step 是 `skipped_tcc`
    （node 在 launchd 会话里缺 Full Disk Access——部署照常完成、web 看板却没
    重建）→ WARN `ui_build_tcc_blocked`；`fail`（只可能来自手动 install.sh）→
    WARN 指向 ui-build.log；其余不出行。"""
    row = board_server.ui_build_row(install_report_step("ui"), installer())
    if row is None:
        return []
    return row_from(row, "board ui build")


def check_auto_deploy(probes):
    """§56 合并即上岗：最近一次自动部署的结果（`deploy_state.read()`：HOME 镜像描述的
    是本 checkout 时读镜像，否则读 state/ 投影；两个文件都不存在 = 这台机器不跑该
    agent → 不出行）。healthy → OK、其余 → WARN、healthy 但 `last_incident` 在案 →
    WARN（#135 review）；文案与修法住 `deploy_state.auto_deploy_row`（§56.4）。"""
    state = deploy_state.read()
    if not state:
        return []
    return row_from(deploy_state.auto_deploy_row(state), "auto-deploy")
