"""v-next web 看板的本地 HTTP + SSE server（stdlib + PyYAML，PR1）。

现有两文件契约的又一个客户端（BUILD-CONTRACT §2.1）：
- 读 ``$AIASSISTANT_HOME/state/dashboard.json``（投影，原样透传）
- 只读解析 ``act/registry/*.yaml``（详情增补，含 archive/ fallback）
- 写 ``state/inbox/*.json``（动作，经 inbox_writer —— actd 一行不改）

安全边界：硬编码 bind 127.0.0.1（宪法：新增网络面仅 localhost）；
绝不 import act 包里带写路径的模块 —— registry 单写者是 actd（CONTRACT §44）。
"""
