"""server/ 测试公共夹具（G5）——本文件不含测试用例，只供 test_server_*.py 复用。

纪律（BUILD-CONTRACT §0.3 + §2.3）：
- 每个 TestCase 用独立 tmpdir 当 AIASSISTANT_HOME，数据一律由 live 树的
  ``scripts/demo_seed.py`` 种入（全虚构数据），绝不触碰生产 state/；
- 真 server：``server.app.make_server(port=0, home=..., ...)`` 起在随机端口，
  serve_forever 跑在 daemon 线程里，调用方 addCleanup 负责 shutdown；
- HTTP 客户端 = stdlib http.client，每个请求新建连接（避免 keep-alive 串台）。
"""
from __future__ import annotations

import importlib.util
import io
import json
import threading
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional

from tests import TMP_HOME  # noqa: F401 - 先落沙箱 env，防任何 act.* 触真库

from server import app as app_mod

# 测试期间静音访问日志（只改测试进程内的 Handler 类，不动源码）
app_mod.Handler.log_message = lambda self, fmt, *a: None  # type: ignore[method-assign]

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "inbox"

# demo_seed.py 以仓内副本为准（集成时随 server 测试一并落仓）。
_DEMO_SEED = REPO_ROOT / "scripts" / "demo_seed.py"
DEMO_SEED_PATH: Optional[Path] = _DEMO_SEED if _DEMO_SEED.is_file() else None

_demo_seed_module = None


def demo_seed():
    """按路径加载 demo_seed.py 模块（stdlib-only，进程内缓存一份）。"""
    global _demo_seed_module
    if _demo_seed_module is None:
        assert DEMO_SEED_PATH is not None, "demo_seed.py not found"
        spec = importlib.util.spec_from_file_location(
            "_zai_demo_seed", DEMO_SEED_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _demo_seed_module = mod
    return _demo_seed_module


SCENES = ("captured", "initial", "approved", "running", "review", "done")


def seed_scene(home: Path, scene: str) -> dict:
    """跑 demo_seed.main() 种指定场景（含其自带 validate 闸门），返回落盘 dict。"""
    rc = None
    with redirect_stdout(io.StringIO()):
        rc = demo_seed().main([str(home), "--scene", scene])
    assert rc == 0, f"demo_seed --scene {scene} failed (rc={rc})"
    return json.loads(dashboard_path(home).read_text(encoding="utf-8"))


def dashboard_path(home: Path) -> Path:
    return home / "state" / "dashboard.json"


def rewrite_board(home: Path, dash: dict) -> None:
    """原子改写 dashboard.json（tmp+replace，复刻 actd/demo_seed 写法）。"""
    p = dashboard_path(home)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(dash, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(p)


# --------------------------------------------------------------------------- #
# server 生命周期
# --------------------------------------------------------------------------- #
def start_server(case, home: Path, *, start_watcher: bool = False):
    """port 0 起真 server，注册 cleanup；返回 (httpd, port)。

    static_dir 指向一个不存在的目录——本组测试不覆盖 web/dist 静态面，
    避免对 A5 的 build 产物产生隐性依赖。
    """
    httpd = app_mod.make_server(port=0, home=home,
                                static_dir=home / "no-dist",
                                start_watcher=start_watcher)
    thread = threading.Thread(target=httpd.serve_forever,
                              kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()

    def _cleanup():
        httpd.shutdown()
        if httpd.watcher is not None:
            httpd.watcher.stop()
        httpd.server_close()
        thread.join(timeout=5)

    case.addCleanup(_cleanup)
    return httpd, httpd.server_address[1]


# --------------------------------------------------------------------------- #
# HTTP 客户端（每请求一条新连接）
# --------------------------------------------------------------------------- #
def http_request(port: int, method: str, path: str, body: Optional[bytes] = None,
                 headers: Optional[dict] = None, timeout: float = 10.0):
    """返回 (status, headers_dict, body_bytes)。"""
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, dict(resp.getheaders()), data
    finally:
        conn.close()


def get_json(port: int, path: str):
    """GET 并解析 JSON body；返回 (status, obj)。"""
    status, _headers, data = http_request(port, "GET", path)
    return status, json.loads(data.decode("utf-8"))


def post_json(port: int, path: str, payload: dict):
    """POST JSON body；返回 (status, obj)。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _headers, data = http_request(
        port, "POST", path, body=body,
        headers={"Content-Type": "application/json"})
    return status, json.loads(data.decode("utf-8"))


def assert_envelope(case, obj: dict, code: str) -> None:
    """断言统一 error envelope 形状 {"error":{"code","message","details"}}。"""
    case.assertIn("error", obj)
    err = obj["error"]
    case.assertEqual(err.get("code"), code)
    case.assertIsInstance(err.get("message"), str)
    case.assertIsInstance(err.get("details"), dict)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
