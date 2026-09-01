#!/usr/bin/env python3
"""「Nightly mutation report」pinned issue 的幂等 create-or-update（CONTRACT §57.8）。

与 insights.yml 的 shell 版同一模式（精确标题匹配 open+closed 全集、绝不
开第二张、closed 先 reopen、pin 尽力而为）；Python 化的理由：更新逻辑要有
判例（tests/test_mutation_issue.py 注入假 gh runner，零网络）+ --dry-run。

调用方：.github/workflows/mutation-nightly.yml（GH_TOKEN 在 env 里，gh CLI
自己读）。本地手跑同样可用：
    python3 scripts/qa/mutation_issue.py --body-file .qa/mutation/report.md \
        --repo Wan-ZL/zelin-ai-assistant [--dry-run]

不接受任何不可信文本进 shell：gh 一律 argv 列表调用（无 shell=True），
issue body 是我们自己的报告文件。
"""
import argparse
import json
import os
import subprocess
import sys

DEFAULT_TITLE = "Nightly mutation report"


def _default_runner(args):
    """gh CLI → (returncode, stdout)。注入缝：测试换成假 runner。"""
    proc = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
    return proc.returncode, proc.stdout


def find_issue(title, repo, runner):
    """精确标题匹配（open+closed 全集）→ {number, state} | None。

    永不开第二张的关键就在 --state all：closed 的同名 issue 必须被找到并
    reopen，而不是再铸一张。
    """
    rc, out = runner(["issue", "list", "-R", repo, "--state", "all",
                      "--limit", "100", "--json", "number,title,state"])
    if rc != 0:
        raise RuntimeError("gh issue list failed")
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        raise RuntimeError(f"gh issue list: unparseable output {out[:200]!r}")
    for row in rows:
        if isinstance(row, dict) and row.get("title") == title:
            return {"number": row.get("number"), "state": row.get("state")}
    return None


def _pin_best_effort(number, repo, runner, log):
    """pin 走 GraphQL，token 可能没权限——失败只记一行，绝不让整轮变红。"""
    rc, out = runner(["api", f"repos/{repo}/issues/{number}"])
    if rc != 0:
        log(f"pin skipped: cannot read issue #{number}")
        return
    try:
        node_id = json.loads(out).get("node_id")
    except ValueError:
        node_id = None
    if not node_id:
        log(f"pin skipped: no node_id for issue #{number}")
        return
    rc, _out = runner([
        "api", "graphql",
        "-f", "query=mutation($id: ID!) { pinIssue(input: {issueId: $id}) "
              "{ issue { number } } }",
        "-f", f"id={node_id}"])
    log(f"pinned #{number}" if rc == 0 else
        f"pin failed for #{number} (already pinned, or token lacks access) — continuing")


def create_or_update(title, body_file, repo, runner, log=print, dry_run=False):
    """幂等投递：无 → create；closed → reopen+edit；open → edit。返回 0/1。"""
    if dry_run:
        log(f"[dry-run] would find issue titled {title!r} in {repo} (state all)")
        log(f"[dry-run] would create it if missing, reopen if closed, "
            f"then edit body from {body_file}")
        log("[dry-run] would pin it (best-effort)")
        return 0
    existing = find_issue(title, repo, runner)
    if existing is None:
        rc, out = runner(["issue", "create", "-R", repo,
                          "--title", title, "--body-file", body_file])
        if rc != 0:
            log("issue create failed")
            return 1
        number = out.strip().rsplit("/", 1)[-1]
        log(f"created issue #{number}")
    else:
        number = existing["number"]
        if existing.get("state") == "CLOSED":
            rc, _out = runner(["issue", "reopen", str(number), "-R", repo])
            if rc != 0:
                log(f"issue reopen failed for #{number}")
                return 1
            log(f"reopened issue #{number}")
        rc, _out = runner(["issue", "edit", str(number), "-R", repo,
                           "--body-file", body_file])
        if rc != 0:
            log(f"issue edit failed for #{number}")
            return 1
        log(f"updated issue #{number}")
    _pin_best_effort(number, repo, runner, log)
    return 0


def main(argv=None, runner=None, log=print):
    parser = argparse.ArgumentParser(
        description="create-or-update the pinned nightly mutation issue")
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印计划动作，不调用 gh（测试/演练用）")
    args = parser.parse_args(argv)
    if not args.repo:
        parser.error("--repo 或 GITHUB_REPOSITORY 必须有一个")
    if not os.path.isfile(args.body_file):
        log(f"no body file at {args.body_file} — nothing to post")
        return 0  # 报告缺席已由 mutate 那一步的红说明，这里不二次报警
    try:
        return create_or_update(args.title, args.body_file, args.repo,
                                runner or _default_runner, log=log,
                                dry_run=args.dry_run)
    except RuntimeError as exc:
        log(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
