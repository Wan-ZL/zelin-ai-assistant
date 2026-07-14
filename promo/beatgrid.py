#!/usr/bin/env python3
"""Estimate BPM and first-beat offset of a music track (stdlib only).

    python3 promo/beatgrid.py path/to/track.mp3 [--bpm-hint 114]

Decodes via ffmpeg to mono 11025 Hz PCM, builds an onset envelope
(positive spectral-energy flux per 23 ms hop), autocorrelates it to find
the tempo, then scans the beat phase that best aligns a comb of beats
with the envelope. Prints BPM, beat period and offset of beat 0 — feed
those into stage/timeline.js so scene cuts land on bar boundaries.
"""
from __future__ import annotations

import argparse
import struct
import subprocess
import sys

SR = 11025
HOP = 256  # ~23.2 ms
HOP_S = HOP / SR


def decode(path: str) -> list[float]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"],
        capture_output=True, check=True).stdout
    n = len(raw) // 2
    return list(struct.unpack(f"<{n}h", raw[: n * 2]))


def onset_envelope(pcm: list[float]) -> list[float]:
    energies = []
    for i in range(0, len(pcm) - HOP, HOP):
        s = 0.0
        for v in pcm[i:i + HOP]:
            s += v * v
        energies.append(s / HOP)
    # positive flux, lightly smoothed
    flux = [0.0]
    for a, b in zip(energies, energies[1:]):
        flux.append(max(0.0, b - a))
    m = max(flux) or 1.0
    return [f / m for f in flux]


def best_bpm(env: list[float], lo: float, hi: float) -> float:
    n = len(env)
    best, best_score = lo, -1.0
    # 0.05 BPM resolution around the autocorrelation peak is overkill;
    # 0.25 steps keep this fast in pure python.
    bpm = lo
    while bpm <= hi:
        period = 60.0 / bpm / HOP_S
        score, count, i = 0.0, 0, period
        while i < n:
            score += env[int(i)]
            count += 1
            i += period
        if count:
            score /= count
        if score > best_score:
            best, best_score = bpm, score
        bpm += 0.25
    return best


def best_offset(env: list[float], bpm: float) -> float:
    period = 60.0 / bpm / HOP_S
    best, best_score = 0.0, -1.0
    steps = int(period)
    for k in range(steps):
        score, count, i = 0.0, 0, float(k)
        while i < len(env):
            score += env[int(i)]
            count += 1
            i += period
        if count:
            score /= count
        if score > best_score:
            best, best_score = float(k), score
    return best * HOP_S


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("track")
    ap.add_argument("--bpm-hint", type=float, default=None,
                    help="search ±8 BPM around this instead of 70–180")
    args = ap.parse_args()

    env = onset_envelope(decode(args.track))
    lo, hi = (args.bpm_hint - 8, args.bpm_hint + 8) if args.bpm_hint else (70, 180)
    bpm = best_bpm(env, lo, hi)
    off = best_offset(env, bpm)
    beat = 60.0 / bpm
    print(f"bpm={bpm:.2f} beat={beat:.4f}s bar={4 * beat:.4f}s offset={off:.3f}s")
    print(f"timeline.js: const BPM={bpm:.2f}, OFFSET={off:.3f};")
    return 0


if __name__ == "__main__":
    sys.exit(main())
