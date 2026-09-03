"""act/radar_slack — the network transport wrappers, self-DM media handling,
the Slack-MCP presence probe and the ``--check`` CLI (§13 / §15 health).

Network is mocked at ``urllib.request.urlopen``; the frame tools at
``shutil.which`` + ``subprocess.run`` (nothing real is spawned). Pinned (P3
mutation net — all of these were <15 % covered):
- ``slack_api``: POST form body + Bearer header, JSON reply passed through,
  any transport/JSON failure -> {"ok": False, "error": "transport:…"};
  ``verify_token`` = auth.test;
- ``download_file``: writes bytes (mkdir -p), False on any failure;
- ``_extract_frames``: neither tool -> None; ffmpeg preferred over framegrab
  (argv shape pinned); tool crash -> []; only image files, capped;
- ``_collect_media``: images downloaded as-is, videos become frames, a video
  with no frame tool adds the user-facing complaint, unknown types / no url /
  non-dict entries ignored, a failed download is skipped;
- ``_probe_slack_mcp`` / ``_slack_mcp_present``: exit code, stdout grep,
  exception -> False; the 30-min cache short-circuits the probe and a fresh
  probe writes it;
- ``_handle_self_message`` pieces: desc composition and the
  nothing-capturable early exit;
- ``_ack_capture``: off switch, unknown kind, failed reaction logs, the
  ``already_reacted`` echo does not;
- CLI ``--check``: no token -> 1, auth ok -> 0, auth error -> 1.
"""
import io
import json
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sets the sandbox env before act imports

from act import radar_slack
from act.lib import analytics, config


class _Resp:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SlackApiTestCase(unittest.TestCase):
    def test_post_shape_and_reply(self):
        seen = {}

        def fake_urlopen(req, timeout=None, context=None):
            seen["url"] = req.full_url
            seen["data"] = req.data
            seen["auth"] = req.get_header("Authorization")
            seen["timeout"] = timeout
            return _Resp(b'{"ok": true, "user_id": "U1"}')

        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            out = radar_slack.verify_token("xoxp-1")
        self.assertEqual(out, {"ok": True, "user_id": "U1"})
        self.assertEqual(seen["url"], radar_slack.SLACK_API + "auth.test")
        self.assertEqual(seen["data"], b"")
        self.assertEqual(seen["auth"], "Bearer xoxp-1")
        self.assertEqual(seen["timeout"], 30)
        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            radar_slack.slack_api("chat.getPermalink", "t", {"channel": "C", "message_ts": "1.0"})
        self.assertEqual(seen["data"], b"channel=C&message_ts=1.0")

    def test_transport_and_json_failures_never_raise(self):
        def boom(req, timeout=None, context=None):
            raise urllib.error.URLError("offline")
        with mock.patch.object(urllib.request, "urlopen", boom):
            out = radar_slack.slack_api("auth.test", "t")
        self.assertFalse(out["ok"])
        self.assertTrue(out["error"].startswith("transport:"))
        with mock.patch.object(urllib.request, "urlopen",
                               lambda *a, **k: _Resp(b"not json")):
            out = radar_slack.slack_api("auth.test", "t")
        self.assertFalse(out["ok"])
        self.assertIn("transport:", out["error"])


class DownloadFileTestCase(unittest.TestCase):
    def test_writes_bytes_and_reports_failure(self):
        dest = Path(tempfile.mkdtemp(prefix="dl-")) / "sub" / "img.png"
        with mock.patch.object(urllib.request, "urlopen",
                               lambda *a, **k: _Resp(b"PNGDATA")):
            self.assertTrue(radar_slack.download_file("t", "https://x/f", dest))
        self.assertEqual(dest.read_bytes(), b"PNGDATA")

        def boom(*a, **k):
            raise OSError("nope")
        with mock.patch.object(urllib.request, "urlopen", boom):
            self.assertFalse(radar_slack.download_file("t", "https://x/f", dest))


class ExtractFramesTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="frames-"))
        self.video = self.tmp / "clip.mp4"
        self.video.write_bytes(b"\x00")
        self.out = self.tmp / "frames"

    def test_no_tool_is_none(self):
        with mock.patch.object(radar_slack.shutil, "which", return_value=None), \
                mock.patch.object(radar_slack, "FRAMEGRAB", self.tmp / "missing-framegrab"):
            self.assertIsNone(radar_slack._extract_frames(self.video, self.out))

    def test_ffmpeg_preferred_and_frames_capped(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            for i in range(5):
                (self.out / f"frame_{i:02d}.jpg").write_bytes(b"j")
            (self.out / "notes.txt").write_text("skip", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0)

        with mock.patch.object(radar_slack.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                mock.patch.object(radar_slack.subprocess, "run", fake_run):
            frames = radar_slack._extract_frames(self.video, self.out, max_frames=3)
        self.assertEqual([p.name for p in frames], ["frame_00.jpg", "frame_01.jpg", "frame_02.jpg"])
        (argv,) = calls
        self.assertEqual(argv[:4], ["/usr/bin/ffmpeg", "-y", "-i", str(self.video)])
        self.assertIn("fps=1", argv)
        self.assertEqual(argv[argv.index("-frames:v") + 1], "3")

    def test_framegrab_fallback_argv(self):
        grab = self.tmp / "framegrab"
        grab.write_text("#!/bin/sh\n", encoding="utf-8")
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0)

        with mock.patch.object(radar_slack.shutil, "which", return_value=None), \
                mock.patch.object(radar_slack, "FRAMEGRAB", grab), \
                mock.patch.object(radar_slack.subprocess, "run", fake_run):
            frames = radar_slack._extract_frames(self.video, self.out)
        self.assertEqual(frames, [])      # tool ran, produced nothing
        self.assertEqual(calls, [[str(grab), str(self.video), str(self.out),
                                  str(radar_slack.MAX_FRAMES)]])

    def test_tool_crash_is_empty_list(self):
        def boom(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 300)
        with mock.patch.object(radar_slack.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                mock.patch.object(radar_slack.subprocess, "run", boom):
            self.assertEqual(radar_slack._extract_frames(self.video, self.out), [])


class CollectMediaTestCase(unittest.TestCase):
    def setUp(self):
        self.media = Path(tempfile.mkdtemp(prefix="media-"))
        patcher = mock.patch.object(radar_slack, "MEDIA_DIR", self.media)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_images_videos_and_ignored_entries(self):
        downloaded = []

        def fake_download(token, url, dest):
            downloaded.append((url, dest.name))
            if "fail" in url:
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x")
            return True

        def fake_frames(video, outdir, max_frames=radar_slack.MAX_FRAMES):
            if "nograb" in video.name:
                return None
            return [outdir / "frame_00.jpg", outdir / "frame_01.jpg"]

        files = [
            {"id": "F1", "name": "photo.JPG", "url_private": "https://x/photo"},
            {"id": "F2", "name": "clip.mov", "url_private_download": "https://x/clip"},
            {"id": "F3", "name": "nograb.mp4", "url_private": "https://x/nograb"},
            {"id": "F4", "name": "bad.png", "url_private": "https://x/fail-png"},
            {"id": "F5", "name": "bad.mp4", "url_private": "https://x/fail-mp4"},
            {"id": "F6", "name": "doc.pdf", "url_private": "https://x/doc"},
            {"id": "F7", "name": "no-url.png"},
            "junk",
            {"id": "F8", "url_private": "https://x/id-only.png"},
        ]
        with mock.patch.object(radar_slack, "download_file", fake_download), \
                mock.patch.object(radar_slack, "_extract_frames", fake_frames):
            images, problems = radar_slack._collect_media("tok", files, "123.4")
        dest_dir = self.media / "123.4"
        self.assertEqual(images, [dest_dir / "photo.JPG",
                                  dest_dir / "frames_clip" / "frame_00.jpg",
                                  dest_dir / "frames_clip" / "frame_01.jpg"])
        self.assertEqual(problems, ["视频暂不支持，请发图片"])
        self.assertEqual([name for _u, name in downloaded],
                         ["photo.JPG", "clip.mov", "nograb.mp4", "bad.png", "bad.mp4"])

    def test_empty_inputs(self):
        self.assertEqual(radar_slack._collect_media("tok", None, ""), ([], []))
        self.assertEqual(radar_slack._attachment({"name": "a.png"}), None)
        self.assertEqual(radar_slack._attachment({"url_private": "u", "id": "F9"}), ("u", "F9"))
        self.assertEqual(radar_slack._attachment({"url_private": "u"}), ("u", "file"))
        self.assertEqual(radar_slack._attachment({"url_private": "u", "name": "../../evil.png"}),
                         ("u", "evil.png"))


class SelfMessageTestCase(unittest.TestCase):
    def test_capture_desc_and_nothing_capturable(self):
        self.assertEqual(radar_slack._capture_desc("hi", []), "hi")
        desc = radar_slack._capture_desc("hi", [Path("/a.png"), Path("/b.png")])
        self.assertTrue(desc.startswith("hi\n\nRead these images first"))
        self.assertTrue(desc.endswith("/a.png\n/b.png"))
        desc2 = radar_slack._capture_desc("", [Path("/a.png")])
        self.assertTrue(desc2.startswith("Read these images first"))
        self.assertTrue(radar_slack._nothing_capturable(["视频暂不支持"], [], ""))
        self.assertFalse(radar_slack._nothing_capturable(["视频暂不支持"], [], "text"))
        self.assertFalse(radar_slack._nothing_capturable(["视频暂不支持"], [Path("/a")], ""))
        self.assertFalse(radar_slack._nothing_capturable([], [], ""))

    def test_handle_self_message_skips_unusable(self):
        cfg = config.Config()
        with mock.patch.object(radar_slack, "_collect_media", return_value=([], ["视频暂不支持"])), \
                mock.patch("act.lib.quick_capture.capture") as capture:
            radar_slack._handle_self_message({"text": "", "files": [{"x": 1}], "ts": "1"},
                                             "tok", cfg)
            radar_slack._handle_self_message({"text": "   "}, "tok", cfg)
        capture.assert_not_called()

    def test_ack_capture_rules(self):
        cfg = config.Config()
        calls = []

        def fake_api(method, token, params=None):
            calls.append((method, params))
            return {"ok": False, "error": params["name"]}

        with mock.patch.object(radar_slack, "slack_api", fake_api), \
                mock.patch.object(analytics, "log_event") as log:
            cfg.slack_capture_receipts = False
            radar_slack._ack_capture("t", {"channel": "C", "ts": "1"}, "proposed", cfg)
            cfg.slack_capture_receipts = True
            radar_slack._ack_capture("t", {"channel": "C", "ts": "1"}, "unknown-kind", cfg)
            radar_slack._ack_capture("t", {"channel": "C"}, "proposed", cfg)
            radar_slack._ack_capture("t", {"channel": "C", "ts": "1"}, "reraised", cfg)
        self.assertEqual(calls, [("reactions.add",
                                  {"channel": "C", "timestamp": "1",
                                   "name": "leftwards_arrow_with_hook"})])
        # #37: only the enum-shaped Slack error code is uploaded (main's
        # _slack_error_code), never free text
        log.assert_called_once_with("capture_receipt_failed",
                                    slack_error="leftwards_arrow_with_hook")
        with mock.patch.object(analytics, "log_event") as log2:
            radar_slack._log_receipt_failure({"ok": False, "error": "already_reacted"})
            radar_slack._log_receipt_failure({"ok": True})
            radar_slack._log_receipt_failure({"ok": False, "error": "Free text, not a code"})
        log2.assert_called_once_with("capture_receipt_failed", slack_error=None)


class McpProbeTestCase(unittest.TestCase):
    def setUp(self):
        p = radar_slack._mcp_present_marker_path()
        if p.exists():
            p.unlink()
        self.addCleanup(lambda: p.unlink() if p.exists() else None)

    def test_probe_outcomes(self):
        def run_ok(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, stdout="slack: npx ... (user)\n")
        with mock.patch("act.llm.claude_bin", return_value="/bin/claude"), \
                mock.patch("act.llm.runner_env", return_value={}), \
                mock.patch.object(radar_slack.subprocess, "run", run_ok):
            self.assertTrue(radar_slack._probe_slack_mcp())
        with mock.patch("act.llm.claude_bin", return_value="/bin/claude"), \
                mock.patch("act.llm.runner_env", return_value={}), \
                mock.patch.object(radar_slack.subprocess, "run",
                                  lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="github: ...")):
            self.assertFalse(radar_slack._probe_slack_mcp())
        with mock.patch("act.llm.claude_bin", return_value="/bin/claude"), \
                mock.patch("act.llm.runner_env", return_value={}), \
                mock.patch.object(radar_slack.subprocess, "run",
                                  lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout="slack")):
            self.assertFalse(radar_slack._probe_slack_mcp())
        with mock.patch("act.llm.claude_bin", side_effect=RuntimeError("no claude")):
            self.assertFalse(radar_slack._probe_slack_mcp())

    def test_presence_cache(self):
        with mock.patch.object(radar_slack, "_probe_slack_mcp", return_value=True) as probe:
            self.assertEqual(radar_slack._slack_mcp_present(), (True, True))
            self.assertEqual(radar_slack._mcp_present_marker_path().read_text(), "1")
            self.assertEqual(radar_slack._slack_mcp_present(), (True, False))   # cached
            self.assertEqual(probe.call_count, 1)
        # stale cache -> re-probe, verdict rewritten
        stale = time.time() - radar_slack._MCP_PRESENT_TTL_S - 5
        p = radar_slack._mcp_present_marker_path()
        import os
        os.utime(p, (stale, stale))
        with mock.patch.object(radar_slack, "_probe_slack_mcp", return_value=False):
            self.assertEqual(radar_slack._slack_mcp_present(), (False, True))
        self.assertEqual(p.read_text(), "0")
        self.assertEqual(radar_slack._slack_mcp_present(), (False, False))


class CheckCliTestCase(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with mock.patch.object(config, "load_config", return_value=config.Config()), \
                redirect_stdout(buf):
            rc = radar_slack._main(argv)
        return rc, buf.getvalue()

    def test_check_paths(self):
        with mock.patch.object(radar_slack, "get_token", return_value=None):
            rc, out = self._run(["--check"])
        self.assertEqual(rc, 1)
        self.assertIn("no token at", out)
        with mock.patch.object(radar_slack, "get_token", return_value="xoxp"), \
                mock.patch.object(radar_slack, "verify_token",
                                  return_value={"ok": True, "user": "z", "user_id": "U1",
                                                "team": "T", "extra": "dropped"}):
            rc, out = self._run(["--check"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"ok": True, "user": "z", "user_id": "U1",
                                           "team": "T", "error": None})
        with mock.patch.object(radar_slack, "get_token", return_value="xoxp"), \
                mock.patch.object(radar_slack, "verify_token",
                                  return_value={"ok": False, "error": "invalid_auth"}):
            rc, out = self._run(["--check"])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["error"], "invalid_auth")

    def test_plain_run_prints_count(self):
        with mock.patch.object(radar_slack, "scan", return_value=4):
            rc, out = self._run(["--once"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "slack radar: 4 new card(s)")


if __name__ == "__main__":
    unittest.main()
