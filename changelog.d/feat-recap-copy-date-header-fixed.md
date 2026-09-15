type: fixed
- **一条会过期的判例（`tests/test_recap_generate_request.py`，§63.8）**：「`requested_at` 在起子进程之前取」那条判例把戳写成字面量 `2026-09-14T12:00:00Z`，而 `recap_requests.record` 写台账时按 `TTL_S`（24 h）剪旧条——于是这条判例在 2026-09-15T12:00Z 一到就开始 `KeyError`，此后每个 PR 的 CI 都红，跟改了什么无关。戳改成按同一格式现取真 now（本例钉的是「mock 的戳被逐字记下」，与它是哪一秒无关）。本 PR 撞上它，顺手修了。
