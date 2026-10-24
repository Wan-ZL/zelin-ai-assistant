"""install.sh 1.x migration + the one-line crontab (CONTRACT §78 / §18 / §23, v2.0.0).

A 1.x install leaves resident pieces the lite footprint (§78) does not allow:
ten LaunchAgents (actd, autodeploy, the radars, syncd, weeklydigest and the two
ingest-side `com.zelin.screenpipe-*` agents), skills symlinks from
scripts/skills_sync.sh inside the owner's ~/.claude/skills, the auto-deploy
deploy_state.json, and cron lines for act.radar / act.digest /
act.analytics_sync. Pinned here by sourcing the REAL install.sh (it runs main
only when executed) with fake launchctl / crontab on PATH, a throwaway $HOME and
a fixture repo — nothing real is booted out, rewritten or unlinked:

  - migrate_from_1x boots out + deletes every retired label it finds, proves it
    (a survivor = `migrate=fail`), leaves other vendors' labels alone, and under
    --no-launchd never calls launchctl at all;
  - only entries of ~/.claude/skills that are symlinks resolving into this repo
    are unlinked (absolute, relative, dangling into a removed repo dir); the
    owner's real skill directories and links elsewhere are never touched;
  - both deploy_state.json files go; the transcript cleanup is PRINTED (never
    run — the projects dir is the owner's) only when 1.x leftovers were found;
  - compose_crontab keeps every unrelated line verbatim (the owner's own jobs,
    comments), drops the 1.x chain with its radar tail and the retired module
    lines, and ends with exactly one ingest chain; it is idempotent, and
    install_cron_lines writes nothing when the crontab already has that shape.
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

RETIRED = ("com.zelin.aiassistant.actd com.zelin.aiassistant.autodeploy "
           "com.zelin.aiassistant.gmailradar com.zelin.aiassistant.slackradar "
           "com.zelin.aiassistant.syncd com.zelin.aiassistant.weeklydigest "
           "com.zelin.aiassistant.imessageradar com.zelin.aiassistant.radar "
           "com.zelin.screenpipe-process com.zelin.screenpipe-prune").split()

# `list` prints $FAKE_LIST; bootout/unload drop the label unless it is named in
# $FAKE_BOOTOUT_FAILS; every call is logged to $FAKE_CALLS.
FAKE_LAUNCHCTL = r'''#!/bin/bash
echo "launchctl $*" >> "$FAKE_CALLS"
case "$1" in
  list) cat "$FAKE_LIST" 2>/dev/null; exit 0 ;;
  bootout) label="${2##*/}" ;;
  unload) b="$(basename "$2")"; label="${b%.plist}" ;;
  *) exit 0 ;;
esac
case " ${FAKE_BOOTOUT_FAILS:-} " in *" $label "*) exit 1 ;; esac
grep -v "	$label\$" "$FAKE_LIST" > "$FAKE_LIST.new" 2>/dev/null || true
mv "$FAKE_LIST.new" "$FAKE_LIST"
exit 0
'''
FAKE_CRONTAB = r'''#!/bin/bash
echo "crontab $*" >> "$FAKE_CALLS"
if [ "${1:-}" = "-l" ]; then cat "$FAKE_CRON" 2>/dev/null; exit 0; fi
if [ "${1:-}" = "-" ]; then cat > "$FAKE_CRON"; exit 0; fi
exit 0
'''

OWNER_LINE = "0 9 * * * /Users/x/Projects/最佳拍档-transcripts/update.sh >> /Users/x/Projects/最佳拍档-transcripts/update.log 2>&1"
OLD_CHAIN = ("*/30 * * * * cd /r && export PATH=/b:$PATH AIASSISTANT_CRON=1 && ./ingest/screenpipe-export.sh "
             "&& ./ingest/screenpipe-cleanup.sh && { ./ingest/process-screenpipe.sh || [ $? -eq 3 ]; } "
             "&& python3 -m act.radar --once >> /r/state/radar.cron.log 2>&1")
DIGEST = "7 9 * * * cd /r && python3 -m act.digest >> /r/state/digest.log 2>&1"
TELEMETRY = "17 * * * * cd /r && python3 -m act.analytics_sync --once >> /r/state/analytics_sync.log 2>&1"
WEEKLY = "0 9 * * 1 cd /r && python3 -m act.weekly_digest"
COMMENT = "# screenpipe-export.sh used to run here (kept: a comment is not a job)"
CHAIN = "*/30 * * * * NEW CHAIN ./ingest/screenpipe-export.sh"


@unittest.skipIf(_WIN, "install.sh is POSIX-only")
class _Sourced(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-migrate-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.la = self.home / "Library" / "LaunchAgents"
        self.la.mkdir(parents=True)
        self.repo = self.tmp / "repo"
        (self.repo / "state").mkdir(parents=True)
        (self.repo / "act" / "launchd").mkdir(parents=True)
        (self.repo / "act" / "launchd" / "com.zelin.aiassistant.server.plist").write_text("<plist/>")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, text in (("launchctl", FAKE_LAUNCHCTL), ("crontab", FAKE_CRONTAB)):
            p = self.bin / name
            p.write_text(text, encoding="utf-8")
            p.chmod(0o755)
        self.calls = self.tmp / "calls.log"
        self.listing = self.tmp / "launchctl.list"
        self.cron = self.tmp / "crontab.txt"

    def run_sourced(self, body, *, listing="", cron=None, env=None):
        self.listing.write_text(listing, encoding="utf-8")
        if cron is not None:
            self.cron.write_text(cron, encoding="utf-8")
        script = ('source "$INSTALL_SH"\n'
                  'REPO_ROOT="$FIXTURE_REPO"\n'
                  'APP_SUPPORT_DIR="$HOME/Library/Application Support/ZelinAIAssistant"\n'
                  + body)
        full = {"PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % self.bin,
                "HOME": str(self.home), "INSTALL_SH": str(REPO / "install.sh"),
                "FIXTURE_REPO": str(self.repo), "FAKE_CALLS": str(self.calls),
                "FAKE_LIST": str(self.listing), "FAKE_CRON": str(self.cron),
                **(env or {})}
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              timeout=60, env=full)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        return proc

    def calls_log(self):
        return self.calls.read_text(encoding="utf-8") if self.calls.exists() else ""


class MigrateFrom1xTestCase(_Sourced):
    BODY = 'migrate_from_1x\nprintf "@@%s" "$REPORT_STEPS"\n'

    def _plant_agents(self, labels):
        for lbl in labels:
            (self.la / (lbl + ".plist")).write_text("<plist/>", encoding="utf-8")
        return "".join("-\t0\t%s\n" % lbl for lbl in labels)

    def test_every_retired_label_is_booted_out_and_deleted(self):
        listing = self._plant_agents(RETIRED)
        (self.la / "com.vendor.other.plist").write_text("<plist/>")
        listing += "-\t0\tcom.vendor.other\n4242\t0\tcom.zelin.aiassistant.server\n"
        proc = self.run_sourced(self.BODY, listing=listing)
        report = proc.stdout.split("@@", 1)[1]
        self.assertTrue(report.startswith("migrate=ok:agents:"), report)
        for lbl in RETIRED:
            self.assertFalse((self.la / (lbl + ".plist")).exists(), lbl)
            self.assertIn("bootout gui/%d/%s" % (os.getuid(), lbl), self.calls_log())
            self.assertIn(lbl, report)
        self.assertTrue((self.la / "com.vendor.other.plist").exists())
        self.assertNotIn("com.vendor.other", self.calls_log().replace("launchctl list", ""))
        self.assertNotIn("bootout gui/%d/com.zelin.aiassistant.server" % os.getuid(), self.calls_log())

    def test_a_survivor_is_a_failed_step(self):
        listing = self._plant_agents(["com.zelin.screenpipe-prune"])
        proc = self.run_sourced(self.BODY, listing=listing,
                                env={"FAKE_BOOTOUT_FAILS": "com.zelin.screenpipe-prune"})
        report = proc.stdout.split("@@", 1)[1]
        self.assertTrue(report.startswith("migrate=fail:still loaded: com.zelin.screenpipe-prune"), report)
        self.assertIn("STILL loaded", proc.stderr)

    def test_no_launchd_never_calls_launchctl(self):
        listing = self._plant_agents(["com.zelin.aiassistant.actd"])
        proc = self.run_sourced("NO_LAUNCHD=1\n" + self.BODY, listing=listing)
        self.assertNotIn("launchctl", self.calls_log())
        self.assertTrue((self.la / "com.zelin.aiassistant.actd.plist").exists())
        self.assertIn("--no-launchd: 1.x LaunchAgents not inspected", proc.stdout)

    def test_only_symlinks_into_this_repo_are_unlinked(self):
        skills = self.home / ".claude" / "skills"
        skills.mkdir(parents=True)
        (self.repo / "skills" / "board-agent").mkdir(parents=True)
        os.symlink(self.repo / "skills" / "board-agent", skills / "board-agent")      # absolute, live
        os.symlink(self.repo / "skills" / "gone", skills / "dangling")                # into a removed dir
        os.symlink(os.path.relpath(self.repo / "skills" / "board-agent", skills), skills / "relative")
        (skills / "write-better").mkdir()                                             # the owner's own skill
        (skills / "write-better" / "SKILL.md").write_text("mine")
        other = self.tmp / "elsewhere" / "test-code"
        other.mkdir(parents=True)
        os.symlink(other, skills / "test-code")                                       # a link elsewhere
        projects = self.home / ".claude" / "projects" / "-x-state"
        projects.mkdir(parents=True)
        proc = self.run_sourced(self.BODY)
        self.assertEqual(sorted(os.listdir(skills)), ["test-code", "write-better"])
        self.assertEqual((skills / "write-better" / "SKILL.md").read_text(), "mine")
        self.assertTrue((self.repo / "skills" / "board-agent").is_dir(), "the target is never touched")
        report = proc.stdout.split("@@", 1)[1]
        for name in ("board-agent", "dangling", "relative"):
            self.assertIn(name, report)
        # leftovers found → the transcript command is printed, never run
        self.assertIn("rm -rf ~/.claude/projects/", proc.stdout)
        self.assertTrue(projects.is_dir())

    def test_symlinked_repo_path_still_matches(self):
        """~/Projects -> /Volumes/… (§55): a link written through the convenience
        path resolves into the physical repo and is ours."""
        skills = self.home / ".claude" / "skills"
        skills.mkdir(parents=True)
        alias = self.tmp / "alias"
        os.symlink(self.repo, alias)
        os.symlink(alias / "skills" / "board-agent", skills / "board-agent")
        self.run_sourced(self.BODY)
        self.assertEqual(os.listdir(skills), [])

    def test_deploy_state_files_are_removed(self):
        support = self.home / "Library" / "Application Support" / "ZelinAIAssistant"
        support.mkdir(parents=True)
        (support / "deploy_state.json").write_text("{}")
        (support / "home.txt").write_text("/r\n")
        (self.repo / "state" / "deploy_state.json").write_text("{}")
        proc = self.run_sourced(self.BODY)
        self.assertFalse((support / "deploy_state.json").exists())
        self.assertFalse((self.repo / "state" / "deploy_state.json").exists())
        self.assertTrue((support / "home.txt").exists())
        self.assertIn("deploy_state.json", proc.stdout.split("@@", 1)[1])

    def test_a_clean_machine_has_nothing_to_migrate(self):
        proc = self.run_sourced(self.BODY, listing="4242\t0\tcom.zelin.aiassistant.server\n")
        self.assertEqual(proc.stdout.split("@@", 1)[1].strip(), "migrate=ok:nothing to migrate")
        self.assertNotIn("~/.claude/projects", proc.stdout)

    def test_transcript_slug_follows_claude_code_naming(self):
        proc = self.run_sourced('claude_project_slug "/Users/zelin/Projects/zelin-ai-assistant/state"\n')
        self.assertEqual(proc.stdout, "-Users-zelin-Projects-zelin-ai-assistant-state")


class ComposeCrontabTestCase(_Sourced):
    def compose(self, current):
        proc = self.run_sourced('INGEST_CHAIN="$CHAIN"\nprintf "%s\\n" "$CUR" | compose_crontab\n',
                                env={"CHAIN": CHAIN, "CUR": current})
        return proc.stdout

    def test_keeps_unrelated_lines_and_ends_with_one_chain(self):
        current = "\n".join([OLD_CHAIN, OWNER_LINE, DIGEST, COMMENT, TELEMETRY, WEEKLY])
        out = self.compose(current).splitlines()
        self.assertEqual(out, [OWNER_LINE, COMMENT, CHAIN])

    def test_idempotent(self):
        once = self.compose("\n".join([OWNER_LINE, OLD_CHAIN]))
        self.assertEqual(self.compose(once.rstrip("\n")), once)

    def test_empty_crontab_gets_the_chain_only(self):
        self.assertEqual(self.compose(""), CHAIN + "\n")

    def test_install_cron_lines_writes_once_then_never_again(self):
        body = ('INGEST_CHAIN="$CHAIN"; CRON_PY=/py\ninstall_cron_lines\n'
                'printf "@@%s" "$REPORT_STEPS"\n')
        proc = self.run_sourced(body, cron="\n".join([OLD_CHAIN, OWNER_LINE, DIGEST]) + "\n",
                                env={"CHAIN": CHAIN})
        self.assertEqual(self.cron.read_text(encoding="utf-8").splitlines(), [OWNER_LINE, CHAIN])
        self.assertIn("cron=ok:ingest chain installed; removed 2 retired line(s)", proc.stdout)
        self.calls.unlink()
        proc = self.run_sourced(body, env={"CHAIN": CHAIN})
        self.assertIn("cron=ok:already installed", proc.stdout)
        self.assertNotIn("crontab -\n", self.calls_log())

    def test_no_launchd_never_reads_or_writes_the_crontab(self):
        proc = self.run_sourced('NO_LAUNCHD=1\nINGEST_CHAIN="$CHAIN"\ninstall_cron_lines\n'
                                'printf "@@%s" "$REPORT_STEPS"\n', cron=OWNER_LINE + "\n",
                                env={"CHAIN": CHAIN})
        self.assertIn("cron=skipped:--no-launchd: crontab not touched", proc.stdout)
        self.assertNotIn("crontab", self.calls_log())
        self.assertEqual(self.cron.read_text(encoding="utf-8"), OWNER_LINE + "\n")

    def test_the_real_chain_is_the_only_line_of_ours(self):
        text = (REPO / "install.sh").read_text(encoding="utf-8")
        for retired in ("DIGEST_LINE=", "TELEMETRY_LINE=", "act.radar --once", "radar.cron.log"):
            self.assertNotIn(retired, text)
        self.assertIn(">> $REPO_ROOT/state/ingest.cron.log 2>&1", text)


if __name__ == "__main__":
    unittest.main()
