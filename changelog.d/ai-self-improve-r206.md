type: changed
- **QR 编码器的测试网补齐（§41/§57）**：`act/lib/qr.py` 的变异测试杀伤率从 62.0% 提到 96.4%（1,198 个位点，存活 455 → 43）。新增四组判例——ISO/IEC 18004 的表结构不变量与 byte-mode 容量边界、40 个 version × EC 组合的编码-解码对拍（判例侧独立解码器 `tests/qr_testkit.py`）、PNG 容器逐字段解回像素、掩码选择与四条罚分规则的非对称素材。编码器行为零改动；剩余 43 个存活体逐个机器复核为等价变异体，理由记在 `docs/design/progress/2026-09-10-self-improve-r206-qr-mutation-kills.md`。
