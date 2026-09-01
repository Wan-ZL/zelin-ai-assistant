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

# 测试沙箱（tests/__init__.py 也自带 tempdir 兜底，这里是 belt-and-braces）
export AIASSISTANT_HOME="${AIASSISTANT_HOME:-$(mktemp -d)}"
export COVERAGE_FILE="$OUT/.coverage"

python3 -m coverage run --source=act,server -m unittest discover -s tests
python3 -m coverage json -o "$OUT/coverage.json"
python3 -m coverage report --sort=cover > "$OUT/coverage.txt"
echo "coverage JSON: $OUT/coverage.json"
