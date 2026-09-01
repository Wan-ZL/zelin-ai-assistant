"""install.sh — retired launchd labels are PROVEN unloaded; orphans are reported (§55).

2026-08-31 audit L3: the iMessage radar agent (transport removed v0.21) kept
running for 51 days and wrote 23,613 tracebacks because install.sh's RETIRED
step piped `launchctl bootout` failures into /dev/null and never looked back;
doctor only ever checked labels that still had a template, so the orphan was
structurally invisible. Pinned here, executing the REAL install.sh functions
against a fake `launchctl` on PATH:

- launchd_retire unloads + deletes the plist + asserts via `launchctl list`;
  a label that survives bootout is reported with [ERR ] and collected in
  RETIRED_STILL_LOADED (install.sh turns that into launchd_retired=fail);
- launchd_orphans lists com.zelin.aiassistant.* labels (loaded OR left in
  ~/Library/LaunchAgents) that have no template in act/launchd — other
  vendors' labels and templated labels are never orphans.

POSIX-only (install.sh is the macOS/Linux installer; install.ps1 has its own).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_WIN = sys.platform.startswith("win")

_FNS = ("launchd_unload", "launchd_label_loaded", "launchd_retire",
        "launchd_orphans")

# the fake launchctl: `list` prints $FAKE_LIST; `bootout gui/UID/label` drops
# the label from $FAKE_LIST unless it is named in $FAKE_BOOTOUT_FAILS; every
# call is appended to $FAKE_CALLS. `unload <plist>` mirrors bootout by basename.
_FAKE_LAUNCHCTL = r'''#!/bin/bash
echo "$*" >> "$FAKE_CALLS"
case "$1" in
  list) cat "$FAKE_LIST"; exit 0 ;;
  bootout) label="${2##*/}" ;;
  unload) b="$(basename "$2")"; label="${b%.plist}" ;;
  *) exit 0 ;;
esac
case " $FAKE_BOOTOUT_FAILS " in
  *" $label "*) exit 1 ;;
esac
grep -v "	$label\$" "$FAKE_LIST" > "$FAKE_LIST.new" || true
mv "$FAKE_LIST.new" "$FAKE_LIST"
exit 0
'''


def _prelude():
    return "".join(
        "eval \"$(awk '/^%s\\(\\) \\{/,/^\\}/' \"$REPO/install.sh\")\"\n" % fn
        for fn in _FNS)


@unittest.skipIf(_WIN, "install.sh is POSIX-only; the Windows installer is install.ps1")
class LaunchdRetireTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="retire-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        shim = self.bin / "launchctl"
        shim.write_text(_FAKE_LAUNCHCTL, encoding="utf-8")
        shim.chmod(0o755)
        self.la_dir = self.tmp / "LaunchAgents"
        self.la_dir.mkdir()
        self.list_file = self.tmp / "launchctl.list"
        self.calls = self.tmp / "calls.log"
        self.calls.write_text("", encoding="utf-8")

    def _run(self, body: str, listing: str, bootout_fails: str = ""):
        self.list_file.write_text(listing, encoding="utf-8")
        script = (
            "set -u\n"
            "ok()   { printf '  [ ok ] %s\\n' \"$1\"; }\n"
            "warn() { printf '  [warn] %s\\n' \"$1\"; }\n"
            "info() { printf '  [info] %s\\n' \"$1\"; }\n"
            "UID_NUM=501\n"
            'LA_DIR="$LA_DIR_IN"\n'
            'REPO_ROOT="$REPO"\n'
            "RETIRED_STILL_LOADED=''\n"
            + _prelude() + body)
        env = {**os.environ,
               "PATH": "%s:%s" % (self.bin, os.environ.get("PATH", "")),
               "REPO": str(REPO), "LA_DIR_IN": str(self.la_dir),
               "FAKE_LIST": str(self.list_file), "FAKE_CALLS": str(self.calls),
               "FAKE_BOOTOUT_FAILS": bootout_fails}
        proc = subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, timeout=60, env=env)
        return proc

    def test_retire_unloads_deletes_and_proves_it(self):
        plist = self.la_dir / "com.zelin.aiassistant.imessageradar.plist"
        plist.write_text("<plist/>", encoding="utf-8")
        proc = self._run(
            'launchd_retire com.zelin.aiassistant.imessageradar\n'
            'printf "STILL=[%s]\\n" "$RETIRED_STILL_LOADED"\n',
            "4242\t0\tcom.zelin.aiassistant.actd\n"
            "-\t1\tcom.zelin.aiassistant.imessageradar\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("unloaded retired agent com.zelin.aiassistant.imessageradar",
                      proc.stdout)
        self.assertIn("STILL=[]", proc.stdout)
        self.assertFalse(plist.exists())
        self.assertIn("bootout gui/501/com.zelin.aiassistant.imessageradar",
                      self.calls.read_text(encoding="utf-8"))
        self.assertNotIn("imessageradar", self.list_file.read_text(encoding="utf-8"))

    def test_retire_that_survives_bootout_is_loud_and_collected(self):
        # the 51-day case: bootout fails, launchctl list still shows the label
        proc = self._run(
            'launchd_retire com.zelin.aiassistant.imessageradar\n'
            'printf "STILL=[%s]\\n" "$RETIRED_STILL_LOADED"\n',
            "-\t1\tcom.zelin.aiassistant.imessageradar\n",
            bootout_fails="com.zelin.aiassistant.imessageradar")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("[ERR ] retired agent com.zelin.aiassistant.imessageradar is "
                      "STILL loaded", proc.stderr)
        self.assertIn("launchctl bootout gui/501/com.zelin.aiassistant.imessageradar",
                      proc.stdout)
        self.assertIn("STILL=[ com.zelin.aiassistant.imessageradar]", proc.stdout)

    def test_retire_of_an_absent_label_is_silent(self):
        proc = self._run('launchd_retire com.zelin.aiassistant.radar\n',
                         "4242\t0\tcom.zelin.aiassistant.actd\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertEqual(proc.stderr.strip(), "")

    def test_orphans_are_prefixed_labels_without_a_template(self):
        (self.la_dir / "com.zelin.aiassistant.oldthing.plist").write_text(
            "<plist/>", encoding="utf-8")
        (self.la_dir / "com.zelin.aiassistant.actd.plist").write_text(
            "<plist/>", encoding="utf-8")
        proc = self._run(
            'launchd_orphans\n',
            "4242\t0\tcom.zelin.aiassistant.actd\n"
            "-\t1\tcom.zelin.aiassistant.imessageradar\n"
            "-\t0\tcom.zelin.storageguard\n"
            "77\t0\tcom.apple.unrelated\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(),
                         ["com.zelin.aiassistant.imessageradar",
                          "com.zelin.aiassistant.oldthing"])

    def test_no_orphans_prints_nothing(self):
        proc = self._run('launchd_orphans\n',
                         "4242\t0\tcom.zelin.aiassistant.actd\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
