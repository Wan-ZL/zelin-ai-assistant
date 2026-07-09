# act/registry/ — 需求注册表（真源）

一条需求一个 YAML 文件（`R-xxx.yaml`），schema 见 `docs/CONTRACT.md` §1。
状态机：`detected → card_sent → approved → executing → review → delivered`，
旁支 `rejected` / `trashed` / `merged_into:<父ID>`。

**此目录的内容由运行时生成**：radar 扫描到新需求、快速捕获、欠账 raise 都会在
这里落盘新条目；actd 推进状态时原地改写。内部版仓库里的真实条目未随公开导出
发布 —— `R-000-example.yaml` 是一个完全虚构的示例，展示文件格式；可以删除。
