#!/usr/bin/env bash
# QA 合并硬门一键跑（docs/CONTRACT.md §58 + §63；CI 的 qa-gates job 与本地共用）。
# 六道门全部跑完再汇总退出码（一道红不遮蔽其余的判决）；判决、榜单与
# 建议账本写进 $QA_REPORT_DIR（默认 .qa-report/，已 gitignore；CI 上传为
# artifact——收账 = 从 artifact 拷回 qa/*_baseline.txt）。
# [ui-parity]（§63.2）要 node：它把 web/src/parity.test.tsx 以 --reporter=json 跑一遍
# （web/ 需先 `npm ci`）——没有 node 就是红，不软化（与 coverage 缺席同款）。
# 用法：bash scripts/qa/run_gates.sh
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

OUT="${QA_REPORT_DIR:-.qa-report}"
mkdir -p "$OUT"

bash scripts/qa/run_coverage.sh "$OUT" || exit 1

fail=0
python3 scripts/qa/complexity.py --check --report "$OUT" || fail=1
python3 scripts/qa/crap.py --check --coverage-json "$OUT/coverage.json" --report "$OUT" || fail=1
python3 scripts/qa/coverage_floor.py --coverage-json "$OUT/coverage.json" --report "$OUT" || fail=1
python3 scripts/qa/depgraph.py --check --report "$OUT" || fail=1
python3 scripts/qa/hygiene.py --check --report "$OUT" || fail=1
python3 scripts/ui/parity_check.py --check --report "$OUT" || fail=1

if [ "$fail" -ne 0 ]; then
  echo "qa-gates: FAIL — verdicts and suggested baselines in $OUT/" >&2
else
  echo "qa-gates: OK"
fi
exit "$fail"
