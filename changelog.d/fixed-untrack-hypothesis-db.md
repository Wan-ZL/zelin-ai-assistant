type: fixed
- 仓库不再跟踪 `.hypothesis/`（hypothesis 的示例库是测试缓存，a514b402 误随 §77 文档提交入库；它会挡住 live checkout 的 `git merge --ff-only` 部署），并加进 `.gitignore`。
