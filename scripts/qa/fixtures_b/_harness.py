"""B 档 fixture 的共用夹具（CONTRACT §58 QA 闸门；沙箱纪律同 tests/__init__.py）。

提供三件事，别的什么都不做：

1. :func:`sandbox` —— 一次性临时 home（``/tmp/zaa-cov-<slug>-XXXX``）。把
   ``act.*`` / ``server.*`` 里那些**模块级**路径常量整体重指到它，退出时逐一
   还原并删目录。单跑与 in-process（tests 套件里）两条路径共用同一段代码，所以
   一个场景在两种跑法下看到的世界逐字相同。
2. :func:`run` —— 场景外壳：跑 ``fn(home) -> (ok, evidence)``，把被测代码的
   stdout 全部改道 stderr（**stdout 只留最后那一行证据**），异常一律收成
   FAIL 而不是 traceback 崩出（同宪法第 11 条的脾气）。
3. 小工具：:func:`fixture_text` / :func:`fixture_json` 读 ``tests/fixtures/``
   下的真形样本，:func:`proc` 造一个假 ``CompletedProcess``（注入缝用）。

纪律：绝不 spawn 真 ``claude``、绝不出网、绝不碰 live checkout 的 state/ 与
act/registry/ —— 一切写操作都发生在本模块给出的临时 home 里。
"""
from __future__ import annotations

import contextlib
import importlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"

# 模块级路径常量表：模块名 -> {属性: home 相对路径}。
# 真源是各模块自己的定义行（``X: Path = config.STATE_DIR / "…"``）——这里逐字
# 镜像它们；漏一个的后果是那个子系统写到上一个 home 里，tests/test_fixtures_b_smoke
# 会当场看见（两次跑同一个场景结果不一致）。
_PATH_ATTRS = {
    "act.lib.config": {
        "HOME": "",
        "STATE_DIR": "state",
        "REGISTRY_DIR": "act/registry",
        "INBOX_DIR": "state/inbox",
        "NOTIFY_QUEUE_DIR": "state/notify_queue",
        "FOLD_RECEIPTS_DIR": "state/fold_receipts",
        "DASHBOARD_PATH": "state/dashboard.json",
        "LOG_DIR": "state/logs",
        "CONFIG_PATH": "config.yaml",
        "CONFIG_EXAMPLE_PATH": "config.example.yaml",
        "SETTINGS_OVERRIDES_PATH": "state/settings_overrides.json",
    },
    "act.lib.registry": {"ARCHIVE_DIR": "act/registry/archive"},
    "act.lib.analytics": {"ANALYTICS_DIR": "state/analytics",
                          "EVENTS_PATH": "state/analytics/events.jsonl"},
    "act.lib.radar_health": {"HEALTH_PATH": "state/radar_health.json",
                             "_LOCK_PATH": "state/radar_health.lock"},
    "act.lib.silent_merge": {"SILENT_DIR": "state/silent_merge"},
    "act.lib.secrets": {"SECRETS_DIR": "config/secrets"},
    "act.lib.dashboard": {"MERGE_DIR": "state/merge"},
    "act.lib.feedback": {"FEEDBACK_DIR": "state/feedback"},
    "act.lib.heartbeat": {"HEARTBEAT_PATH": "state/actd.heartbeat"},
    "act.lib.search_index": {"INDEX_PATH": "state/search_index.json"},
    "act.radar_slack": {"MEDIA_DIR": "state/media"},
}


def _target(home: Path, rel: str) -> Path:
    return home if rel == "" else home / rel


class _Repoint:
    """把 :data:`_PATH_ATTRS` 指向 ``home``，``undo()`` 逐字还原。"""

    def __init__(self, home: Path):
        self.home = home
        self._saved: list = []

    def apply(self) -> None:
        for mod_name, attrs in _PATH_ATTRS.items():
            mod = importlib.import_module(mod_name)
            for attr, rel in attrs.items():
                self._saved.append((mod, attr, getattr(mod, attr)))
                setattr(mod, attr, _target(self.home, rel))

    def undo(self) -> None:
        for mod, attr, old in reversed(self._saved):
            setattr(mod, attr, old)
        self._saved = []


def _restore_env(key: str, old) -> None:
    if old is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = old


@contextlib.contextmanager
def env_patch(overrides: dict):
    """临时环境变量（退出逐字还原——缺席的键还原成缺席）。"""
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, old in saved.items():
            _restore_env(key, old)


def _reset_store() -> None:
    """registry 的进程内 Store 单例：换 home 前后各清一次（永不抛）。"""
    try:
        importlib.import_module("act.lib.registry").reset_store_cache()
    except Exception:                                      # noqa: BLE001
        pass


@contextlib.contextmanager
def sandbox(slug: str, env: dict = None):
    """一次性临时 home；``env`` 是本场景额外要设的环境变量（退出还原）。"""
    root = Path(tempfile.mkdtemp(prefix=f"zaa-cov-{slug}-", dir="/tmp"))
    home = root / "home"
    for rel in ("state/inbox", "state/logs", "act/registry", "config/secrets"):
        (home / rel).mkdir(parents=True, exist_ok=True)
    repoint = _Repoint(home)
    overrides = dict(env or {}, AIASSISTANT_HOME=str(home))
    try:
        with env_patch(overrides):
            repoint.apply()
            _reset_store()
            yield home
    finally:
        repoint.undo()
        _reset_store()
        shutil.rmtree(root, ignore_errors=True)


def _drop_boot_home() -> None:
    """单跑 bootstrap 建的那个空 home（见 __init__）——收尾一并删掉。"""
    pkg = sys.modules.get("scripts.qa.fixtures_b")
    boot = getattr(pkg, "BOOT_HOME", None) if pkg else None
    if boot:
        shutil.rmtree(boot, ignore_errors=True)


def run(fixture_id: str, fn: Callable, env: dict = None) -> int:
    """跑一个场景：stdout 只留最后一行证据，回 0（PASS）/ 1（FAIL）。"""
    slug = fixture_id.lower().replace(":", "-")
    buf = io.StringIO()
    try:
        with sandbox(slug, env=env) as home:
            with contextlib.redirect_stdout(buf):
                ok, evidence = fn(home)
    except Exception as exc:                               # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        ok, evidence = False, f"raised {type(exc).__name__}: {exc}"
    finally:
        _drop_boot_home()
    noise = buf.getvalue().strip()
    if noise:
        print(noise, file=sys.stderr)
    line = f"{fixture_id} {'PASS' if ok else 'FAIL'} {evidence}"
    print(line[:200].replace("\n", " "))
    return 0 if ok else 1


def fixture_text(rel: str) -> str:
    """``tests/fixtures/<rel>`` 的正文（真机 / 真形样本）。"""
    return (FIXTURE_DIR / rel).read_text(encoding="utf-8")


def fixture_json(rel: str):
    import json
    return json.loads(fixture_text(rel))


def proc(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    """假 ``CompletedProcess``——注入缝的返回形（绝不真起进程）。"""
    return subprocess.CompletedProcess(args=["fixture"], returncode=returncode,
                                       stdout=stdout, stderr="")


@contextlib.contextmanager
def patched(obj, attr: str, value):
    """临时换掉 ``obj.attr``（注入缝用；退出逐字还原——in-process 跑法的卫生）。"""
    old = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield value
    finally:
        setattr(obj, attr, old)


class FakeLLM:
    """一个注入件吃掉雷达一个 pass 的两次调用（抄 tests/test_radar_triage.py）。

    提取 prompt 拿 ``extraction``；三选一闸门的 prompt（认 ``入库把关`` 标记）
    拿 ``decision``。每条 prompt 都留底，给围栏断言用。绝不 spawn 真 claude。
    """

    def __init__(self, extraction=None, decision=None):
        import json
        self._json = json
        self.extraction = extraction if extraction is not None else []
        self.decision = decision
        self.calls: list = []
        self.triage_calls: list = []

    def __call__(self, prompt: str):
        self.calls.append(prompt)
        if "入库把关" in prompt:
            self.triage_calls.append(prompt)
            if self.decision is not None:
                return proc(self._json.dumps(self.decision, ensure_ascii=False))
        return proc(self._json.dumps(self.extraction, ensure_ascii=False))


def judge_runner():
    """§44.2 fold 判官的注入缝：恒判「不同」——本档场景钉的是闸门不是判官。"""
    return lambda prompt: proc('{"same_thing": false, "brief": "不同的事"}')


def card_facts(card) -> dict:
    """卡（或 None）→ ``{id, status, channel}``；缺席一律空串。

    判据行里因此不再出现 ``bool(card) and …`` 这种分支（复杂度门 §58 的算法按
    分支计数，一行一个 and 就够超线——事实取一次，断言只做等值比较）。
    """
    if card is None:
        return {"id": "none", "status": "", "channel": ""}
    first = (card.sources or [{}])[0] or {}
    return {"id": card.id, "status": card.status, "channel": first.get("channel", "")}


def check(conditions: list) -> "tuple[bool, str]":
    """``[(名字, 真值), …]`` -> (全过?, 第一条没过的名字 / 全过的摘要)。"""
    bad = [name for name, ok in conditions if not ok]
    return (not bad), ("failed=" + ",".join(bad) if bad else "checks=" + str(len(conditions)))
