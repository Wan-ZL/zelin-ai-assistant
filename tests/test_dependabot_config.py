"""`.github/dependabot.yml` 成立且 vite 工具链成组（CONTRACT §54 依赖澄清、§56.8、§70.3 ⑪ 判例）。

七项 required check 里没有一项解析这个文件：Lint 只跑 shellcheck + ruff，qlty 的
actionlint / zizmor 只管 workflows。一个拼错的键（`patterns` → `pattern`）或缩进
错位（`groups` 掉进 `schedule`）会过掉所有门合进 main，然后 GitHub 拒掉整份配置、
两条生态的 version updates 一起静默停摆——只在 Insights → Dependency graph →
Dependabot 里可见（PR #245 review 判定）。本文件钉的形状（PyYAML，不起网络、不跑
dependabot 的 JSON schema 校验器，键名白名单抄 GitHub 文档）：

  - version 2；两条生态 github-actions（/）与 npm（/web），均 weekly；
  - 每层的键都在 GitHub 认的集合里——拼错键、错层键都红；
  - npm 面 `groups.vite-toolchain.patterns` = vite / @vitejs/* / vitest / @vitest/*：
    vite 跨大版本时 @vitejs/plugin-react 的 peer range 跟着换代（4.x 只认 vite ≤7、
    6.x 只认 vite 8），单包 bump 在 `npm ci` 那一步 ERESOLVE、「Web tests」与
    「QA gates」在装依赖时就红（判例 #197，vite 6→8）；web/package.json 里今天在装的
    vite / @vitejs/plugin-react / vitest 都被 pattern 覆盖（组不是空转），运行时依赖
    react / react-dom 不在组里；
  - 分组不关掉任何更新：npm 面无 `ignore` / `allow`，组不带 `applies-to`（只管
    version updates，security updates 不受影响）；
  - 检查器本身不是空转：拼错键与错层键的阴性对照必须被抓到。
"""
import copy
import fnmatch
import json
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPENDABOT_YML = REPO_ROOT / ".github" / "dependabot.yml"
WEB_PACKAGE_JSON = REPO_ROOT / "web" / "package.json"

# 键名白名单 = GitHub 文档「Dependabot options reference」(dependabot.yml v2)。
# 少一个合法键只会让本判例误报（改这里即可）；多一个拼错键则 GitHub 拒掉整份配置。
TOP_KEYS = {"version", "enable-beta-ecosystems", "registries", "updates", "multi-ecosystem-groups"}
UPDATE_KEYS = {
    "package-ecosystem", "directory", "directories", "schedule", "allow", "assignees",
    "commit-message", "cooldown", "groups", "ignore", "insecure-external-code-execution",
    "labels", "milestone", "multi-ecosystem-group", "open-pull-requests-limit",
    "pull-request-branch-name", "rebase-strategy", "registries", "reviewers", "target-branch",
    "vendor", "versioning-strategy",
}
SCHEDULE_KEYS = {"interval", "day", "time", "timezone", "cronjob"}
GROUP_KEYS = {"applies-to", "dependency-type", "patterns", "exclude-patterns", "update-types"}
COMMIT_MESSAGE_KEYS = {"prefix", "prefix-development", "include"}

VITE_TOOLCHAIN_PATTERNS = ["vite", "@vitejs/*", "vitest", "@vitest/*"]


def _load():
    return yaml.safe_load(DEPENDABOT_YML.read_text(encoding="utf-8"))


def _unknown(where, mapping, allowed):
    if not isinstance(mapping, dict):
        return ["%s: expected a mapping, got %s" % (where, type(mapping).__name__)]
    return ["%s: unknown key %r" % (where, k) for k in sorted(mapping) if k not in allowed]


def problems(doc):
    """dependabot.yml 的结构问题清单；空清单 = 形状成立。"""
    out = _unknown("top", doc, TOP_KEYS)
    if not isinstance(doc, dict):
        return out
    if doc.get("version") != 2:
        out.append("top: version must be 2, got %r" % (doc.get("version"),))
    updates = doc.get("updates")
    if not isinstance(updates, list) or not updates:
        return out + ["top: updates must be a non-empty list"]
    for i, entry in enumerate(updates):
        where = "updates[%d]" % i
        out += _unknown(where, entry, UPDATE_KEYS)
        if not isinstance(entry, dict):
            continue
        for required in ("package-ecosystem", "schedule"):
            if required not in entry:
                out.append("%s: missing %r" % (where, required))
        if "directory" not in entry and "directories" not in entry:
            out.append("%s: missing directory / directories" % where)
        if "schedule" in entry:
            out += _unknown(where + ".schedule", entry["schedule"], SCHEDULE_KEYS)
            if isinstance(entry["schedule"], dict) and "interval" not in entry["schedule"]:
                out.append("%s.schedule: missing interval" % where)
        if "commit-message" in entry:
            out += _unknown(where + ".commit-message", entry["commit-message"], COMMIT_MESSAGE_KEYS)
        if "groups" in entry:
            groups = entry["groups"]
            if not isinstance(groups, dict) or not groups:
                out.append("%s.groups: must be a non-empty mapping of name -> rules" % where)
                continue
            for name, rules in groups.items():
                gw = "%s.groups.%s" % (where, name)
                out += _unknown(gw, rules, GROUP_KEYS)
                if not isinstance(rules, dict):
                    continue
                if not any(k in rules for k in ("patterns", "dependency-type", "update-types")):
                    out.append("%s: needs patterns / dependency-type / update-types" % gw)
                for key in ("patterns", "exclude-patterns", "update-types"):
                    if key in rules and not (isinstance(rules[key], list) and rules[key]
                                             and all(isinstance(p, str) for p in rules[key])):
                        out.append("%s.%s: must be a non-empty list of strings" % (gw, key))
    return out


def _entry(doc, ecosystem):
    matches = [u for u in doc["updates"] if u.get("package-ecosystem") == ecosystem]
    assert len(matches) == 1, "expected exactly one %s entry, got %d" % (ecosystem, len(matches))
    return matches[0]


class ShapeTestCase(unittest.TestCase):
    def setUp(self):
        self.doc = _load()

    def test_file_has_no_structural_problems(self):
        self.assertEqual(problems(self.doc), [])

    def test_two_ecosystems_weekly_with_the_pr_cap(self):
        self.assertEqual([u["package-ecosystem"] for u in self.doc["updates"]], ["github-actions", "npm"])
        self.assertEqual(_entry(self.doc, "github-actions")["directory"], "/")
        self.assertEqual(_entry(self.doc, "npm")["directory"], "/web")
        for entry in self.doc["updates"]:
            self.assertEqual(entry["schedule"]["interval"], "weekly")
            self.assertEqual(entry["open-pull-requests-limit"], 5)
            self.assertEqual(entry["labels"], ["dependencies"])


class ViteToolchainGroupTestCase(unittest.TestCase):
    def setUp(self):
        self.npm = _entry(_load(), "npm")
        self.group = self.npm["groups"]["vite-toolchain"]

    def test_group_patterns_are_exactly_the_vite_toolchain(self):
        self.assertEqual(self.npm["groups"], {"vite-toolchain": {"patterns": VITE_TOOLCHAIN_PATTERNS}})

    def test_group_covers_the_installed_toolchain_and_nothing_at_runtime(self):
        pkg = json.loads(WEB_PACKAGE_JSON.read_text(encoding="utf-8"))
        dev = pkg["devDependencies"]

        def grouped(name):
            return any(fnmatch.fnmatchcase(name, p) for p in self.group["patterns"])

        for name in ("vite", "@vitejs/plugin-react", "vitest"):
            self.assertIn(name, dev, "%s left web/package.json — refit the vite-toolchain group" % name)
            self.assertTrue(grouped(name), "%s no longer matches the vite-toolchain patterns" % name)
        for name in pkg["dependencies"]:
            self.assertFalse(grouped(name), "runtime dependency %s must not ride the toolchain group" % name)
        # the rest of the dev toolchain keeps its own PRs
        for name in ("typescript", "jsdom", "@testing-library/react", "@playwright/test", "axe-core"):
            self.assertIn(name, dev)
            self.assertFalse(grouped(name), "%s is not vite toolchain" % name)

    def test_grouping_disables_no_update(self):
        # grouping only changes PR shape: no ignore / allow filters, no applies-to
        # (security updates keep their own path, non-matching packages keep single PRs)
        self.assertNotIn("ignore", self.npm)
        self.assertNotIn("allow", self.npm)
        self.assertNotIn("applies-to", self.group)
        self.assertNotIn("exclude-patterns", self.group)


class CheckerBitesTestCase(unittest.TestCase):
    """阴性对照：检查器对真实文件安静，对典型手滑必须叫。"""

    def setUp(self):
        self.doc = _load()
        self.npm = _entry(self.doc, "npm")

    def test_misspelled_patterns_key_is_caught(self):
        bad = copy.deepcopy(self.doc)
        group = _entry(bad, "npm")["groups"]["vite-toolchain"]
        group["pattern"] = group.pop("patterns")
        found = problems(bad)
        self.assertTrue(any("unknown key 'pattern'" in p for p in found), found)
        self.assertTrue(any("needs patterns" in p for p in found), found)

    def test_groups_indented_under_schedule_is_caught(self):
        bad = copy.deepcopy(self.doc)
        entry = _entry(bad, "npm")
        entry["schedule"]["groups"] = entry.pop("groups")
        self.assertTrue(any("schedule: unknown key 'groups'" in p for p in problems(bad)))

    def test_unknown_update_key_and_wrong_version_are_caught(self):
        bad = copy.deepcopy(self.doc)
        bad["version"] = 1
        _entry(bad, "npm")["open-pull-request-limit"] = 5
        found = problems(bad)
        self.assertTrue(any("version must be 2" in p for p in found), found)
        self.assertTrue(any("unknown key 'open-pull-request-limit'" in p for p in found), found)


if __name__ == "__main__":
    unittest.main()
