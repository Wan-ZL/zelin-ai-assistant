type: fixed
- **静默并入的 sweep 不再被缺 `id` 的 job 文件崩掉（§44）**：`state/silent_merge/SM-*.json` 记录若没有 `id` 字段，`_finish` 现在按文件名补回 id 再落盘，而不是 KeyError 出 actd 的 pass（宪法第 11 条）。由 R-198 变异测试补判例时挖出：`act/lib/silent_merge.py` 存活体 101 → 3（剩 3 个为等价变异体，理由记在 `tests/test_silent_merge_mutation_kills.py` docstring），`qa/mutation_targets.toml` 的映射补上 `test_silent_merge_jobs_edge.py` 与新判例文件。
