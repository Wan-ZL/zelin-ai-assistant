"""server/ skill store face (CONTRACT §67; §49 routes).

- GET /api/skills: manifest rows with this machine's state (token-light read).
- POST /api/skills {name, action}: all four write gates (same as every POST),
  field whitelist 400 UNKNOWN_FIELD, shape 400 INVALID_FIELD, unknown skill
  404, custom copy 409 CONFLICT with the store's SKILL_* code in details;
  success = fresh snapshot and a real symlink under (temp) ~/.claude/skills.
- broken skills/index.yaml → 409 CONFLICT naming the file, never a 500.

Real server on a random port (tests/test_server_common.py); the served home is
a tempdir carrying a fixture store (skills/index.yaml + two skills), HOME is
pointed at a tempdir so the developer's real ~/.claude is never touched.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from tests.test_server_common import (assert_envelope, auth_headers, get_json,
                                      http_request, post_json, start_server)
from tests.test_skills_store import make_repo, skill_md

_WIN = sys.platform.startswith("win")


@unittest.skipIf(_WIN, "symlink semantics are POSIX here")
class SkillsApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-skills-api-")
        self.addCleanup(self.tmp.cleanup)
        self.home = make_repo(Path(self.tmp.name) / "home")
        (self.home / "state").mkdir()
        self.user_home = Path(self.tmp.name) / "user"
        self.user_home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.user_home),
                                           "USERPROFILE": str(self.user_home)})
        env.start()
        self.addCleanup(env.stop)
        self.link_dir = self.user_home / ".claude" / "skills"
        _httpd, self.port = start_server(self, self.home)

    def test_get_lists_rows_with_machine_state(self):
        status, doc = get_json(self.port, "/api/skills")
        self.assertEqual(status, 200)
        self.assertEqual(doc["skills_dir"], str(self.link_dir))
        self.assertEqual(doc["repo_skills_dir"], str(self.home / "skills"))
        self.assertEqual(doc["state_path"], str(self.home / "state" / "skills.json"))
        rows = {r["name"]: r for r in doc["skills"]}
        self.assertEqual(sorted(rows), ["alpha", "beta"])
        self.assertEqual(rows["alpha"]["state"], "disabled")
        self.assertEqual(rows["alpha"]["toggle"], "enable")
        self.assertTrue(rows["alpha"]["project_visible"])
        for key in ("version", "upstream_version", "default_enabled", "description", "path",
                    "target", "link", "stale_target", "installed_version", "relation",
                    "distance", "decision"):
            self.assertIn(key, rows["alpha"], key)

    def test_post_enable_then_disable(self):
        status, doc = post_json(self.port, "/api/skills", {"name": "alpha", "action": "enable"})
        self.assertEqual(status, 200)
        row = {r["name"]: r for r in doc["skills"]}["alpha"]
        self.assertEqual((row["state"], row["toggle"], row["decision"]), ("enabled", "disable", "enabled"))
        link = self.link_dir / "alpha"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(str(link)), str(self.home / "skills" / "alpha"))
        status, doc = post_json(self.port, "/api/skills", {"name": "alpha", "action": "disable"})
        self.assertEqual(status, 200)
        self.assertEqual({r["name"]: r["state"] for r in doc["skills"]}, {"alpha": "disabled", "beta": "disabled"})
        self.assertFalse(link.exists() or link.is_symlink())
        state = json.loads((self.home / "state" / "skills.json").read_text(encoding="utf-8"))
        self.assertEqual(state["decisions"], {"alpha": "disabled"})

    def test_field_whitelist_and_shapes(self):
        status, doc = post_json(self.port, "/api/skills", {"name": "alpha", "action": "enable", "force": True})
        self.assertEqual(status, 400)
        assert_envelope(self, doc, "UNKNOWN_FIELD")
        self.assertEqual(doc["error"]["details"]["fields"], ["force"])
        status, doc = post_json(self.port, "/api/skills", {"name": "", "action": "enable"})
        self.assertEqual(status, 400)
        assert_envelope(self, doc, "INVALID_FIELD")
        status, doc = post_json(self.port, "/api/skills", {"name": "alpha", "action": "delete"})
        self.assertEqual(status, 400)
        assert_envelope(self, doc, "INVALID_FIELD")
        self.assertEqual(doc["error"]["details"]["field"], "action")
        status, doc = post_json(self.port, "/api/skills", {"name": 7, "action": "enable"})
        self.assertEqual(status, 400)

    def test_unknown_skill_is_404(self):
        status, doc = post_json(self.port, "/api/skills", {"name": "gamma", "action": "enable"})
        self.assertEqual(status, 404)
        assert_envelope(self, doc, "NOT_FOUND")
        self.assertEqual(doc["error"]["details"]["name"], "gamma")

    def test_custom_copy_is_refused_409_with_store_code(self):
        self.link_dir.mkdir(parents=True)
        shutil.copytree(str(self.home / "skills" / "alpha"), str(self.link_dir / "alpha"))
        (self.link_dir / "alpha" / "SKILL.md").write_text(skill_md("alpha", "1.0.0", "# mine\n"), encoding="utf-8")
        status, doc = get_json(self.port, "/api/skills")
        row = {r["name"]: r for r in doc["skills"]}["alpha"]
        self.assertEqual((row["state"], row["toggle"], row["relation"], row["distance"]),
                         ("custom", "locked", "behind", 2))
        for action in ("enable", "disable"):
            status, doc = post_json(self.port, "/api/skills", {"name": "alpha", "action": action})
            self.assertEqual(status, 409, action)
            assert_envelope(self, doc, "CONFLICT")
            self.assertEqual(doc["error"]["details"]["code"], "SKILL_CUSTOM_KEEP")
            self.assertIn(str(self.link_dir / "alpha"), doc["error"]["message"])
        self.assertIn("mine", (self.link_dir / "alpha" / "SKILL.md").read_text(encoding="utf-8"))

    def test_reveal_skill_selects_its_skill_md_copy_first_then_the_store_original(self):
        """POST /api/reveal {target:"skill", name}（§67.5「在 Finder 显示」）：客户端只传清单里的名字，路径 server 推导——
        本机副本 / 链接下的 SKILL.md 优先，否则仓库商店原件；未知名 404；名字不是非空字串 400。"""
        from server import files as files_mod
        with mock.patch.object(files_mod.sys, "platform", "darwin"), mock.patch.object(files_mod.subprocess, "run") as run:
            status, doc = post_json(self.port, "/api/reveal", {"target": "skill", "name": "alpha"})
            self.assertEqual(status, 200)
            self.assertEqual(doc["revealed"], str(self.home / "skills" / "alpha" / "SKILL.md"))
            self.assertEqual(run.call_args[0][0], ["open", "-R", str(self.home / "skills" / "alpha" / "SKILL.md")])
            post_json(self.port, "/api/skills", {"name": "alpha", "action": "enable"})
            status, doc = post_json(self.port, "/api/reveal", {"target": "skill", "name": "alpha"})
            self.assertEqual(status, 200)
            self.assertEqual(doc["revealed"], str(self.link_dir / "alpha" / "SKILL.md"))   # 已链：选中 ~/.claude/skills 下的那份
            status, doc = post_json(self.port, "/api/reveal", {"target": "skill", "name": "nope"})
            self.assertEqual(status, 404)
            for name in ("", "  ", 3, None):
                status, doc = post_json(self.port, "/api/reveal", {"target": "skill", "name": name})
                self.assertEqual(status, 400, name)
                assert_envelope(self, doc, "INVALID_FIELD")
            status, doc = post_json(self.port, "/api/reveal", {"target": "skill", "name": "alpha", "path": "/etc"})
            self.assertEqual(status, 400)
            assert_envelope(self, doc, "UNKNOWN_FIELD")

    def test_broken_manifest_is_409_not_500(self):
        (self.home / "skills" / "index.yaml").write_text("schema: 1\nskills: []\n", encoding="utf-8")
        status, doc = get_json(self.port, "/api/skills")
        self.assertEqual(status, 409)
        assert_envelope(self, doc, "CONFLICT")
        self.assertIn("index.yaml", doc["error"]["message"])
        status, doc = post_json(self.port, "/api/skills", {"name": "alpha", "action": "enable"})
        self.assertEqual(status, 409)

    def test_store_unimportable_answers_501(self):
        from server import settings as settings_mod
        with mock.patch.object(settings_mod, "skill_store", None):
            status, doc = get_json(self.port, "/api/skills")
            self.assertEqual(status, 501)
            assert_envelope(self, doc, "NOT_IMPLEMENTED")
            status, doc = post_json(self.port, "/api/skills", {"name": "alpha", "action": "enable"})
            self.assertEqual(status, 501)

    def test_write_gates_apply(self):
        body = json.dumps({"name": "alpha", "action": "enable"}).encode("utf-8")
        status, _h, data = http_request(self.port, "POST", "/api/skills", body=body,
                                        headers={"Content-Type": "application/json"})
        self.assertEqual(status, 401)
        assert_envelope(self, json.loads(data.decode("utf-8")), "UNAUTHORIZED")
        self.assertFalse((self.link_dir / "alpha").exists(), "no token → nothing written")
        headers = auth_headers(self.port)
        status, _h, data = http_request(self.port, "POST", "/api/skills", body=body, headers=headers)
        self.assertEqual(status, 200)
