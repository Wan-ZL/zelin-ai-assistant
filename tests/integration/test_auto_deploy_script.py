"""scripts/auto-deploy.sh 行为判例（CONTRACT §56 合并即上岗）——真 bash + 真 git。

住在 tests/integration/（防腐 #7：真 IO 只许住这里，单文件时间预算见
BUDGET_SECONDS）。整套夹具是一个临时 bare origin + 一个 live clone，clone 里的
install.sh / act/doctor.py / act/lib/notify.py / act/auto_deploy.py 全是**记录
调用、按剧本出退出码**的假货，PATH 前置一个假 `curl`（按剧本回 GitHub
check-runs JSON），只有 scripts/auto-deploy.sh、scripts/version_stamp.py 与
act/lib/version.py 是真的（逐字拷进夹具并提交）。版本真源 = tag（§56.1）：
`push(version)` 在 origin 上建 `v<version>` tag，脚本 `fetch --tags` 后用
version_stamp.py 算期望版本，假 install.sh 用同一把尺盖章 + 写心跳。
不出网、不起 launchd、不碰真 $HOME（HOME 指到临时目录）。

钉住的行为：
  - HEAD == origin/main **且机器真在跑这个版本**（install_report.json 版本 ==
    checkout、actd.heartbeat 版本 == checkout 且新鲜）→ up_to_date，install 不跑；
  - **deployed 就是在跑**（2026-09-02 事故；§56.3 第 2 步）：HEAD 到位但三件事
    不齐 → install_incomplete（reason token + detail 点名）；**第一眼只记账**
    （incomplete_seen），下一轮仍不齐才重跑一次 install.sh（先过与第 3 步同一道 CI
    闸门：pending 等、红 → incomplete_sha 中毒 + 一条通知、非 github 远端不重跑；
    --force 两道都跳）；重跑后齐了 → deployed；只是心跳过期（版本都对）先等
    AUTODEPLOY_HEARTBEAT_GRACE 秒看它会不会再跳（刚唤醒 / 重启窗口）；**每个 sha
    最多重跑 N 次、成功也计**（起来又死的 daemon 不能每 10 分钟重装一次）→
    incomplete_sha 中毒 + 每 sha 一条通知，--force / main 前进解毒；回滚被拒留在
    新 sha 的机器由后续轮次把安装做完；回滚判决另存 last_incident，直到下一次
    deployed 才清（up_to_date 不清——#135 review 的「10 分钟后被冲掉」）；
  - 锁住 $HOME；升级窗口里 v0.48.19 的 state/auto-deploy.lock 活着 → 跳过，死了 → 清；
  - **卷访问探针 + HOME 镜像**（同一事故；§56.3 第 1 步 / §56.4）：第一次 git
    调用前读 repo + 在 state/ 里 mkstemp，PermissionError → blocked_tcc、HEAD 不
    动、日志点名 plist ProgramArguments[0]、通知一天一次；状态先写
    ~/Library/Application Support/ZelinAIAssistant/deploy_state.json（真源；
    state/ 不可写时照样落盘），repo 的 state/deploy_state.json 是尽力投影；锁也
    住 $HOME；终端触发（tty / TERM_PROGRAM / AUTODEPLOY_TRIGGER=terminal）不改写
    unattended_* 三元组；镜像不存在时从 repo 投影播种（failed_sha 不丢）；
  - **CI 闸门**（PR #124 审查 B1）：ff 之前查 origin/main **那个 sha** 的 `ci`
    check-run；success 才部署；in_progress / 尚无 run / API 不可达 → ci_pending
    不动 HEAD 下轮再试；红 → ci_failed + failed_sha 记账 + 一条通知；只有
    CI_CHECKS 里的名字算数；--force 跳过闸门；origin 不是 github.com 且没设
    AUTODEPLOY_CI_REPO → failed + 通知一次，不猜；
  - **合并风暴下的部署目标**（2026-09-03 live：head 每 10–20 min 换一个、CI 排队
    20+ min，五轮「CI not green yet on <越来越新的 sha>」而绿的 v1.0.4–1.0.6 几小时
    没上机）：head 不绿（pending / 红 / 已中毒）→ 沿 first-parent 从 head 往回走到
    PREV 之前（最多 AUTODEPLOY_CI_WALK 个）最新的绿 commit 并部署它（仍是 ff）；
    红的 ancestor 当场中毒 + 一条通知、以后不再问；没有 `ci` run 的 commit 是
    pending 不是绿；`behind_main` / `behind_main_why` 记下被跳过的 head 与原因，部署到
    head 或 up_to_date 时清掉；中毒账本 `failed_shas`（镜像私账）装得下红 head + 回滚
    过的 ancestor 两者——单槽 `failed_sha` 会让两者互相覆盖、每轮重部署再回滚；
    --force 仍只认 head；
  - origin/main 前进 + 树干净 → ff、自检、install --non-interactive、等新 actd
    心跳、doctor 基线/复查、deployed（state 文件 + 一条通知）；
  - **自检**（B3）：合进来的 scripts/auto-deploy.sh 不能 `bash -n` 或
    act.auto_deploy import 不了 → 不装、回滚；
  - **就绪等待**（B2）：install 后必须等到 state/actd.heartbeat 由**新进程**
    （pid 变了）写下**新版本** + phase=idle；旧 daemon 的心跳不算；超时 =
    actd:no_heartbeat_from_new_version 回滚；新 daemon pass 抛 `failed` 只在旧
    daemon 也 `failed` 时算 pre-existing；**「新版本」= install.sh `version` step 真盖
    的号**（install_report `version=ok:<v>`），不是 checkout 的预测；stamp 步失败
    （`version=warn:…`，2026-09-02T10:14Z：陈旧的手写 stamp 让新 actd 报旧号、好部署被
    误回滚）→ 身份不可验证：只看新 pid + idle，日志 WARN，deployed 的 detail 点名；
    下一轮 running_mismatch 看到旧号 → install_incomplete → 重跑 install.sh 重盖章自愈；
  - install 失败 / doctor 出现**新增** FAIL → git reset --hard 回旧 sha + 再装 +
    rolled_back + failed_sha 记账，下一轮对同一 sha 不再重试，--force 才重试；
  - **settle-before-verdict**（首次实战 2026-09-01 的假阳性回滚）：装后 doctor
    判决是重试环（默认 3 次、间隔 AUTODEPLOY_DOCTOR_SETTLE），只有撑到**最后
    一次**的新增 FAIL 才回滚；瞬态 FAIL（重启后 daemon 还在 settle / EPERM 窗口）
    自愈 → 照常 deployed；判决只看最后一次（早轮的名字不进 detail）；
  - 部署前已红的 doctor 项不归咎新版本（deployed，detail 点名 pre-existing）；
  - doctor 自身跑不出 JSON（import 崩 / 打印垃圾）= 致命而非 pre-existing：基线
    阶段就回滚且不装新版本；装完后**持续**崩同样回滚，瞬态崩走 settle 重试；
  - 脏树拒绝（不动 HEAD、同一 sha 只通知一次）；不在 main 拒绝；
  - 回滚前重验：部署期间 owner 改了 tracked 文件 → 回滚**被拒**（rollback_failed、
    通知、改动保留、HEAD 留在新版本）；install.sh 自己的 +x 位翻转不算改动，照常回滚；
  - **git 答不上来 ≠ detached**（首次实战：EPERM 窗口里 symbolic-ref/rev-parse
    全空，误报 detached + 空 sha）：入口与回滚重验都区分 rc>1（git 读不了 checkout
    → 诚实报错，不 reset）与 rc=1（真 detached）；"checkout left at" 永不插空值；
  - **store2 迁移感知回滚**：state/store2_truth.json 在本次部署期间出现、或
    state/store2.db 的 PRAGMA user_version 在部署期间升高（schema bump：标记
    早已在场，单看标记会漏，#135 review）→ 回滚被拒 + 指向
    docs/TROUBLESHOOTING.md「store2 回滚」；标记先在且 schema 没动 → 照常回滚；
    判决在**冻结的账本**上取：先 bootout actd（kill 会被 KeepAlive 复活）再重
    采样（正好在停止那一刻落盘的迁移也被抓住），拒绝路径把 actd bootstrap 回
    来；user_version 探针答不上来 = unknown = **fail closed** 拒绝，绝不当 0；
  - write_state（镜像 / repo 投影各自）/ notify 失败时日志行携带子进程异常（首次
    实战两行 non-fatal 全裸，PermissionError 只在 launchd stderr 里）；notify()
    吞掉队列写失败只返回 False 的路径同样记行；
  - install.sh 非零永不 deployed；incomplete_runs 按 sha 计；投影 detail 不带本机路径；
  - 每轮都写 last_run（回滚路径、poisoned-sha 跳过也写）；
  - 锁：活 PID 持锁则跳过，死 PID 的锁视为陈旧；
  - 日志 1 MB 自压；ff-merge 途中脚本自身被替换（哪怕换成执行即 exit 99 的
    booby trap）也照常按旧逻辑跑完——main 包裹 + git rename 写文件；推论：
    第 N 版新增的闸门保护的是 N 之后的部署，永远保护不了部署 N 自己的那一轮。
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "auto-deploy.sh"
# the real version machinery rides along (the script derives the expected version
# through scripts/version_stamp.py, which imports the fixture's act.lib.version)
REAL_COPIES = ("scripts/version_stamp.py", "act/lib/version.py")
FIXTURE_INIT = ('"""fixture act: resolves like the real package (stamp -> git tag -> fallback)."""\n'
                "from act.lib import version as _version\n"
                '__version__ = "0.0.0"\n'
                "__version__ = _version.resolve(__version__)\n")
_WIN = sys.platform.startswith("win")
BUDGET_SECONDS = 300  # ~80 runs of real bash+git; ~170 s on a 2024 Mac (v0.48.20: +33 runs)
_T0 = time.monotonic()

FAKE_INSTALL = r"""#!/bin/bash
# fake install.sh: record the call, exit per FAKE_INSTALL_RC_PLAN (one rc per line, consumed)
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# like the real install.sh (§56.1): stamp act/_version.py from the tag and report
# that number (§23 step `version=ok:<v>`); a pre-cutover checkout (no stamper)
# still has the literal line. FAKE_INSTALL_STAMP_FAIL models the 2026-09-02T10:14Z
# shape: the stamp step fails (TCC-denied interpreter), a STALE act/_version.py
# survives, the daemons read it → heartbeat + report carry the stale number,
# the report's `version` step says warn.
stamp_step=""
if [ -n "${FAKE_INSTALL_STAMP_FAIL:-}" ]; then
    ver="$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "$here/act/_version.py" 2>/dev/null)"
    [ -n "$ver" ] || ver="$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "$here/act/__init__.py")"
    stamp_step='{"name": "version", "status": "warn", "detail": "stamp failed — /opt/homebrew/bin/python3: [Errno 1] Operation not permitted"}'
elif [ -f "$here/scripts/version_stamp.py" ]; then
    ver="$("${AIASSISTANT_PYTHON:-python3}" "$here/scripts/version_stamp.py" --write 2>/dev/null)"
    stamp_step="$(printf '{"name": "version", "status": "ok", "detail": "%s"}' "$ver")"
else
    ver="$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "$here/act/__init__.py")"
fi
printf 'install %s head=%s version=%s active=%s\n' "$*" "$(git -C "$here" rev-parse HEAD)" "$ver" \
    "${AIASSISTANT_AUTODEPLOY_ACTIVE:-}" >> "$FAKE_INSTALL_LOG"
rc=0
if [ -n "${FAKE_INSTALL_RC_PLAN:-}" ] && [ -s "$FAKE_INSTALL_RC_PLAN" ]; then
    rc="$(head -n 1 "$FAKE_INSTALL_RC_PLAN")"
    tail -n +2 "$FAKE_INSTALL_RC_PLAN" > "$FAKE_INSTALL_RC_PLAN.tmp" && mv "$FAKE_INSTALL_RC_PLAN.tmp" "$FAKE_INSTALL_RC_PLAN"
elif [ -n "${FAKE_INSTALL_STEPS_PLAN:-}" ] && [ -s "$FAKE_INSTALL_STEPS_PLAN" ]; then
    # §23/§56.5: derive the exit code from a report-step line set through the
    # REAL failed_deploy_steps() (copied out of the repo's install.sh by the
    # test) — one plan line per call, `|` separates the step lines.
    steps="$(head -n 1 "$FAKE_INSTALL_STEPS_PLAN" | tr '|' '\n')"
    tail -n +2 "$FAKE_INSTALL_STEPS_PLAN" > "$FAKE_INSTALL_STEPS_PLAN.tmp" && mv "$FAKE_INSTALL_STEPS_PLAN.tmp" "$FAKE_INSTALL_STEPS_PLAN"
    # shellcheck disable=SC1090
    . "$FAKE_INSTALL_FDS"
    REPORT_STEPS="$steps
"
    rc="$(failed_deploy_steps | grep -c . || true)"
    printf 'steps rc=%s: %s\n' "$rc" "$(printf '%s' "$steps" | tr '\n' ' ')" >> "$FAKE_INSTALL_LOG"
fi
[ -n "${FAKE_INSTALL_SLEEP:-}" ] && sleep "$FAKE_INSTALL_SLEEP"
# simulate the owner editing a tracked file while the deploy runs / install.sh's own chmod
[ -n "${FAKE_INSTALL_EDIT:-}" ] && printf 'owner edit mid-deploy\n' >> "$here/README.md"
[ -n "${FAKE_INSTALL_CHMOD:-}" ] && chmod +x "$here/README.md"
# simulate the new actd's first pass migrating the registry truth to SQLite
# (§53 activation happens between the script's "before" sample and any rollback)
if [ -n "${FAKE_INSTALL_STORE2:-}" ]; then
    mkdir -p "$here/state"
    printf '{"activated_at": "2026-09-01T21:07:19Z"}\n' > "$here/state/store2_truth.json"
fi
# simulate the new actd bumping the store2 schema on first DB open (#135 shape:
# the truth marker already exists, only PRAGMA user_version moves)
if [ -n "${FAKE_INSTALL_STORE2_UV:-}" ]; then
    mkdir -p "$here/state"
    python3 - "$here/state/store2.db" "$FAKE_INSTALL_STORE2_UV" <<'EOF'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("PRAGMA user_version=%d" % int(sys.argv[2]))
con.commit()
con.close()
EOF
fi
# corrupt the ledger mid-deploy: the rollback-time user_version probe must
# fail CLOSED (report unknown, refuse) instead of assuming 0
if [ -n "${FAKE_INSTALL_STORE2_CORRUPT:-}" ]; then
    mkdir -p "$here/state"
    printf 'NOT A SQLITE DB' > "$here/state/store2.db"
fi
# generic trigger file (the fake git uses it to start failing mid-run)
[ -n "${FAKE_INSTALL_TOUCH:-}" ] && touch "$FAKE_INSTALL_TOUCH"
# simulate §23 cron=skipped_tcc: crontab TCC-refused, warned about, NOT counted
# in the exit code (the rc plan stays authoritative — real mapping pinned in
# tests/test_install_cron_tcc.py)
if [ -n "${FAKE_INSTALL_CRON_TCC:-}" ]; then
    echo "  [warn] crontab is TCC-blocked in this session — grant Full Disk Access to /pinned/python3, then rerun bash install.sh"
    echo "install.sh --non-interactive: ok (cron=skipped_tcc)"
fi
# the restarted actd's heartbeat (§47.4): a NEW pid ($$ of this install run),
# the checkout's version, phase per FAKE_INSTALL_HEARTBEAT (default idle = one
# full pass done; "none" = the new daemon never writes one, e.g. dies on
# import). ts = now: the "deployed means running" predicate reads its age.
hb="${FAKE_INSTALL_HEARTBEAT:-idle}"
if [ "$hb" != "none" ]; then
    mkdir -p "$here/state"
    printf '{"ts": "%s", "phase": "%s", "pid": %s, "interval": 10, "stale_after_s": 90, "version": "%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$hb" "$$" "$ver" > "$here/state/actd.heartbeat"
fi
# §23 install report — the version install.sh actually finished on (a run that
# dies before its last step never writes it; rc≠0 here models that)
if { [ "$rc" -eq 0 ] && [ -z "${FAKE_INSTALL_NO_REPORT:-}" ]; } || [ -n "${FAKE_INSTALL_REPORT_ANYWAY:-}" ]; then
    mkdir -p "$here/state"
    printf '{"version": "%s", "generated_at": "%s", "mode": "non-interactive", "steps": [%s]}\n' \
        "$ver" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$stamp_step" > "$here/state/install_report.json"
fi
exit "$rc"
"""

FAKE_CURL = r"""#!/bin/bash
# fake curl: record the URL, answer the check-runs API per FAKE_CURL_PLAN (one
# word per line, consumed): success | failure | pending | missing | rerun |
# UNREACHABLE | GARBAGE. Default success. The success body also carries a RED
# non-required check (Lint) to pin that only CI_CHECKS names gate the deploy.
set -u
for a in "$@"; do case "$a" in http*) printf '%s\n' "$a" >> "$FAKE_CURL_LOG" ;; esac; done
verdict=success
if [ -n "${FAKE_CURL_PLAN:-}" ] && [ -s "$FAKE_CURL_PLAN" ]; then
    verdict="$(head -n 1 "$FAKE_CURL_PLAN")"
    tail -n +2 "$FAKE_CURL_PLAN" > "$FAKE_CURL_PLAN.tmp" && mv "$FAKE_CURL_PLAN.tmp" "$FAKE_CURL_PLAN"
fi
run() { printf '{"id": %s, "name": "%s", "status": "%s", "conclusion": %s}' "$1" "$2" "$3" "$4"; }
case "$verdict" in
    UNREACHABLE) exit 22 ;;
    GARBAGE)  printf 'not json at all' ;;
    missing)  printf '{"total_count": 0, "check_runs": []}' ;;
    pending)  printf '{"check_runs": [%s]}' "$(run 1 ci in_progress null)" ;;
    failure)  printf '{"check_runs": [%s]}' "$(run 1 ci completed '"failure"')" ;;
    rerun)    printf '{"check_runs": [%s, %s]}' "$(run 1 ci completed '"failure"')" "$(run 2 ci completed '"success"')" ;;
    *)        printf '{"check_runs": [%s, %s]}' "$(run 1 ci completed '"success"')" \
                     "$(run 2 'Lint (shellcheck + ruff)' completed '"failure"')" ;;
esac
exit 0
"""

FAKE_GIT = r"""#!/bin/bash
# fake git: once FAKE_GIT_BREAK_FILE exists, symbolic-ref and every HEAD query
# fail like the live EPERM window (2026-09-01: git went dark mid-rollback and
# the refusal blamed a phantom 'detached'); everything else passes through.
set -u
if [ -n "${FAKE_GIT_BREAK_FILE:-}" ] && [ -f "$FAKE_GIT_BREAK_FILE" ]; then
    for a in "$@"; do
        case "$a" in
            symbolic-ref|HEAD)
                echo "fatal: Operation not permitted (fixture EPERM)" >&2
                exit 128 ;;
        esac
    done
fi
exec @REAL_GIT@ "$@"
"""

FAKE_LAUNCHCTL = r"""#!/bin/bash
# fake launchctl: records every call — the REAL one must never run under the
# tests (it would boot out the owner's live actd). TOCTOU seam: on `bootout`,
# optionally drop the store2 marker, simulating an actd that finishes its
# migration at the exact moment the rollback stops it; the frozen re-sample
# must still catch it.
[ -n "${FAKE_LAUNCHCTL_LOG:-}" ] && printf '%s\n' "$*" >> "$FAKE_LAUNCHCTL_LOG"
if [ "${1:-}" = "bootout" ] && [ -n "${FAKE_LAUNCHCTL_BOOTOUT_MARKER:-}" ]; then
    printf '{"activated_at": "at-the-stop-point"}\n' > "$FAKE_LAUNCHCTL_BOOTOUT_MARKER"
fi
exit 0
"""

FAKE_SHIM = '"""fake act.auto_deploy: only has to import (the script self-checks it after the merge)."""\n'

FAKE_DOCTOR = r'''"""fake act.doctor: FAIL names per call from FAKE_DOCTOR_PLAN (one line per call, consumed).
A name written `<row>@owner_action` FAILs with that §25 row_class; plain names omit the key
(the shape of a doctor from before the key existed — a rollback target)."""
import json, os, sys
from act import __version__
plan = os.environ.get("FAKE_DOCTOR_PLAN")
names = []
if plan and os.path.exists(plan):
    with open(plan, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    if lines:
        first, rest = lines[0], lines[1:]
        with open(plan, "w", encoding="utf-8") as fh:
            fh.write("\n".join(rest) + ("\n" if rest else ""))
        names = [n for n in first.split(",") if n and n != "-"]
log = os.environ.get("FAKE_DOCTOR_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("doctor %s version=%s fails=%s\n" % (" ".join(sys.argv[1:]), __version__, ",".join(names) or "-"))
if "GARBAGE" in names:
    # the doctor itself is broken on this commit: no JSON at all
    print("Traceback (most recent call last): ModuleNotFoundError: No module named 'yaml'")
    sys.exit(1)
checks = []
for n in names:
    name, _, row_class = n.partition("@")
    row = {"name": name, "status": "fail", "detail": "", "fix": ""}
    if row_class:
        row["row_class"] = row_class
    checks.append(row)
checks.append({"name": "home", "status": "ok", "detail": "", "fix": ""})
print(json.dumps({"home": os.getcwd(), "checks": checks}))
sys.exit(len(names))
'''

FAKE_NOTIFY = '''"""fake act.lib.notify: append title|body to FAKE_NOTIFY_LOG."""
import os
def notify(title, body, subtitle=None, req=None, kind=None):
    path = os.environ.get("FAKE_NOTIFY_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s|%s\\n" % (title, body))
    return not os.environ.get("FAKE_NOTIFY_RETURN_FALSE")
'''


def _install_sh_fn(name):
    """install.sh 里 `name() {` … 行首 `}` 的原文（同 tests/test_auto_deploy_agent）。"""
    import re
    text = (REPO / "install.sh").read_text(encoding="utf-8")
    m = re.search(r"^%s\(\) \{.*?^\}" % re.escape(name), text, flags=re.S | re.M)
    assert m, "install.sh no longer defines %s()" % name
    return m.group(0) + "\n"


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=60, check=True).stdout.strip()


def tearDownModule():
    elapsed = time.monotonic() - _T0
    if elapsed > BUDGET_SECONDS:
        raise AssertionError("tests/integration/test_auto_deploy_script.py took %.0fs > %ds budget"
                             % (elapsed, BUDGET_SECONDS))


@unittest.skipIf(_WIN, "bash + install.sh are POSIX-only; the Windows installer is install.ps1")
class AutoDeployScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autodeploy-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.logs = self.tmp / "logs"
        self.notify_log = self.tmp / "notify.log"
        self.install_log = self.tmp / "install.log"
        self.doctor_log = self.tmp / "doctor.log"
        self.doctor_plan = self.tmp / "doctor.plan"
        self.install_rc_plan = self.tmp / "install.rc"
        self.install_steps_plan = self.tmp / "install.steps"
        # the REAL failed_deploy_steps() from the repo's install.sh (§56.5
        # exit-code rule), sourced by the fake install.sh when a steps plan is set
        self.install_fds = self.tmp / "failed_deploy_steps.sh"
        self.install_fds.write_text(_install_sh_fn("failed_deploy_steps"), encoding="utf-8")
        self.curl_log = self.tmp / "curl.log"
        self.curl_plan = self.tmp / "curl.plan"
        # fake curl shadows the real one on PATH (the script only calls curl
        # for the check-runs API)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake_curl = self.bin / "curl"
        fake_curl.write_text(FAKE_CURL, encoding="utf-8")
        fake_curl.chmod(0o755)
        # fake launchctl ALWAYS shadows the real one: rollback() boots out the
        # actd label, and on the dev Mac that label is the owner's LIVE daemon
        self.launchctl_log = self.tmp / "launchctl.log"
        fake_launchctl = self.bin / "launchctl"
        fake_launchctl.write_text(FAKE_LAUNCHCTL, encoding="utf-8")
        fake_launchctl.chmod(0o755)

        # bare origin whose HEAD is main, seeded from a scratch clone
        self.origin = self.tmp / "origin.git"
        _git(self.tmp, "init", "-q", "--bare", str(self.origin))
        _git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        seed = self.tmp / "seed"
        seed.mkdir()
        _git(seed, "init", "-q")
        _git(seed, "symbolic-ref", "HEAD", "refs/heads/main")
        self._write_tree(seed, "0.48.3")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-q", "-m", "seed v0.48.3")
        _git(seed, "tag", "v0.48.3")
        _git(seed, "remote", "add", "origin", str(self.origin))
        _git(seed, "push", "-q", "origin", "main", "v0.48.3")
        self.dev = seed  # later commits are made here and pushed

        # the live checkout under test
        self.live = self.tmp / "live"
        _git(self.tmp, "clone", "-q", str(self.origin), str(self.live))
        self.script = self.live / "scripts" / "auto-deploy.sh"
        self.base_sha = _git(self.live, "rev-parse", "HEAD")
        # the machine is RUNNING the base version: install.sh finished on it
        # (install_report) and its actd is beating (deployed means running,
        # §56.3 step 2 — without these an up-to-date checkout is "incomplete")
        self.seed_running("0.48.3")
        # the HOME mirror (§56.4): the script's own truth, never TCC-gated
        self.mirror_dir = self.home / "Library" / "Application Support" / "ZelinAIAssistant"
        self.lock = self.mirror_dir / "auto-deploy.lock"

    # -- fixture helpers ---------------------------------------------------- #

    def _write_tree(self, root, version):
        (root / "act" / "lib").mkdir(parents=True, exist_ok=True)
        (root / "act" / "__init__.py").write_text(FIXTURE_INIT, encoding="utf-8")
        (root / "act" / "lib" / "__init__.py").write_text("", encoding="utf-8")
        (root / "RELEASE").write_text(version + "\n", encoding="utf-8")   # the commit that gets tagged v<version>
        for rel in REAL_COPIES:
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(REPO / rel), str(dst))
        (root / "act" / "lib" / "notify.py").write_text(FAKE_NOTIFY, encoding="utf-8")
        (root / "act" / "doctor.py").write_text(FAKE_DOCTOR, encoding="utf-8")
        (root / "act" / "auto_deploy.py").write_text(FAKE_SHIM, encoding="utf-8")
        inst = root / "install.sh"
        inst.write_text(FAKE_INSTALL, encoding="utf-8")
        inst.chmod(0o755)
        (root / "scripts").mkdir(exist_ok=True)
        real = root / "scripts" / "auto-deploy.sh"
        shutil.copyfile(str(SCRIPT), str(real))
        real.chmod(0o755)
        (root / "README.md").write_text("fixture\n", encoding="utf-8")

    def push(self, version, extra=None):
        """Advance origin/main by one commit tagged v<version> (+ optional extra
        edits) — the shape release-on-merge leaves behind (§56.2)."""
        (self.dev / "RELEASE").write_text(version + "\n", encoding="utf-8")
        if extra:
            extra(self.dev)
        _git(self.dev, "add", "-A")
        _git(self.dev, "commit", "-q", "-m", "v%s" % version)
        _git(self.dev, "tag", "v%s" % version)
        _git(self.dev, "push", "-q", "origin", "main", "v%s" % version)
        return _git(self.dev, "rev-parse", "HEAD")

    def run_script(self, *args, doctor_plan=None, install_rc=None, ci=None, env=None,
                   install_steps=None):
        if doctor_plan is not None:
            self.doctor_plan.write_text("\n".join(doctor_plan) + "\n", encoding="utf-8")
        elif self.doctor_plan.exists():
            self.doctor_plan.unlink()
        if install_rc is not None:
            self.install_rc_plan.write_text("\n".join(str(r) for r in install_rc) + "\n", encoding="utf-8")
        elif self.install_rc_plan.exists():
            self.install_rc_plan.unlink()
        if install_steps is not None:
            self.install_steps_plan.write_text("\n".join(install_steps) + "\n", encoding="utf-8")
        elif self.install_steps_plan.exists():
            self.install_steps_plan.unlink()
        if ci is not None:
            self.curl_plan.write_text("\n".join(ci) + "\n", encoding="utf-8")
        elif self.curl_plan.exists():
            self.curl_plan.unlink()
        # TERM_PROGRAM / SSH_TTY are the script's "started from a terminal"
        # tells (detect_trigger); subprocess.run has no tty, so without them
        # every fixture run reads as launchd-spawned = unattended — the shape
        # the incident had. Tests that model the owner's terminal set
        # AUTODEPLOY_TRIGGER=terminal explicitly.
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("AIASSISTANT_", "AUTODEPLOY_", "FAKE_", "GIT_"))
                and k not in ("TERM_PROGRAM", "SSH_TTY")}
        full = {
            **base,
            "PATH": str(self.bin) + os.pathsep + base.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "AIASSISTANT_PYTHON": sys.executable,
            "AUTODEPLOY_LOG_DIR": str(self.logs),
            "AUTODEPLOY_HEARTBEAT_DEADLINE": "1",
            # the stale-heartbeat grace is a real wait; the one test that
            # exercises it sets its own value
            "AUTODEPLOY_HEARTBEAT_GRACE": "0",
            "AUTODEPLOY_INSTALL_TIMEOUT": "30",
            "AUTODEPLOY_DOCTOR_SETTLE": "0",
            # the fixture origin is a local bare repo, not github.com: name the
            # repo the fake curl "serves" so the CI gate runs like on the owner Mac
            "AUTODEPLOY_CI_REPO": "fixture/repo",
            "FAKE_NOTIFY_LOG": str(self.notify_log),
            "FAKE_LAUNCHCTL_LOG": str(self.launchctl_log),
            "FAKE_INSTALL_LOG": str(self.install_log),
            "FAKE_DOCTOR_LOG": str(self.doctor_log),
            "FAKE_DOCTOR_PLAN": str(self.doctor_plan),
            "FAKE_INSTALL_RC_PLAN": str(self.install_rc_plan),
            "FAKE_INSTALL_STEPS_PLAN": str(self.install_steps_plan),
            "FAKE_INSTALL_FDS": str(self.install_fds),
            "FAKE_CURL_LOG": str(self.curl_log),
            "FAKE_CURL_PLAN": str(self.curl_plan),
            **(env or {}),
        }
        proc = subprocess.run(["bash", str(self.script), *args], cwd=str(self.tmp),
                              capture_output=True, text=True, timeout=110, env=full)
        return proc

    def state(self):
        """The repo projection state/deploy_state.json (what dashboard/doctor read)."""
        path = self.live / "state" / "deploy_state.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def mirror(self):
        """The HOME mirror — the script's own truth + private bookkeeping."""
        path = self.mirror_dir / "deploy_state.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def head(self):
        return _git(self.live, "rev-parse", "HEAD")

    def log_text(self):
        p = self.logs / "auto-deploy.log"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def installs(self):
        return self.install_log.read_text(encoding="utf-8").splitlines() if self.install_log.exists() else []

    def notifications(self):
        return self.notify_log.read_text(encoding="utf-8").splitlines() if self.notify_log.exists() else []

    def ci_queries(self):
        return self.curl_log.read_text(encoding="utf-8").splitlines() if self.curl_log.exists() else []

    def queried_shas(self):
        """The commits the check-runs API was asked about, in call order."""
        return [q.split("/commits/", 1)[1].split("/", 1)[0] for q in self.ci_queries()]

    def launchctl_calls(self):
        return self.launchctl_log.read_text(encoding="utf-8").splitlines() if self.launchctl_log.exists() else []

    def doctor_runs(self):
        return self.doctor_log.read_text(encoding="utf-8").splitlines() if self.doctor_log.exists() else []

    def seed_heartbeat(self, version, phase, pid=1, age_s=0):
        """A heartbeat left by the daemon running BEFORE the deploy (fresh unless age_s)."""
        hb = self.live / "state" / "actd.heartbeat"
        hb.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age_s))
        hb.write_text(json.dumps({"ts": ts, "phase": phase, "pid": pid,
                                  "interval": 10, "stale_after_s": 90, "version": version}),
                      encoding="utf-8")

    def seed_install_report(self, version):
        """What install.sh left behind when it last finished (§23)."""
        rep = self.live / "state" / "install_report.json"
        rep.parent.mkdir(parents=True, exist_ok=True)
        rep.write_text(json.dumps({"version": version, "mode": "non-interactive", "steps": []}),
                       encoding="utf-8")

    def seed_running(self, version):
        """The machine RUNS this version: installed on it, actd beating on it."""
        self.seed_install_report(version)
        self.seed_heartbeat(version, "idle", pid=1)

    def clear_heartbeat(self):
        hb = self.live / "state" / "actd.heartbeat"
        if hb.exists():
            hb.unlink()

    def sighting(self, **kw):
        """The first run that sees a mismatch only records it (§56.3 step 2:
        confirm before repairing) — no install.sh, no CI query, no notification."""
        installs, queries, notes = len(self.installs()), len(self.ci_queries()), len(self.notifications())
        proc = self.run_script(**kw)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), installs, "a first sighting never installs")
        self.assertEqual(len(self.ci_queries()), queries, "…nor asks CI")
        self.assertEqual(len(self.notifications()), notes, "…nor notifies")
        st = self.state()
        self.assertEqual(st["status"], "install_incomplete")
        self.assertIn("first sighting", st["detail"])
        self.assertEqual(self.mirror()["incomplete_seen"], self.head())
        return proc

    def seed_store2_db(self, user_version):
        """A store2 ledger left by the daemon running BEFORE the deploy."""
        db = self.live / "state" / "store2.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(db))
        con.execute("PRAGMA user_version=%d" % user_version)
        con.commit()
        con.close()
        return db

    def install_fake_git(self):
        """Shadow git on the script's PATH with the EPERM-window wrapper."""
        real = shutil.which("git")
        fake = self.bin / "git"
        fake.write_text(FAKE_GIT.replace("@REAL_GIT@", real), encoding="utf-8")
        fake.chmod(0o755)
        return self.tmp / "git.break"  # tests hand this path to FAKE_GIT_BREAK_FILE

    # -- 1. nothing to do ---------------------------------------------------- #

    def test_up_to_date_runs_nothing_and_records_it(self):
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [], "install.sh must not run when HEAD == origin/main")
        st = self.state()
        self.assertEqual(st["status"], "up_to_date")
        self.assertEqual(st["head"], self.base_sha)
        self.assertEqual(st["version"], "0.48.3")
        self.assertNotIn("last_deployed", st, "never deployed by this job → no last_deployed")
        self.assertEqual(self.notifications(), [])
        self.assertEqual(self.ci_queries(), [], "nothing to deploy → the CI API is not asked")
        self.assertFalse(self.lock.exists(), "lock released")
        self.assertFalse((self.live / "state" / "auto-deploy.lock").exists(),
                         "the lock lives in $HOME now, never on the (TCC-gated) volume")

    # -- 2. the happy path --------------------------------------------------- #

    def test_tag_created_after_the_first_fetch_is_still_seen(self):
        # release-on-merge tags the commit about a minute after the push; the
        # first interval usually fetches the commit BEFORE the tag exists (CI
        # pending) and a plain `git fetch origin main` never auto-follows a tag
        # created later — the deployed version would read 0.48.3+1. The script
        # fetches --tags (§56.3 step 2), so the second run sees v0.48.4.
        (self.dev / "RELEASE").write_text("0.48.4\n", encoding="utf-8")
        _git(self.dev, "add", "-A")
        _git(self.dev, "commit", "-q", "-m", "v0.48.4 (untagged yet)")
        _git(self.dev, "push", "-q", "origin", "main")
        target = _git(self.dev, "rev-parse", "HEAD")
        proc = self.run_script(ci=["pending"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.state()["status"], "ci_pending")
        self.assertEqual(_git(self.live, "tag", "-l", "v0.48.4"), "", "not tagged yet")
        _git(self.dev, "tag", "v0.48.4")
        _git(self.dev, "push", "-q", "origin", "v0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual((st["status"], st["head"]), ("deployed", target))
        self.assertEqual(st["version"], "0.48.4", "the late tag must be fetched, not 0.48.3+1")
        self.assertIn("version=0.48.4", self.installs()[0])

    def test_local_tag_diverged_from_origin_is_realigned_not_a_fetch_failure(self):
        # The owner Mac carries an old hand-made tag that points somewhere else
        # than origin's tag of the same name. `git fetch --tags` refuses to
        # clobber it and exits 1 — without --force every run would end in
        # fetch_failed and nothing would ever deploy again. origin's tags are
        # the truth (§56.1): the fetch must succeed and realign the tag.
        origin_sha = _git(self.live, "rev-parse", "v0.48.3")
        _git(self.live, "commit", "-q", "--allow-empty", "-m", "local junk")
        _git(self.live, "tag", "-f", "v0.48.3", "HEAD")
        _git(self.live, "reset", "-q", "--hard", "origin/main")
        self.assertNotEqual(_git(self.live, "rev-parse", "v0.48.3"), origin_sha, "fixture: tag diverged")
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual((st["status"], st["head"]), ("deployed", target), self.log_text())
        self.assertEqual(_git(self.live, "rev-parse", "v0.48.3"), origin_sha, "the stale local tag now matches origin")
        self.assertEqual(st["version"], "0.48.4")

    def test_rollback_onto_a_pre_cutover_checkout_reads_the_literal_version_line(self):
        # transition (§56.1): the rollback target may predate the stamper — that
        # tree has no scripts/version_stamp.py and a literal `__version__ = "…"`
        # in act/__init__.py. repo_version() must still name it (deploy_state,
        # notifications), via the legacy sed.
        legacy_sha = self.push("0.48.4", extra=self._make_legacy_tree)
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), legacy_sha)
        self.assertEqual(self.state()["version"], "0.48.4", "legacy checkout: version from the literal line")
        target = self.push("0.48.5", extra=self._restore_stamper_tree)
        proc = self.run_script(doctor_plan=["-", "-"], install_rc=[1, 0])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), legacy_sha, "rolled back onto the pre-cutover commit")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertEqual(st["version"], "0.48.4", "after the reset the literal line is the only source")

    def _make_legacy_tree(self, root):
        (root / "scripts" / "version_stamp.py").unlink()
        (root / "act" / "lib" / "version.py").unlink()
        (root / "act" / "__init__.py").write_text('__version__ = "0.48.4"\n', encoding="utf-8")

    def _restore_stamper_tree(self, root):
        (root / "act" / "__init__.py").write_text(FIXTURE_INIT, encoding="utf-8")
        for rel in REAL_COPIES:
            shutil.copyfile(str(REPO / rel), str(root / rel))

    def test_fast_forward_deploy_installs_once_and_notifies(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(_git(self.live, "symbolic-ref", "--short", "HEAD"), "main", "stays on main")
        inst = self.installs()
        self.assertEqual(len(inst), 1, inst)
        self.assertIn("install --non-interactive", inst[0])
        self.assertIn("head=%s" % target, inst[0], "install.sh runs on the NEW checkout")
        self.assertIn("active=1", inst[0], "install.sh is told it runs inside the agent")
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertEqual(st["version"], "0.48.4")
        self.assertEqual(st["head"], target)
        self.assertEqual(st["prev"], self.base_sha)
        self.assertIn("last_deployed", st)
        self.assertNotIn("failed_sha", st)
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("v0.48.4", notes[0])
        # doctor ran twice, both times with the NEW code (baseline is pre-install, same doctor)
        doc = self.doctor_runs()
        self.assertEqual(len(doc), 2, doc)
        self.assertTrue(all("version=0.48.4" in ln and "--fast --json" in ln for ln in doc), doc)
        # the CI gate asked about THE sha being deployed, once, at the configured repo
        queries = self.ci_queries()
        self.assertEqual(len(queries), 1, queries)
        self.assertIn("/repos/fixture/repo/commits/%s/check-runs" % target, queries[0])
        self.assertIn("CI green on %s" % target[:7], self.log_text())

    def test_second_run_after_deploy_is_up_to_date_and_keeps_last_deployed(self):
        self.push("0.48.4")
        self.run_script(doctor_plan=["-", "-"])
        deployed_at = self.state()["last_deployed"]
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual(st["status"], "up_to_date")
        self.assertEqual(st["last_deployed"], deployed_at, "carried over, not rewritten")
        self.assertEqual(st["prev"], self.base_sha)
        self.assertEqual(len(self.installs()), 1)

    def test_script_replaced_by_the_merge_still_completes(self):
        # the ff-merge rewrites scripts/auto-deploy.sh on disk mid-run; bash
        # must finish on the OLD logic: the whole file was parsed before main
        # ran (main "$@" wrapper), every main() path exits without returning
        # to the reader, and git writes by rename so the running fd keeps the
        # pre-merge inode. The replacement here is a booby trap that would
        # exit 99 if ANY line of it executed — which also pins the flip side
        # (Codex review P1 on #130): guards added in version N protect deploys
        # FROM N onward, never the deploy OF N itself; no snapshot/exec trick
        # changes that, because any copy taken before the merge IS the old
        # script.
        def swap_script(dev):
            p = dev / "scripts" / "auto-deploy.sh"
            p.write_text("#!/bin/bash\necho BOOBYTRAP-NEW-SCRIPT-EXECUTED >&2\nexit 99\n",
                         encoding="utf-8")
        target = self.push("0.48.4", extra=swap_script)
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("BOOBYTRAP", proc.stderr, "no line of the merged-in script may run")
        self.assertNotIn("BOOBYTRAP", self.log_text())
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")
        merged = (self.live / "scripts" / "auto-deploy.sh").read_text(encoding="utf-8")
        self.assertIn("BOOBYTRAP", merged, "the merge really did replace the script on disk")

    # -- 2b. the CI gate (B1) ------------------------------------------------- #
    # ruleset protect-main 是 non-strict：PR head 绿了就能合，合出来的 merge
    # commit 的树没人测过；main 上的 `ci` 跑 ~8 min，这个 job 在 +10 min 开火。
    # 所以部署前必须问 GitHub：**这个 sha** 的 ci 结束了且绿了吗。

    def test_ci_still_running_defers_without_touching_the_checkout(self):
        target = self.push("0.48.4")
        for plan, why in (("pending", "in_progress"), ("missing", "no ci check-run yet"),
                          ("UNREACHABLE", "unreachable"), ("GARBAGE", "no JSON")):
            proc = self.run_script(ci=[plan])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(self.head(), self.base_sha, plan)
            self.assertEqual(self.installs(), [], plan)
            st = self.state()
            self.assertEqual(st["status"], "ci_pending", plan)
            self.assertIn(target[:7], st["detail"], plan)
            self.assertIn(why, st["detail"], plan)
            self.assertEqual(st["head"], self.base_sha)
            self.assertEqual(st["version"], "0.48.3")
            self.assertNotIn("failed_sha", st, "pending is not a verdict — never poisons")
        self.assertEqual(self.notifications(), [], "waiting for CI is not news")
        self.assertEqual(len(self.ci_queries()), 4, "asked once per run")
        # CI finishes green → the very next run deploys
        proc = self.run_script(doctor_plan=["-", "-"], ci=["success"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    def test_red_ci_on_main_poisons_the_sha_notifies_once_and_never_merges(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], ci=["failure"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "a red sha is never fast-forwarded to")
        self.assertEqual(self.installs(), [])
        self.assertEqual(self.doctor_runs(), [], "no doctor either — nothing changed on disk")
        st = self.state()
        self.assertEqual(st["status"], "ci_failed")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("ci failure", st["detail"])
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("CI red", notes[0])
        # next interval: poisoned → quiet, and the API is not even asked again
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        self.assertEqual(len(self.ci_queries()), 1)
        self.assertEqual(len(self.notifications()), 1)
        self.assertIn("already failed", self.log_text())
        # a new commit on main is asked about normally
        target2 = self.push("0.48.5")
        proc = self.run_script(doctor_plan=["-", "-"], ci=["success"])
        self.assertEqual(self.head(), target2)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertNotIn("failed_sha", self.state())

    def test_force_skips_the_ci_gate(self):
        # --force = the owner asked for THIS sha: no API call, straight to the deploy
        target = self.push("0.48.4")
        self.run_script(ci=["failure"])
        self.assertEqual(self.state()["status"], "ci_failed")
        proc = self.run_script("--force", doctor_plan=["-", "-"], ci=["failure"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertEqual(len(self.ci_queries()), 1, "the forced run did not ask")
        self.assertIn("CI gate skipped", self.log_text())

    def test_rerun_of_a_red_check_the_newest_run_wins(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], ci=["rerun"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    def test_only_the_configured_checks_gate(self):
        # the default fake body carries a red Lint run next to the green ci run
        # (the happy path deploys on it: Lint is not in CI_CHECKS); naming Lint
        # as required turns that same body into a refusal
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"],
                               env={"AUTODEPLOY_CI_CHECKS": "ci, Lint (shellcheck + ruff)"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "ci_failed")
        self.assertIn("Lint (shellcheck + ruff) failure", st["detail"])
        self.assertEqual(st["failed_sha"], target)

    def test_non_github_origin_without_ci_repo_refuses_and_notifies_once(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], env={"AUTODEPLOY_CI_REPO": ""})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "never deploys what it cannot verify")
        self.assertEqual(self.installs(), [])
        st = self.state()
        self.assertEqual(st["status"], "failed")
        self.assertIn("AUTODEPLOY_CI_REPO", st["detail"])
        self.assertEqual(self.ci_queries(), [])
        self.assertEqual(len(self.notifications()), 1)
        self.run_script(doctor_plan=["-", "-"], env={"AUTODEPLOY_CI_REPO": ""})
        self.assertEqual(len(self.notifications()), 1, "same pending sha: quiet")
        # naming the repo unblocks it
        self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(self.head(), target)

    # -- 2b'. merge burst: the newest GREEN ancestor, not the ever-moving head - #
    # 2026-09-03T00:38Z→01:18Z live：每 10–20 min 一次合并、CI 排队 20+ min，五轮连续
    # 「CI not green yet on <越来越新的 sha>」，v1.0.4–1.0.6 绿着躺在后面几小时没上机。
    # head 不绿 → 沿 first-parent 往回走到 PREV 之前最新的绿 commit 部署它（仍是 ff）；
    # 红的每个 sha 中毒一次（failed_shas 账本），--force 照旧只认 head。

    def test_newest_red_and_older_green_deploys_the_older_and_keeps_the_head_poisoned(self):
        older = self.push("0.48.4")
        head = self.push("0.48.5")
        proc = self.run_script(doctor_plan=["-", "-"], ci=["failure", "success"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.queried_shas(), [head, older], "head first, then its first parent")
        self.assertEqual(self.head(), older, "the newest green commit is deployed, not the red head")
        st = self.state()
        self.assertEqual((st["status"], st["head"], st["prev"], st["version"]),
                         ("deployed", older, self.base_sha, "0.48.4"))
        self.assertEqual(st["failed_sha"], head, "the red head stays poisoned")
        self.assertEqual(self.mirror()["failed_shas"], head)
        self.assertEqual(st["behind_main"], head)
        self.assertIn("head CI failed", st["behind_main_why"])
        self.assertIn("newest green ancestor of origin/main %s" % head[:7], st["detail"])
        self.assertIn("head=%s" % older, self.installs()[0])
        notes = self.notifications()
        self.assertEqual(len(notes), 2, notes)
        self.assertIn("CI red", notes[0])
        self.assertIn("v0.48.4", notes[1])
        self.assertIn("deploying its newest green ancestor %s" % older[:7], self.log_text())
        # next interval: the head is remembered as red — not asked again, not
        # announced again, nothing newer than the deployed sha exists
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.queried_shas()), 2, "the poisoned head is never re-asked")
        self.assertEqual(len(self.installs()), 1)
        self.assertEqual(len(self.notifications()), 2)
        self.assertEqual(self.state()["status"], "deployed", "the verdict on file stands")
        self.assertIn("already failed", self.log_text())

    def test_all_red_deploys_nothing_and_poisons_every_red_sha_once(self):
        older = self.push("0.48.4")
        head = self.push("0.48.5")
        proc = self.run_script(doctor_plan=["-", "-"], ci=["failure", "failure"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "nothing green → nothing deployed")
        self.assertEqual(self.installs(), [])
        self.assertEqual(self.queried_shas(), [head, older])
        st = self.state()
        self.assertEqual(st["status"], "ci_failed")
        self.assertIn("no green commit between the deployed", st["detail"])
        self.assertEqual(set(self.mirror()["failed_shas"].split()), {head, older})
        self.assertEqual(len(self.notifications()), 2, "one 'main CI red' per red sha")
        # next interval: both poisoned → zero API calls, zero notifications
        self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(len(self.queried_shas()), 2)
        self.assertEqual(len(self.notifications()), 2)
        self.assertEqual(self.installs(), [])
        self.assertEqual(self.state()["status"], "ci_failed")
        # a green commit on top clears the whole ledger
        fixed = self.push("0.48.6")
        self.run_script(doctor_plan=["-", "-"], ci=["success"])
        self.assertEqual(self.head(), fixed)
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertNotIn("failed_sha", st)
        self.assertNotIn("failed_shas", self.mirror())
        self.assertNotIn("behind_main", st)

    def test_green_head_deploys_the_head_without_walking(self):
        self.push("0.48.4")
        head = self.push("0.48.5")
        proc = self.run_script(doctor_plan=["-", "-"], ci=["success"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), head)
        self.assertEqual(self.queried_shas(), [head], "a green head is the answer; no ancestor is asked")
        st = self.state()
        self.assertEqual((st["status"], st["version"]), ("deployed", "0.48.5"))
        self.assertNotIn("behind_main", st)

    def test_pending_head_deploys_the_green_ancestor_then_waits_then_catches_up(self):
        older = self.push("0.48.4")
        head = self.push("0.48.5")
        # `missing` = no `ci` check-run on the head at all: pending, NEVER green
        # (a path-filtered run that does not exist is not a passed run)
        proc = self.run_script(doctor_plan=["-", "-"], ci=["missing", "success"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), older)
        st = self.state()
        self.assertEqual((st["status"], st["version"]), ("deployed", "0.48.4"))
        self.assertEqual(st["behind_main"], head)
        self.assertIn("no ci check-run yet", st["behind_main_why"])
        self.assertNotIn("failed_sha", st, "pending is not a verdict — never poisons")
        # next interval, head still pending: the deployed sha is the walk's
        # floor — nothing to ask about, nothing to install; an honest ci_pending
        proc = self.run_script(doctor_plan=["-", "-"], ci=["pending"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.queried_shas()[2:], [head], "only the head is asked; the deployed ancestor is the floor")
        self.assertEqual(len(self.installs()), 1)
        st = self.state()
        self.assertEqual((st["status"], st["head"]), ("ci_pending", older))
        self.assertIn("no other commit between the deployed %s" % older[:7], st["detail"])
        self.assertEqual(st["behind_main"], head, "carried: the last deploy did stop short of it")
        # the head turns green → deployed, and the machine is no longer behind
        proc = self.run_script(doctor_plan=["-", "-"], ci=["success"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), head)
        st = self.state()
        self.assertEqual((st["status"], st["version"], st["prev"]), ("deployed", "0.48.5", older))
        self.assertNotIn("behind_main", st)
        self.assertNotIn("behind_main_why", st)

    def test_rolled_back_ancestor_is_not_redeployed_while_the_head_stays_red(self):
        # the one-slot failed_sha would loop here: run N poisons the head, deploys
        # the ancestor, rolls it back (slot := ancestor); run N+1 re-asks the head
        # (slot says ancestor) → red → notify → the ancestor "is not poisoned" →
        # redeploy → rollback → … every interval. The ledger holds both.
        older = self.push("0.48.4")
        head = self.push("0.48.5")
        proc = self.run_script(doctor_plan=["-", "-"], ci=["failure", "success"], install_rc=[1, 0])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "rolled back to PREV")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], older)
        self.assertEqual(set(self.mirror()["failed_shas"].split()), {head, older})
        self.assertEqual(len(self.installs()), 2, "install at the ancestor, rollback install at PREV")
        self.assertEqual(len(self.notifications()), 2, "CI red on the head + the rollback")
        for _ in range(2):
            proc = self.run_script(doctor_plan=["-", "-"])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(self.head(), self.base_sha)
            self.assertEqual(len(self.queried_shas()), 2, "neither sha is asked again")
            self.assertEqual(len(self.installs()), 2, "no deploy→rollback storm")
            self.assertEqual(len(self.notifications()), 2)
            self.assertEqual(self.state()["status"], "rolled_back", "the verdict stands")
        # --force still means THIS head, gate and walk skipped
        proc = self.run_script("--force", doctor_plan=["-", "-"])
        self.assertEqual(self.head(), head)
        self.assertEqual(len(self.queried_shas()), 2, "the forced run did not ask")
        self.assertNotIn("failed_shas", self.mirror())

    def test_walk_is_bounded(self):
        oldest = self.push("0.48.4")
        mid1 = self.push("0.48.5")
        mid2 = self.push("0.48.6")
        head = self.push("0.48.7")
        # AUTODEPLOY_CI_WALK=2: the head plus the two commits behind it are
        # examined; the (green) oldest lies beyond the bound and is not asked
        proc = self.run_script(doctor_plan=["-", "-"], ci=["pending", "pending", "pending", "success"],
                               env={"AUTODEPLOY_CI_WALK": "2"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.queried_shas(), [head, mid2, mid1])
        self.assertEqual(self.head(), self.base_sha)
        self.assertEqual(self.state()["status"], "ci_pending")
        self.assertNotIn(oldest, self.queried_shas(), "beyond the bound: not asked, not deployed")
        self.assertIn("2 examined", self.state()["detail"])

    # -- 2c. self-check of the deploy agent (B3) ----------------------------- #

    def test_merge_that_breaks_the_deploy_script_rolls_back_before_installing(self):
        def break_script(dev):
            p = dev / "scripts" / "auto-deploy.sh"
            p.write_text(p.read_text(encoding="utf-8") + "\nif [ 1 -eq 1 ]; then\n", encoding="utf-8")
        target = self.push("0.48.4", extra=break_script)
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "rolled back to PREV")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("self-check", st["detail"])
        self.assertIn("bash -n", st["detail"])
        inst = self.installs()
        self.assertEqual(len(inst), 1, inst)
        self.assertIn("head=%s" % self.base_sha, inst[0], "the only install is the rollback at PREV")
        self.assertEqual(self.doctor_runs(), [], "self-check comes before the doctor baseline")
        self.assertEqual(len(self.notifications()), 1)

    def test_merge_that_breaks_the_launchd_shim_rolls_back_before_installing(self):
        def break_shim(dev):
            (dev / "act" / "auto_deploy.py").write_text("def (:\n", encoding="utf-8")
        target = self.push("0.48.4", extra=break_shim)
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("act.auto_deploy does not import", st["detail"])
        self.assertEqual(len(self.installs()), 1)

    # -- 2d. readiness: the NEW actd must complete a pass (B2) ---------------- #
    # 30 s 静置 + 一次 launchctl 采样是抛硬币：import 即死的 KeepAlive actd 每个
    # ~10 s 节流周期亮 ~0.5 s 的 pid，旧 daemon 的 heartbeat/dashboard 文件又
    # 90 s 内都算新鲜。心跳文件带 version + pid + phase（§47.4）——等它。

    def test_new_actd_that_never_completes_a_pass_rolls_back(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_HEARTBEAT": "starting"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("actd:no_heartbeat_from_new_version", st["detail"])
        self.assertIn("0.48.4", st["detail"])
        self.assertEqual(len(self.installs()), 2, "install at NEW, rollback install at PREV")
        self.assertEqual(len(self.doctor_runs()), 1, "no post-install doctor: not ready is already the verdict")

    def test_the_old_daemons_heartbeat_does_not_count_even_with_the_same_version(self):
        # no version bump (§56.1 says every PR bumps, but a merge that does not
        # must still not be waved through by the OLD daemon's idle beat) and
        # the new actd dies on import (writes nothing)
        target = self.push("0.48.4")
        self.seed_heartbeat("0.48.4", "idle", pid=1)
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_HEARTBEAT": "none"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("no_heartbeat_from_new_version", st["detail"])

    def test_no_heartbeat_file_at_all_before_and_after_rolls_back(self):
        self.push("0.48.4")
        self.clear_heartbeat()
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_HEARTBEAT": "none"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.state()["status"], "rolled_back")
        self.assertIn("no_heartbeat_from_new_version", self.state()["detail"])

    def test_new_actd_whose_pass_throws_rolls_back_when_the_old_one_was_fine(self):
        self.push("0.48.4")
        self.seed_heartbeat("0.48.3", "idle")
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_HEARTBEAT": "failed"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        self.assertEqual(self.state()["status"], "rolled_back")

    def test_pre_existing_failing_pass_is_not_blamed_on_the_new_version(self):
        # the old daemon was already throwing every pass: the new one doing the
        # same is pre-existing (else the machine could never take the fix)
        target = self.push("0.48.4")
        self.seed_heartbeat("0.48.3", "failed")
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_HEARTBEAT": "failed"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    def test_failed_stamp_step_does_not_roll_back_a_good_deploy_and_the_next_runs_re_stamp(self):
        # 2026-09-02T10:14Z: the stamp step failed (Homebrew python TCC-denied), a
        # hand-written 0.48.21 stamp survived on the v0.48.22 checkout, the NEW actd
        # (new pid, idle pass) reported 0.48.21 → rolled back as no_heartbeat_from_new_version.
        stale = self.live / "act" / "_version.py"
        stale.write_text('__version__ = "0.48.3"\n', encoding="utf-8")
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_STAMP_FAIL": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual(st["status"], "deployed", self.log_text())
        self.assertEqual(self.head(), target, "no rollback")
        self.assertEqual(st["version"], "0.48.4", "the checkout's version")
        self.assertEqual(st["running_version"], "0.48.3", "…while the daemons read the stale stamp")
        self.assertEqual(st["install_report_version"], "0.48.3")
        self.assertIn("act/_version.py not stamped", st["detail"])
        self.assertIn("version identity unverified", st["detail"])
        log = self.log_text()
        self.assertIn("WARN install.sh could not stamp act/_version.py (stamp failed — /opt/homebrew/bin/python3: [Errno 1] Operation not permitted)", log)
        self.assertNotIn("ROLLBACK", log)
        self.assertEqual(len(self.installs()), 1)
        # next run: the stale identity is a running_mismatch — first sighting only records…
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual(st["status"], "install_incomplete")
        self.assertIn("heartbeat_version_mismatch", st["reason"])
        self.assertEqual(len(self.installs()), 1, "first sighting: no re-run yet")
        # …the second sighting re-runs install.sh, whose stamp step now succeeds → identity restored
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual(st["status"], "deployed", self.log_text())
        self.assertEqual(st["running_version"], "0.48.4")
        self.assertEqual(st["install_report_version"], "0.48.4")
        self.assertEqual(len(self.installs()), 2)
        self.assertIn('__version__ = "0.48.4"', stale.read_text(encoding="utf-8"), "re-stamped")

    def test_a_report_left_over_from_the_previous_install_never_keys_readiness(self):
        # install.sh exited 0 but (somehow) did not rewrite the report: the previous
        # run's `version=ok:0.48.3` must not make the new 0.48.4 daemon look wrong.
        # The guard is "the report changed since before install.sh ran", not the
        # wall clock (Codex P2 on #146): a generated_at in the FUTURE is still the
        # previous run's report when it is byte-for-byte the one sampled before.
        rep = self.live / "state" / "install_report.json"
        rep.write_text(json.dumps({"version": "0.48.3", "generated_at": "2999-01-01T00:00:00Z",
                                   "mode": "non-interactive",
                                   "steps": [{"name": "version", "status": "ok", "detail": "0.48.3"}]}),
                       encoding="utf-8")
        self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_NO_REPORT": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.state()["status"], "deployed", self.log_text())
        self.assertNotIn("ROLLBACK", self.log_text())
        self.assertNotIn("readiness keyed on the stamp", self.log_text(), "unchanged report ignored → checkout prediction, as before")

    def test_a_fresh_report_counts_even_when_the_clock_stepped_backwards(self):
        # the fake stamps its report with a generated_at BEFORE the one sampled
        # pre-install (a clock stepped back mid-install): still this run's report
        rep = self.live / "state" / "install_report.json"
        rep.write_text(json.dumps({"version": "0.48.3", "generated_at": "2999-01-01T00:00:00Z",
                                   "mode": "non-interactive", "steps": []}), encoding="utf-8")
        stale = self.live / "act" / "_version.py"
        stale.write_text('__version__ = "0.48.3"\n', encoding="utf-8")
        self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_STAMP_FAIL": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.state()["status"], "deployed", self.log_text())
        self.assertIn("WARN install.sh could not stamp act/_version.py", self.log_text(),
                      "the warn step of THIS run's report was read — no false rollback")
        self.assertNotIn("ROLLBACK", self.log_text())

    def test_readiness_is_keyed_on_the_stamp_install_sh_wrote_not_the_prediction(self):
        # the report says ok:<v>; the heartbeat must carry THAT v (here identical
        # to the checkout's — the fake stamps with the real stamper), and a daemon
        # still beating the OLD version under a new pid is not ready
        self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertNotIn("WARN install.sh could not stamp", self.log_text())
        self.assertNotIn("readiness keyed on the stamp", self.log_text(), "stamp == checkout: nothing to say")

    def test_ready_heartbeat_is_logged_and_the_doctor_runs_after_it(self):
        self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("completed a pass (heartbeat: 0.48.4 ", self.log_text())
        self.assertEqual(len(self.doctor_runs()), 2)

    # -- 3. rollback --------------------------------------------------------- #

    def test_new_doctor_failure_rolls_back_and_poisons_that_sha(self):
        target = self.push("0.48.4")
        # green baseline, red after install — and red on every settle retry
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "back on the previous commit")
        self.assertEqual(_git(self.live, "symbolic-ref", "--short", "HEAD"), "main")
        inst = self.installs()
        self.assertEqual(len(inst), 2, inst)
        self.assertIn("head=%s" % target, inst[0])
        self.assertIn("head=%s" % self.base_sha, inst[1], "re-installed at PREV")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertEqual(st["head"], self.base_sha)
        self.assertEqual(st["version"], "0.48.3")
        self.assertIn("dashboard", st["detail"])
        self.assertIn("last_run", st, "§56.4: last_run describes THIS run, rollback included")
        rolled_back_at = st["last_run"]
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("rolled back to %s" % self.base_sha[:7], notes[0])
        # next interval: same origin/main sha → no retry storm
        time.sleep(1.1)  # last_run has 1 s resolution
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), 2, "no third install for the poisoned sha")
        self.assertEqual(self.head(), self.base_sha)
        self.assertIn("already failed", self.log_text())
        self.assertEqual(len(self.notifications()), 1, "silent while waiting")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back", "verdict carried: still poisoned")
        self.assertNotEqual(st["last_run"], rolled_back_at, "but the skip round still stamps last_run")
        # a new commit on main is tried normally
        target2 = self.push("0.48.5")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(self.head(), target2)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertNotIn("failed_sha", self.state())

    def test_force_retries_the_poisoned_sha(self):
        target = self.push("0.48.4")
        self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"])
        self.assertEqual(self.state()["status"], "rolled_back")
        proc = self.run_script("--force", doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    def test_install_failure_rolls_back(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-"], install_rc=[2, 0])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("install.sh exited 2", st["detail"])
        self.assertEqual(len(self.installs()), 2)

    # -- §56.5 `ui` step: skipped is success, fail is a rollback ------------- #
    # The verdict is install.sh's exit code = count of failed_deploy_steps()
    # lines; the fake install.sh here runs the REAL function over a planned
    # report-step set, so the rule is pinned end to end.

    def test_ui_step_skipped_is_a_successful_deploy(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], install_steps=[
            "config=ok:kept|app=skipped:non-interactive|"
            "ui=skipped:web skipped (no node/npm); shell skipped (no swift toolchain); 0s total|"
            "launchd=ok:7 agents loaded|cron=ok"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")
        inst = self.installs()
        self.assertEqual(len([ln for ln in inst if ln.startswith("install ")]), 1, inst)
        self.assertIn("steps rc=0", "\n".join(inst))

    def test_ui_step_fail_rolls_back(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-"], install_steps=[
            # the deploy: web build broke on the new code → ui=fail
            "config=ok:kept|app=skipped:non-interactive|"
            "ui=fail:web fail (npm run build exit 2); shell ok (9s); 41s total|launchd=ok:7 agents loaded",
            # the rollback re-install on PREV: toolchain fine, UI ok again
            "config=ok:kept|ui=ok:web ok (npm ci 0s, build 12s); shell ok (8s); 20s total|launchd=ok"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "ui=fail must roll the deploy back")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("install.sh exited 1", st["detail"])
        inst = "\n".join(self.installs())
        self.assertIn("steps rc=1", inst)
        self.assertIn("steps rc=0", inst)

    def test_pre_existing_red_is_not_blamed_on_the_new_version(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["cron", "cron"])  # same FAIL before and after
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertIn("pre-existing", st["detail"])
        self.assertIn("cron", st["detail"])
        self.assertEqual(len(self.installs()), 1)

    def test_install_reporting_skipped_tcc_cron_still_deploys(self):
        # §56.5（2026-09-02 v0.48.12 实战）：crontab 被 TCC 拒写记 cron=skipped_tcc，
        # install.sh --non-interactive 退出 0（映射判例 tests/test_install_cron_tcc.py）
        # ——部署层的判决必须是 SUCCESS 而非回滚：回滚重装只会撞同一堵 TCC 墙，
        # 还会把 sha 毒成停摆（当晚实况）。
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], env={"FAKE_INSTALL_CRON_TCC": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertNotIn("failed_sha", st)
        self.assertEqual(len(self.installs()), 1, "no rollback install")
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("v0.48.4", notes[0], "the one notification is the deploy, not a rollback")

    def test_unparseable_doctor_on_the_new_code_rolls_back_before_installing(self):
        # H1：baseline 与复查都用新代码——doctor 在两次都 unparseable 时「新增 FAIL」
        # 为空，旧逻辑会判 deployed 并把 doctor:unparseable 写成 pre-existing。
        # 恰恰是 doctor 自己 import 崩的那种提交，必须回滚，且新版本压根不该被装。
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["GARBAGE", "GARBAGE"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "rolled back to PREV")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("unparseable", st["detail"])
        inst = self.installs()
        self.assertEqual(len(inst), 1, inst)
        self.assertIn("head=%s" % self.base_sha, inst[0], "the only install is the rollback at PREV")
        self.assertNotIn("pre-existing", st["detail"])
        self.assertEqual(len(self.notifications()), 1)

    def test_unparseable_doctor_after_install_rolls_back(self):
        # 持续 unparseable（每次 settle 重试都崩）才回滚——瞬态崩见 settle 测试
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "GARBAGE", "GARBAGE", "GARBAGE"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("unparseable", st["detail"])
        self.assertEqual(len(self.installs()), 2, "install at NEW, then the rollback install at PREV")

    def test_rollback_refuses_to_destroy_edits_made_during_the_deploy(self):
        # P1（review）：step 4 的脏树检查到 reset --hard 之间隔着 install + settle +
        # doctor；owner 在这几分钟里改了 tracked 文件，reset 会无声毁掉它（§0.2）。
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"],
                               env={"FAKE_INSTALL_EDIT": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target, "not reset: the owner's edit would be lost")
        self.assertIn("owner edit mid-deploy", (self.live / "README.md").read_text(encoding="utf-8"))
        st = self.state()
        self.assertEqual(st["status"], "rollback_failed")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("refused", st["detail"])
        self.assertIn("README.md", st["detail"])
        self.assertEqual(len(self.installs()), 1, "no rollback install either")
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("REFUSED", notes[0])
        self.assertIn("rollback REFUSED", self.log_text())

    def test_rollback_ignores_install_sh_own_mode_flips(self):
        # install.sh 的 chmod +x 是它自己的脚印，不是 owner 的工作：不得阻止回滚
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"],
                               env={"FAKE_INSTALL_CHMOD": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "rolled back despite the mode-only change")
        self.assertEqual(self.state()["status"], "rolled_back")
        self.assertEqual(self.state()["failed_sha"], target)
        self.assertEqual(len(self.installs()), 2)

    # -- 3b. 首次实战修的四类 bug（2026-09-01 v0.48.8，§56.3 step 10 修订） ---- #
    # 实录：install.sh 重启全部 daemon 后 12 s 就取 doctor 判决，撞上 store2 首跑
    # 迁移 + 外置卷瞬态 EPERM 窗口 → 6 个假「new FAIL」→ 假阳性回滚；回滚重验里
    # symbolic-ref / rev-parse 全空 → 误报 "HEAD is on 'detached'" + 空 sha（歪打
    # 正着拦下了会 strand SQLite 账本的回退——本组测试把这份运气变成结构）；
    # write_state / notify 的失败行不带原因（PermissionError 只在 launchd stderr）。

    def test_transient_doctor_fail_after_install_settles_and_deploys(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "store2,config.yaml", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target, "a FAIL that clears on retry is settling, not a verdict")
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertNotIn("failed_sha", st)
        self.assertEqual(len(self.installs()), 1, "no rollback install")
        self.assertEqual(len(self.doctor_runs()), 3, "baseline + first sample + the clean retry")
        self.assertIn("daemons may still be settling", self.log_text())
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("v0.48.4", notes[0])

    def test_transient_unparseable_doctor_after_install_settles(self):
        # 装后第一采样 doctor 整个崩（EPERM 窗口里连 import 都可能失败）——基线
        # 已证明新代码的 doctor 能跑，装后的瞬态崩走同一条 settle 重试路
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "GARBAGE", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertEqual(len(self.installs()), 1)

    def test_persistent_new_fail_verdict_names_only_the_final_run(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "store2", "dashboard", "dashboard"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "still red on the final attempt → rollback")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("dashboard", st["detail"])
        self.assertNotIn("store2", st["detail"], "a transient first-attempt name is not the verdict")
        self.assertEqual(len(self.doctor_runs()), 4, "baseline + 3 attempts")

    # -- 3c. owner-action rows never trigger a rollback（2026-09-03 v1.0.7 事故，§56.3 step 10） -- #
    # 实录：install.sh 刚建好 claude 稳定副本并打印「一次性授权指引」，30 s 后 doctor 在
    # launchd 会话里、cwd 在外置卷上跑 `<副本> --version`——副本还没拿到完全磁盘访问，
    # `stable claude` 从基线的 WARN（缺失）变成 FAIL（跑不了）→ 三次采样都在 → 回滚到
    # v1.0.3；几分钟后 owner 在终端里跑同一探针是绿的（终端借出自己的授权）。授权只有
    # owner 能点，代码回滚治不了它，因此 §25 `row_class=owner_action` 的 FAIL 永不进判决。

    def test_new_owner_action_fail_never_rolls_back_and_is_reported_as_needs_owner(self):
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "stable claude@owner_action"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target, "a grant the owner has yet to click is not a broken deploy")
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertNotIn("failed_sha", st)
        self.assertIn("needs owner: stable claude", st["detail"])
        self.assertNotIn("pre-existing", st["detail"])
        self.assertEqual(len(self.installs()), 1, "no rollback install")
        self.assertEqual(len(self.doctor_runs()), 2, "baseline + one sample: nothing to settle")
        log = self.log_text()
        self.assertIn("needs owner (not a rollback trigger): stable claude", log)
        self.assertNotIn("daemons may still be settling", log)
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("v0.48.4", notes[0])
        self.assertIn("needs owner: stable claude", notes[0], "the owner is told what to click")

    def test_owner_action_fail_does_not_shield_a_real_new_fail(self):
        target = self.push("0.48.4")
        both = "stable claude@owner_action,dashboard"
        proc = self.run_script(doctor_plan=["-", both, both, both])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "the code-class new FAIL still rolls back")
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("dashboard", st["detail"])
        self.assertNotIn("stable claude", st["detail"], "the owner-action row is not the verdict")

    def test_pre_existing_owner_action_fail_is_needs_owner_not_pre_existing(self):
        # the live shape before the fix landed: `launchd claude` red (claude_blind)
        # on every baseline since 2026-09-02 — it was already excluded as
        # pre-existing; now it is reported for what it is
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["launchd claude@owner_action", "launchd claude@owner_action"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertIn("needs owner: launchd claude", st["detail"])
        self.assertNotIn("pre-existing", st["detail"])
        log = self.log_text()
        self.assertIn("baseline (pre-install) FAIL needs owner", log)
        self.assertNotIn("doctor baseline (pre-install) FAIL: launchd claude", log)

    def test_owner_action_row_that_turns_into_a_code_fail_is_a_new_fail(self):
        # same row name, different failure: `launchd claude` was the grant
        # (owner_action) before the install and an unclassified FAIL after —
        # the class changed, so the name IS new to the verdict
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["launchd claude@owner_action",
                                            "launchd claude", "launchd claude", "launchd claude"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        st = self.state()
        self.assertEqual(st["status"], "rolled_back")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("launchd claude", st["detail"])

    def test_git_failure_during_rollback_is_reported_as_git_not_detached(self):
        target = self.push("0.48.4")
        trigger = self.install_fake_git()
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"],
                               env={"FAKE_GIT_BREAK_FILE": str(trigger),
                                    "FAKE_INSTALL_TOUCH": str(trigger)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target, "never resets through a git it cannot trust")
        st = self.state()
        self.assertEqual(st["status"], "rollback_failed")
        self.assertIn("git cannot read the checkout", st["detail"])
        self.assertNotIn("detached", st["detail"])
        self.assertNotIn("detached", self.log_text())
        self.assertIn("checkout left at unknown", self.log_text(),
                      "the refusal line never interpolates empty git output")
        self.assertEqual(len(self.installs()), 1, "no rollback install")
        self.assertEqual(len(self.notifications()), 1)

    def test_git_failure_at_entry_is_an_environment_error_not_refused_branch(self):
        self.push("0.48.4")
        trigger = self.install_fake_git()
        trigger.write_text("", encoding="utf-8")  # git is dark from the very first call
        proc = self.run_script(env={"FAKE_GIT_BREAK_FILE": str(trigger)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [])
        st = self.state()
        self.assertEqual(st["status"], "failed")
        self.assertIn("cannot read HEAD", st["detail"])
        self.assertNotIn("detached", st["detail"])
        self.assertEqual(self.notifications(), [], "an environment hiccup retries silently")

    def test_store2_truth_appearing_during_the_deploy_refuses_code_rollback(self):
        # D2 安全网：本次部署里 actd 把卡片真源迁到 SQLite，回退代码会让账本落在
        # 没有 store2 运行时的版本上——拒绝，并指向 TROUBLESHOOTING 的手动流程
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"],
                               env={"FAKE_INSTALL_STORE2": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target, "code stays on the new version")
        st = self.state()
        self.assertEqual(st["status"], "rollback_failed")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("TROUBLESHOOTING", st["detail"])
        self.assertEqual(len(self.installs()), 1, "no rollback install")
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("store2", notes[0])
        self.assertIn("TROUBLESHOOTING", notes[0])

    def test_store2_truth_present_before_the_deploy_rolls_back_normally(self):
        self.push("0.48.4")
        marker = self.live / "state" / "store2_truth.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"activated_at": "2026-08-01T00:00:00Z"}\n', encoding="utf-8")
        self.seed_store2_db(1)  # ledger active and its schema does NOT move this deploy
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha,
                         "a pre-existing marker + unchanged schema never blocks a rollback")
        self.assertEqual(self.state()["status"], "rolled_back")
        self.assertTrue(marker.exists(), "reset --hard does not touch untracked state/")

    def test_store2_schema_bump_during_the_deploy_refuses_code_rollback(self):
        # #135 review：真源早已是 sqlite（标记先在），新版本首开 DB 把 PRAGMA
        # user_version 1→2——标记闸门看不见它，但回退代码后旧 store.py 会对每次
        # registry 调用抛 StoreError（db user_version=2, store2 supports 1）：
        # 账本数据没丢，actd 却全灭。schema 前进 = 同样拒绝代码回滚。
        target = self.push("0.48.4")
        marker = self.live / "state" / "store2_truth.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"activated_at": "2026-08-01T00:00:00Z"}\n', encoding="utf-8")
        self.seed_store2_db(1)
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"],
                               env={"FAKE_INSTALL_STORE2_UV": "2"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target, "code stays on the new version")
        st = self.state()
        self.assertEqual(st["status"], "rollback_failed")
        self.assertEqual(st["failed_sha"], target)
        self.assertIn("user_version 1 -> 2", st["detail"])
        self.assertIn("TROUBLESHOOTING", st["detail"])
        self.assertEqual(len(self.installs()), 1, "no rollback install")
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("user_version", notes[0])
        self.assertIn("TROUBLESHOOTING", notes[0])

    def test_actd_change_landing_at_the_stop_point_still_refuses_rollback(self):
        # TOCTOU（#130 review P1）：判决前先 bootout actd（kill 会被 KeepAlive
        # 复活）——正在收尾的迁移若恰好在停止那一刻落盘（fake launchctl 在
        # bootout 时写标记），冻结后的重采样必须看见它并拒绝；采样后、reset 前
        # 不得留窗口。拒绝路径还必须把停掉的 actd bootstrap 回来。
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"],
                               env={"FAKE_LAUNCHCTL_BOOTOUT_MARKER":
                                    str(self.live / "state" / "store2_truth.json")})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target, "never resets against a ledger that just moved")
        st = self.state()
        self.assertEqual(st["status"], "rollback_failed")
        self.assertIn("store2", st["detail"])
        self.assertEqual(len(self.installs()), 1, "no rollback install")
        calls = self.launchctl_calls()
        self.assertTrue(any(c.startswith("bootout") for c in calls), calls)
        self.assertTrue(any(c.startswith("bootstrap") for c in calls),
                        "a refusal must restart the actd it stopped: %s" % calls)

    def test_unreadable_user_version_probe_fails_closed(self):
        # 探针错 ≠ 0（#130 review P1）：EPERM 窗口 / 坏文件里读不出 user_version
        # 时按「不明」拒绝——当 0 处理会在这 PR 本来要治的那类窗口里静默缴械
        target = self.push("0.48.4")
        marker = self.live / "state" / "store2_truth.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"activated_at": "2026-08-01T00:00:00Z"}\n', encoding="utf-8")
        self.seed_store2_db(1)
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"],
                               env={"FAKE_INSTALL_STORE2_CORRUPT": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target, "an unreadable ledger is never assumed safe to strand")
        st = self.state()
        self.assertEqual(st["status"], "rollback_failed")
        self.assertIn("unknown", st["detail"])
        self.assertIn("TROUBLESHOOTING", st["detail"])
        self.assertEqual(len(self.installs()), 1, "no rollback install")

    def test_write_state_repo_copy_failure_logs_the_cause_and_keeps_the_mirror(self):
        # the repo copy is the best-effort projection: its failure is logged with
        # the child's exception, and the HOME mirror still has the verdict
        (self.live / "state").mkdir(exist_ok=True)
        (self.live / "state" / "deploy_state.json.tmp").mkdir()  # open(tmp, "w") → IsADirectoryError
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("mirror written, repo copy failed (non-fatal): IsADirectoryError", self.log_text())
        self.assertIsNone(self.state(), "repo copy could not be written")
        self.assertEqual(self.mirror()["status"], "up_to_date", "the mirror is the truth")

    def test_write_state_mirror_failure_logs_the_cause(self):
        self.mirror_dir.mkdir(parents=True)
        (self.mirror_dir / "deploy_state.json.tmp").mkdir()
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("write_state failed (non-fatal): IsADirectoryError", self.log_text())

    def test_notify_failure_logs_the_cause(self):
        self.push("0.48.4")
        (self.live / "README.md").write_text("dirty\n", encoding="utf-8")  # → one notify
        proc = self.run_script(env={"FAKE_NOTIFY_LOG": str(self.tmp)})  # open(dir, "a") → IsADirectoryError
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("notify failed (non-fatal)", self.log_text())
        self.assertIn("IsADirectoryError", self.log_text())

    def test_notify_returning_false_logs_the_cause(self):
        # act.lib.notify.notify() 按内部约定 never raises：队列写失败被吞掉、只返回
        # False（Codex review P2）——部署脚本的通知是唯一推送通道，静默丢失 = owner
        # 不知道机器停在坏版本上，所以 False 也必须记一行
        self.push("0.48.4")
        (self.live / "README.md").write_text("dirty\n", encoding="utf-8")  # → one notify
        proc = self.run_script(env={"FAKE_NOTIFY_RETURN_FALSE": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("notify failed (non-fatal)", self.log_text())
        self.assertIn("returned False", self.log_text())

    def test_install_timeout_counts_as_failure(self):
        self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-"], install_rc=[0, 0],
                               env={"AUTODEPLOY_INSTALL_TIMEOUT": "1", "FAKE_INSTALL_SLEEP": "5"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        self.assertEqual(self.state()["status"], "rollback_failed",
                         "the rollback install also hits the 1s timeout → honest rollback_failed")
        self.assertIn("timeout", self.log_text())

    # -- 4. refusals --------------------------------------------------------- #

    def test_dirty_tree_refuses_and_notifies_once_per_sha(self):
        target = self.push("0.48.4")
        (self.live / "README.md").write_text("local edit\n", encoding="utf-8")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "HEAD untouched")
        self.assertEqual(self.installs(), [])
        st = self.state()
        self.assertEqual(st["status"], "refused_dirty")
        self.assertIn("README.md", st["detail"])
        self.assertEqual(len(self.notifications()), 1)
        self.assertIn("dirty", self.notifications()[0])
        # same pending sha, still dirty: quiet
        self.run_script()
        self.assertEqual(len(self.notifications()), 1)
        self.assertEqual(self.state()["status"], "refused_dirty")
        # the owner cleans up → the very next run deploys
        _git(self.live, "checkout", "--", "README.md")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    def test_untracked_files_do_not_count_as_dirty(self):
        target = self.push("0.48.4")
        (self.live / "scratch.txt").write_text("untracked\n", encoding="utf-8")
        self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")

    def test_not_on_main_refuses(self):
        self.push("0.48.4")
        _git(self.live, "checkout", "-q", "-b", "experiment")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [])
        st = self.state()
        self.assertEqual(st["status"], "refused_branch")
        self.assertIn("experiment", st["detail"])

    def test_diverged_local_main_refuses_without_force_push(self):
        self.push("0.48.4")
        (self.live / "README.md").write_text("local commit\n", encoding="utf-8")
        _git(self.live, "commit", "-q", "-am", "local divergence")
        local = self.head()
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), local, "never resets a diverged local main")
        self.assertEqual(self.state()["status"], "failed")
        self.assertIn("ff-only", self.state()["detail"])
        self.assertEqual(len(self.notifications()), 1)

    # -- 5. lock + log cap --------------------------------------------------- #

    def test_live_lock_skips_and_stale_lock_is_reclaimed(self):
        self.push("0.48.4")
        lock = self.lock
        lock.mkdir(parents=True)
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(sleeper.kill)
        (lock / "pid").write_text("%d\n" % sleeper.pid, encoding="utf-8")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [], "another live run holds the lock")
        self.assertIn("another auto-deploy run is active", self.log_text())
        self.assertTrue(lock.exists(), "a live lock is never removed")
        # stale: the holder is gone
        sleeper.kill()
        sleeper.wait()
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("removing stale lock", self.log_text())
        self.assertEqual(self.state()["status"], "deployed")
        self.assertFalse(lock.exists())

    def test_live_legacy_state_lock_is_honoured_and_a_stale_one_is_cleared(self):
        # upgrade window: a pre-v0.48.20 run still holds state/auto-deploy.lock
        # while it fast-forwards to THIS script — a run of the new script must
        # not deploy alongside it (Codex review P1 on #140)
        self.push("0.48.4")
        legacy = self.live / "state" / "auto-deploy.lock"
        legacy.mkdir(parents=True)
        sleeper = subprocess.Popen(["sleep", "30"])
        self.addCleanup(sleeper.kill)
        (legacy / "pid").write_text("%d\n" % sleeper.pid, encoding="utf-8")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [], "the legacy holder is live")
        self.assertIn("pre-v0.48.20 auto-deploy run still holds", self.log_text())
        self.assertTrue(legacy.exists(), "never removed while live")
        self.assertFalse(self.lock.exists(), "the HOME lock was not even taken")
        sleeper.kill()
        sleeper.wait()
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("removed stale legacy lock", self.log_text())
        self.assertFalse(legacy.exists())
        self.assertEqual(self.state()["status"], "deployed")

    def test_fresh_lock_without_pid_is_live_and_old_one_is_stale(self):
        # P2（review）：mkdir 与写 pid 之间的另一实例看到「无 pid」不得当陈旧锁回收
        self.push("0.48.4")
        lock = self.lock
        lock.mkdir(parents=True)
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [], "a fresh pid-less lock is a holder mid-mkdir")
        self.assertIn("has not written its pid yet", self.log_text())
        self.assertTrue(lock.exists())
        # the same pid-less dir, but old: a crash between mkdir and printf → stale
        old = time.time() - 600
        os.utime(str(lock), (old, old))
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("removing stale lock", self.log_text())
        self.assertEqual(self.state()["status"], "deployed")
        self.assertFalse(lock.exists())

    def test_log_is_capped(self):
        self.logs.mkdir()
        big = self.logs / "auto-deploy.log"
        big.write_text("x" * (1048576 + 4096), encoding="utf-8")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLess(big.stat().st_size, 1048576)
        self.assertIn("log capped", big.read_text(encoding="utf-8"))

    def test_not_a_git_checkout_exits_1(self):
        plain = self.tmp / "plain"
        (plain / "scripts").mkdir(parents=True)
        shutil.copyfile(str(SCRIPT), str(plain / "scripts" / "auto-deploy.sh"))
        proc = subprocess.run(["bash", str(plain / "scripts" / "auto-deploy.sh")],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "HOME": str(self.home),
                                   "AIASSISTANT_PYTHON": sys.executable,
                                   "AUTODEPLOY_LOG_DIR": str(self.logs)})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not a git checkout", self.log_text())

    # -- 6. deployed means running（2026-09-02 事故；§56.3 step 2） ------------- #
    # 实录：timer 起的一轮把 checkout 推到 v0.48.11，install.sh 被 EPERM（exit 126）、
    # 回滚被拒；20 分钟后下一轮看到 HEAD == origin/main 就写了 up_to_date，而
    # actd 内存里还是 v0.48.8。HEAD 到位只是必要条件：install_report.json 与
    # actd.heartbeat 都得说同一个版本、且心跳新鲜，否则 install_incomplete + 重装。

    def test_up_to_date_head_with_an_older_running_version_reinstalls_and_deploys(self):
        # the incident's second run: checkout v0.48.3, install.sh last finished on
        # v0.48.2 and the daemon in memory is v0.48.2
        self.seed_running("0.48.2")
        self.sighting()
        self.assertEqual(self.mirror()["reason"], "install_report_version_mismatch heartbeat_version_mismatch")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        inst = self.installs()
        self.assertEqual(len(inst), 1, "install.sh re-run exactly once, on the confirming run")
        self.assertIn("head=%s" % self.base_sha, inst[0])
        st = self.state()
        self.assertEqual(st["status"], "deployed", "the re-run completed the install")
        self.assertEqual(st["version"], "0.48.3")
        self.assertEqual(st["running_version"], "0.48.3")
        self.assertEqual(st["install_report_version"], "0.48.3")
        self.assertIn("install completed on re-run", st["detail"])
        self.assertIn("install_report.json says v0.48.2", st["detail"])
        self.assertIn("actd heartbeat says v0.48.2", st["detail"])
        self.assertNotIn("reason", st)
        self.assertNotIn("incomplete_seen", self.mirror(), "the sighting is spent")
        self.assertIn("install_incomplete", self.log_text())
        self.assertNotIn("up_to_date", self.log_text() + json.dumps(st))
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("install re-run", notes[0])
        # and now it really is up to date
        proc = self.run_script()
        self.assertEqual(self.state()["status"], "up_to_date")
        self.assertEqual(len(self.installs()), 1)

    def test_stale_heartbeat_with_the_right_version_is_not_up_to_date(self):
        # report says v0.48.3, heartbeat says v0.48.3 — but an hour old: nothing
        # is running that code right now
        self.seed_heartbeat("0.48.3", "idle", age_s=3600)
        self.sighting(env={"FAKE_INSTALL_HEARTBEAT": "none"})
        proc = self.run_script(env={"FAKE_INSTALL_HEARTBEAT": "none"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), 1, "re-run once")
        st = self.state()
        self.assertEqual(st["status"], "install_incomplete", "the re-run brought no fresh heartbeat")
        self.assertEqual(st["reason"], "heartbeat_stale")
        self.assertRegex(st["detail"], r"heartbeat is 36\d\ds old \(> 600s\)")
        self.assertEqual(st["running_version"], "0.48.3")
        self.assertEqual(self.notifications(), [], "first incomplete run is not yet news")

    def test_missing_heartbeat_and_report_are_spelled_out(self):
        self.clear_heartbeat()
        (self.live / "state" / "install_report.json").unlink()
        self.sighting()
        # and the re-run fails too, bringing neither a report nor a heartbeat
        proc = self.run_script(install_rc=[2], env={"FAKE_INSTALL_HEARTBEAT": "none"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual(st["status"], "install_incomplete")
        self.assertEqual(st["reason"], "install_failed install_report_version_mismatch heartbeat_missing")
        self.assertIn("install.sh exited 2", st["detail"])
        self.assertIn("v0.48.3", st["detail"])
        self.assertNotIn("install_report_version", st, "no report → no version to record")
        self.assertIn("no actd heartbeat at all", self.log_text())

    def test_persistently_incomplete_install_poisons_after_n_runs_and_force_rearms(self):
        # the re-run never completes (install.sh keeps failing → no report):
        # bounded — one re-run per run, poison + ONE notification at the limit,
        # then silence until main moves / --force / a hand-run install.sh
        (self.live / "state" / "install_report.json").unlink()
        self.sighting(install_rc=[2], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "3"})
        for n in (1, 2):
            proc = self.run_script(install_rc=[2], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "3"})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            st = self.state()
            self.assertEqual(st["status"], "install_incomplete", n)
            self.assertIn("re-run %d/3" % n, st["detail"])
            self.assertNotIn("incomplete_sha", self.mirror(), "not poisoned yet")
            self.assertEqual(self.notifications(), [], "quiet below the limit")
        proc = self.run_script(install_rc=[2], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "3"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), 3, "one re-run per run, three runs")
        st = self.state()
        self.assertEqual(st["status"], "install_incomplete")
        self.assertIn("3 install.sh re-runs at this sha did not complete it", st["detail"])
        self.assertEqual(self.mirror()["incomplete_sha"], self.base_sha, "poisoned in its own ledger")
        self.assertNotIn("failed_sha", self.mirror(), "not the rollback / CI-red ledger")
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("install incomplete", notes[0])
        # poisoned: no install, last_run still stamped, still no second notification
        time.sleep(1.1)
        proc = self.run_script(install_rc=[2], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "3"})
        self.assertEqual(len(self.installs()), 3, "no fourth install.sh")
        self.assertIn("gave up after 3 install.sh re-runs", self.log_text())
        self.assertNotEqual(self.state()["last_run"], st["last_run"])
        self.assertEqual(self.state()["status"], "install_incomplete", "verdict carried")
        self.assertEqual(len(self.notifications()), 1)
        # the owner repairs by hand (install.sh finished, actd beating) → up_to_date, no --force needed
        self.seed_running("0.48.3")
        proc = self.run_script()
        self.assertEqual(self.state()["status"], "up_to_date")
        self.assertNotIn("incomplete_sha", self.mirror())
        self.assertEqual(len(self.installs()), 3)
        # …but the per-sha budget is spent: should it fall over again, it is
        # poisoned on sight (no fourth install.sh) and NOT announced twice
        (self.live / "state" / "install_report.json").unlink()
        self.sighting(install_rc=[2], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "3"})
        self.run_script(install_rc=[2], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "3"})
        self.assertEqual(len(self.installs()), 3, "budget per sha is for life, not per streak")
        self.assertEqual(self.mirror()["incomplete_sha"], self.base_sha)
        self.assertIn("already re-run 3 times at this sha", self.state()["detail"])
        self.assertEqual(len(self.notifications()), 1, "one poison notice per sha")

    def test_force_rearms_a_poisoned_incomplete_install(self):
        (self.live / "state" / "install_report.json").unlink()
        self.sighting(install_rc=[2], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "2"})
        for _ in range(2):
            self.run_script(install_rc=[2], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "2"})
        self.assertEqual(self.mirror()["incomplete_sha"], self.base_sha)
        proc = self.run_script("--force")   # install.sh succeeds this time; no confirming run
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), 3)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertNotIn("incomplete_sha", self.mirror())
        self.assertEqual(self.mirror()["incomplete_runs"], "1", "--force re-armed the budget too")

    def test_refused_rollback_leaves_head_on_the_new_sha_and_the_next_run_finishes_the_install(self):
        # the incident chain, minus TCC: install.sh dies (126) on the new sha,
        # rollback is refused (store2 advanced during the deploy) — HEAD stays on
        # v0.48.4 with failed_sha set. The next run must NOT call that up_to_date:
        # the machine still runs v0.48.3; finishing the install is the repair,
        # and failed_sha (a verdict about the ROLLBACK) does not block it.
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-"], install_rc=[126],
                               env={"FAKE_INSTALL_STORE2": "1", "FAKE_INSTALL_HEARTBEAT": "none"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "rollback_failed")
        self.assertEqual(self.state()["failed_sha"], target)
        self.assertIn("rollback_failed: rollback refused", self.state()["last_incident"])
        self.sighting()
        self.assertIn("rollback_failed", self.state()["last_incident"], "a sighting keeps the verdict")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), 2, "the confirming run re-ran install.sh")
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertEqual(st["version"], "0.48.4")
        self.assertEqual(st["running_version"], "0.48.4")
        self.assertNotIn("failed_sha", st)
        self.assertNotIn("last_incident", st, "a completed repair IS the next successful deploy")
        self.assertNotIn("up_to_date", self.log_text())

    def test_repair_waits_for_ci_and_never_reinstalls_a_red_head(self):
        # §56.5 still holds on the repair path: the owner may have `git pull`ed a
        # main whose CI is pending or red — install.sh is not re-run on it
        # (Codex review P1 on #140); the same --force exit applies
        self.seed_running("0.48.2")
        self.sighting()
        proc = self.run_script(ci=["pending"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [], "CI still running → no re-run")
        st = self.state()
        self.assertEqual(st["status"], "install_incomplete")
        self.assertIn("ci_pending", st["reason"])
        self.assertIn("waiting for CI", st["detail"])
        self.assertEqual(self.notifications(), [])
        proc = self.run_script(ci=["failure"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [], "red CI → never re-run on that sha")
        st = self.state()
        self.assertEqual(st["status"], "install_incomplete")
        self.assertIn("ci_failed", st["reason"])
        self.assertEqual(self.mirror()["incomplete_sha"], self.base_sha, "poisoned in the repair ledger")
        self.assertEqual(len(self.notifications()), 1)
        self.assertIn("CI red", self.notifications()[0])
        # poisoned: no further CI query, no second notification
        self.run_script(ci=["failure"])
        self.assertEqual(len(self.ci_queries()), 2)
        self.assertEqual(len(self.notifications()), 1)
        # --force is the owner's exit: skips the gate and repairs
        proc = self.run_script("--force", ci=["failure"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), 1)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertEqual(len(self.ci_queries()), 2, "forced run did not ask")

    def test_repair_without_a_github_remote_and_no_ci_repo_does_not_reinstall(self):
        self.seed_running("0.48.2")
        self.sighting(env={"AUTODEPLOY_CI_REPO": ""})
        proc = self.run_script(env={"AUTODEPLOY_CI_REPO": ""})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.installs(), [])
        st = self.state()
        self.assertEqual(st["status"], "install_incomplete")
        self.assertIn("ci_unverifiable", st["reason"])
        self.assertIn("AUTODEPLOY_CI_REPO", st["detail"])

    def test_repair_install_that_exits_non_zero_is_never_deployed(self):
        # install.sh's exit code counts failed steps (crontab, launchd…) while the
        # report still records the new version and actd beats on it — that is
        # not `deployed` (Codex review P1 on #140)
        self.seed_running("0.48.2")
        self.sighting()
        proc = self.run_script(install_rc=[1], env={"FAKE_INSTALL_REPORT_ANYWAY": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), 1)
        st = self.state()
        self.assertEqual(st["status"], "install_incomplete")
        self.assertEqual(st["reason"], "install_failed")
        self.assertIn("install.sh exited 1", st["detail"])
        self.assertEqual(st["running_version"], "0.48.3", "report + heartbeat did agree…")
        self.assertEqual(st["install_report_version"], "0.48.3", "…but the installer said no")
        self.assertEqual(self.notifications(), [])

    def test_incomplete_counter_restarts_for_a_new_sha(self):
        # sha A collected two incomplete runs; main moves to B, B's install dies
        # and its rollback is refused (store2 advanced) → HEAD on B with two of
        # three strikes inherited would poison B on its FIRST repair (Codex P2)
        (self.live / "state" / "install_report.json").unlink()
        self.sighting(install_rc=[2], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "3"})
        for _ in range(2):
            self.run_script(install_rc=[2], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "3"})
        self.assertEqual(self.mirror()["incomplete_runs"], "2")
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-"], install_rc=[126],
                               env={"FAKE_INSTALL_STORE2": "1", "FAKE_INSTALL_HEARTBEAT": "none",
                                    "AUTODEPLOY_INCOMPLETE_LIMIT": "3"})
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "rollback_failed")
        self.sighting(install_rc=[2], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "3"})
        proc = self.run_script(install_rc=[2], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "3"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        m = self.mirror()
        self.assertEqual(m["status"], "install_incomplete")
        self.assertEqual(m["incomplete_runs"], "1", "B starts its own count")
        self.assertEqual(m["incomplete_runs_sha"], target)
        self.assertNotIn("incomplete_sha", m, "not poisoned on the first strike")
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("REFUSED", notes[0], "only the deploy's refused-rollback notice; no poison notice")

    def test_a_real_deploy_records_the_running_and_report_versions(self):
        self.push("0.48.4")
        self.run_script(doctor_plan=["-", "-"])
        st = self.state()
        self.assertEqual(st["status"], "deployed")
        self.assertEqual(st["running_version"], "0.48.4")
        self.assertEqual(st["install_report_version"], "0.48.4")

    def test_stale_heartbeat_gets_a_grace_to_beat_again(self):
        # the Mac just woke: launchd fires the missed interval at once, actd is
        # still finishing the sleep it went down in — its heartbeat is hours old
        # for a few more seconds. Right version everywhere → wait, do not repair.
        self.seed_heartbeat("0.48.3", "idle", age_s=3600)
        hb = self.live / "state" / "actd.heartbeat"
        fresh = json.dumps({"ts": "@NOW@", "phase": "idle", "pid": 1, "interval": 10,
                            "stale_after_s": 90, "version": "0.48.3"})
        beater = subprocess.Popen(["bash", "-c",
                                   'sleep 2; printf "%s" "$1" | sed "s/@NOW@/$(date -u +%Y-%m-%dT%H:%M:%SZ)/" > "$2"',
                                   "_", fresh, str(hb)])
        self.addCleanup(beater.kill)
        proc = self.run_script(env={"AUTODEPLOY_HEARTBEAT_GRACE": "20"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.state()["status"], "up_to_date")
        self.assertEqual(self.installs(), [])
        self.assertNotIn("install_incomplete", self.log_text())
        self.assertNotIn("incomplete_seen", self.mirror())
        # a wrong version gets no grace: the report says v0.48.2 → sighting at once
        self.seed_install_report("0.48.2")
        t0 = time.monotonic()
        self.sighting(env={"AUTODEPLOY_HEARTBEAT_GRACE": "20"})
        self.assertLess(time.monotonic() - t0, 15, "no grace wait for a version mismatch")

    def test_install_rerun_budget_is_per_sha_even_when_each_repair_succeeds(self):
        # a daemon that comes up, passes once and dies again before the next
        # interval: every repair "succeeds" — counted per streak it would be
        # reinstalled (and announced) every 20 min forever. Budget is per sha.
        for _ in range(2):
            self.seed_heartbeat("0.48.3", "idle", age_s=3600)     # died again
            self.sighting(env={"AUTODEPLOY_INCOMPLETE_LIMIT": "2"})
            proc = self.run_script(env={"AUTODEPLOY_INCOMPLETE_LIMIT": "2"})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(self.state()["status"], "deployed")
        self.assertEqual(len(self.installs()), 2)
        self.assertEqual(self.mirror()["incomplete_runs"], "2", "successes count against the budget")
        self.assertEqual(len(self.notifications()), 2, "two 'auto-deployed (install re-run)' notices")
        self.seed_heartbeat("0.48.3", "idle", age_s=3600)         # …and again
        self.sighting(env={"AUTODEPLOY_INCOMPLETE_LIMIT": "2"})
        proc = self.run_script(env={"AUTODEPLOY_INCOMPLETE_LIMIT": "2"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.installs()), 2, "budget spent: no third install.sh")
        self.assertEqual(self.mirror()["incomplete_sha"], self.base_sha)
        self.assertEqual(self.state()["status"], "install_incomplete")
        self.assertIn("already re-run 2 times at this sha", self.state()["detail"])
        self.assertEqual(len(self.notifications()), 3, "one poison notice")
        # main moving on is a fresh budget
        self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "-"], env={"AUTODEPLOY_INCOMPLETE_LIMIT": "2"})
        self.assertEqual(self.state()["status"], "deployed")
        m = self.mirror()
        for key in ("incomplete_runs", "incomplete_runs_sha", "incomplete_sha", "incomplete_notified_sha"):
            self.assertNotIn(key, m, key)

    def test_refused_rollback_verdict_survives_the_routine_up_to_date_write(self):
        # #135 review: a refused rollback (store2 advanced; install.sh and actd
        # fine) leaves HEAD on the new sha with everything aligned — the next
        # interval's `up_to_date` erased the verdict 10 min after it was raised.
        # `last_incident` keeps it on the dashboard until the next real deploy.
        target = self.push("0.48.4")
        proc = self.run_script(doctor_plan=["-", "dashboard", "dashboard", "dashboard"],
                               env={"FAKE_INSTALL_STORE2": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual(st["status"], "rollback_failed")
        self.assertRegex(st["last_incident"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ rollback_failed: rollback refused")
        self.assertIn("dashboard", st["last_incident"], "carries the reason the rollback was wanted")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = self.state()
        self.assertEqual(st["status"], "up_to_date", "install.sh had finished and actd runs v0.48.4")
        self.assertEqual(st["last_incident"], self.mirror()["last_incident"])
        self.assertIn("rollback_failed", st["last_incident"], "not erased by the routine write")
        self.assertNotIn("failed_sha", st, "HEAD == origin/main: the poison is moot and cleared")
        self.run_script("--force", doctor_plan=["-", "-"])
        self.assertIn("rollback_failed", self.state()["last_incident"], "--force alone clears nothing here")
        self.push("0.48.5")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(self.state()["status"], "deployed")
        self.assertNotIn("last_incident", self.state(), "the next successful deploy clears it")
        self.assertNotEqual(self.head(), target)

    # -- 7. volume-access probe + HOME mirror（TCC；2026-09-02 事故） ------------ #
    # macOS 按 responsible executable 给外置卷授权，launchd 任务收不到弹窗；终端里
    # 跑的每一次都把终端的授权借给子进程，所以「我手跑是好的」不证明任何事。探针
    # 在第一次 git 调用之前跑；拒绝 = blocked_tcc 写进 $HOME 的镜像 + 日志点名
    # plist 里那个解释器，通知一天一次，HEAD 不动。fixture 用 chmod 000 造出
    # PermissionError（errno 13；真 TCC 是 errno 1——脚本只看异常类型）。

    def _plist(self, interpreter="/fake/launchd/python3"):
        p = self.home / "Library" / "LaunchAgents" / "com.zelin.aiassistant.autodeploy.plist"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("<plist><dict><key>Label</key><string>x</string>\n<key>ProgramArguments</key>\n"
                     "<array>\n  <string>%s</string>\n  <string>-m</string>\n</array></dict></plist>\n"
                     % interpreter, encoding="utf-8")
        return p

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "chmod 000 does not bind root")
    def test_unreadable_repo_file_is_blocked_tcc_and_moves_nothing(self):
        target = self.push("0.48.4")
        self._plist()
        (self.live / "install.sh").chmod(0)
        self.addCleanup(lambda: (self.live / "install.sh").chmod(0o755))
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "HEAD never moves before the probe passes")
        self.assertEqual(_git(self.live, "rev-parse", "refs/remotes/origin/main"), self.base_sha,
                         "git fetch was not even attempted")
        self.assertEqual(self.installs(), [])
        self.assertEqual(self.ci_queries(), [])
        log = self.log_text()
        self.assertRegex(log, r"volume_access=denied \(errno \d+\)")
        self.assertIn("grant Full Disk Access to /fake/launchd/python3", log,
                      "names the plist's ProgramArguments[0], the binary TCC judges")
        self.assertIn("trigger=launchd", log)
        m = self.mirror()
        self.assertEqual(m["status"], "blocked_tcc")
        self.assertEqual(m["reason"], "volume_access_denied")
        self.assertEqual(m["interpreter"], "/fake/launchd/python3")
        self.assertEqual(m["trigger"], "launchd")
        self.assertEqual(m["repo"], str(self.live.resolve()))
        self.assertTrue(m["volume"].startswith("/"), m)
        self.assertEqual(m["unattended_status"], "blocked_tcc", "the doctor reads this triple")
        self.assertEqual(m["denied_path"], str(self.live.resolve() / "install.sh"))
        # the projected detail carries NO local path (it rides into the dashboard
        # and, in cloud mode, into the encrypted snapshot): paths live in the
        # mirror-only keys the doctor row renders
        for value in (m["detail"], m["unattended_detail"], self.state()["detail"]):
            self.assertIn("volume_access=denied", value)
            self.assertNotIn(str(self.live), value)
            self.assertNotIn("/fake/launchd/python3", value)
        notes = self.notifications()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("/fake/launchd/python3", notes[0])
        self.assertIn("完全磁盘访问", notes[0])
        # same day: quiet; yesterday's stamp: one more
        self.run_script()
        self.assertEqual(len(self.notifications()), 1, "once per day, not every 10 min")
        self.assertEqual(self.mirror()["tcc_notified_day"], time.strftime("%Y-%m-%d", time.gmtime()))
        path = self.mirror_dir / "deploy_state.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["tcc_notified_day"] = "2000-01-01"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.run_script()
        self.assertEqual(len(self.notifications()), 2)
        self.assertEqual(self.head(), self.base_sha)
        # access granted → the very next run deploys normally, nothing was poisoned
        (self.live / "install.sh").chmod(0o755)
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), target)
        self.assertEqual(self.state()["status"], "deployed")
        self.assertEqual(self.mirror()["unattended_status"], "deployed", "a good unattended run clears it")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "chmod 000 does not bind root")
    def test_unwritable_state_dir_still_records_blocked_tcc_in_the_home_mirror(self):
        # the incident's write_state / rm-lock EPERMs: state/ itself is off limits
        self.push("0.48.4")
        state_dir = self.live / "state"
        state_dir.chmod(0)
        self.addCleanup(lambda: state_dir.chmod(0o755))
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha)
        m = self.mirror()
        self.assertEqual(m["status"], "blocked_tcc")
        self.assertEqual(m["denied_path"], str(state_dir.resolve()))
        self.assertNotIn(str(state_dir), m["detail"], "paths stay out of the projected detail")
        self.assertIn("mirror written, repo copy failed", self.log_text())
        self.assertFalse(self.lock.exists(), "the HOME lock is released cleanly")
        self.assertNotIn("could not remove", self.log_text())
        state_dir.chmod(0o755)
        self.assertFalse((state_dir / "deploy_state.json").exists(), "nothing landed on the volume")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "chmod 000 does not bind root")
    def test_terminal_runs_do_not_overwrite_the_unattended_verdict(self):
        # a green run from the owner's terminal inherits the terminal's TCC grants:
        # it proves nothing about the launchd job, so the unattended triple stays
        self.push("0.48.4")
        (self.live / "install.sh").chmod(0)
        self.addCleanup(lambda: (self.live / "install.sh").chmod(0o755))
        self.run_script()
        self.assertEqual(self.mirror()["unattended_status"], "blocked_tcc")
        (self.live / "install.sh").chmod(0o755)
        proc = self.run_script(doctor_plan=["-", "-"], env={"AUTODEPLOY_TRIGGER": "terminal"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        m = self.mirror()
        self.assertEqual(m["status"], "deployed")
        self.assertEqual(m["trigger"], "terminal")
        self.assertEqual(m["unattended_status"], "blocked_tcc", "untouched by the terminal run")
        # TERM_PROGRAM alone marks a terminal too (an orchestrator started from one)
        self.run_script(env={"TERM_PROGRAM": "Apple_Terminal"})
        self.assertEqual(self.mirror()["trigger"], "terminal")
        self.assertEqual(self.mirror()["unattended_status"], "blocked_tcc")
        # the next launchd-spawned run rewrites it
        self.run_script()
        self.assertEqual(self.mirror()["unattended_status"], "up_to_date")

    def test_without_a_plist_the_interpreter_named_is_the_launchers_own(self):
        self.push("0.48.4")
        self.seed_running("0.48.2")   # any run writes interpreter/trigger/repo
        self.run_script()
        self.assertEqual(self.mirror()["interpreter"], sys.executable,
                         "AIASSISTANT_PYTHON is argv0 when the shim started us")

    def test_mirror_seeds_itself_from_the_repo_copy_on_first_run(self):
        # upgrade path: pre-v0.48.20 machines only have state/deploy_state.json;
        # its failed_sha bookkeeping must survive into the mirror
        target = self.push("0.48.4")
        (self.live / "state" / "deploy_state.json").write_text(
            json.dumps({"status": "rolled_back", "version": "0.48.3", "failed_sha": target,
                        "last_deployed": "2026-09-01T00:00:00Z"}), encoding="utf-8")
        proc = self.run_script(doctor_plan=["-", "-"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.head(), self.base_sha, "the poisoned sha is still poisoned")
        self.assertIn("already failed", self.log_text())
        self.assertEqual(self.mirror()["failed_sha"], target)
        self.assertEqual(self.mirror()["last_deployed"], "2026-09-01T00:00:00Z")
        self.assertEqual(self.state()["failed_sha"], target, "repo copy stays in step")


if __name__ == "__main__":
    unittest.main()
