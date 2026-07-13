#!/usr/bin/env python3
"""interop.py — Python half of the cross-language E2E crypto interop test.

Proves the Swift `ios/Sources/E2E.swift` byte-interops with Python
`act/lib/e2e.py`. Two subcommands:

  emit <out.json>       Python encrypts board/label/action fixtures + builds a
                        pairing blob (the DOWN + pairing directions the Swift
                        side must decrypt), and lists plaintexts the Swift side
                        must encrypt (the UP direction).

  verify <swift.json>   Decrypt the blobs the Swift harness produced (UP
                        direction) and assert the plaintext round-trips.

`act.lib.e2e` must be importable — set PYTHONPATH to a repo checkout that has
`act/lib/e2e.py` (the feat/ios-cloud-crypto Phase-1a work; run.sh wires this).
"""
import base64
import json
import sys

from act.lib import e2e

B = lambda b: base64.b64encode(bytes(b)).decode("ascii")  # noqa: E731
U = base64.b64decode

_K = bytes(range(1, 33))                      # deterministic 32-byte key
_DEV = "11111111-1111-4111-8111-111111111111"
_AID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_BOARD = ('{"generated_at":"2026-07-12T00:00:00Z","counts":{"needs_approval":1},'
          '"needs_approval":[{"id":"R-001","title":"公司 Mac 上的提案","show_cost":false}]}').encode()
_ACTION = b'{"action":"approve","comment":null,"id":"R-001","ts":"2026-07-12T00:00:00Z"}'


def emit(path: str) -> None:
    doc = {
        # DOWN + pairing: Python encrypts, Swift must decrypt.
        "decrypt_cases": [
            {"kind": "board", "k": B(_K), "epoch": 1, "device_id": _DEV, "seq": 7,
             "plaintext": B(_BOARD), "blob": B(e2e.encrypt_board(_K, 1, _DEV, 7, _BOARD))},
            {"kind": "label", "k": B(_K), "epoch": 5, "device_id": _DEV,
             "plaintext": B("公司 Mac".encode()),
             "blob": B(e2e.encrypt_label(_K, 5, _DEV, "公司 Mac"))},
            {"kind": "action", "k": B(_K), "epoch": 1, "device_id": _DEV, "action_id": _AID,
             "board_seq": 7, "plaintext": B(_ACTION),
             "blob": B(e2e.encrypt_action(_K, 1, _DEV, _AID, 7, _ACTION))},
            {"kind": "action", "k": B(_K), "epoch": 1, "device_id": _DEV, "action_id": _AID,
             "board_seq": None, "plaintext": B(_ACTION),
             "blob": B(e2e.encrypt_action(_K, 1, _DEV, _AID, None, _ACTION))},
        ],
        "pairing": {
            "blob": e2e.build_pairing_blob(_DEV, 3, _K, "书房 Mac mini"),
            "expect": {"device_id": _DEV, "epoch": 3, "key": B(_K), "label": "书房 Mac mini"},
        },
        # UP: Swift must encrypt these; verify() decrypts what Swift produced.
        "encrypt_specs": [
            {"kind": "action", "k": B(_K), "epoch": 1, "device_id": _DEV, "action_id": _AID,
             "board_seq": 7, "plaintext": B(_ACTION)},
            {"kind": "action", "k": B(_K), "epoch": 2, "device_id": _DEV, "action_id": _AID,
             "board_seq": None, "plaintext": B(_ACTION)},
            {"kind": "board", "k": B(_K), "epoch": 9, "device_id": _DEV, "seq": 42,
             "plaintext": B(_BOARD)},
            {"kind": "label", "k": B(_K), "epoch": 1, "device_id": _DEV,
             "plaintext": B("iPhone 15".encode())},
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    print(f"emit: wrote {len(doc['decrypt_cases'])} decrypt cases + "
          f"{len(doc['encrypt_specs'])} encrypt specs + pairing → {path}")


def verify(path: str) -> int:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    ok = True
    for i, c in enumerate(doc["encrypted"]):
        k, ep, dev = U(c["k"]), int(c["epoch"]), c["device_id"]
        blob, want = U(c["blob"]), U(c["plaintext"])
        try:
            if c["kind"] == "action":
                got = e2e.decrypt_action(k, ep, dev, c["action_id"], c["board_seq"], blob)
            elif c["kind"] == "board":
                got = e2e.decrypt_board(k, ep, dev, int(c["seq"]), blob)
            elif c["kind"] == "label":
                got = e2e.decrypt_label(k, ep, dev, blob).encode()
            else:
                raise ValueError(f"unknown kind {c['kind']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL[{i}] {c['kind']}: Python could not decrypt Swift blob: {exc}")
            ok = False
            continue
        if got == want:
            print(f"  PASS[{i}] {c['kind']}: Python decrypted Swift blob, plaintext matches")
        else:
            print(f"  FAIL[{i}] {c['kind']}: plaintext mismatch\n    want={want!r}\n    got ={got!r}")
            ok = False
    print("verify:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("emit", "verify"):
        print(__doc__)
        sys.exit(2)
    sys.exit(emit(sys.argv[2]) or 0 if sys.argv[1] == "emit" else verify(sys.argv[2]))
