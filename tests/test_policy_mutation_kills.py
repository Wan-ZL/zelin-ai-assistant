"""policy — judgments that pin the survivors of the P3b mutation round (CONTRACT §50 / §51 / §65).

``scripts/qa/mutate.py`` on act/lib/policy.py (pre-refactor, 124 sites) left 18
survivors. Six were equivalent (``_raw_block`` / ``autodispatch_config``
``and→or`` on the cfg-shape probe — both branches end in the same empty block;
the trust ranks ``3→4`` / ``0→-1`` keep the order; ``_origin_gate``'s lane flag
on an already-blocked origin is never read). The rest were real holes, each
pinned here:

  * trust ranks must be STRICTLY ordered — a tie makes ``min()`` order-dependent,
    so mixed sources would classify by list position instead of least trust;
  * ``self_improve.tick_minutes`` accepts exactly ``>= 1`` (1 stays 1, 0 → default);
  * ``same_repo`` / ``is_self_improve_sources`` fail-closed answers are the
    literal ``False`` (callers compare identity in the wire), and a blank side
    never reaches ``realpath`` (a constant realpath would otherwise "match");
  * ``auto_dispatch_note`` names its lane on BOTH branches.
"""
import itertools
import unittest

from act.lib import policy


class TrustRankOrderTest(unittest.TestCase):
    def test_every_channel_pair_classifies_by_least_trust_in_either_order(self):
        rank = {policy.HAND: 3, policy.PROPOSED: 2, policy.MEETING: 1, policy.EXTERNAL: 0}
        channels = list(policy.CHANNEL_CLASS) + ["telegram"]   # unknown → external
        for a, b in itertools.product(channels, repeat=2):
            expected = min(policy.channel_class(a), policy.channel_class(b), key=rank.get)
            got_ab = policy.classify_origin([{"channel": a}, {"channel": b}])
            got_ba = policy.classify_origin([{"channel": b}, {"channel": a}])
            self.assertEqual(got_ab, expected, (a, b))
            self.assertEqual(got_ba, expected, (b, a))

    def test_meeting_beats_proposed_and_external_beats_meeting(self):
        # the three adjacent ranks, both orders — a tie anywhere flips one of these
        self.assertEqual(policy.classify_origin([{"channel": "digest"}, {"channel": "meeting"}]),
                         policy.MEETING)
        self.assertEqual(policy.classify_origin([{"channel": "meeting"}, {"channel": "digest"}]),
                         policy.MEETING)
        self.assertEqual(policy.classify_origin([{"channel": "meeting"}, {"channel": "slack"}]),
                         policy.EXTERNAL)
        self.assertEqual(policy.classify_origin([{"channel": "slack"}, {"channel": "meeting"}]),
                         policy.EXTERNAL)
        self.assertEqual(policy.classify_origin([{"channel": "quick"}, {"channel": "digest"}]),
                         policy.PROPOSED)
        self.assertEqual(policy.classify_origin([{"channel": "digest"}, {"channel": "quick"}]),
                         policy.PROPOSED)

    def test_capture_channel_ties_break_the_same_way(self):
        self.assertEqual(policy.classify_origin([{"channel": "quick"}], "slack"), policy.EXTERNAL)
        self.assertEqual(policy.classify_origin([{"channel": "slack"}], "quick"), policy.EXTERNAL)


class SelfImproveTickMinutesTest(unittest.TestCase):
    def _minutes(self, value):
        return policy.self_improve_config({"self_improve": {"tick_minutes": value}})["tick_minutes"]

    def test_one_is_accepted_zero_falls_back(self):
        default = policy.SELF_IMPROVE_DEFAULTS["tick_minutes"]
        self.assertEqual(self._minutes(1), 1)
        self.assertEqual(self._minutes(2), 2)
        self.assertEqual(self._minutes(0), default)
        self.assertEqual(self._minutes(-5), default)
        self.assertEqual(self._minutes("1"), 1)
        self.assertEqual(self._minutes("x"), default)


class FailClosedLiteralsTest(unittest.TestCase):
    def test_same_repo_blank_side_is_false_without_consulting_realpath(self):
        constant = lambda p: "/same"   # noqa: E731 - a realpath that would "match" anything
        self.assertIs(policy.same_repo("", "/x", realpath=constant), False)
        self.assertIs(policy.same_repo("/x", "", realpath=constant), False)
        self.assertIs(policy.same_repo(None, "/x", realpath=constant), False)
        self.assertIs(policy.same_repo("  ", "/x", realpath=constant), False)
        self.assertIs(policy.same_repo("/a", "/b", realpath=constant), True)

    def test_is_self_improve_sources_fail_closed_is_literal_false(self):
        self.assertIs(policy.is_self_improve_sources([]), False)
        self.assertIs(policy.is_self_improve_sources("garbage"), False)
        self.assertIs(policy.is_self_improve_sources(None), False)
        self.assertIs(policy.is_self_improve_sources([{"channel": "self_improve"}, "x"]), False)
        self.assertIs(policy.is_self_improve_sources([{"channel": " SELF_IMPROVE "}]), True)


class AutoDispatchNoteTest(unittest.TestCase):
    def test_both_lanes_are_named(self):
        lane = policy.auto_dispatch_note("ok:self_improve", 0.0, "2026-09-02")
        hand = policy.auto_dispatch_note("ok", 2.5, "2026-09-02")
        self.assertIn("self_improve 通道免批自动派发", lane)
        self.assertIn("草稿 PR", lane)
        self.assertTrue(lane.startswith("[2026-09-02 auto-dispatch]"), lane)
        self.assertIn("hand 出身免批自动派发（est $2.5）", hand)
        self.assertTrue(hand.startswith("[2026-09-02 auto-dispatch]"), hand)
        self.assertNotIn("self_improve", hand)


if __name__ == "__main__":
    unittest.main()
