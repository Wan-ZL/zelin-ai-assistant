# Transplant notes — v-next wire → v0.47 base（移植台账）

Task C（inventory + copy）的碰撞记录。wire 分支（feature/vnext-wire，基于 orphan 公开导出线 8fd3b33）里 `git diff --name-status 8fd3b33...feature/vnext-wire` 共 193 项：184 A（新增）+ 9 M（修改）。184 个 A 路径中 182 个已逐字节复制进本 worktree；下面 2 个路径在 v0.47 里已存在且内容不同——按碰撞规则**保留 v0.47 版本**，wire 侧的 delta 原文记录在此，交给 porter 在真实 v0.47 文件体上重放。

9 个 M 路径（porter 的活，不在本文件范围）：`.gitignore`、`act/actd.py`、`act/executor.py`、`act/lib/config.py`、`act/lib/dashboard.py`、`act/lib/quick_capture.py`、`act/lib/registry.py`、`act/radar_slack.py`、`config.example.yaml`。

## 碰撞 1：`act/webui.py`（保留 v0.47；wire delta 待重放）

wire 版自述是「从 live tree（= v0.47 行为）verbatim 移植 + W18 remote direct-run gate」。因此下面这份 `v0.47 → wire` 的 diff 里，除 port note 注释外的实质增量就是 **W18 闸门**（远端 ingress 默认关闭 capture `mode:"run"` 直跑，未开启配置时静默降级为 propose + `notice` 字段，见 docs/design/vnext-amendments.md §W18）及 `from act.lib import risk` 的引入。porter 请在 v0.47 的 `act/webui.py` 上重放 W18 逻辑；wire 版注释里「v0.10.3 actd 只认 ALLOWED_ACTIONS 子集」的说法针对 orphan 基线，在 v0.47 上不成立，相关注释不必照搬。

```diff
--- /Volumes/Storage/Server/Projects/.worktree/zelin-ai-assistant/wt-vnext-047/act/webui.py	2026-08-31 01:19:17
+++ /Volumes/Storage/Server/Projects/.worktree/zelin-ai-assistant/wt-vnext-wire/act/webui.py	2026-08-30 21:33:55
@@ -8,6 +8,13 @@
 and deletes them). Exactly the same read-only + inbox-write pattern as the Mac
 app (mac/Sources/AppDelegate.swift ``writeInboxFile``) and the planned iOS app.
 
+vnext port note: this module is ported verbatim from the live tree (v0.47
+behavior — the READ-ONLY truth) into the v0.10.3-based worktree, plus the W18
+remote direct-run gate (docs/design/vnext-amendments.md §W18). The v0.10.3
+actd applies a SUBSET of ALLOWED_ACTIONS and ignores capture ``mode`` — the
+allow-list is kept faithful to live so the actd-side ports land against a
+stable ingress; deltas are recorded in vnext-amendments.md, not guessed here.
+
 Security model (a local service that can APPROVE work must resist a malicious
 local web page doing CSRF / DNS-rebinding):
   * Bound to 127.0.0.1 ONLY — never 0.0.0.0.
@@ -19,6 +26,10 @@
     Origin validation on POST (blocks cross-origin form/CSRF posts).
   * Static serving is a fixed allow-list of files — no path joining with the
     request path, so directory traversal is impossible.
+  * W18: capture ``mode:"run"`` (direct-run, skips the human preview §34) is
+    gated OFF by default for this network ingress — without the config opt-in
+    it silently downgrades to a plain propose capture (with a notice), never
+    an error.
 
 Stdlib only (http.server) — no new dependencies beyond PyYAML (unused here).
 This module deliberately does NOT wire into install.sh / launchd / the Swift
@@ -40,7 +51,7 @@
 from typing import Optional
 from urllib.parse import urlparse
 
-from act.lib import config
+from act.lib import config, risk
 
 # --------------------------------------------------------------------------- #
 # inbox action allow-list
@@ -49,6 +60,8 @@
 # act/actd.py: the no-requirement dispatch in ``process_inbox()`` plus the
 # ``_apply_decision()`` elif whitelist. Keep in sync with actd; anything not in
 # here is rejected with 400 before an inbox file is ever written.
+# vnext note: kept identical to the live tree; the v0.10.3 actd honors a subset
+# (see vnext-amendments.md §M4 基线差异) — extras no-op there until the ports land.
 ALLOWED_ACTIONS = frozenset({
     # requirement-level (actd._apply_decision elif chain + §37 set_title)
     "approve", "reject", "comment", "raise", "trash", "restore", "pin",
@@ -91,6 +104,8 @@
 _PORT_FALLBACKS = 10  # try _DEFAULT_PORT .. _DEFAULT_PORT+9, then an ephemeral port
 
 # webui/ source assets live at the repo root next to act/ (NOT under state).
+# vnext note: the static front end is NOT ported yet — "/" answers 500 until it
+# lands; every /api/* endpoint is fully functional without it.
 _WEBUI_DIR = Path(__file__).resolve().parent.parent / "webui"
 
 # Static allow-list: request path -> (filename on disk, content-type). No path
@@ -100,7 +115,13 @@
     "/style.css": ("style.css", "text/css; charset=utf-8"),
 }
 
+# W18 降级通知（响应字段 ``notice``，add-only）：远端提交方立即得知直跑被
+# 降级成提案——卡片照常进 triage，闸门不吞任务也不撒谎说「已开跑」。
+_RUN_DOWNGRADE_NOTICE = ("direct run is disabled for remote capture "
+                         "(remote.allow_direct_run=false); "
+                         "saved as a proposal instead")
 
+
 def _iso_now() -> str:
     return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
 
@@ -161,6 +182,11 @@
         rec.setdefault("comment", None)
     rec["action"] = action
     rec["ts"] = _iso_now()
+    # ingress 落款（vnext-amendments T-28，add-only）：本面是网络远程 ingress，
+    # 一律 via:"remote"——客户端不可 spoof（via 不在 _INBOX_KEYS 白名单，这里
+    # 无条件盖章）。actd 据此把 capture 落 remote_capture 通道（PROPOSED，
+    # 永不自动派发）、comment 只记录不 steer。
+    rec["via"] = "remote"
 
     # capture keeps the Mac app's ``capture-`` filename prefix (§10/§15); every
     # other action gets a plain uuid name.
@@ -380,6 +406,16 @@
         if mode is not None and (action != "capture" or mode != "run"):
             self._json(400, {"error": "mode is only capture mode:\"run\""})
             return
+        # W18 远程直跑闸门（vnext-amendments §W18）：webui 是网络 ingress——
+        # direct-run 跳过人审预览（§34），默认不许从这里进来。闸门关着时
+        # **降级不报错**：去掉 ``mode`` 字段按普通 propose capture 落 inbox
+        # （提案照常进 triage），响应带 add-only ``notice`` 告知提交方。
+        # fail-closed：config 读不了/字段缺失 = 闸门关。
+        notice: Optional[str] = None
+        if action == "capture" and mode == "run" \
+                and not risk.remote_direct_run_allowed():
+            payload = {k: v for k, v in payload.items() if k != "mode"}
+            notice = _RUN_DOWNGRADE_NOTICE
         # §39.2: answer_input's text is bounded 1..4000 (code points) — reject
         # here with a 400 so an oversize/empty answer never reaches the inbox
         # (actd would archive-and-noop it, but the API caller deserves the
@@ -397,7 +433,10 @@
             self.log_error("inbox write failed: %s", e)
             self._json(500, {"error": "internal error"})
             return
-        self._json(200, {"ok": True, "file": name})
+        resp = {"ok": True, "file": name}
+        if notice:
+            resp["notice"] = notice
+        self._json(200, resp)
 
     # quieter, single-line logging to stderr (default BaseHTTPRequestHandler is
     # noisy); keep it so a curl proof still shows requests.
```

## 碰撞 2：`scripts/demo_seed.py`（保留 v0.47；wire delta 待重放）

wire 版 = v0.47 同源体 + v-next add-only 演示字段：`origin_trust`（信任矩阵 hand/external/缺席）、`effective_tier`（external 提级 T2，只升不降）、`queued_reason`（结构化排队原因，`waiting_card` 必带 `blocking_id`）、`steers`（捎话回执台账）、新增 `steer` scene，以及对应的 validator 词表（与 `act/lib/risk.py`、`act/lib/store2/schema.sql` CHECK、`web/src/steer.ts` 对齐）。porter 请把这些 add-only 字段重放到 v0.47 的 `scripts/demo_seed.py` 上——v0.47 版在 wire fork 之后可能又演进过，重放时以字段语义为准而不是行号。

```diff
--- /Volumes/Storage/Server/Projects/.worktree/zelin-ai-assistant/wt-vnext-047/scripts/demo_seed.py	2026-08-31 01:19:17
+++ /Volumes/Storage/Server/Projects/.worktree/zelin-ai-assistant/wt-vnext-wire/scripts/demo_seed.py	2026-08-30 19:58:34
@@ -20,6 +20,25 @@
   generated_at / trashed_at are ISO-8601 strings;
 - queued running items carry NO session_id/copy_cmd keys (no session yet).
 
+v-next add-only fields (信任矩阵/排队原因/捎话；wire 真源 =
+docs/design/vnext-amendments.md 的 ratification-ready 草案 + 已落仓实现——
+``act/lib/risk.py`` 词表、``act/lib/store2/schema.sql`` CHECK 集合、
+``web/src/steer.ts`` 解析器；出入以它们为准)：
+
+- ``origin_trust``（可选 string，``hand``/``external``）：信任矩阵档位——
+  ``hand`` = owner 亲笔（quick capture / 直跑框 / Slack self-DM），可
+  auto-dispatch；``external`` = 外部渠道铸卡，永不自动派发且强制 plan
+  展开（W17）；**缺席** = AI 提案/会议音频等中间档（要人批、不提级）；
+- ``effective_tier``（可选 string）：审批时生效档位——external 卡提到 T2，
+  **只升不降**（低于声明 tier 视为违约；声明 ``tier`` 字段永不改写）；
+- ``queued_reason``（可选，仅 queued 行）：结构化排队原因
+  ``{kind, detail?, blocking_id?}``——``waiting_card`` 必带 ``blocking_id``
+  （被等卡 id）；过渡期也接受纯字符串形（web/src/steer.ts 双兼容）；
+- ``steers``（可选 list，运行行）：owner 捎话（steer）回执台账，每条
+  ``{text, ts, status, delivered_at}``——``ts`` 为 ISO 字符串（带时间戳的
+  dedup key，重复文本合法），status=delivered 必带 ISO delivered_at，
+  其余状态必须为 null（诚实投递状态，绝不假装送达）。
+
 All names, repos, quotes and drafts below are fictional (example-bench,
 inkweld, alex.doe, sam.rivera…) — never real coworker or company data.
 
@@ -33,13 +52,22 @@
 import sys
 from pathlib import Path
 
-SCENES = ("captured", "initial", "approved", "running", "review", "done")
+SCENES = ("captured", "initial", "approved", "running", "steer", "review", "done")
 
 HERO_ID = "R-101"  # the card the --scene flag walks through the pipeline
 
 HOME = "~/Projects/zelin-ai-assistant"
 
+# v-next 词表（TODO(contract): 尚未入 CONTRACT，以 docs/design/vnext-amendments.md
+# 草案为准；validator 与 demo 数据共用这一份，改这里两边同步）。
+# origin_trust 与 act/lib/risk.py TRUST_* / store2 schema.sql CHECK 集合对齐
+# （信任矩阵更细的 self_dm/meeting/ai 档在 wire 上折叠为 hand/external/缺席）。
+ORIGIN_TRUST = ("hand", "external")
+TIER_RANK = {"T0": 0, "T1": 1, "T2": 2}
+QUEUED_REASON_KINDS = ("waiting_card", "waiting_budget")   # web/src/steer.ts 词表
+STEER_STATUSES = ("queued", "delivered", "dropped")
 
+
 def _iso(t: dt.datetime) -> str:
     return t.strftime("%Y-%m-%dT%H:%M:%SZ")
 
@@ -68,6 +96,8 @@
         "target_kind": "existing",
         "tier": "T1",
         "tier_hint": "一键可批",
+        # meeting/audio-born → 信任矩阵中间档：要人批但不提级——wire 上表现为
+        # 「无 origin_trust 字段」（词表只有 hand/external，缺席即中间档）
         "hardness": "hard",
         "deadline": deadline,
         "days_left": (dt.date.fromisoformat(deadline) - now.date()).days,
@@ -117,6 +147,9 @@
             "target_kind": "new",
             "tier": "T2",
             "tier_hint": "需文字确认",
+            # 外部渠道催生（sales/客户）→ external；声明已是 T2，提级无感
+            "origin_trust": "external",
+            "effective_tier": "T2",
             "hardness": "hard",
             "deadline": deadline_t2,
             "days_left": (dt.date.fromisoformat(deadline_t2) - now.date()).days,
@@ -155,7 +188,12 @@
             "target_name": "workbench",
             "target_kind": "existing",
             "tier": "T1",
-            "tier_hint": "一键可批",
+            "tier_hint": "外部来源提级，需文字确认",
+            # W17 展示位：gmail（外部渠道）铸卡 → effective_tier 提到 T2 且
+            # 强制 plan 展开（act/lib/risk.py effective_tier）；声明 tier 保持
+            # T1 不被改写（add-only，不动老字段语义）
+            "origin_trust": "external",
+            "effective_tier": "T2",
             "hardness": "soft",
             "deadline": _date(now, 3),
             "days_left": 3,
@@ -208,6 +246,8 @@
             "agent_name": "fix flaky e2e retries",
             "cwd": "~/Projects/example-bench",
             "state": "working",
+            # 手打卡（quick capture 直跑框）→ 信任矩阵 auto-dispatch 档
+            "origin_trust": "hand",
             "started_at": e - 1500,
             "summary": "e2e 套件里 3 个用例偶发超时，加统一的 retry + 诊断日志。",
             "plan": [
@@ -231,6 +271,11 @@
             "dod": ["新人照 README 十分钟内跑起来"],
             "delivery_mode": "repo",
             "dispatch_error": None,
+            # Slack self-DM 铸卡 = owner 亲笔 → 盖 hand（auto-dispatch 档）；
+            # 今日预算耗尽先排队，UI 由 kind 渲染「排队中 · 等预算」chip
+            # （结构化原因走闭集 kind，非自由文本——web/src/steer.ts 词表）
+            "origin_trust": "hand",
+            "queued_reason": {"kind": "waiting_budget"},
         },
         {
             "id": "R-107",
@@ -241,6 +286,7 @@
             "agent_name": "dataset v2 loader shim",
             "cwd": "~/Projects/example-bench",
             "state": "working",
+            "origin_trust": "hand",
             "started_at": e - 7200,
             "summary": "数据集 v2 换了 schema，加兼容层让老评测脚本不用改。",
             "plan": ["对比 v1/v2 schema 差异", "写字段映射兼容层", "老脚本回归全过"],
@@ -468,6 +514,9 @@
         "dod": h["dod"],
         "delivery_mode": "repo",
         "dispatch_error": None,
+        # 同仓 R-105 还在跑 → 排队等它；UI chip「排队中 · 等 R-105」
+        # （meeting-born 卡无 origin_trust 字段——中间档，人批后照常排队派发）
+        "queued_reason": {"kind": "waiting_card", "blocking_id": "R-105"},
     }
 
 
@@ -492,6 +541,33 @@
         "delivery_mode": "repo",
         "last_error": None,
     }
+
+
+def _hero_steer(now: dt.datetime) -> dict:
+    """scene=steer——R-101 executing 途中 owner 在卡上留了两条捎话（steer）：
+    第一条已经过 §44.3 送达点注入会话（delivered，带 ISO delivered_at），
+    第二条还在等安全窗口（queued，delivered_at 必须为 null）——投递状态诚实
+    可见，绝不假装已送达。ts 是 ISO 字符串（带时间戳的 dedup key：同文重申
+    是新指令，web/src/steer.ts 只认 string ts）。"""
+    card = _hero_running(now)
+    e = _epoch(now)
+    card["started_at"] = e - 900
+    card["dispatched_at"] = e - 960
+    card["steers"] = [
+        {
+            "text": "导出格式优先 markdown，png 可以放到后续 PR",
+            "ts": _iso(now - dt.timedelta(seconds=600)),
+            "status": "delivered",
+            "delivered_at": _iso(now - dt.timedelta(seconds=540)),
+        },
+        {
+            "text": "报告文件名里带上日期，方便归档",
+            "ts": _iso(now - dt.timedelta(seconds=60)),
+            "status": "queued",
+            "delivered_at": None,
+        },
+    ]
+    return card
 
 
 def _hero_review(now: dt.datetime) -> dict:
@@ -559,6 +635,8 @@
         running = [_hero_queued(now)] + running
     elif scene == "running":
         running = [_hero_running(now)] + running
+    elif scene == "steer":
+        running = [_hero_steer(now)] + running
     elif scene == "review":
         review = [_hero_review(now)] + review
     elif scene == "done":
@@ -621,8 +699,88 @@
     for k in keys:
         if not isinstance(item.get(k), str) or not item[k]:
             problems.append(f"{where}.{k}: required non-empty string")
+
+
+def _check_origin_trust(problems: list, where: str, item: dict) -> None:
+    # 可选字段（老 dashboard 没有）；一旦出现必须落在枚举内
+    v = item.get("origin_trust")
+    if v is not None and v not in ORIGIN_TRUST:
+        problems.append(f"{where}.origin_trust: must be one of {ORIGIN_TRUST}")
 
 
+def _check_effective_tier(problems: list, where: str, item: dict) -> None:
+    et = item.get("effective_tier")
+    if et is None:
+        return
+    if et not in TIER_RANK:
+        problems.append(f"{where}.effective_tier: must be one of "
+                        f"{tuple(TIER_RANK)}")
+        return
+    tier = item.get("tier")
+    if tier in TIER_RANK and TIER_RANK[et] < TIER_RANK[tier]:
+        problems.append(f"{where}.effective_tier={et} below tier={tier} — "
+                        f"W17 escalation is one-way (提级不降级)")
+
+
+def _check_queued_reason(problems: list, where: str, item: dict) -> None:
+    qr = item.get("queued_reason")
+    if qr is None:
+        return
+    if item.get("state") != "queued":
+        problems.append(f"{where}.queued_reason: only queued items may carry it")
+    if isinstance(qr, str):
+        # 过渡期纯字符串形（web/src/steer.ts 双兼容）——非空即可
+        if not qr.strip():
+            problems.append(f"{where}.queued_reason: string form must be "
+                            f"non-empty")
+        return
+    if not isinstance(qr, dict):
+        problems.append(f"{where}.queued_reason: must be an object "
+                        f"{{kind, detail?, blocking_id?}} or a string")
+        return
+    if qr.get("kind") not in QUEUED_REASON_KINDS:
+        problems.append(f"{where}.queued_reason.kind: must be one of "
+                        f"{QUEUED_REASON_KINDS} (closed list — UI 由 kind 渲染)")
+    for k in ("detail", "blocking_id"):
+        if qr.get(k) is not None and not isinstance(qr[k], str):
+            problems.append(f"{where}.queued_reason.{k}: string or null")
+    if qr.get("kind") == "waiting_card" and not (
+            isinstance(qr.get("blocking_id"), str) and qr["blocking_id"]):
+        problems.append(f"{where}.queued_reason.blocking_id: waiting_card "
+                        f"must carry the blocking card id")
+
+
+def _check_steers(problems: list, where: str, item: dict) -> None:
+    notes = item.get("steers")
+    if notes is None:
+        return
+    if not isinstance(notes, list):
+        problems.append(f"{where}.steers: must be a list")
+        return
+    for j, n in enumerate(notes):
+        nw = f"{where}.steers[{j}]"
+        if not isinstance(n, dict):
+            problems.append(f"{nw}: not a dict")
+            continue
+        if not isinstance(n.get("text"), str) or not n["text"]:
+            problems.append(f"{nw}.text: required non-empty string")
+        if not isinstance(n.get("ts"), str) or not n["ts"]:
+            problems.append(f"{nw}.ts: required ISO string — the "
+                            f"timestamp-bearing dedup key (verbatim repeats "
+                            f"are legitimate steers; web parser drops rows "
+                            f"without a string ts)")
+        status = n.get("status")
+        if status not in STEER_STATUSES:
+            problems.append(f"{nw}.status: must be one of {STEER_STATUSES}")
+        elif status == "delivered":
+            if not isinstance(n.get("delivered_at"), str) or not n["delivered_at"]:
+                problems.append(f"{nw}.delivered_at: delivered notes must "
+                                f"carry an ISO string (honest status)")
+        elif n.get("delivered_at") is not None:
+            problems.append(f"{nw}.delivered_at: must be null unless "
+                            f"status=delivered (honest status)")
+
+
 def validate(dash: dict) -> list[str]:
     problems: list[str] = []
     if not isinstance(dash, dict):
@@ -654,6 +812,8 @@
             if not isinstance(c.get(k), list):
                 problems.append(f"{w}.{k}: required list")
         _check_sources(problems, w, c.get("sources") or [])
+        _check_origin_trust(problems, w, c)
+        _check_effective_tier(problems, w, c)
         if c.get("cost_usd") is not None and not isinstance(c["cost_usd"], (int, float)):
             problems.append(f"{w}.cost_usd: number or null")
 
@@ -662,6 +822,9 @@
             w = f"{sec}[{i}]"
             _check_str(problems, w, t, "id", "name", "state")
             _check_epoch(problems, w, t, "started_at", "dispatched_at", "accepted_at")
+            _check_origin_trust(problems, w, t)
+            _check_queued_reason(problems, w, t)
+            _check_steers(problems, w, t)
             if t.get("state") == "queued":
                 for k in ("session_id", "copy_cmd", "short_id"):
                     if k in t:
@@ -679,6 +842,7 @@
         if not isinstance(r.get("dod"), list):
             problems.append(f"{w}.dod: required list")
         _check_sources(problems, w, r.get("sources") or [])
+        _check_origin_trust(problems, w, r)
         _check_epoch(problems, w, r, "dispatched_at", "review_at")
         if r.get("delivery_mode") not in ("chat", "repo"):
             problems.append(f"{w}.delivery_mode: must be 'chat' or 'repo'")
```

## P2 移植台账：quick_capture W1 + config W1.c/W18（已落地）

`act/lib/quick_capture.py`、`act/lib/config.py`、`config.example.yaml` 的 wire delta 已语义重放到 v0.47 文件体上（非逐字 hunk）：`_inventory_reqs` 反转配额时保留了 v0.47 的 ARCHIVED 排除、`State.MERGED` closed 判定与 §37/§38 display-corpus 管线；config 侧用 v0.47 的 `_dict_or/_int_or/_bool_or` 宽容解析器（wire 的裸 `int()/bool()` 未照搬）；`remote_allow_direct_run` 未进 `_OVERRIDE_FIELDS`（§W18 fail-closed，表尾留了守卫注释）。wire 注释里的 v0.10.3 基线 caveat（"尚无 thread_key 字段"）未带入——v0.47 有 `registry.derive_thread_key`，W1.b 兜底在本树是真实存在的机制。

**旧判例显式修法（非静默改测试；vnext-amendments §W1，owner 已拍板）** — `tests/test_card_lifecycle.py`：

- 判例 9 `test_disabled_by_default`（default off / no attr -> off）被 §W1.c 修法拆成两半：默认值半句由 `test_inventory_quota.py::ArchiveAfterDaysDefaultTestCase` 钉 30；保留半句改钉 `test_zero_disables`（0 仍关闭）+ `test_missing_attr_fails_off`（缺字段 cfg 照旧 fail-off）。
- 判例 13 `test_delivered_pinned_even_past_cap`（v0.20.0 pinning：delivered 硬钉进窗）正是 §W1.a 要修的病根，同场景（65 open + 1 delivered）改钉反转后的行为：`test_open_cards_never_dropped_delivered_yields`。

**遗留 TODO（actd porter）**：`act/actd.py:2292` `archive_stale` docstring 与 `act/actd.py:3232` 调用点注释仍写 "DEFAULT OFF / defaults to 0"——W1.c 后已失实（代码路径 `getattr(cfg, "archive_after_days", 0)` 无需改动，默认 30 由 Config 字段供给）。actd 是别的 porter 的 M-path，此处只记账不代改。
