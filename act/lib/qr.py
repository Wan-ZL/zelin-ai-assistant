"""qr.py — a tiny, pure-stdlib QR-code encoder (no pip deps; PyYAML-only floor).

契约：CONTRACT §41 区的 QR-only 能力模型（v0.30.0 supersession note：配对二维码
= 该 Mac 看板的主钥匙，`e2e.build_channel_qr` 的载荷经这里渲染成终端/PNG）。

Just enough to turn a channel pairing blob (``act.lib.e2e.build_channel_qr`` —
a ~120–200 char base64url string) into a *scannable* QR code, both as a
Unicode/ASCII block matrix for the terminal and as a PNG file. Byte mode only,
error-correction level M by default (L available), versions 1–10 (byte capacity
at 10-M is 216 bytes — comfortably above our payload). If a payload will not fit
version 10, ``ValueError`` is raised (shorten the device label).

This is a correctness-first minimal encoder: mode = byte, full Reed–Solomon over
GF(256), all 8 data masks scored by the standard four penalty rules, format +
version information via BCH. It is NOT a general QR library (no kanji/numeric
modes, no versions > 10) — but what it emits is a spec-valid QR matrix that
real phone scanners read.

Public API:
    qr_terminal(data: str, ec: str = "M", quiet: int = 4) -> str
    qr_png(data: str, path, ec: str = "M", scale: int = 8, quiet: int = 4) -> None
    qr_matrix(data: str, ec: str = "M") -> list[list[bool]]   # True = dark
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import List, Tuple

# --------------------------------------------------------------------------- #
# GF(256) arithmetic (primitive polynomial 0x11d) for Reed–Solomon.
# --------------------------------------------------------------------------- #
_GF_EXP = [0] * 512
_GF_LOG = [0] * 256


def _init_gf() -> None:
    x = 1
    for i in range(255):
        _GF_EXP[i] = x
        _GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]


_init_gf()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator_poly(nsym: int) -> List[int]:
    g = [1]
    for i in range(nsym):
        # multiply g by (x + a^i)
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c
            ng[j + 1] ^= _gf_mul(c, _GF_EXP[i])
        g = ng
    return g  # length nsym+1, leading coeff 1


def _rs_ec(data: List[int], nsym: int) -> List[int]:
    gen = _rs_generator_poly(nsym)
    res = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = res[i]
        if coef != 0:
            for j in range(1, len(gen)):
                res[i + j] ^= _gf_mul(gen[j], coef)
    return res[len(data):]


# --------------------------------------------------------------------------- #
# Version / error-correction tables (ISO/IEC 18004), versions 1–10.
# Per (version, ec-level): (ec_codewords_per_block,
#   g1_blocks, g1_data_cw, g2_blocks, g2_data_cw).  Levels order: L, M, Q, H.
# --------------------------------------------------------------------------- #
_EC_LEVELS = ("L", "M", "Q", "H")
_EC_TABLE = {
    1:  [(7, 1, 19, 0, 0), (10, 1, 16, 0, 0), (13, 1, 13, 0, 0), (17, 1, 9, 0, 0)],
    2:  [(10, 1, 34, 0, 0), (16, 1, 28, 0, 0), (22, 1, 22, 0, 0), (28, 1, 16, 0, 0)],
    3:  [(15, 1, 55, 0, 0), (26, 1, 44, 0, 0), (18, 2, 17, 0, 0), (22, 2, 13, 0, 0)],
    4:  [(20, 1, 80, 0, 0), (18, 2, 32, 0, 0), (26, 2, 24, 0, 0), (16, 4, 9, 0, 0)],
    5:  [(26, 1, 108, 0, 0), (24, 2, 43, 0, 0), (18, 2, 15, 2, 16), (22, 2, 11, 2, 12)],
    6:  [(18, 2, 68, 0, 0), (16, 4, 27, 0, 0), (24, 4, 19, 0, 0), (28, 4, 15, 0, 0)],
    7:  [(20, 2, 78, 0, 0), (18, 4, 31, 0, 0), (18, 2, 14, 4, 15), (26, 4, 13, 1, 14)],
    8:  [(24, 2, 97, 0, 0), (22, 2, 38, 2, 39), (22, 4, 18, 2, 19), (26, 4, 14, 2, 15)],
    9:  [(30, 2, 116, 0, 0), (22, 3, 36, 2, 37), (20, 4, 16, 4, 17), (24, 4, 12, 4, 13)],
    10: [(18, 2, 68, 2, 69), (26, 4, 43, 1, 44), (24, 6, 19, 2, 20), (28, 6, 15, 2, 16)],
}
# Alignment-pattern centre coordinates per version (empty for v1).
_ALIGN_POS = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
}
_FORMAT_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}


def _data_capacity_cw(version: int, ec: str) -> int:
    _ecpb, g1, g1d, g2, g2d = _EC_TABLE[version][_EC_LEVELS.index(ec)]
    return g1 * g1d + g2 * g2d


def _char_count_bits(version: int) -> int:
    # byte mode: 8 bits for versions 1-9, 16 bits for 10-26
    return 8 if version <= 9 else 16


def _choose_version(nbytes: int, ec: str) -> int:
    for v in range(1, 11):
        need_bits = 4 + _char_count_bits(v) + 8 * nbytes
        need_cw = (need_bits + 7) // 8
        if need_cw <= _data_capacity_cw(v, ec):
            return v
    raise ValueError(
        f"payload of {nbytes} bytes does not fit a version-10 QR at EC level {ec}; "
        "use a shorter label"
    )


# --------------------------------------------------------------------------- #
# Bit / codeword assembly.
# --------------------------------------------------------------------------- #
def _data_bits(data: bytes, version: int, cap_cw: int) -> List[int]:
    """Byte-mode header + payload + terminator (≤4 zero bits, capacity-bounded)
    + zero padding to a byte boundary."""
    bits: List[int] = []

    def put(val: int, n: int) -> None:
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(0b0100, 4)                       # byte mode indicator
    put(len(data), _char_count_bits(version))
    for b in data:
        put(b, 8)
    cap_bits = cap_cw * 8
    for _ in range(min(4, cap_bits - len(bits))):
        bits.append(0)
    while len(bits) % 8 != 0:
        bits.append(0)
    return bits


def _pad_codewords(bits: List[int], cap_cw: int) -> List[int]:
    """Bits → codewords, padded with alternating 0xEC / 0x11 up to capacity."""
    cw = [int("".join(str(x) for x in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    i = 0
    while len(cw) < cap_cw:
        cw.append(pad[i % 2])
        i += 1
    return cw


def _split_blocks(cw: List[int], g1: int, g1d: int, g2: int, g2d: int) -> List[List[int]]:
    blocks: List[List[int]] = []
    pos = 0
    for _ in range(g1):
        blocks.append(cw[pos:pos + g1d])
        pos += g1d
    for _ in range(g2):
        blocks.append(cw[pos:pos + g2d])
        pos += g2d
    return blocks


def _interleave_data(blocks: List[List[int]]) -> List[int]:
    result: List[int] = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                result.append(b[i])
    return result


def _interleave_ec(ec_blocks: List[List[int]], ecpb: int) -> List[int]:
    result: List[int] = []
    for i in range(ecpb):
        for b in ec_blocks:
            result.append(b[i])
    return result


def _encode_codewords(data: bytes, version: int, ec: str) -> List[int]:
    cap_cw = _data_capacity_cw(version, ec)
    cw = _pad_codewords(_data_bits(data, version, cap_cw), cap_cw)
    # split into blocks, compute EC, interleave
    ecpb, g1, g1d, g2, g2d = _EC_TABLE[version][_EC_LEVELS.index(ec)]
    blocks = _split_blocks(cw, g1, g1d, g2, g2d)
    ec_blocks = [_rs_ec(b, ecpb) for b in blocks]
    return _interleave_data(blocks) + _interleave_ec(ec_blocks, ecpb)


# --------------------------------------------------------------------------- #
# Matrix construction (finder / timing / alignment / format / version / data).
# --------------------------------------------------------------------------- #
class _Matrix:
    def __init__(self, version: int):
        self.version = version
        self.size = version * 4 + 17
        self.mods = [[False] * self.size for _ in range(self.size)]
        self.fun = [[False] * self.size for _ in range(self.size)]

    def _set(self, x: int, y: int, dark: bool) -> None:
        self.mods[y][x] = dark
        self.fun[y][x] = True

    def _finder(self, x: int, y: int) -> None:
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                xx, yy = x + dx, y + dy
                if 0 <= xx < self.size and 0 <= yy < self.size:
                    dist = max(abs(dx), abs(dy))
                    self._set(xx, yy, dist not in (2, 4))

    def _align(self, x: int, y: int) -> None:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self._set(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)

    def draw_function_patterns(self) -> None:
        n = self.size
        # timing
        for i in range(n):
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        # finders + separators
        self._finder(3, 3)
        self._finder(n - 4, 3)
        self._finder(3, n - 4)
        # alignment (skip ones overlapping finders)
        pos = _ALIGN_POS[self.version]
        for a in pos:
            for b in pos:
                if (a, b) in ((6, 6), (6, n - 7), (n - 7, 6)):
                    continue
                self._align(a, b)
        # reserve format-info area (drawn for real later)
        self._draw_format(0)
        # version info (v >= 7)
        self._draw_version()

    def _draw_format(self, mask: int, ec: str = "M") -> None:
        data = (_FORMAT_BITS[ec] << 3) | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((data << 10) | rem) ^ 0x5412
        n = self.size

        def gb(i: int) -> bool:
            return ((bits >> i) & 1) != 0

        for i in range(6):
            self._set(8, i, gb(i))
        self._set(8, 7, gb(6))
        self._set(8, 8, gb(7))
        self._set(7, 8, gb(8))
        for i in range(9, 15):
            self._set(14 - i, 8, gb(i))
        for i in range(8):
            self._set(n - 1 - i, 8, gb(i))
        for i in range(8, 15):
            self._set(8, n - 15 + i, gb(i))
        self._set(8, n - 8, True)   # always-dark module

    def _draw_version(self) -> None:
        if self.version < 7:
            return
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = (self.version << 12) | rem
        n = self.size
        for i in range(18):
            bit = ((bits >> i) & 1) != 0
            a = n - 11 + i % 3
            b = i // 3
            self._set(a, b, bit)
            self._set(b, a, bit)

    def _place_bit(self, x: int, y: int, codewords: List[int], i: int) -> int:
        """Write data bit ``i`` at (x, y) unless the module is a function
        pattern or the stream is exhausted; returns the next bit index."""
        if not self.fun[y][x] and i < len(codewords) * 8:
            byte = codewords[i >> 3]
            self.mods[y][x] = ((byte >> (7 - (i & 7))) & 1) != 0
            return i + 1
        return i

    def _place_column_pair(self, col: int, codewords: List[int], i: int) -> int:
        """Fill the two-module-wide column ``col`` (zigzag: upward when
        ``(col + 1) & 2 == 0``); returns the next bit index."""
        n = self.size
        upward = ((col + 1) & 2) == 0
        for row_iter in range(n):
            y = (n - 1 - row_iter) if upward else row_iter
            for c in range(2):
                i = self._place_bit(col - c, y, codewords, i)
        return i

    def draw_codewords(self, codewords: List[int]) -> None:
        i = 0
        col = self.size - 1
        while col > 0:
            if col == 6:
                col = 5
            i = self._place_column_pair(col, codewords, i)
            col -= 2

    @staticmethod
    def _mask_bit(mask: int, x: int, y: int) -> bool:
        """The eight ISO 18004 mask conditions (mask 7 for anything else)."""
        if mask == 0:
            return (x + y) % 2 == 0
        if mask == 1:
            return y % 2 == 0
        if mask == 2:
            return x % 3 == 0
        if mask == 3:
            return (x + y) % 3 == 0
        return _Matrix._mask_bit_high(mask, x, y)

    @staticmethod
    def _mask_bit_high(mask: int, x: int, y: int) -> bool:
        if mask == 4:
            return (y // 2 + x // 3) % 2 == 0
        if mask == 5:
            return (x * y) % 2 + (x * y) % 3 == 0
        if mask == 6:
            return ((x * y) % 2 + (x * y) % 3) % 2 == 0
        return ((x + y) % 2 + (x * y) % 3) % 2 == 0

    def apply_mask(self, mask: int) -> None:
        n = self.size
        for y in range(n):
            for x in range(n):
                if self.fun[y][x]:
                    continue
                if self._mask_bit(mask, x, y):
                    self.mods[y][x] = not self.mods[y][x]

    @staticmethod
    def _run_penalty(line: List[bool]) -> int:
        """Rule 1 on one row/column: every run of ≥5 same-colour modules
        scores 3 + (run − 5)."""
        score = 0
        run = 1
        for k in range(1, len(line)):
            if line[k] == line[k - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
        return score

    def _rule1(self) -> int:
        m, n = self.mods, self.size
        score = sum(self._run_penalty(m[y]) for y in range(n))
        score += sum(self._run_penalty([m[y][x] for y in range(n)]) for x in range(n))
        return score

    def _rule2(self) -> int:
        """2x2 blocks of the same colour: 3 each."""
        m, n = self.mods, self.size
        score = 0
        for y in range(n - 1):
            for x in range(n - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        return score

    _PATT_A = [True, False, True, True, True, False, True, False, False, False, False]
    _PATT_B = [False, False, False, False, True, False, True, True, True, False, True]

    @staticmethod
    def _finder_like_count(line: List[bool]) -> int:
        """Windows of 11 modules in one row/column matching either pattern."""
        count = 0
        for x in range(len(line) - 10):
            seg = line[x:x + 11]
            if seg == _Matrix._PATT_A or seg == _Matrix._PATT_B:
                count += 1
        return count

    def _rule3(self) -> int:
        """Finder-like 1:1:3:1:1 patterns (with 4 light) in rows/cols: 40 each."""
        m, n = self.mods, self.size
        hits = sum(self._finder_like_count(m[y]) for y in range(n))
        hits += sum(self._finder_like_count([m[y][x] for y in range(n)]) for x in range(n))
        return hits * 40

    def _rule4(self) -> int:
        """Deviation of the dark-module proportion from 50%."""
        m, n = self.mods, self.size
        dark = sum(row.count(True) for row in m)
        ratio = dark * 100 // (n * n)
        prev = (ratio // 5) * 5
        nxt = prev + 5
        return min(abs(prev - 50), abs(nxt - 50)) // 5 * 10

    def penalty(self) -> int:
        return self._rule1() + self._rule2() + self._rule3() + self._rule4()


def qr_matrix(data: str, ec: str = "M") -> List[List[bool]]:
    """Encode ``data`` → a QR module matrix (list of rows; ``True`` = dark)."""
    ec = ec.upper()
    if ec not in _EC_LEVELS:
        raise ValueError(f"ec must be one of {_EC_LEVELS}")
    raw = data.encode("utf-8")
    version = _choose_version(len(raw), ec)
    codewords = _encode_codewords(raw, version, ec)

    best: Tuple[int, _Matrix] = (1 << 60, None)  # type: ignore[assignment]
    for mask in range(8):
        mx = _Matrix(version)
        mx.draw_function_patterns()
        mx.draw_codewords(codewords)
        mx.apply_mask(mask)
        mx._draw_format(mask, ec)
        p = mx.penalty()
        if p < best[0]:
            best = (p, mx)
    return best[1].mods


# --------------------------------------------------------------------------- #
# Renderers.
# --------------------------------------------------------------------------- #
_HALF_BLOCKS = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}


def qr_terminal(data: str, ec: str = "M", quiet: int = 4) -> str:
    """Render ``data`` as a scannable Unicode half-block QR (dark on a light
    terminal). Two module-rows per text-row keep the aspect ratio square."""
    m = qr_matrix(data, ec)
    n = len(m)
    q = quiet
    full = n + 2 * q

    def dark(x: int, y: int) -> bool:
        mx, my = x - q, y - q
        return 0 <= mx < n and 0 <= my < n and m[my][mx]

    lines = []
    for y in range(0, full, 2):
        row = [_HALF_BLOCKS[(dark(x, y), dark(x, y + 1) if y + 1 < full else False)]
               for x in range(full)]
        lines.append("".join(row))
    return "\n".join(lines)


def _png_bytes(matrix: List[List[bool]], scale: int, quiet: int) -> bytes:
    n = len(matrix)
    full = n + 2 * quiet
    size = full * scale
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter type 0 (None)
        my = y // scale - quiet
        for x in range(size):
            mx = x // scale - quiet
            dark = 0 <= my < n and 0 <= mx < n and matrix[my][mx]
            raw.append(0 if dark else 255)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)  # 8-bit grayscale
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def qr_png(data: str, path, ec: str = "M", scale: int = 8, quiet: int = 4) -> None:
    """Write a PNG QR of ``data`` to ``path`` (pure stdlib: zlib + PNG chunks)."""
    m = qr_matrix(data, ec)
    Path(path).write_bytes(_png_bytes(m, scale, quiet))
