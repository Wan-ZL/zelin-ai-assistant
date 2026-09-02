"""§54 board server as a resident service — template, port knob, busy-port exit.

2026-09-02 live: the thin shell spawned `python3 -m server` as its own child and
the child died with "No module named server" — a GUI app is the TCC responsible
process for what it spawns and the shell bundle holds no disk grant. The server
is a launchd agent now (systemd unit on Linux); the shell only connects.

Pinned here:
  - act/launchd/com.zelin.aiassistant.server.plist: KeepAlive resident,
    `<python> -m server`, ZAI_PORT in EnvironmentVariables, log
    server.launchd.log (the generic §55 path discipline for every template is
    tests/test_launchd_render.py — this file adds the server-specific shape);
  - install.sh render_launchd_plist rewrites the ZAI_PORT value from
    SERVER_PORT (real function run under bash); default 47820 when unset;
  - act/systemd/zelin-server.service mirrors it (@ZAI_PORT@ token, Restart=always);
  - config.yaml `server.port` → Config.server_port, 1..65535, garbage → default;
    the default is the same number server/app.py binds (mirror pin);
  - server.app.main() on a busy port prints ONE line and exits 75 — no traceback
    per KeepAlive cycle (log growth discipline, §55 audit L3).
"""
import io
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env before any act.* import

from act.lib import config, systemd
from server import app as server_app

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "act" / "launchd" / "com.zelin.aiassistant.server.plist"
_WIN = sys.platform.startswith("win")

_RENDER_FNS = ("physical_path", "py_imports_yaml", "pick_python", "pinned_python",
               "repo_outside_home", "daemon_python_candidates", "_sed_escape",
               "render_launchd_plist")


def _bare_plist(text):
    return plistlib.loads(re.sub(r"<!--.*?-->", "", text, flags=re.S).encode("utf-8"))


class ServerPlistShapeTestCase(unittest.TestCase):
    def setUp(self):
        self.obj = _bare_plist(TEMPLATE.read_text(encoding="utf-8"))

    def test_label_and_module(self):
        self.assertEqual(self.obj["Label"], "com.zelin.aiassistant.server")
        argv = self.obj["ProgramArguments"]
        self.assertEqual(argv[1:], ["-m", "server"])
        self.assertIn("python", argv[0].rsplit("/", 1)[-1],
                      "argv0 must be the rendered daemon interpreter (§55), never bash")

    def test_resident_keepalive(self):
        self.assertIs(self.obj["KeepAlive"], True)
        self.assertIs(self.obj["RunAtLoad"], True)
        self.assertNotIn("StartInterval", self.obj)

    def test_port_rides_environment_variables(self):
        env = self.obj["EnvironmentVariables"]
        self.assertEqual(env["ZAI_PORT"], str(config.DEFAULT_SERVER_PORT))
        self.assertIn("AIASSISTANT_HOME", env)
        self.assertIn("PYTHONPATH", env)

    def test_port_key_and_value_share_one_line(self):
        # install.sh keys its ZAI_PORT substitution on this exact shape
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertRegex(text, r"<key>ZAI_PORT</key><string>\d+</string>")

    def test_log_is_server_launchd_log(self):
        for key in ("StandardOutPath", "StandardErrorPath"):
            self.assertTrue(self.obj[key].endswith("/zelin-ai-assistant/server.launchd.log"),
                            self.obj[key])

    def test_fd_soft_limit_matches_the_other_templates(self):
        self.assertEqual(self.obj["SoftResourceLimits"]["NumberOfFiles"], 8192)
        self.assertNotIn("HardResourceLimits", self.obj)


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class InstallShRendersThePortTestCase(unittest.TestCase):
    def _render(self, server_port):
        tmp = Path(tempfile.mkdtemp(prefix="server-plist-render-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        out = tmp / "out.plist"
        prelude = "".join(
            "eval \"$(awk '/^%s\\(\\) \\{/,/^\\}/' \"$REPO/install.sh\")\"\n" % fn
            for fn in _RENDER_FNS)
        script = ("set -u\n" + prelude
                  + 'REPO_ROOT="$1"; RUNTIME_PY="$2"; CLAUDE_LOGIN_BIN=/fake/claude\n'
                  + ('SERVER_PORT="$3"\n' if server_port is not None else "")
                  + 'render_launchd_plist "$REPO/act/launchd/com.zelin.aiassistant.server.plist" "$OUT"\n')
        proc = subprocess.run(
            ["bash", "-c", script, "bash", "/Volumes/External/zelin-ai-assistant",
             sys.executable, str(server_port or "")],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "REPO": str(REPO), "OUT": str(out), "HOME": str(tmp)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return _bare_plist(out.read_text(encoding="utf-8"))

    def test_server_port_from_config_lands_in_zai_port(self):
        obj = self._render(47999)
        self.assertEqual(obj["EnvironmentVariables"]["ZAI_PORT"], "47999")
        self.assertEqual(obj["Label"], "com.zelin.aiassistant.server")

    def test_unset_server_port_keeps_the_default(self):
        obj = self._render(None)
        self.assertEqual(obj["EnvironmentVariables"]["ZAI_PORT"], str(config.DEFAULT_SERVER_PORT))


class SystemdUnitMirrorTestCase(unittest.TestCase):
    def test_server_unit_renders_port_and_is_resident(self):
        rendered = systemd.render_all("/usr/bin/python3", "/home/f/repo", "/home/f/.local/bin",
                                      zai_port="47999")
        unit = rendered["zelin-server.service"]
        self.assertIn("ExecStart=/usr/bin/python3 -m server", unit)
        self.assertIn("Environment=ZAI_PORT=47999", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("WantedBy=default.target", unit)
        self.assertNotIn("@ZAI_PORT@", unit)

    def test_default_port_matches_the_python_side(self):
        rendered = systemd.render_all("/usr/bin/python3", "/home/f/repo", "/home/f/.local/bin")
        self.assertIn("Environment=ZAI_PORT=%d" % config.DEFAULT_SERVER_PORT,
                      rendered["zelin-server.service"])

    def test_cli_accepts_zai_port(self):
        tmp = Path(tempfile.mkdtemp(prefix="systemd-cli-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with redirect_stdout(io.StringIO()):
            rc = systemd.main(["--python", "/usr/bin/python3", "--repo-root", "/r",
                               "--claude-bin-dir", "/c", "--out", str(tmp), "--zai-port", "48000"])
        self.assertEqual(rc, 0)
        self.assertIn("Environment=ZAI_PORT=48000",
                      (tmp / "zelin-server.service").read_text(encoding="utf-8"))


class ServerPortKnobTestCase(unittest.TestCase):
    def test_default_mirrors_server_app(self):
        self.assertEqual(config.DEFAULT_SERVER_PORT, server_app.DEFAULT_PORT)
        self.assertEqual(int(systemd.DEFAULT_ZAI_PORT), server_app.DEFAULT_PORT)
        self.assertEqual(config.Config().server_port, server_app.DEFAULT_PORT)

    def test_server_port_parsed_and_clamped(self):
        self.assertEqual(config._server_port_from({"server": {"port": 47999}}), 47999)
        self.assertEqual(config._server_port_from({"server": {"port": "48000"}}), 48000)
        for bad in ({}, {"server": "hi"}, {"server": {"port": 0}}, {"server": {"port": 70000}},
                    {"server": {"port": True}}, {"server": {"port": "eighty"}},
                    {"server": {"port": None}}):
            self.assertEqual(config._server_port_from(bad), config.DEFAULT_SERVER_PORT, bad)

    def test_load_config_reads_the_block(self):
        tmp = Path(tempfile.mkdtemp(prefix="server-port-cfg-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "config.yaml").write_text("server:\n  port: 47821\n", encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", tmp / "config.yaml"), \
                mock.patch.object(config, "SETTINGS_OVERRIDES_PATH", tmp / "none.json"):
            self.assertEqual(config.load_config().server_port, 47821)


@unittest.skipIf(_WIN, "Windows SO_REUSEADDR lets a second bind on a busy port succeed, "
                       "so main() never sees EADDRINUSE and would serve_forever (hang)")
class BusyPortExitTestCase(unittest.TestCase):
    def test_main_on_a_busy_port_prints_one_line_and_exits_75(self):
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        self.addCleanup(holder.close)
        port = holder.getsockname()[1]
        home = Path(tempfile.mkdtemp(prefix="server-busy-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"ZAI_PORT": str(port), "AIASSISTANT_HOME": str(home)}), \
                redirect_stdout(out):
            rc = server_app.main()
        self.assertEqual(rc, server_app.EX_PORT_BUSY)
        text = out.getvalue()
        self.assertEqual(len(text.strip().splitlines()), 1, text)
        self.assertIn("is busy", text)
        self.assertIn(str(port), text)
        self.assertNotIn("Traceback", text)


if __name__ == "__main__":
    unittest.main()
