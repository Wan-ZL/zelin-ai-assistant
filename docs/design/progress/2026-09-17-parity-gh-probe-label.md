pr: `fix/parity-gh-probe-label`（2026-09-17 全覆盖收尾）
phase: 横切（QA；§66.2 归属表，owner 决策 **D79** 追記）
law: §66.2 归属表一条（`control:deps:label:which-gh-login-shell` → retired）；D79 追記

全覆盖 run4 之后 `ui/parity/pending.txt` 只剩一条：依赖页 gh 行的 detail 文案 `which gh（登录 shell）`。它不是一个控件，是原生**自己的探针命令的自述**——`Pages.swift:198` 的 `DepRowState` 把 `Shell.ok("which gh")`（`/bin/zsh -lc`）的做法写进了 detail；web 的依赖页把探测交给 server（`GET /api/deps`）并显示 server 的 detail，照抄这句等于对用户说「web 也在你的登录 shell 里 which gh」。同屏其它探针描述（`npx…`、`claude executable…`、`CGPreflight…`）抽取器本来就按 informational copy 走，这条是被当成了控件。

按 §66.2 末句「新的不搬判断走归属表」：`scripts/ui/extract_native_inventory.py` 的 `CONTROL_OWNER` 加一条 `owner: retired` 带理由与 D79 引用，`--write` 重铸 `ui/parity/native-inventory.json`（gated=false），`pending.txt` 划掉该行（shrink-only），`qa/coverage_inventory.json` 随之重铸（1363 → 1362，`missing_proof=0`）。不动 `waivers.txt`（它明文只许划掉不许新增）。至此 [ui-parity] 门 PENDING 归零。
