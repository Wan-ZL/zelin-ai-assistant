"""web analytics 事件词表的跨层镜像（CONTRACT §16 追记 / §49 追记；owner 决策 D48）。

server/analytics_ingest.EVENTS 是 ``POST /api/analytics`` 事件名的闭集真源（白名单外 400 INVALID_FIELD）；
web/src/types.ts 的 ``WebAnalyticsEvent`` 是同一词表在 web 侧的手抄镜像——``trackEvent`` 按设计永不 reject，
所以任一边单独改名 / 加项，症状是 web 静默发一个 server 拒掉的名字、事件无声断流，两层各自的判例全绿。
本文件把两边钉成同一个集合（先例 tests/test_skip_reason_vocabulary_mirror.py 读 tsx 钉词表，防腐 #10）：

- server 白名单的每个事件名，web 联合类型里都要有；
- web 联合类型不许有 server 不收的名字（发明的名字 = 永远被 400 的死事件）。
"""
import re
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env 先于任何 act.* import

from server import analytics_ingest

REPO_ROOT = Path(__file__).resolve().parent.parent
TYPES_TS = REPO_ROOT / "web" / "src" / "types.ts"

# `export type WebAnalyticsEvent = "a" | "b";`（允许换行的联合成员）
_UNION = re.compile(r"export type WebAnalyticsEvent\s*=\s*(.*?);", re.DOTALL)
_MEMBER = re.compile(r'"([a-z_]+)"')


def _web_vocabulary() -> set:
    src = TYPES_TS.read_text(encoding="utf-8")
    m = _UNION.search(src)
    if not m:
        return set()
    return set(_MEMBER.findall(m.group(1)))


class WebAnalyticsEventVocabularyMirrorTestCase(unittest.TestCase):
    def setUp(self):
        self.web = _web_vocabulary()

    def test_parser_finds_the_web_union(self):
        self.assertTrue(self.web,
                        "no `export type WebAnalyticsEvent = \"…\" | …;` found in web/src/types.ts — the "
                        "type moved or changed shape; fix the regex, not the invariant")

    def test_web_vocabulary_equals_the_server_whitelist(self):
        server = set(analytics_ingest.EVENTS)
        self.assertEqual(
            self.web, server,
            "web WebAnalyticsEvent %s != server analytics_ingest.EVENTS %s — a name on one side only is "
            "either a dead event (web-only: always 400) or unreachable (server-only: never sent); add it to "
            "both in the same PR (§16 追记: 加事件 = server 加一行 + docs/TELEMETRY.md 表加一行 + web 类型词表加一项)"
            % (sorted(self.web), sorted(server)))


if __name__ == "__main__":
    unittest.main()
