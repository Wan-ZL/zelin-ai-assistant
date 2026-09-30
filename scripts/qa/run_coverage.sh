#!/usr/bin/env bash
# 覆盖率原料（docs/CONTRACT.md §58.2）：在 coverage.py 下跑全套 unittest，
# 产出 JSON——crap.py 与 coverage_floor.py 的唯一输入。coverage 是 dev/CI
# 侧依赖（§0 宪法第 7 条：运行时白名单 stdlib+PyYAML 不变），CI 只在
# qa-gates job 安装；本地没装时报一句就停。
# 用法：bash scripts/qa/run_coverage.sh [输出目录，默认 .qa-report]
set -euo pipefail
cd "$(dirname "$0")/../.."

OUT="${1:-.qa-report}"
mkdir -p "$OUT"

if ! python3 -c "import coverage" 2>/dev/null; then
  echo "coverage.py not installed — pip install coverage (dev/CI-side only)" >&2
  exit 1
fi

# 测试沙箱（CONTRACT §82.3）：**无条件**，不是 `${AIASSISTANT_HOME:-…}`。
# 那个 `:-` 是 issue #452 的一半病根——无人值守的会话从守护进程继承
# `AIASSISTANT_HOME=<live checkout>`，回落值永远命不中，全套 unittest 就跑在真账本上。
# 两条语句而不是 `export X="$(mktemp -d)"`：后者触发 shellcheck SC2155。
ZAI_COV_HOME="$(mktemp -d)"
export AIASSISTANT_HOME="$ZAI_COV_HOME"
trap 'rm -rf "$ZAI_COV_HOME"' EXIT INT TERM
export COVERAGE_FILE="$OUT/.coverage"

python3 -m coverage run --source=act,server -m unittest discover -s tests
python3 -m coverage json -o "$OUT/coverage.json"
python3 -m coverage report --sort=cover > "$OUT/coverage.txt"
echo "coverage JSON: $OUT/coverage.json"
