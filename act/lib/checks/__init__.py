"""doctor 的探针家族（CONTRACT §25 doctor 行；入口 ``act/doctor.py``）。

每个子模块 = 一个探针家族，公开函数 ``check_*(probes) -> CheckResult | list``
接收 ``act.doctor.Probes``（注入缝，duck-typed——本层不 import act.doctor，
lib 永不向上，§58.3）。``core`` 放共享件：OK/WARN/FAIL、CheckResult、label
常量、子进程 runner、双语 pick。家族：``environment``（工具链 / 配置 /
凭证 / 目录）、``launchd``（macOS 服务 + §55 路径纪律 + TCC 三幕）、
``services``（systemd / Task Scheduler 镜像）、``cron``（§18 链 + FDA 探针）、
``pipeline``（store2 / dashboard / 心跳 / 看板 server / 部署）。模型旋钮两行
（§59）留在 act/doctor.py——它们经 act/llm.py 单一边界，lib 层不可达。
"""
