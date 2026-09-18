type: changed
- **test-code skill 的 `checks.py` 补上测试网（§57/§58）**：变异测试杀伤率从 51.85% 提到 99.76%（839 个位点，存活 404 → 2），剩下 2 个是可证等价体、理由记在判例的 docstring 里。生产代码零改动。诚实口径：99.76% 是全口径，把「照现状抄的 golden 表」换成运行时现算后的地板是 81.4%，连文档 oracle 也拿掉的最保守地板是 76.4%——三个数与复测方法都在 `docs/design/progress/2026-09-18-self-improve-r001-checks-residual-mutants.md`。
