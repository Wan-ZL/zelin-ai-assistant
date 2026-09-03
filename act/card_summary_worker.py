"""card_summary_worker — 待验收卡摘要+评语的 detached 判官（CONTRACT §64；issue #128）。

CLI：``python -m act.card_summary_worker <card_id>``，由 actd 每 pass 的
``act.lib.card_summary.tick`` 起（silent_merge 判官同款两段式）。本进程对
registry **只读**、只回写自己的作业文件——落卡由 actd 写者线程的
``consume`` 完成（§0 单写者）。LLM 调用经 ``act/llm.py`` 单一边界
（``models.pipeline`` 旋钮，tool-less ``claude -p``，中性 cwd）。

作业指纹与卡当前指纹不一致（评的时候内容又变了）= failed「content changed」，
actd 下个 pass 按新指纹重派；卡不在了 = failed「card vanished」。任何异常都落
failed，绝不留 pending 悬挂（超时清扫是最后一道网）。
"""
from __future__ import annotations

import subprocess
import sys
from typing import Optional

from act import llm
from act.lib import card_summary, config, registry

CLAUDE_TIMEOUT = 180   # 一次 summarize+judge 的墙钟上限（秒）


def default_runner(prompt: str) -> subprocess.CompletedProcess:
    # §59 single LLM boundary: scrub + argv + --model live in act/llm.py.
    # No tools: a pure judgment over pre-gathered card material.
    return llm.run(prompt, mode=llm.MODE_PIPELINE, timeout=CLAUDE_TIMEOUT,
                   cwd=config.headless_cwd())


def _run_job(card_id: str, job: dict, runner: card_summary.Runner) -> None:
    req = registry.load(card_id)
    if req is None:
        card_summary.finish(card_id, "failed", error="card vanished")
        return
    if card_summary.source_hash(req) != str(job.get("source_hash") or ""):
        card_summary.finish(card_id, "failed", error="content changed")
        return
    result = card_summary.assess(req, runner)
    if result is None:
        card_summary.finish(card_id, "failed", error="assess failed (exit/parse)")
        return
    card_summary.finish(card_id, "done", result=result)


def run(card_id: str, runner: card_summary.Runner) -> int:
    """一张卡的判官流程；非 pending 作业直接退出（重复起跑 / 已被清扫）。"""
    job = card_summary.load_job(card_id)
    if not job or job.get("status") != "pending":
        return 0
    try:
        _run_job(card_id, job, runner)
    except Exception as e:  # noqa: BLE001 - 绝不留 pending 悬挂
        card_summary.finish(card_id, "failed",
                            error=f"worker crashed: {e}"[:card_summary.ERROR_MAX_CHARS])
    return 0


def main(argv: Optional[list] = None,
         runner: Optional[card_summary.Runner] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 2
    return run(str(args[0]), runner or default_runner)


if __name__ == "__main__":
    raise SystemExit(main())
