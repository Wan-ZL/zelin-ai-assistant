"""全覆盖跑者的三个解析器：fixture 脚本、http 的 ctype=、列表型占位符（CONTRACT §58 QA 闸门）。

判例（纯解析，绝不起子进程、不联网、不起 server）：
  - `fixture:<slug>`：清单 id（`B-03-slack-poison-reject`）与脚本名
    （`slack_poison_reject.py`）不必逐字相等——候选序 = 行的 note、`<slug>.py`、
    去掉 `^B-\\d+-` 前缀并 `-`→`_`；一个都不在才记 MISSING，且 reason 列出候选；
  - `ctype=<mime>`：显式 Content-Type，写动词没带 body 时发**空** bytes
    （`POST /api/attachments` 要 image/*，否则 415 挡在 401 之前）；旧 proof
    不带 ctype 时形制逐字不变（字段 add-only）；
  - `{section}` / `{log}` / `{job}` / `{recap}`：与 `{id}` 同一口味——问列表端点
    取第一项；列表空 → `__absent__` 且证据行明说（绝不假装解析到了）。
"""
import importlib.util
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "coverage_run_resolvers_under_test", REPO / "scripts" / "qa" / "coverage_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cr = _load()


class FakeServer:
    def __init__(self, base_url="http://127.0.0.1:1", token="tok"):
        self.base_url = base_url
        self.token = token


class RecordingHttp:
    """(method, path) → (status, text) 的假 HTTP；记下每次调用。"""

    def __init__(self, table=None, default=(404, '{"error": {}}')):
        self.table = dict(table or {})
        self.default = default
        self.calls = []

    def request(self, method, url, body=None, headers=None, timeout=None):
        path = url.split("http://127.0.0.1:1", 1)[-1]
        self.calls.append({"method": method, "path": path, "body": body,
                           "headers": dict(headers or {})})
        status, text = self.table.get((method, path), self.default)
        return cr.Resp(status, text)


# --------------------------------------------------------------------------- #
# fixture: 脚本解析
# --------------------------------------------------------------------------- #
class FixtureScriptResolverTestCase(unittest.TestCase):
    def test_candidates_are_note_then_slug_then_stripped_prefix(self):
        cands = cr.fixture_candidates("B-03-slack-poison-reject",
                                      "scripts/qa/fixtures_b/slack_poison_reject.py")
        self.assertEqual(cands, ["scripts/qa/fixtures_b/slack_poison_reject.py",
                                 "scripts/qa/fixtures_b/B-03-slack-poison-reject.py",
                                 "scripts/qa/fixtures_b/slack_poison_reject.py"])

    def test_candidates_without_a_note_still_strip_the_number_prefix(self):
        self.assertEqual(cr.fixture_candidates("B-10-media-prune-60min"),
                         ["scripts/qa/fixtures_b/B-10-media-prune-60min.py",
                          "scripts/qa/fixtures_b/media_prune_60min.py"])

    def test_the_note_wins_when_it_is_an_existing_path(self):
        seen = []

        def exists(path):
            seen.append(path)
            return path.endswith("/notes_path.py")

        script = cr.resolve_fixture_script("B-01-slack-mint", "scripts/qa/fixtures_b/notes_path.py",
                                           exists=exists)
        self.assertEqual(script, str(REPO / "scripts/qa/fixtures_b/notes_path.py"))
        self.assertEqual(len(seen), 1)              # 第一候选就命中 = 不再试后面的

    def test_slug_named_script_wins_over_the_stripped_form(self):
        script = cr.resolve_fixture_script(
            "B-01-slack-mint", None,
            exists=lambda path: path.endswith(("B-01-slack-mint.py", "slack_mint.py")))
        self.assertTrue(script.endswith("B-01-slack-mint.py"), script)

    def test_stripped_prefix_form_is_the_last_resort(self):
        script = cr.resolve_fixture_script("B-15-terminal-enqueue-with-heartbeat", "not a path",
                                           exists=lambda path: path.endswith(
                                               "terminal_enqueue_with_heartbeat.py"))
        self.assertEqual(script,
                         str(REPO / "scripts/qa/fixtures_b/terminal_enqueue_with_heartbeat.py"))

    def test_nothing_on_disk_resolves_to_none(self):
        self.assertIsNone(cr.resolve_fixture_script("B-99-never-written", None,
                                                    exists=lambda path: False))

    def test_every_b_row_in_the_committed_inventory_resolves_on_disk(self):
        rows = [row for row in cr.load_inventory(str(REPO / "qa" / "coverage_inventory.json"))
                if str(row.get("id") or "").startswith("B-")]
        self.assertTrue(rows, "清单里没有 B- 行——fixtures 侧的真源丢了")
        unresolved = [row["id"] for row in rows
                      if cr.resolve_fixture_script(row["id"], cr._row_note(row)) is None]
        self.assertEqual(unresolved, [])

    def test_absolute_note_paths_are_used_verbatim(self):
        script = cr.resolve_fixture_script("B-02-slack-fold", "/tmp/zaa-abs/slack_fold.py",
                                           exists=lambda path: path.startswith("/tmp/zaa-abs/"))
        self.assertEqual(script, "/tmp/zaa-abs/slack_fold.py")


class FixtureDispatchTestCase(unittest.TestCase):
    """Judge 把整行（不只是 proof 参数）交给 fixture:——note 才是第一候选。"""

    def _judge(self, shell):
        tools = _FakeTools()
        return cr.Judge(tools, shell, RecordingHttp(), cr.TempHomes(), _Opts())

    def test_the_rows_note_selects_the_script(self):
        shell = _ScriptedShell([(0, "fold receipt written\n")])
        judge = self._judge(shell)
        row = {"id": "B-02-slack-fold", "status": "todo", "proof": "fixture:B-02-slack-fold",
               "note": "scripts/qa/fixtures_b/slack_fold.py"}
        verdict = judge.row(row)
        self.assertEqual(verdict.state, cr.PRESENT, verdict.reason)
        self.assertEqual(shell.calls[0]["cmd"][1],
                         str(REPO / "scripts/qa/fixtures_b/slack_fold.py"))
        self.assertEqual(verdict.evidence, "fold receipt written")

    def test_a_missing_script_names_every_candidate_tried(self):
        judge = self._judge(_ScriptedShell([]))
        verdict = judge.row({"id": "B-99-never-written", "status": "todo",
                             "proof": "fixture:B-99-never-written", "note": None})
        self.assertEqual(verdict.state, cr.MISSING)
        self.assertIn("B-99-never-written.py", verdict.reason)
        self.assertIn("never_written.py", verdict.reason)


# --------------------------------------------------------------------------- #
# http: ctype=
# --------------------------------------------------------------------------- #
class HttpCtypeTestCase(unittest.TestCase):
    def test_parse_keeps_the_declared_mime(self):
        spec = cr.parse_http_proof("POST /api/attachments ctype=image/png noauth expect=401")
        self.assertEqual(spec["ctype"], "image/png")
        self.assertTrue(spec["noauth"])
        self.assertEqual(spec["expect"], 401)
        self.assertIsNone(spec["body"])

    def test_old_proofs_parse_exactly_as_before(self):
        spec = cr.parse_http_proof("POST /api/cards/{id}/x body=@f.json noauth expect=409 contains=ok")
        self.assertEqual(spec["method"], "POST")
        self.assertEqual(spec["path"], "/api/cards/{id}/x")
        self.assertEqual(spec["body"], {"file": "f.json"})
        self.assertEqual(spec["contains"], "ok")
        self.assertIsNone(spec["ctype"])

    def test_ctype_sets_the_header_and_sends_an_empty_body(self):
        http = RecordingHttp({("POST", "/api/attachments"): (401, '{"error": {"code": "NO_TOKEN"}}')})
        verdict = cr.run_http_proof("POST /api/attachments ctype=image/png noauth expect=401",
                                    http, FakeServer(), [])
        self.assertEqual(verdict.state, cr.PRESENT, verdict.reason)
        call = http.calls[0]
        self.assertEqual(call["headers"]["Content-Type"], "image/png")
        self.assertNotIn("X-Zai-Token", call["headers"])     # noauth
        self.assertEqual(call["body"], "")                   # 空 bytes，不是 `{}`

    def test_without_ctype_a_write_still_sends_the_json_object(self):
        http = RecordingHttp({("POST", "/api/actions"): (200, "{}")})
        verdict = cr.run_http_proof("POST /api/actions expect=200", http, FakeServer(), [])
        self.assertEqual(verdict.state, cr.PRESENT, verdict.reason)
        self.assertEqual(http.calls[0]["body"], "{}")
        self.assertEqual(http.calls[0]["headers"]["Content-Type"], "application/json")
        self.assertEqual(http.calls[0]["headers"]["X-Zai-Token"], "tok")

    def test_ctype_overrides_the_json_default_for_a_declared_body(self):
        http = RecordingHttp({("PUT", "/api/x"): (200, "{}")})
        cr.run_http_proof("PUT /api/x body={} ctype=text/plain expect=200",
                          http, FakeServer(), [])
        self.assertEqual(http.calls[0]["headers"]["Content-Type"], "text/plain")


# --------------------------------------------------------------------------- #
# 列表型占位符
# --------------------------------------------------------------------------- #
_SETTINGS = json.dumps({"sections": [{"id": "general", "fields": []},
                                     {"id": "storage", "fields": []}]})
_DIAGNOSTICS = json.dumps({"logs": [{"name": "screenpipe-auto.log", "size": 1},
                                    {"name": "actd.log", "size": 2}]})


class PlaceholderTestCase(unittest.TestCase):
    def _listings(self, table):
        return cr.Listings(RecordingHttp(table), FakeServer())

    def test_section_comes_from_the_settings_catalog(self):
        listings = self._listings({("GET", "/api/settings"): (200, _SETTINGS)})
        path, absent = cr.resolve_placeholders("/api/settings/{section}", listings)
        self.assertEqual(path, "/api/settings/general")
        self.assertEqual(absent, [])

    def test_log_falls_back_to_the_diagnostics_log_list(self):
        listings = self._listings({("GET", "/api/diagnostics"): (200, _DIAGNOSTICS)})
        path, absent = cr.resolve_placeholders("/api/logs/{log}", listings)
        self.assertEqual(path, "/api/logs/screenpipe-auto.log")
        self.assertEqual(absent, [])

    def test_job_and_recap_come_from_their_own_listings(self):
        listings = self._listings({
            ("GET", "/api/ingest/jobs"): (200, json.dumps({"jobs": [{"id": "job-7"}]})),
            ("GET", "/api/recaps"): (200, json.dumps({"recaps": [{"key": "2026-09-17-standup"}]})),
        })
        self.assertEqual(cr.resolve_placeholders("/api/ingest/jobs/{job}", listings)[0],
                         "/api/ingest/jobs/job-7")
        self.assertEqual(cr.resolve_placeholders("/api/recaps/history?key={recap}", listings)[0],
                         "/api/recaps/history?key=2026-09-17-standup")

    def test_a_string_list_is_accepted_too(self):
        listings = self._listings({("GET", "/api/recaps"): (200, json.dumps(
            {"keys": ["2026-09-01-1on1", "2026-09-02-sync"]}))})
        self.assertEqual(cr.resolve_placeholders("?key={recap}", listings)[0],
                         "?key=2026-09-01-1on1")

    def test_an_empty_listing_substitutes_absent_and_says_so(self):
        listings = self._listings({("GET", "/api/ingest/jobs"): (200, json.dumps({"jobs": []}))})
        path, absent = cr.resolve_placeholders("/api/ingest/jobs/{job}", listings)
        self.assertEqual(path, "/api/ingest/jobs/__absent__")
        self.assertEqual(len(absent), 1)
        self.assertIn("{job}", absent[0])
        self.assertIn("__absent__", absent[0])

    def test_a_404_listing_route_is_treated_as_empty(self):
        listings = self._listings({})                  # 每个端点都 404
        path, absent = cr.resolve_placeholders("/api/logs/{log}", listings)
        self.assertEqual(path, "/api/logs/__absent__")
        self.assertIn("{log}", absent[0])

    def test_each_placeholder_is_asked_only_once(self):
        http = RecordingHttp({("GET", "/api/settings"): (200, _SETTINGS)})
        listings = cr.Listings(http, FakeServer())
        for _ in range(3):
            cr.resolve_placeholders("/api/settings/{section}", listings)
        self.assertEqual([c["path"] for c in http.calls], ["/api/settings"])

    def test_paths_without_placeholders_are_untouched_and_ask_nothing(self):
        http = RecordingHttp({})
        path, absent = cr.resolve_placeholders("/api/board", cr.Listings(http, FakeServer()))
        self.assertEqual(path, "/api/board")
        self.assertEqual(absent, [])
        self.assertEqual(http.calls, [])

    def test_the_listing_request_carries_the_instance_token(self):
        http = RecordingHttp({("GET", "/api/settings"): (200, _SETTINGS)})
        cr.Listings(http, FakeServer()).first("{section}")
        self.assertEqual(http.calls[0]["headers"]["X-Zai-Token"], "tok")

    def test_the_absent_note_reaches_the_evidence_line(self):
        http = RecordingHttp({("GET", "/api/logs/__absent__"): (400, '{"error": {}}')})
        verdict = cr.run_http_proof("GET /api/logs/{log} expect=400", http, FakeServer(), [],
                                    cr.Listings(http, FakeServer()))
        self.assertEqual(verdict.state, cr.PRESENT, verdict.reason)
        self.assertIn("__absent__", verdict.evidence)
        self.assertIn("{log}", verdict.evidence)

    def test_http_proof_without_a_listings_resolver_still_runs(self):
        """旧调用形制（4 个参数）保持可用：占位符解析不到 → __absent__。"""
        http = RecordingHttp({("GET", "/api/settings/__absent__"): (404, "{}")})
        verdict = cr.run_http_proof("GET /api/settings/{section} expect=404", http,
                                    FakeServer(), [])
        self.assertEqual(verdict.state, cr.PRESENT, verdict.reason)


# --------------------------------------------------------------------------- #
# 本文件用的三个假货（Shell / Tools / Opts）
# --------------------------------------------------------------------------- #
class _ScriptedShell:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = []

    def run(self, cmd, cwd=None, env=None, timeout=None, log_name=None):
        self.calls.append({"cmd": list(cmd), "env": dict(env or {}), "log_name": log_name})
        if not self.replies:
            return cr.Proc(0, "")
        rc, out = self.replies.pop(0)
        return cr.Proc(rc, out)


class _FakeTools:
    def __init__(self):
        self.python = "/fake/python3"
        self.pythonpath = str(REPO)

    def server(self):
        return None, "no server in this test"


class _Opts:
    def __init__(self):
        self.logdir = None
        self.skip_ax = False
        self.unittest_scope = "listed"
        self.unittest_listed_max = 40


if __name__ == "__main__":
    unittest.main()
