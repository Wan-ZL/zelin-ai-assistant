#!/usr/bin/env python3
"""test-ui skill · `--against` 解析：把参照（REFERENCE）的七种写法变成一条 side 记录，
每个传感器（STRUCTURE / TOKENS / VISUAL）各带一个仪器模式（runtime | source | frozen | na）。

法典指针：docs/CONTRACT.md §58（只读项目配置）、§UI-parity（parity 契约：`native` 别名 =
ui/parity/native-inventory.json + ui/tokens/native-tokens.json，冻结源；skill 只读）。
设计 = docs/design/vnext2-plan.md R2.8 / D14；SKILL.md「Reference — --against」。

写法：
  design-system        项目 tokens + references/rules/*，无第二份清单
  <alias>              ui/parity/config.json [references.<alias>]；本 repo 缺配置时内置 `native`
  git:<ref>            `git worktree add --detach` 到 <repo>/.test-ui/cache/ref-<sha>/（永不碰活树）
  dir:<path>           另一实现的源码目录或已存产物目录
  url:<http…>          只 runtime；VISUAL 拒绝（不是 skill 自己种的 seed）
  app:<argv>           起进程 → 等 ready → 当 url
  inventory:<file>     冻结清单文件
解析不出 → ReferenceError（run_ui 转 exit 2 并列候选）。marker 探针只打 127.0.0.1。
判例：tests/test_skill_test_ui_reference.py（FakeRunner，零子进程）。
"""

import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ladder_common_vendored as lc  # noqa: E402
import testui_common as tc  # noqa: E402

CONFIG_REL = os.path.join("ui", "parity", "config.json")
NATIVE_INVENTORY_REL = os.path.join("ui", "parity", "native-inventory.json")
NATIVE_TOKENS_REL = os.path.join("ui", "tokens", "native-tokens.json")
KINDS = ("design-system", "alias", "git", "dir", "url", "app", "inventory")
_PREFIX_RE = re.compile(r"^(git|dir|url|app|inventory):(.+)$", re.S)


class ReferenceError(ValueError):
    """`--against` 解析不出或指向的文件读不到 → exit 2。"""


# --------------------------------------------------------------------------- #
# 项目配置（ui/parity/config.json，只读；缺席 = 内置 native 别名 + 空配置）
# --------------------------------------------------------------------------- #

def _builtin_native(repo):
    inv = os.path.join(repo, NATIVE_INVENTORY_REL)
    if not os.path.exists(inv):
        return {}
    return {"native": {"inventory": NATIVE_INVENTORY_REL.replace(os.sep, "/"),
                       "tokens": NATIVE_TOKENS_REL.replace(os.sep, "/"), "goldens": None,
                       "produced_by": ["scripts/ui/extract_native_inventory.py",
                                       "scripts/ui/extract_native_tokens.py"],
                       "mode": "frozen", "stack": "swiftui"}}


def load_config(repo):
    """→ {config, source}；坏 JSON 抛 ReferenceError（配置坏了必须停，不猜）。"""
    path = os.path.join(repo, CONFIG_REL)
    if not os.path.exists(path):
        return {"references": _builtin_native(repo)}, None
    try:
        cfg = tc.read_json(path)
    except (OSError, ValueError) as exc:
        raise ReferenceError("%s unreadable: %s" % (CONFIG_REL, exc))
    refs = dict(_builtin_native(repo))
    refs.update(cfg.get("references") or {})
    cfg["references"] = refs
    return cfg, CONFIG_REL.replace(os.sep, "/")


def candidates(config):
    """ASK / exit 2 时给人看的可选参照列表。"""
    return sorted(config.get("references") or {}) + ["design-system", "git:origin/main", "dir:<path>",
                                                     "url:<http://127.0.0.1:port/>", "inventory:<file>"]


def default_against(config, runner, repo):
    """省略 --against 时：[references] 第一个别名 → git:origin/main → design-system。"""
    aliases = sorted(config.get("references") or {})
    if aliases:
        return aliases[0]
    if lc.resolve_base(runner, repo, "origin/main"):
        return "git:origin/main"
    return "design-system"


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #

def parse_against(text, config):
    """字符串 → {kind, locator}；别名要在配置里；其它前缀原样。"""
    value = (text or "").strip()
    if value == "design-system":
        return {"kind": "design-system", "locator": value}
    match = _PREFIX_RE.match(value)
    if match:
        return {"kind": match.group(1), "locator": match.group(2).strip()}
    if value in (config.get("references") or {}):
        return {"kind": "alias", "locator": value}
    raise ReferenceError("cannot resolve --against %r; candidates: %s" % (value, ", ".join(candidates(config))))


def _modes(structure, tokens, visual):
    return {"structure": structure, "tokens": tokens, "visual": visual}


def _file_resolved(repo, rel):
    path = os.path.join(repo, rel) if rel and not os.path.isabs(rel) else rel
    if not path or not os.path.exists(path):
        return None, None
    return path, "sha256:%s" % tc.sha256_file(path)


def _side(kind, locator, **extra):
    side = {"role": "reference", "kind": kind, "locator": locator, "resolved": None, "stack": None,
            "mode": _modes("na", "na", "na"), "inventory": None, "tokens": None, "goldens": None,
            "launch": None, "produced_by": [], "hint": None}
    side.update(extra)
    return side


def _regen_hint(entry):
    producers = entry.get("produced_by") or []
    return "regenerate with: %s" % " ; ".join("python3 %s --out <report>/inventory/" % p for p in producers)


def _mode_if(present, mode):
    return mode if present else "na"


def _alias_side(repo, locator, config):
    entry = config["references"][locator]
    inv, inv_sha = _file_resolved(repo, entry.get("inventory"))
    tok, _tok_sha = _file_resolved(repo, entry.get("tokens"))
    goldens = os.path.join(repo, entry["goldens"]) if entry.get("goldens") else None
    mode = entry.get("mode", "frozen")
    return _side("alias", locator, resolved=inv_sha, stack=entry.get("stack"),
                 mode=_modes(_mode_if(inv, mode), _mode_if(tok, mode), _mode_if(goldens, "frozen")),
                 inventory=inv, tokens=tok, goldens=goldens, produced_by=list(entry.get("produced_by") or []),
                 hint=None if inv else _regen_hint(entry))


def _inventory_side(locator):
    path, sha = _file_resolved(None, locator)
    if path is None:
        raise ReferenceError("inventory file not found: %s" % locator)
    return _side("inventory", locator, resolved=sha, inventory=path, mode=_modes("frozen", "na", "na"))


def _dir_side(locator):
    path = os.path.abspath(locator)
    if not os.path.isdir(path):
        raise ReferenceError("dir not found: %s" % locator)
    return _side("dir", locator, resolved="path:%s" % path, stack="unknown", mode=_modes("source", "source", "na"),
                 inventory=None, launch=None)


def _design_system_side(repo, config):
    goldens = (config.get("goldens") or {}).get("dir")
    goldens_dir = os.path.join(repo, goldens) if goldens else None
    return _side("design-system", "design-system", resolved="path:%s" % repo, stack="tokens",
                 mode=_modes("na", "source", "frozen" if goldens_dir else "na"), goldens=goldens_dir)


def _url_side(locator):
    if not locator.startswith(("http://", "https://")):
        raise ReferenceError("url reference must start with http:// or https://: %s" % locator)
    return _side("url", locator, resolved="url:%s" % locator, stack="web-dom", mode=_modes("runtime", "runtime", "na"),
                 hint="VISUAL refused: the skill did not seed this URL (demo marker cannot be trusted)")


def _app_side(locator):
    argv = shlex.split(locator)
    if not argv:
        raise ReferenceError("app reference needs an argv")
    return _side("app", locator, resolved="argv:%s" % json.dumps(argv), stack="web-dom",
                 mode=_modes("runtime", "runtime", "runtime"), launch={"argv": argv})


def resolve_side(repo, against, config, runner=lc.run_command, cache_dir=None):
    """`--against` → side 记录（reference 角色）。git: 需要 runner（建 detached worktree）。"""
    parsed = parse_against(against, config)
    kind, locator = parsed["kind"], parsed["locator"]
    builders = {"alias": lambda: _alias_side(repo, locator, config), "inventory": lambda: _inventory_side(locator),
                "dir": lambda: _dir_side(locator), "design-system": lambda: _design_system_side(repo, config),
                "url": lambda: _url_side(locator), "app": lambda: _app_side(locator),
                "git": lambda: git_side(repo, locator, runner, cache_dir)}
    return builders[kind]()


# --------------------------------------------------------------------------- #
# git:<ref> —— detached worktree 进 <repo>/.test-ui/cache/ref-<sha>/（永不碰活树）
# --------------------------------------------------------------------------- #

def git_side(repo, ref, runner, cache_dir=None):
    """只解析 sha 并记下 worktree 落点；真正 `git worktree add` 留给 ensure_worktree（探测无副作用）。"""
    sha_lines = lc.git_lines(runner, repo, ["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
    if not sha_lines:
        raise ReferenceError("git ref not found: %s" % ref)
    sha = sha_lines[0]
    cache = cache_dir or os.path.join(repo, ".test-ui", "cache")
    return _side("git", "git:%s" % ref, resolved="sha:%s" % sha, stack="unknown", mode=_modes("source", "source", "na"),
                 launch=None, hint="runtime-vs-runtime only at tier 4 (reference_runtime) when its launch recipe works",
                 worktree=os.path.join(cache, "ref-%s" % sha[:12]), sha=sha, worktree_ready=False)


def ensure_worktree(repo, side, runner=lc.run_command):
    """需要读参照源码时才建 detached worktree（永不碰活树）；已在则复用。失败抛 ReferenceError。"""
    path = side.get("worktree")
    if not path:
        return None
    if not os.path.isdir(path):
        res = runner(["git", "worktree", "add", "--detach", path, side["sha"]], cwd=repo, timeout=300)
        if not res.ok:
            raise ReferenceError("git worktree add failed: %s" % res.stderr.strip()[:200])
    side["worktree_ready"] = True
    return path


def remove_git_side(repo, side, runner=lc.run_command):
    """跑完清 worktree（`git worktree remove --force`）；失败只返回 False，不抛。"""
    path = side.get("worktree")
    if not path or not side.get("worktree_ready"):
        return False
    return runner(["git", "worktree", "remove", "--force", path], cwd=repo, timeout=120).ok


# --------------------------------------------------------------------------- #
# subject 侧 + demo marker 探针（只打 127.0.0.1）
# --------------------------------------------------------------------------- #

def subject_side(repo, stack, runtime_ok, launch, commit=None, dirty=False):
    """被测侧：runtime 可用（node+playwright+launch 配方）→ runtime；否则 source。"""
    mode = "runtime" if runtime_ok else "source"
    return {"role": "subject", "kind": "dir", "locator": repo, "resolved": "sha:%s" % (commit or "unknown"),
            "stack": stack, "mode": _modes(mode, mode, "runtime" if runtime_ok else "na"),
            "launch": launch, "commit": commit, "dirty": dirty,
            "seed": {"recipe": (launch or {}).get("seed"), "seeded_by_skill": False,
                     "marker": {"how": (launch or {}).get("marker"), "seen": None}}}


def _loopback(url):
    host = re.sub(r"^https?://", "", url).split("/", 1)[0].split(":", 1)[0]
    return host in ("127.0.0.1", "localhost", "[::1]")


def _dig(obj, dotted):
    node = obj
    for part in [p for p in dotted.split(".") if p]:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def probe_marker(base_url, marker, fetch=None):
    """marker = {"path": "/api/health", "expr": ".demo == true"} → seen bool；非回环地址 = False。
    fetch(url) -> text 可注入（测试永不出网）。"""
    if not marker or not _loopback(base_url):
        return False
    fetch = fetch or _fetch_loopback
    try:
        body = json.loads(fetch(base_url.rstrip("/") + marker.get("path", "/api/health")))
    except (OSError, ValueError):
        return False
    key, _sep, expected = (marker.get("expr") or ".demo == true").partition("==")
    value = _dig(body, key.strip())
    return json.dumps(value) == expected.strip()


def _fetch_loopback(url):
    """回环 GET：绕过一切代理设置（macOS 系统代理 / http_proxy 会把 127.0.0.1 也送去代理）。"""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=10) as resp:  # noqa: S310 — loopback only, checked above
        return resp.read().decode("utf-8", "replace")
