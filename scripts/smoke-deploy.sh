#!/bin/bash
# scripts/smoke-deploy.sh — post-deploy 冒烟：装完（mac/build.sh --install /
# .pkg / Sparkle 更新）后一键回答「本机跑的真是这个版本吗」。四项检查，任一
# 失败 = 非零退出 + 说人话的诊断（发生过的事故：编译错被吞、旧产物被当新
# 构建装了出去——见 mac/build.sh 的 build_failed）。
#
# Usage: bash scripts/smoke-deploy.sh    （在 repo checkout 里，装完后跑）
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_NAME="Zelin's AI Assistant"
ACTD_LABEL="com.zelin.aiassistant.actd"
# actd 默认 10s 一轮、每轮重写 dashboard.json —— 60s 内没动过 = 守护进程僵了。
DASHBOARD_MAX_AGE=60

# 本版关键特征标记：release 里新增的 user-visible 字符串（L() 的中文文案最稳，
# 英文文案容易撞上系统库字符串）。直接 grep 安装的二进制 —— 版本号对得上但
# 二进制是旧的（stale bundle、缓存的 .pkg）就靠这个抓。【发版时更新】：换成
# 本版的新文案，1-3 个足够。
MARKERS=(
    "同时公开到 GitHub 建议跟踪表"   # v0.46 提建议弹窗的 GitHub tracker 勾选
    "按分组合并"                     # v0.46 多对多合并（合并建议分组执行）
)

FAILS=0
ok()   { printf "  [ ok ] %s\n" "$1"; }
fail() { printf "  [FAIL] %s\n" "$1"; FAILS=$((FAILS + 1)); }

# --- locate the installed app (install.sh falls back to ~/Applications) ---
APP="/Applications/$APP_NAME.app"
if [ ! -d "$APP" ]; then
    if [ -d "$HOME/Applications/$APP_NAME.app" ]; then
        APP="$HOME/Applications/$APP_NAME.app"
        ok "app found at fallback location: $APP"
    else
        fail "app not installed — neither /Applications nor ~/Applications has '$APP_NAME.app' (run: bash mac/build.sh --install)"
    fi
fi
BIN="$APP/Contents/MacOS/ZelinAIEngineer"

# --- 1. installed version == act.__version__ (the single source of truth) ---
echo "==> 1. version match"
EXPECTED="$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' "$REPO_ROOT/act/__init__.py")"
if [ -z "$EXPECTED" ]; then
    fail "could not read __version__ from act/__init__.py — is $REPO_ROOT a full checkout?"
elif [ ! -d "$APP" ]; then
    fail "version check skipped — no installed app to compare against"
else
    INSTALLED="$(plutil -extract CFBundleShortVersionString raw "$APP/Contents/Info.plist" 2>/dev/null || echo "?")"
    if [ "$INSTALLED" = "$EXPECTED" ]; then
        ok "installed app is $INSTALLED (== act/__init__.py)"
    else
        fail "installed app reports $INSTALLED but the checkout says $EXPECTED — the install did not land; re-run: bash mac/build.sh --install"
    fi
fi

# --- 2. binary carries this release's marker strings ---
echo "==> 2. binary feature markers"
if [ ! -f "$BIN" ]; then
    fail "binary missing at $BIN — broken bundle?"
else
    for marker in "${MARKERS[@]}"; do
        # -a: the executable is binary; -c counts matching "lines" (>=1 = present)
        COUNT="$(LC_ALL=C grep -ac -- "$marker" "$BIN" 2>/dev/null || true)"
        if [ "${COUNT:-0}" -ge 1 ]; then
            # ${marker} braced: /bin/bash 3.2 misparses $marker followed by a
            # CJK byte as part of the variable name (unbound under set -u).
            ok "marker present: 「${marker}」"
        else
            fail "marker MISSING: 「${marker}」 — version stamp matches but the binary looks OLD (stale bundle / interrupted install); rebuild + reinstall"
        fi
    done
fi

# --- 3. actd alive: launchd agent running + dashboard.json freshly written ---
echo "==> 3. actd liveness"
ACTD_INFO="$(launchctl list "$ACTD_LABEL" 2>/dev/null)"
if [ -z "$ACTD_INFO" ]; then
    fail "launchd agent $ACTD_LABEL is not loaded — run: bash install.sh (step 5 loads it)"
elif ! printf '%s\n' "$ACTD_INFO" | grep -q '"PID"'; then
    fail "launchd agent $ACTD_LABEL is loaded but NOT running (no PID) — check: launchctl list $ACTD_LABEL, then log/actd.log"
else
    ok "launchd agent $ACTD_LABEL is running"
fi
DASH="${AIASSISTANT_HOME:-$REPO_ROOT}/state/dashboard.json"
if [ ! -f "$DASH" ]; then
    fail "state/dashboard.json missing at $DASH — actd has never completed a pass here"
else
    AGE=$(( $(date +%s) - $(stat -f %m "$DASH") ))
    if [ "$AGE" -lt "$DASHBOARD_MAX_AGE" ]; then
        ok "dashboard.json written ${AGE}s ago (fresh)"
    else
        fail "dashboard.json is ${AGE}s old (>${DASHBOARD_MAX_AGE}s) — actd is loaded but not cycling; check log/actd.log"
    fi
fi

# --- 4. full doctor via install.sh --check (exit code = number of FAILs) ---
echo "==> 4. doctor (bash install.sh --check)"
DOCTOR_OUT="$(bash "$REPO_ROOT/install.sh" --check 2>&1)"
DOCTOR_LAST="$(printf '%s\n' "$DOCTOR_OUT" | tail -n 1)"
case "$DOCTOR_LAST" in
    *" 0 fail"*)
        ok "doctor: $DOCTOR_LAST"
        ;;
    *)
        fail "doctor reports failures — full output below"
        printf '%s\n' "$DOCTOR_OUT" | sed 's/^/       /'
        ;;
esac

# --- 5. main-thread hang reports (布局风暴防复发哨) ---
# 2026-07-28/29 两次看板布局风暴把主线程卡死 17min/70s，系统各写了一份
# ZelinAIEngineer 的 .hang 取证到 DiagnosticReports。这里扫：比当前安装的
# app 还新的 hang 报告 = 这个 build 卡死过主线程 → FAIL 报警；更老的报告
# 只提示（历史事故的尸检，不阻塞本次部署）。目录同时看系统级与用户级
# （macOS 对用户进程通常写 ~/Library，Retired/ 是系统轮转后的归档）。
echo "==> 5. hang reports"
HANG_DIRS=(
    "/Library/Logs/DiagnosticReports"
    "$HOME/Library/Logs/DiagnosticReports"
    "$HOME/Library/Logs/DiagnosticReports/Retired"
)
if [ ! -f "$APP/Contents/Info.plist" ]; then
    fail "hang scan skipped — no installed app to date reports against"
else
    NEW_HANGS=""
    OLD_COUNT=0
    for dir in "${HANG_DIRS[@]}"; do
        [ -d "$dir" ] || continue
        # ZelinAIEngineer-*.hang / ZelinAIEngineer_*.hang 两种命名都见过
        while IFS= read -r report; do
            [ -n "$report" ] || continue
            if [ "$report" -nt "$APP/Contents/Info.plist" ]; then
                NEW_HANGS="$NEW_HANGS$report"$'\n'
            else
                OLD_COUNT=$((OLD_COUNT + 1))
            fi
        done < <(find "$dir" -maxdepth 1 -name 'ZelinAIEngineer*.hang' 2>/dev/null)
    done
    if [ -n "$NEW_HANGS" ]; then
        fail "main-thread HANG report(s) newer than the installed app — this build froze the UI (布局风暴回归？); read the report(s):"
        printf '%s' "$NEW_HANGS" | sed 's/^/       /'
    elif [ "$OLD_COUNT" -gt 0 ]; then
        ok "no new hang reports (老报告 $OLD_COUNT 份，早于本次安装，仅存档)"
    else
        ok "no hang reports for ZelinAIEngineer"
    fi
fi

# --- verdict ---
echo ""
if [ "$FAILS" -eq 0 ]; then
    echo "SMOKE PASS — installed app matches this checkout and the pipeline is alive."
    exit 0
else
    echo "SMOKE FAIL — $FAILS problem(s) above. Do NOT call this deploy done."
    exit 1
fi
