"""qr 判例的公用解码工具（§41 配对二维码）——扫描枪那一侧的视角。

只依赖 stdlib 与被测模块的静态表：从成品矩阵读回格式信息、反掩码、按 ISO 的
之字形取回码字流、拆回块并算 Reed–Solomon 校验子。测试用它证明「编出来的东西
真的能被解开」，而不是复述编码器的内部状态。
"""
from act.lib import qr

# ISO/IEC 18004 表 7 公布的 byte-mode 容量（字符数），versions 1–10。
# 外部真源：不是从 act/lib/qr.py 的表推出来的，抄错了判例就红。
ISO_BYTE_CAPACITY = {
    "L": [17, 32, 53, 78, 106, 134, 154, 192, 230, 271],
    "M": [14, 26, 42, 62, 84, 106, 122, 152, 180, 213],
    "Q": [11, 20, 32, 46, 60, 74, 86, 108, 130, 151],
    "H": [7, 14, 24, 34, 44, 58, 64, 84, 98, 119],
}

# 左上角那份格式信息的 15 个位置（bit 0 → bit 14）。
FORMAT_READ_ORDER = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
                     (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]


def read_format(matrix):
    """→ (ec_bits, mask)，从矩阵本身读，不看编码器内部。"""
    bits = 0
    for i, (x, y) in enumerate(FORMAT_READ_ORDER):
        bits |= (1 if matrix[y][x] else 0) << i
    data = (bits ^ 0x5412) >> 10
    return data >> 3, data & 7


def mask_bit(mask, x, y):
    """ISO 18004 的八个掩码条件——判例侧独立重写，不调用被测实现。"""
    if mask == 0:
        return (x + y) % 2 == 0
    if mask == 1:
        return y % 2 == 0
    if mask == 2:
        return x % 3 == 0
    if mask == 3:
        return (x + y) % 3 == 0
    if mask == 4:
        return (y // 2 + x // 3) % 2 == 0
    if mask == 5:
        return (x * y) % 2 + (x * y) % 3 == 0
    if mask == 6:
        return ((x * y) % 2 + (x * y) % 3) % 2 == 0
    return ((x + y) % 2 + (x * y) % 3) % 2 == 0


def read_codewords(matrix):
    """反掩码 + 之字形回读 → 交错后的完整码字流（含纠错码字）。"""
    n = len(matrix)
    version = (n - 17) // 4
    fun = qr._Matrix(version)
    fun.draw_function_patterns()
    _ec_bits, mask = read_format(matrix)

    grid = [[matrix[y][x] for x in range(n)] for y in range(n)]
    for y in range(n):
        for x in range(n):
            if not fun.fun[y][x] and mask_bit(mask, x, y):
                grid[y][x] = not grid[y][x]

    bits = []
    col = n - 1
    while col > 0:
        if col == 6:
            col = 5
        upward = ((col + 1) & 2) == 0
        for step in range(n):
            y = (n - 1 - step) if upward else step
            for c in range(2):
                x = col - c
                if not fun.fun[y][x]:
                    bits.append(1 if grid[y][x] else 0)
        col -= 2
    whole = bits[: (len(bits) // 8) * 8]
    return [int("".join(map(str, whole[i:i + 8])), 2) for i in range(0, len(whole), 8)]


def deinterleave(codewords, version, ec):
    """→ 每块的 [数据 + 纠错] 列表（ISO 的交错规则反着走一遍）。"""
    ecpb, g1, g1d, g2, g2d = qr._EC_TABLE[version][qr._EC_LEVELS.index(ec)]
    lengths = [g1d] * g1 + [g2d] * g2
    nblocks = g1 + g2
    total_data = sum(lengths)
    data_cw = codewords[:total_data]
    ec_cw = codewords[total_data:total_data + ecpb * nblocks]

    dblocks = [[] for _ in range(nblocks)]
    idx = 0
    for i in range(max(lengths)):
        for b in range(nblocks):
            if i < lengths[b]:
                dblocks[b].append(data_cw[idx])
                idx += 1
    eblocks = [[] for _ in range(nblocks)]
    idx = 0
    for i in range(ecpb):
        for b in range(nblocks):
            eblocks[b].append(ec_cw[idx])
            idx += 1
    return [dblocks[b] + eblocks[b] for b in range(nblocks)], ecpb


def syndromes(block, ecpb):
    """块的 Reed–Solomon 校验子；全 0 = 该块无误。"""
    out = []
    for s in range(ecpb):
        acc = 0
        for coef in block:
            acc = qr._gf_mul(acc, qr._GF_EXP[s]) ^ coef
        out.append(acc)
    return out


def read_payload(matrix, ec):
    """反解出 byte-mode 的原始载荷（头 4 bit 模式 + 计数 + 数据）。"""
    n = len(matrix)
    version = (n - 17) // 4
    blocks, _ecpb = deinterleave(read_codewords(matrix), version, ec)
    ecpb, g1, g1d, g2, g2d = qr._EC_TABLE[version][qr._EC_LEVELS.index(ec)]
    lengths = [g1d] * g1 + [g2d] * g2
    data = []
    for block, length in zip(blocks, lengths):
        data.extend(block[:length])

    bits = []
    for byte in data:
        bits.extend((byte >> i) & 1 for i in range(7, -1, -1))
    mode = int("".join(map(str, bits[:4])), 2)
    ccbits = qr._char_count_bits(version)
    count = int("".join(map(str, bits[4:4 + ccbits])), 2)
    start = 4 + ccbits
    payload = bytearray()
    for i in range(count):
        chunk = bits[start + 8 * i:start + 8 * i + 8]
        payload.append(int("".join(map(str, chunk)), 2))
    return mode, bytes(payload)
