"""policy — judgments that pin the survivors of the P3b mutation round (CONTRACT §50 / §51).

``scripts/qa/mutate.py`` on act/lib/policy.py (pre-refactor, 124 sites) left 18
survivors. Six were equivalent (``_raw_block`` / ``autodispatch_config``
``and→or`` on the cfg-shape probe — both branches end in the same empty block;
the trust ranks ``3→4`` / ``0→-1`` keep the order; ``_origin_gate``'s lane flag
on an already-blocked origin is never read). The rest were real holes, each
pinned here:

  * trust ranks must be STRICTLY ordered — a tie makes ``min()`` order-dependent,
    so mixed sources would classify by list position instead of least trust;
  * ``is_self_improve_sources`` fail-closed answers are the literal ``False``
    (callers compare identity in the wire).

(The ``self_improve.tick_minutes`` / ``same_repo`` / ``auto_dispatch_note`` kills
retired with the §65 lane, D86 — those functions no longer exist.)
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


class FailClosedLiteralsTest(unittest.TestCase):
    def test_is_self_improve_sources_fail_closed_is_literal_false(self):
        self.assertIs(policy.is_self_improve_sources([]), False)
        self.assertIs(policy.is_self_improve_sources("garbage"), False)
        self.assertIs(policy.is_self_improve_sources(None), False)
        self.assertIs(policy.is_self_improve_sources([{"channel": "self_improve"}, "x"]), False)
        self.assertIs(policy.is_self_improve_sources([{"channel": " SELF_IMPROVE "}]), True)


if __name__ == "__main__":
    unittest.main()
