#!/usr/bin/env python3
"""Derive the Sparkle EdDSA public key from a private key read on stdin.

Release preflight helper (.github/workflows/release.yml): the release signs
the update .pkg with the SPARKLE_ED_PRIVATE_KEY secret, while every installed
app validates enclosures against the SUPublicEDKey baked into mac/Info.plist.
Nothing else checks the two are a pair — a mismatch ships a green release
whose auto-update every client then silently discards. This script lets the
workflow assert the match BEFORE the expensive build.

stdin:  the private key exactly as Sparkle's `generate_keys -x` exports it
        (and as `sign_update -f` consumes it) — base64 of either
          - 32 bytes: the Ed25519 seed (current Sparkle format), or
          - 96 bytes: legacy `private(64) || public(32)` concatenation.
stdout: base64 of the 32-byte Ed25519 public key (the SUPublicEDKey format).

Requires the `cryptography` package for the seed format (same lazy dependency
as act/lib/e2e.py). The derivation is verified against Sparkle's own
sign_update: a signature it produces from a seed file validates against the
public key this script derives from that seed.
"""
import base64
import sys


def main() -> int:
    try:
        raw = base64.b64decode(sys.stdin.read().strip(), validate=True)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: private key is not valid base64: {exc}", file=sys.stderr)
        return 1
    if len(raw) == 32:
        # current format: the seed — derive the public half
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        pub = Ed25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    elif len(raw) == 96:
        # legacy format: sign_update itself takes the public key from the tail
        pub = raw[64:96]
    else:
        print(f"ERROR: unrecognized Sparkle private key: {len(raw)} bytes after base64-decode "
              "(expected 32 = seed, or 96 = legacy private||public)", file=sys.stderr)
        return 1
    print(base64.b64encode(pub).decode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
