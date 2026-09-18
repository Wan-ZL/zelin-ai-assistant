#!/usr/bin/env bash
# full_coverage.sh —— 全覆盖跑者的入口（docs/CONTRACT.md §58 QA 闸门）。
#
#   bash scripts/qa/full_coverage.sh [--inventory PATH] [--logdir DIR] [--only <id prefix>]
#                                    [--skip-ax] [--report PATH] [--no-web-build]
#
# 做三件事，其余全在 scripts/qa/coverage_run.py：
#   1. 备齐 web/dist（PWA / playwright 两类 proof 要它；缺席且 node+node_modules 在场
#      就现场 build 一次，--no-web-build 可关）；
#   2. 跑 coverage_run.py（清单 × proof DSL → qa/coverage-report/report.md）；
#   3. trap 收尾：临时 HOME（/tmp/zaa-cov-*）与自己起的子进程一个不留。
#
# 退出码 = coverage_run.py 的：0 = MISSING=0，1 = 还有 MISSING，2 = 清单缺席。
# 绝不碰 live 数据：demo server / install.sh / uninstall.sh / doctor 全跑在临时 HOME 里。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${PYTHON:-python3}"
NO_WEB_BUILD=0

ARGS=()
for arg in "$@"; do
    case "$arg" in
        --no-web-build) NO_WEB_BUILD=1 ;;
        -h|--help)
            sed -n '2,14p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *) ARGS+=("$arg") ;;
    esac
done

# 本次跑起来的临时 HOME 都长这个样（coverage_run.py 的 TempHomes 同一前缀）——
# python 侧已有 atexit/信号 trap，这里是第二道保险（被 kill -9 之后的下一次跑）。
cleanup() {
    rc=$?
    rm -rf /tmp/zaa-cov-*.stale 2>/dev/null || true
    exit $rc
}
trap cleanup EXIT INT TERM

if [ "$NO_WEB_BUILD" -eq 0 ] && [ ! -f "$ROOT/web/dist/index.html" ] \
   && [ -d "$ROOT/web/node_modules" ] && command -v npm >/dev/null 2>&1; then
    echo "full_coverage: web/dist 缺席 —— 先 build 一次（PWA / playwright proof 要它）"
    (cd "$ROOT/web" && npm run build >/dev/null) || \
        echo "full_coverage: npm run build 失败 —— 相关 proof 会诚实记 MISSING" >&2
fi

PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    "$PY" "$ROOT/scripts/qa/coverage_run.py" ${ARGS+"${ARGS[@]}"}
