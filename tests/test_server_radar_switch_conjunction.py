"""§48.1 合取写（server/settings_catalog.update_section → apply_radar_switch_conjunction）。

原生 SettingsGmail.setEnabled / SettingsSlack.persistFlag：面板里把雷达源开关翻 **开** = 显式动作，
同一笔把 ``<src>_enabled`` 与 ``features.<src>_radar`` 都写进 override（override 压过 yaml 里关着的
flag——只写开关的话面板显示「开启」而雷达永远静默）；翻 **关** 只写单键（合取，一票否决）。
web 的 ``PUT /api/settings/{slack,gmail}`` 自此同款；flag 走目录同一条 diff-write（yaml 本就 true 时
不落键，effective 仍是 true）。agent 的装 / 卸不在这条路上（§48.7）。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first
from server import settings_catalog
from tests.test_server_common import auth_headers, get_json, http_request, start_server, write_text


def put_json(port, path, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _h, data = http_request(port, "PUT", path, body=body, headers=auth_headers(port))
    return status, json.loads(data.decode("utf-8"))


class RadarSwitchConjunctionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-radar-switch-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)
        _httpd, self.port = start_server(self, self.home)

    @property
    def overrides_path(self) -> Path:
        return self.home / "state" / "settings_overrides.json"

    def _overrides(self):
        return json.loads(self.overrides_path.read_text(encoding="utf-8"))

    def _flag(self, key):
        _s, flags = get_json(self.port, "/api/settings/flags")
        return next(f for f in flags["fields"] if f["key"] == key)

    def test_gmail_switch_on_writes_the_flag_when_yaml_holds_it_off(self):
        write_text(self.home / "config.yaml",
                   "features:\n  gmail_radar: false\nsources:\n  gmail:\n    enabled: false\n")
        status, obj = put_json(self.port, "/api/settings/gmail", {"gmail_enabled": True})
        self.assertEqual(status, 200)
        self.assertEqual(obj["id"], "gmail")   # 回执仍是本区快照（§68.1 PUT 形不变）
        self.assertEqual(self._overrides(), {"gmail_enabled": True, "features": {"gmail_radar": True}})
        self.assertEqual((self._flag("features.gmail_radar")["effective"], self._flag("features.gmail_radar")["source"]),
                         (True, "override"))

    def test_slack_switch_on_writes_the_flag_when_yaml_holds_it_off(self):
        write_text(self.home / "config.yaml", "features:\n  slack_radar: false\n")
        _s, _obj = put_json(self.port, "/api/settings/slack", {"slack_enabled": True})
        self.assertEqual(self._overrides(), {"features": {"slack_radar": True}})   # 开关本身等于 yaml 默认 → 不落键

    def test_switch_on_lifts_an_override_that_held_the_flag_off(self):
        # 用户先在 Feature flags 区把 flag 关了、再在接入区翻开开关：显式动作压过旧 override（diff-write：
        # 等于 yaml / 默认的 true 即删键，effective 回到 true）
        write_text(self.overrides_path, json.dumps({"features": {"gmail_radar": False, "digest": False}, "gmail_enabled": False}))
        _s, _obj = put_json(self.port, "/api/settings/gmail", {"gmail_enabled": True})
        self.assertEqual(self._overrides(), {"features": {"digest": False}})
        self.assertEqual(self._flag("features.gmail_radar")["effective"], True)

    def test_switch_off_writes_only_the_single_key(self):
        write_text(self.home / "config.yaml", "features:\n  gmail_radar: false\n")
        _s, _obj = put_json(self.port, "/api/settings/gmail", {"gmail_enabled": False})
        self.assertEqual(self._overrides(), {"gmail_enabled": False})
        self.assertEqual(self._flag("features.gmail_radar")["effective"], False)   # 关不动 flag（一票否决已成立）

    def test_other_keys_in_the_same_put_do_not_trigger_the_conjunction(self):
        write_text(self.home / "config.yaml", "features:\n  gmail_radar: false\n")
        _s, _obj = put_json(self.port, "/api/settings/gmail", {"gmail_address": "you@gmail.com"})
        self.assertEqual(self._overrides(), {"gmail_address": "you@gmail.com"})

    def test_flag_section_put_does_not_write_the_switch(self):
        # 反向不成立（原生「功能开关面板只写 override flag」）：flags 区翻 flag 不碰 <src>_enabled
        write_text(self.home / "config.yaml", "sources:\n  gmail:\n    enabled: false\n")
        _s, _obj = put_json(self.port, "/api/settings/flags", {"features.gmail_radar": True})
        self.assertNotIn("gmail_enabled", self._overrides())

    def test_conjunction_lands_with_the_rest_of_the_put_in_the_final_snapshot(self):
        # 终态：开关、同 PUT 里的其它键、flag 一起出现在 overrides（一次 PUT，不用先翻开关再去 flags 区）
        write_text(self.home / "config.yaml", "features:\n  slack_radar: false\nsources:\n  slack:\n    enabled: false\n")
        _s, _obj = put_json(self.port, "/api/settings/slack", {"slack_enabled": True, "owner_slack_user_id": "U1"})
        self.assertEqual(self._overrides(), {"slack_enabled": True, "owner_slack_user_id": "U1", "features": {"slack_radar": True}})


class RadarSwitchConjunctionWriteCountTestCase(unittest.TestCase):
    """「一笔落盘」在进程内数 write_overrides 的调用（HTTP 层只看得见终态；两次 write 中间态在磁盘上是看得见的）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-radar-switch-inproc-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)

    def _overrides(self):
        return json.loads((self.home / "state" / "settings_overrides.json").read_text(encoding="utf-8"))

    def test_switch_on_plus_flag_is_exactly_one_write(self):
        write_text(self.home / "config.yaml", "features:\n  slack_radar: false\nsources:\n  slack:\n    enabled: false\n")
        with mock.patch.object(settings_catalog, "write_overrides", wraps=settings_catalog.write_overrides) as writes:
            settings_catalog.update_section(self.home, "slack", {"slack_enabled": True, "owner_slack_user_id": "U1"})
        self.assertEqual(writes.call_count, 1)
        self.assertEqual(writes.call_args.args[1], {"slack_enabled": True, "owner_slack_user_id": "U1", "features": {"slack_radar": True}})
        self.assertEqual(self._overrides(), writes.call_args.args[1])

    def test_switch_off_is_also_exactly_one_write(self):
        write_text(self.home / "config.yaml", "features:\n  gmail_radar: false\n")
        with mock.patch.object(settings_catalog, "write_overrides", wraps=settings_catalog.write_overrides) as writes:
            settings_catalog.update_section(self.home, "gmail", {"gmail_enabled": False})
        self.assertEqual(writes.call_count, 1)
        self.assertEqual(self._overrides(), {"gmail_enabled": False})


if __name__ == "__main__":
    unittest.main()
