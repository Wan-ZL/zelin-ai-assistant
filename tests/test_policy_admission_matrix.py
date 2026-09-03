"""policy.may_auto_dispatch admission matrix — characterization golden (CONTRACT §50 / §51 / §65).

Pins the FULL decision table of the auto-dispatch admission gate over an
enumerated product of every input the gate reads: sources shape × channel
(every CHANNEL_CLASS row, unknown / malformed / mixed / case-and-space
variants), target_kind, cost_estimate_usd shapes, tier shapes,
green_sign_required, type, target_repo (self repo / existing / missing /
blank / None), needs_mcp, six config variants (autodispatch on/off, text-
confirm line, default_target_repo fallback, self_improve on/off, foreign
repo_path) and lane_paused. ~580k cases, all pure (``path_exists`` and
``realpath`` injected — no filesystem), so the whole table runs in about two
seconds. The cost axis includes the exact text-confirm line (3.0 vs
``confirm3``) so the ``>`` / ``>=`` boundary is pinned (mutation round 1).

Three pins, from loose to strict, so a break is diagnosable:
  * the SET of reason tokens that ever occur (against ``policy.MAY_REASONS``);
  * a HISTOGRAM reason → count;
  * a SHA-256 over every ``case → (ok, reason)`` line (byte-for-byte).
Plus a curated sample list (first three cases per reason) stored in the
fixture for humans. The fixture ``tests/fixtures/policy_admission_matrix.json``
was minted from the pre-refactor policy (P3b); regenerate ONLY with an
intentional admission change in the same PR (``REGEN_POLICY_MATRIX=1``) and
say so in CONTRACT §51.
"""
import hashlib
import itertools
import json
import os
import unittest
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandbox env before act imports

from act.lib import config, policy
from act.lib.config import Config

FIXTURE = Path(__file__).parent / "fixtures" / "policy_admission_matrix.json"

_SELF = str(config.HOME)   # the §65 lane's only admitted repo (symbolic "self")


def _src(channel):
    return {"who": "someone", "channel": channel, "date": "2026-09-02", "quote": "q"}


# name → sources value (names are what the digest hashes — never raw paths)
SOURCES = {f"chan:{c}": [_src(c)] for c in policy.CHANNEL_CLASS}
SOURCES.update({
    "chan:unknown": [_src("telegram")],
    "chan:none": [_src(None)],
    "chan:spaced": [_src(" Quick ")],
    "entry:not-dict": ["not-a-dict"],
    "sources:garbage": "not-a-list",
    "sources:empty": [],
    "sources:none": None,
    "mixed:quick+slack": [_src("quick"), _src("slack")],
    "mixed:self_improve+quick": [_src("self_improve"), _src("quick")],
    "mixed:self_improve+self_improve": [_src("self_improve"), _src("SELF_IMPROVE")],
})
TARGET_KIND = {"existing": "existing", "new": "new", "spaced-new": " NEW ", "none": None}
COST = {"none": None, "zero": 0, "two": 2.0, "three": 3.0, "str4.5": "4.5",
        "garbage": "abc", "bool": True, "fifty": 50}
TIER = {"T1": "T1", "T2": "T2", "spaced-t2": " t2 ", "none": None}
GREEN = {"no": False, "yes": True}
TYPE = {"other": "other", "comms": "comms", "spaced-comms": " Comms "}
REPO = {"self": _SELF, "exists": "~/repo/exists", "missing": "~/repo/missing",
        "blank": "", "none": None}
MCP = {"no": False, "yes": True}
LANE = {"open": False, "paused": True}


def _cfg(auto=None, si=None, **attrs):
    raw = {}
    if auto is not None:
        raw["autodispatch"] = auto
    if si is not None:
        raw["self_improve"] = si
    cfg = Config(raw=raw)
    for k, v in attrs.items():
        setattr(cfg, k, v)
    return cfg


CFGS = {
    "default": lambda: _cfg(require_text_confirm_above_usd=None, default_target_repo=""),
    "auto-off": lambda: _cfg(auto={"enabled": False},
                             require_text_confirm_above_usd=None, default_target_repo=""),
    "confirm3": lambda: _cfg(require_text_confirm_above_usd=3.0, default_target_repo=""),
    "fallback-repo": lambda: _cfg(require_text_confirm_above_usd=None,
                                  default_target_repo="~/repo/exists"),
    "si-off": lambda: _cfg(si={"enabled": False},
                           require_text_confirm_above_usd=None, default_target_repo=""),
    "si-foreign": lambda: _cfg(si={"repo_path": "~/other/repo"},
                               require_text_confirm_above_usd=None, default_target_repo=""),
}

# trimmed product: every dimension keeps the values that reach a distinct branch
_DIMS = (
    ("sources", list(SOURCES)),
    ("cfg", list(CFGS)),
    ("lane", list(LANE)),
    ("repo", list(REPO)),
    ("kind", ["existing", "new", "none"]),
    ("mcp", list(MCP)),
    ("tier", ["T1", "T2", "none"]),
    ("green", list(GREEN)),
    ("cost", ["none", "two", "three", "garbage", "fifty"]),   # three == the confirm3 line (boundary)
    ("type", ["other", "comms"]),
)


def _exists(path):
    return "missing" not in str(path)


def _identity(path):
    return path


def _card(sources, kind, mcp, tier, green, cost, ctype, repo):
    return {
        "sources": SOURCES[sources], "target_kind": TARGET_KIND[kind],
        "needs_mcp": MCP[mcp], "tier": TIER[tier], "green_sign_required": GREEN[green],
        "cost_estimate_usd": COST[cost], "type": TYPE[ctype], "target_repo": REPO[repo],
    }


def enumerate_matrix():
    """Yield (case_key, ok, reason) for the whole trimmed product, in a fixed order."""
    cfgs = {name: make() for name, make in CFGS.items()}
    names = [d[0] for d in _DIMS]
    for combo in itertools.product(*(d[1] for d in _DIMS)):
        c = dict(zip(names, combo))
        ok, reason = policy.may_auto_dispatch(
            _card(c["sources"], c["kind"], c["mcp"], c["tier"], c["green"],
                  c["cost"], c["type"], c["repo"]),
            cfgs[c["cfg"]], path_exists=_exists,
            lane_paused=LANE[c["lane"]], realpath=_identity)
        yield "|".join(f"{k}={c[k]}" for k in names), bool(ok), str(reason)


def build_golden():
    digest = hashlib.sha256()
    histogram = {}
    samples = {}
    n = 0
    for key, ok, reason in enumerate_matrix():
        n += 1
        digest.update(f"{key}\t{ok}\t{reason}\n".encode("utf-8"))
        histogram[reason] = histogram.get(reason, 0) + 1
        bucket = samples.setdefault(reason, [])
        if len(bucket) < 3:
            bucket.append({"case": key, "ok": ok})
    return {"cases": n, "digest": digest.hexdigest(),
            "histogram": dict(sorted(histogram.items())),
            "samples": dict(sorted(samples.items()))}


class PolicyAdmissionMatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = build_golden()
        if os.environ.get("REGEN_POLICY_MATRIX") == "1":
            FIXTURE.write_text(json.dumps(cls.golden, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
        assert FIXTURE.exists(), "golden missing — REGEN_POLICY_MATRIX=1 to mint"
        cls.expected = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_reason_vocabulary_is_exactly_the_live_tokens(self):
        seen = set(self.golden["histogram"])
        self.assertTrue(seen <= set(policy.MAY_REASONS), seen - set(policy.MAY_REASONS))
        # every token that can still be produced is exercised by the matrix
        self.assertEqual(seen, set(self.expected["histogram"]))
        self.assertIn("ok", seen)
        self.assertIn("ok:self_improve", seen)

    def test_histogram_matches_golden(self):
        self.assertEqual(self.golden["cases"], self.expected["cases"])
        self.assertEqual(self.golden["histogram"], self.expected["histogram"])

    def test_every_case_matches_golden_digest(self):
        self.assertEqual(self.golden["digest"], self.expected["digest"],
                         "admission table changed — see samples in the fixture; "
                         "REGEN_POLICY_MATRIX=1 only with a CONTRACT §51 amendment")

    def test_samples_match_golden(self):
        self.assertEqual(self.golden["samples"], self.expected["samples"])

    def test_hand_card_default_admits_and_lane_card_admits_without_cost(self):
        # two anchors, readable without the fixture
        hand = _card("chan:quick", "existing", "no", "T1", "no", "two", "other", "exists")
        lane = _card("chan:self_improve", "existing", "no", "T1", "no", "none", "other", "self")
        cfg = CFGS["default"]()
        self.assertEqual(policy.may_auto_dispatch(hand, cfg, path_exists=_exists,
                                                  realpath=_identity), (True, "ok"))
        self.assertEqual(policy.may_auto_dispatch(lane, cfg, path_exists=_exists,
                                                  realpath=_identity),
                         (True, "ok:self_improve"))


if __name__ == "__main__":
    unittest.main()
