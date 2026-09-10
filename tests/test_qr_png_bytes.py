"""qr — 解回 PNG 字节：容器合法且像素就是模块矩阵（§41 配对二维码的 PNG 出口）。

原有判例只看头 8 个魔术字节和 IHDR/IEND 是否出现——于是 `_png_bytes` 里的静止区
（安静区宽度、缩放偏移、滤波字节、黑白像素值、IHDR 的位深/颜色类型、chunk CRC）
全是变异测试的免检区（§57 夜报在这一段留了 20+ 个存活体）。

这里把 PNG 真的**解回来**：逐 chunk 校验 CRC、读 IHDR 字段、zlib 解压 IDAT、
逐行确认滤波字节是 0（None）、再按 scale/quiet 还原模块矩阵与 `qr_matrix` 对拍。
扫描枪读的是像素，所以判例也读像素。
"""
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tests import TMP_HOME  # noqa: F401 - sandboxes AIASSISTANT_HOME first

from act.lib import qr


def _chunks(blob: bytes):
    """逐个 (tag, payload) 产出，顺带校验长度与 CRC——CRC 错就是坏 PNG。"""
    assert blob[:8] == b"\x89PNG\r\n\x1a\n", "PNG signature"
    pos = 8
    while pos < len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        tag = blob[pos + 4:pos + 8]
        payload = blob[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", blob[pos + 8 + length:pos + 12 + length])
        assert len(payload) == length, (tag, length, len(payload))
        assert crc == zlib.crc32(tag + payload) & 0xFFFFFFFF, tag
        yield tag, payload
        pos += 12 + length


def _decode(blob: bytes):
    """→ (width, height, rows of pixel values)；只支持本编码器发的 8-bit 灰度。"""
    parts = list(_chunks(blob))
    tags = [t for t, _ in parts]
    assert tags[0] == b"IHDR" and tags[-1] == b"IEND", tags
    header = dict(parts)[b"IHDR"]
    width, height, depth, color, comp, filt, interlace = struct.unpack(">IIBBBBB", header)
    assert (depth, color, comp, filt, interlace) == (8, 0, 0, 0, 0), header
    raw = zlib.decompress(b"".join(p for t, p in parts if t == b"IDAT"))
    stride = width + 1
    assert len(raw) == stride * height, (len(raw), stride, height)
    rows = []
    for y in range(height):
        line = raw[y * stride:(y + 1) * stride]
        assert line[0] == 0, f"row {y} filter type {line[0]} (expected 0/None)"
        rows.append(list(line[1:]))
    return width, height, rows


class QrPngContainerTestCase(unittest.TestCase):
    DATA = "ZQR1-" + "y" * 60

    def _write(self, **kwargs) -> bytes:
        path = Path(tempfile.mkdtemp()) / "qr.png"
        qr.qr_png(self.DATA, path, **kwargs)
        return path.read_bytes()

    def test_default_scale_and_quiet_zone_set_the_image_size(self):
        modules = len(qr.qr_matrix(self.DATA))
        width, height, _rows = _decode(self._write())
        self.assertEqual(width, height)
        self.assertEqual(width, (modules + 2 * 4) * 8)     # 默认 quiet=4, scale=8

    def test_pixels_reproduce_the_module_matrix_with_its_quiet_zone(self):
        matrix = qr.qr_matrix(self.DATA)
        n = len(matrix)
        scale, quiet = 3, 2
        width, height, rows = _decode(self._write(scale=scale, quiet=quiet))
        self.assertEqual((width, height), ((n + 2 * quiet) * scale,) * 2)
        for y in range(height):
            for x in range(width):
                my, mx = y // scale - quiet, x // scale - quiet
                inside = 0 <= my < n and 0 <= mx < n
                dark = inside and matrix[my][mx]
                self.assertEqual(rows[y][x], 0 if dark else 255, (x, y))

    def test_quiet_zone_is_light_all_the_way_round(self):
        scale, quiet = 2, 3
        width, height, rows = _decode(self._write(scale=scale, quiet=quiet))
        band = quiet * scale
        for y in range(height):
            for x in range(width):
                if y < band or y >= height - band or x < band or x >= width - band:
                    self.assertEqual(rows[y][x], 255, (x, y))

    def test_only_black_and_white_pixels_are_emitted(self):
        _w, _h, rows = _decode(self._write(scale=1, quiet=1))
        self.assertEqual({v for row in rows for v in row}, {0, 255})

    def test_the_top_left_finder_is_a_solid_black_square(self):
        scale, quiet = 4, 4
        _w, _h, rows = _decode(self._write(scale=scale, quiet=quiet))
        origin = quiet * scale
        for dy in range(scale):                 # 模块 (0,0) 整格必须是黑的
            for dx in range(scale):
                self.assertEqual(rows[origin + dy][origin + dx], 0)
        ring = origin + 1 * scale               # 模块 (1,1) 在 finder 的浅色环上
        self.assertEqual(rows[ring][ring], 255)

    def test_error_correction_level_reaches_the_png(self):
        path = Path(tempfile.mkdtemp()) / "h.png"
        qr.qr_png(self.DATA, path, ec="H", scale=2, quiet=1)
        width, _h, _rows = _decode(path.read_bytes())
        self.assertEqual(width, (len(qr.qr_matrix(self.DATA, "H")) + 2) * 2)

    def test_path_is_accepted_as_a_string(self):
        path = Path(tempfile.mkdtemp()) / "s.png"
        qr.qr_png(self.DATA, str(path), scale=1, quiet=0)
        width, _h, _rows = _decode(path.read_bytes())
        self.assertEqual(width, len(qr.qr_matrix(self.DATA)))


if __name__ == "__main__":
    unittest.main()
