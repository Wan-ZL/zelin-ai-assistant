"""原始媒体保留期是一把旋钮，两侧同一条规则（CONTRACT §72.4；issue #28）。

`recording.media_retention_minutes`（设置页扁平键 `screenpipe_media_retention_minutes`）此前是
`ingest/screenpipe-cleanup.sh` 里写死的 `-mmin +60`。本判例钉住：

- act 侧三层（override → config.yaml → 出厂 60）与区间 [MIN, MAX]：**区间外 = 坏值**，
  yaml 路径回落出厂值、overrides 路径整条跳过——绝不夹到边界上；
- server 目录（§68.1）的 `bounds` 与 act 的区间**逐字同一对数**，越界 PUT 400（不夹取）、
  合法值 diff-write 落同一个扁平键，等于生效值即删键；
- 目录读到的越界值（有人手改 config.yaml）按缺席落到下一层——设置页显示的数
  必须就是 cron 真用的那个数。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import TMP_HOME  # noqa: F401 - sandbox env first

from act.lib import config as act_config
from server import settings_catalog as catalog
from server.errors import InvalidFieldError

FIELD_KEY = "screenpipe_media_retention_minutes"


def _field() -> dict:
    return catalog.field_index(catalog.lookup("storage"))[FIELD_KEY]


class ActLayerTestCase(unittest.TestCase):
    """config.yaml / settings_overrides.json 的三层与区间。"""

    def test_factory_default_is_todays_hardcoded_window(self):
        self.assertEqual(act_config.Config().screenpipe_media_retention_minutes, 60)
        self.assertEqual(act_config.DEFAULT_MEDIA_RETENTION_MINUTES, 60)

    def test_yaml_value_inside_the_range_wins(self):
        cfg = act_config.Config()
        act_config._apply_recording(cfg, {"recording": {"media_retention_minutes": 240}})
        self.assertEqual(cfg.screenpipe_media_retention_minutes, 240)

    def test_yaml_out_of_range_or_garbage_falls_back_to_the_default(self):
        for bad in (1, 0, -5, act_config.MAX_MEDIA_RETENTION_MINUTES + 1, "soon", None, True):
            with self.subTest(bad=bad):
                cfg = act_config.Config()
                cfg.screenpipe_media_retention_minutes = 240      # 前一轮的值也不许留下
                act_config._apply_recording(cfg, {"recording": {"media_retention_minutes": bad}})
                self.assertEqual(cfg.screenpipe_media_retention_minutes, 60)

    def test_override_layer_wins_and_bad_entries_are_skipped_one_by_one(self):
        self.assertIn(FIELD_KEY, act_config._OVERRIDE_FIELDS)
        for value, expected in ((30, 30), (2, 60), (525601, 60), ("nope", 60), (True, 60)):
            with self.subTest(value=value):
                cfg = act_config.Config()
                with mock.patch.object(act_config, "_read_overrides", return_value={FIELD_KEY: value}):
                    act_config._apply_settings_overrides(cfg)
                self.assertEqual(cfg.screenpipe_media_retention_minutes, expected)

    def test_the_boundaries_themselves_are_legal(self):
        for good in (act_config.MIN_MEDIA_RETENTION_MINUTES, act_config.MAX_MEDIA_RETENTION_MINUTES):
            with self.subTest(good=good):
                self.assertEqual(act_config.coerce_media_retention_minutes(good), good)


class CatalogMirrorTestCase(unittest.TestCase):
    """server 目录侧（§68.1）：同一对区间、同一个扁平键、越界 400。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zai-media-retention-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "state").mkdir(parents=True)

    def overrides(self) -> dict:
        path = self.home / "state" / "settings_overrides.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def test_bounds_mirror_act(self):
        self.assertEqual(_field()["bounds"],
                         (act_config.MIN_MEDIA_RETENTION_MINUTES, act_config.MAX_MEDIA_RETENTION_MINUTES))
        self.assertEqual(_field()["config"], ("recording", "media_retention_minutes"))

    def test_put_inside_the_range_writes_the_flat_key(self):
        catalog.update_section(self.home, "storage", {FIELD_KEY: 120})
        self.assertEqual(self.overrides()[FIELD_KEY], 120)

    def test_put_equal_to_the_default_deletes_the_key(self):
        catalog.update_section(self.home, "storage", {FIELD_KEY: 120})
        catalog.update_section(self.home, "storage", {FIELD_KEY: 60})
        self.assertNotIn(FIELD_KEY, self.overrides())

    def test_out_of_range_is_400_and_never_clamped(self):
        for bad in (0, 4, -1, act_config.MAX_MEDIA_RETENTION_MINUTES + 1):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidFieldError) as caught:
                    catalog.update_section(self.home, "storage", {FIELD_KEY: bad})
                self.assertEqual(caught.exception.details.get("min"), act_config.MIN_MEDIA_RETENTION_MINUTES)
                self.assertNotIn(FIELD_KEY, self.overrides())

    def test_an_out_of_range_file_value_reads_as_absent(self):
        # 有人手改 config.yaml 写了 1 分钟：act 回落 60，目录也必须报 60（同一个数）
        (self.home / "config.yaml").write_text("recording:\n  media_retention_minutes: 1\n", encoding="utf-8")
        self.assertEqual(catalog.effective_value(self.home, "storage", FIELD_KEY), 60)


if __name__ == "__main__":
    unittest.main()
