"""设置目录（§68）里 server-owned 的披露句逐字镜像原生（§14bis 命令契约 / §15 telemetry 知情披露 / §15.3 v0.14 接入区导语）。

parity 批 catalog-help-copy（gap settings-gmail-fetch-command-contract-copy / settings-telemetry-disclosure-copy-trimmed /
settings-source-section-intro-copy）：
- ``gmail_fetch_command`` 的 help 说的是 §14bis 契约（直接执行不走 shell、``GMAIL_RADAR_LAST_UID`` 带进度、stdout 一个
  JSON 数组、字段表、「跑没跑成看下面「运行状态」」）——不再是「stdout 一行一封」（照那句写出来的命令只会 command_bad_output）；
  字段表与 act/radar_gmail.fetch_via_command 读的键互为镜像（tests 层可 import act，server 不 import act——§49）；
- telemetry 三段披露：级别句列出元数据字段 + 随机设备号；文本句写明 500 字截断 + 密钥掩码 + 关前已记录行的去向；
  区导语 = 「关掉最上方开关即完全停止全部上传；本地统计文件不受影响。详见 docs/TELEMETRY.md。」；
- slack / gmail 两区带区首导语（原生 SettingsSlack / SettingsGmail body 首段）；
- 全部经 GET /api/settings 的投影原样外发（zh / en 两键）。
"""
import inspect
import tempfile
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act import radar_gmail
from server import settings_catalog as catalog

# 原生 SettingsGmail.swift commandCard 的字段表 = fetch_via_command 读的键（gmail_thread_id 可选，原生句没列、这里不要求）
COMMAND_FIELDS = ("uid", "from", "subject", "date", "message_id", "body")


def _field(section_id: str, key: str) -> dict:
    return catalog.field_index(catalog.lookup(section_id))[key]


def _help(section_id: str, key: str) -> dict:
    return _field(section_id, key)["help"]


class GmailFetchCommandContractCopyTestCase(unittest.TestCase):
    def test_help_states_the_14bis_contract_in_both_languages(self):
        help_ = _help("gmail", "gmail_fetch_command")
        zh, en = help_["zh"], help_["en"]
        self.assertIn("不走 shell", zh)
        self.assertIn("(no shell)", en)
        self.assertIn("GMAIL_RADAR_LAST_UID", zh)
        self.assertIn("GMAIL_RADAR_LAST_UID", en)
        self.assertIn("JSON 数组", zh)
        self.assertIn("JSON array", en)
        self.assertIn("uid（单调递增）", zh)
        self.assertIn("uid (monotonic)", en)
        for key in COMMAND_FIELDS:
            self.assertIn(key, zh, key)
            self.assertIn(key, en, key)
        self.assertIn("Gmail API 脚本、MCP 客户端都可以", zh)
        self.assertIn("A Gmail-API script or an MCP client both qualify", en)
        self.assertIn("跑没跑成看下面「运行状态」", zh)
        self.assertIn('see "Run status" below', en)

    def test_the_wrong_one_mail_per_line_contract_is_gone(self):
        help_ = _help("gmail", "gmail_fetch_command")
        self.assertNotIn("一行一封", help_["zh"])
        self.assertNotIn("per stdout line", help_["en"])

    def test_help_still_says_which_path_a_value_selects(self):
        help_ = _help("gmail", "gmail_fetch_command")
        self.assertTrue(help_["zh"].startswith("填了就走 B"))
        self.assertTrue(help_["en"].startswith("Set = path B"))

    def test_field_table_and_env_name_mirror_the_radar(self):
        """server 不 import act（§49）——这里是那道 pin：help 里的字段名与环境变量名都在 fetch_via_command 一侧出现。"""
        source = inspect.getsource(radar_gmail)
        self.assertIn("GMAIL_RADAR_LAST_UID", source)
        for key in COMMAND_FIELDS:
            self.assertIn('"%s"' % key, source, key)


class TelemetryDisclosureCopyTestCase(unittest.TestCase):
    def test_level_help_lists_the_metadata_fields_and_the_random_device_id(self):
        help_ = _help("telemetry", "telemetry.level")
        self.assertEqual(
            help_["zh"],
            "基础与详细都发送匿名事件元数据——事件名、时间、页面/动作、耗时计数、随机设备号、版本号。切到基础还会同时停掉下方的输入文本上传（文本需要详细级）。")
        self.assertEqual(
            help_["en"],
            "Both Basic and Detailed send anonymous event metadata — event name, time, page/action, timing counts, random device id, app version. Switching to Basic also stops the typed-text upload below (text requires Detailed).")

    def test_capture_input_help_states_truncation_masking_and_the_pre_switch_off_caveat(self):
        help_ = _help("telemetry", "telemetry.capture_input")
        self.assertIn("截断 500 字符", help_["zh"])
        self.assertIn("内置密钥掩码", help_["zh"])
        self.assertIn("关前已记录、尚未上传的少量行仍会随行为统计发出", help_["zh"])
        self.assertIn("绝不含 AI 的回答、屏幕录制内容、邮件或 Slack 消息", help_["zh"])
        self.assertIn("truncated to 500 chars", help_["en"])
        self.assertIn("built-in key masking", help_["en"])
        self.assertIn("a few lines recorded before the switch-off may still upload with behavior stats", help_["en"])
        self.assertIn("never the AI's answers, screen-recording content, emails or Slack messages", help_["en"])

    def test_section_help_is_the_top_toggle_sentence(self):
        section = catalog.lookup("telemetry")
        self.assertEqual(section["help"]["zh"], "关掉最上方开关即完全停止全部上传；本地统计文件不受影响。详见 docs/TELEMETRY.md。")
        self.assertEqual(section["help"]["en"], "Turning the top toggle off stops all uploads entirely; the local stats file is unaffected. See docs/TELEMETRY.md.")

    def test_enabled_help_is_unchanged(self):
        help_ = _help("telemetry", "telemetry.enabled")
        self.assertEqual(help_["zh"], "默认开：只上传事件元数据（事件名 / 耗时 / 计数）。关 = 完全不上传。")


class SourceSectionIntroCopyTestCase(unittest.TestCase):
    def test_slack_section_has_the_native_intro(self):
        section = catalog.lookup("slack")
        self.assertEqual(
            section["help"]["zh"],
            "把「别人在 Slack 上找你的事」（DM / 群 / @提及）自动变成提案卡。3 步全在这里完成，不用改任何文件；对外只出草稿，永远你自己发。此区改动即时生效。")
        self.assertEqual(
            section["help"]["en"],
            "Turns \"people needing you on Slack\" (DMs / groups / @mentions) into proposal cards automatically. All 3 setup steps happen right here — no files to edit; outbound replies are drafts only, you always send them yourself. Changes apply immediately.")

    def test_gmail_section_has_the_native_intro(self):
        section = catalog.lookup("gmail")
        self.assertEqual(
            section["help"]["zh"],
            "轮询收件箱里的未读邮件，需要你处理的自动变成提案卡（纯通知/营销直接过滤）。只读——邮件绝不会被标成已读。全部在这里配好，不用改任何文件；此区改动即时生效。")
        self.assertEqual(
            section["help"]["en"],
            "Polls unread inbox mail and turns the ones needing you into proposal cards (notifications/marketing filtered out). Read-only — mail is never marked read. Everything is set up right here, no files to edit; changes apply immediately.")


class ProjectionCarriesTheCopyTestCase(unittest.TestCase):
    """GET /api/settings 的投影把 section help 与 field help 两键原样外发（web 只渲染，防腐 #10）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-help-copy-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)

    def test_snapshot_carries_section_and_field_help_verbatim(self):
        snap = catalog.snapshot(self.home)
        by_id = {s["id"]: s for s in snap["sections"]}
        for sid in ("slack", "gmail", "telemetry"):
            self.assertEqual(by_id[sid]["help"], catalog.lookup(sid)["help"])
        projected = {f["key"]: f for f in by_id["gmail"]["fields"]}["gmail_fetch_command"]
        self.assertEqual(projected["help"], _help("gmail", "gmail_fetch_command"))
        projected = {f["key"]: f for f in by_id["telemetry"]["fields"]}["telemetry.capture_input"]
        self.assertEqual(projected["help"], _help("telemetry", "telemetry.capture_input"))


if __name__ == "__main__":
    unittest.main()
