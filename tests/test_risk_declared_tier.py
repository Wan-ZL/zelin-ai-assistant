"""risk — the declared-tier / external-reason helpers behind effective_tier (§50).

Pins the P3b split of ``effective_tier``: blank tier strings fall back to T1,
the explicit stamp wins over the sources computation (reason text is the
stamp's), and a non-external card yields no reason at all.
"""
import unittest

from act.lib import risk


class DeclaredTierTestCase(unittest.TestCase):
    def test_blank_and_whitespace_tier_default_to_t1(self):
        self.assertEqual(risk._declared_tier({"tier": ""}), "T1")
        self.assertEqual(risk._declared_tier({"tier": "   "}), "T1")
        self.assertEqual(risk._declared_tier({}), "T1")

    def test_declared_tier_is_stripped_not_normalised(self):
        self.assertEqual(risk._declared_tier({"tier": " T0 "}), "T0")
        self.assertEqual(risk._declared_tier({"tier": "t2"}), "t2")


class ExternalReasonTestCase(unittest.TestCase):
    def test_stamp_wins_over_sources(self):
        card = {"origin_trust": " External ", "sources": [{"channel": "quick"}]}
        self.assertEqual(risk._external_reason(card), "origin_trust=external")

    def test_sources_external_without_stamp(self):
        card = {"sources": [{"channel": "slack"}]}
        self.assertEqual(risk._external_reason(card), "sources=external")

    def test_hand_card_has_no_reason(self):
        self.assertIsNone(risk._external_reason({"origin_trust": "hand",
                                                 "sources": [{"channel": "quick"}]}))
        self.assertIsNone(risk._external_reason({"sources": None}))

    def test_effective_tier_reason_is_the_helper_reason(self):
        card = {"tier": "T0", "sources": [{"channel": "gmail"}]}
        et = risk.effective_tier(card)
        self.assertEqual((et.tier, et.forced_expand, et.reason),
                         ("T2", True, "sources=external"))


if __name__ == "__main__":
    unittest.main()
