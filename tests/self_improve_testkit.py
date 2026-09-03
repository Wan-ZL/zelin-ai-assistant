"""§64 self_improve 判例共用的假 gh runner + 卡片/PR 工厂（零子进程、零网络）。

``FakeGh`` 按 argv 前缀路由：`pr view N` / `pr list --head B` / `pr checks N` /
`pr edit N` / `label create` / `api user` / `api repos/.../pulls/N/comments`；
每次调用记进 ``calls``（判例钉 argv 形状）。未登记的 PR = 404 形（rc 1，空
stdout），与真 gh 一致。
"""
from __future__ import annotations

import json

from act.lib import config
from act.lib.registry import Requirement, State

SI_SRC = [{"who": "loop", "channel": "self_improve", "date": "2026-09-02",
           "ref": "proposal:abc", "quote": "让 doctor 多一行"}]


def lane_card(req_id="P-7", status=State.EXECUTING.value, sources=None,
              execution=None, **over):
    base = dict(id=req_id, title="lane 测试卡", type="self-improvement", tier="T1",
                status=status, sources=list(sources or SI_SRC),
                target_repo=str(config.HOME), target_kind="existing",
                delivery_mode="repo", work_id="R-900",
                execution=execution if execution is not None else {"session_id": "aaaa1111"})
    base.update(over)
    return Requirement(**base)


def pr_doc(number=123, *, branch="ai/self-improve/R-900", draft=True, state="OPEN",
           base="main", files=("act/doctor.py",), sha="deadbeef", url=None):
    return {
        "number": number, "url": url or f"https://github.com/o/r/pull/{number}",
        "state": state, "isDraft": draft, "baseRefName": base,
        "headRefName": branch, "headRefOid": sha,
        "files": [{"path": p, "additions": 1, "deletions": 0} for p in files],
        "mergedAt": "2026-09-02T12:00:00Z" if state == "MERGED" else None,
        "closedAt": "2026-09-02T12:00:00Z" if state in ("MERGED", "CLOSED") else None,
    }


class FakeGh:
    """可编程的 gh 替身。``prs`` = {number: pr_doc}；``comments`` / ``reviews`` /
    ``inline`` = {number: [...]}；``checks`` = {number: [{"name","bucket"}]}。"""

    def __init__(self, prs=None, *, login="Wan-ZL", comments=None, reviews=None,
                 inline=None, checks=None, label_rc=0, edit_rc=0):
        self.prs = dict(prs or {})
        self.login = login
        self.comments = dict(comments or {})
        self.reviews = dict(reviews or {})
        self.inline = dict(inline or {})
        self.checks = dict(checks or {})
        self.label_rc = label_rc
        self.edit_rc = edit_rc
        self.calls: list = []
        self.cwds: list = []

    # -- routing ------------------------------------------------------------ #
    def __call__(self, args, cwd):
        self.calls.append(list(args))
        self.cwds.append(cwd)
        head = tuple(args[:2])
        if head == ("pr", "view"):
            return self._pr_view(args)
        if head == ("pr", "list"):
            return self._pr_list(args)
        if head == ("pr", "checks"):
            return self._pr_checks(args)
        if head == ("pr", "edit"):
            return self.edit_rc, ""
        if head == ("label", "create"):
            return self.label_rc, ""
        if head == ("api", "user"):
            return 0, json.dumps({"login": self.login})
        if args[0] == "api" and args[1].endswith("/comments"):
            number = int(args[1].split("/")[-2])
            return 0, json.dumps(self.inline.get(number, []))
        return 1, ""

    def _pr_view(self, args):
        number = int(args[2])
        pr = self.prs.get(number)
        if pr is None:
            return 1, ""
        fields = args[args.index("--json") + 1].split(",")
        doc = {k: pr.get(k) for k in fields if k not in ("comments", "reviews")}
        if "comments" in fields:
            doc["comments"] = self.comments.get(number, [])
        if "reviews" in fields:
            doc["reviews"] = self.reviews.get(number, [])
        return 0, json.dumps(doc)

    def _pr_list(self, args):
        branch = args[args.index("--head") + 1]
        rows = [{"number": n, "state": p["state"]} for n, p in self.prs.items()
                if p.get("headRefName") == branch]
        return 0, json.dumps(rows)

    def _pr_checks(self, args):
        number = int(args[2])
        rows = self.checks.get(number, [])
        rc = 1 if any(r.get("bucket") == "fail" for r in rows) else 0
        return rc, json.dumps(rows)

    # -- assertions helpers -------------------------------------------------- #
    def argv_with(self, *prefix):
        return [c for c in self.calls if tuple(c[:len(prefix)]) == prefix]


def unavailable_gh(args, cwd):
    """gh 不可用形（rc=None）——default_gh 在 AIASSISTANT_GH=0 下的返回。"""
    return None, ""
