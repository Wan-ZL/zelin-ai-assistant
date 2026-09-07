"""POST /api/attachments → ``state/attachments/<uuid>-1.png``（§10bis 贴图的 web 落盘面；路由登记 §49；owner 决策 D41）。

原生 ``mac/Sources/PastedImages.swift`` 把粘贴的截图降采样后直接写进
``state/attachments/`` 再把绝对路径塞进 capture 的 ``images[]``；web 页面没有
文件系统，这里是它唯一的落盘通道——**本面自 §49「零上传」以来的第一条二进制
入站路由**，所以纪律比 JSON 面只多不少：

- **四闸同 /api/actions**（Host / Origin / Content-Type / instance token，
  server/security.py）；Content-Type 闸对本路由认的是 ``image/png`` 而不是
  ``application/json``——image/png 不是 CORS-safelisted 类型（那三种是
  text/plain / multipart/form-data / application/x-www-form-urlencoded），跨源
  fetch 带它必触发 preflight，本面对 OPTIONS 不答 CORS 头（test_server_auth
  钉），浏览器根本不会发出真请求；present-only 的 Origin 判定不因此失去兜底
  （security.origin_ok docstring 的耦合纪律）。multipart 一律不收：那是
  simple-request 向量。
- **body = 原始 PNG 字节**，上限 ``MAX_BYTES``（独立于 app.py 的 1MiB JSON 上限
  ——§68.14 说的「第二条上传通道」就是它；超限 413，裁决只看 Content-Length、超限的体
  不解析不落盘——app.py ``_body_length`` 先把在路上的体读掉丢弃再关连接，客户端才读得到
  envelope 而不是 BrokenPipe）；
  前 8 字节必须是 PNG magic（客户端 canvas 已转 PNG；别的格式 400，不落盘）。
- **路径永不由客户端决定**：文件名 = server 铸的 uuid4 + ``-1``（§10bis 的
  ``<uuid>-<n>`` 形，web 每次上传就是一批一张），目录 = ``state/attachments/``
  固定；query / 头 / body 里的任何「名字」都不参与。0600 + O_EXCL + O_NOFOLLOW
  写 tmp 再 ``os.replace``——半截文件永不以 .png 出现。
- **回执** ``{"ok": true, "path": "<绝对路径>", "bytes": N}``；客户端只把 path
  原样塞进 capture 的 ``images[]``（inbox_writer ``_require_image_list`` 仍只验
  「绝对路径 / 去重 / ≤4」——wire 零改动）。
- **留存 = 既有的附件 GC**（§10bis：actd 日频清扫 ``state/attachments/`` 里
  「无引用且 mtime > 30 天」的文件）：草稿被丢弃 / capture 没发出去的 PNG 是孤儿，
  30 天后由它收走，本面不另起台账、不加 DELETE 路由。
- 本面**没有读回路由**：GET /api/attachments 404，``/files/`` 也不服务它——缩略图
  用客户端自己的 blob URL。

stdlib only；不 import act（§44 单写者纪律：server 只写 state/attachments/ 与
inbox，永不碰 registry）。判例：tests/test_server_attachments.py。
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from server import paths
from server.errors import InvalidFieldError

ROUTE = "/api/attachments"
# 本路由唯一接受的 Content-Type（非 CORS-safelisted，见模块 docstring）
CONTENT_TYPE = "image/png"
# 单张 PNG 上限：客户端先降采样到最长边 2560px（web/src/components/board/pastedImages.ts
# 同源常量），截图级 PNG 通常 1-4 MB；8 MiB 给噪点多的照片留余量，再大也不该进 prompt。
MAX_BYTES = 8 << 20
# PNG 文件签名（8 字节）
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def is_png(data: bytes) -> bool:
    """前 8 字节是 PNG 签名且后面还有内容——只验 magic，不解码（stdlib 无解码器；
    客户端 canvas 产物 + 四闸鉴权已足够，坏 PNG 至多让 agent 的 Read 报错）。"""
    return len(data) > len(PNG_MAGIC) and data.startswith(PNG_MAGIC)


def _write_0600(target: Path, data: bytes) -> None:
    """O_EXCL + O_NOFOLLOW + 0600 新建（tmp 名含 uuid，EXCL 撞上 = 真异常）。"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    try:
        os.chmod(target, 0o600)  # umask 只会更严；显式钉死（security.py 同款）
    except OSError:
        pass


def save(home: Path, data: bytes) -> dict:
    """校验 PNG 签名并原子落盘；返回 ``{"ok", "path", "bytes"}``。

    异常契约（app.py 依赖）：InvalidFieldError（非 PNG / 空体 → 400）；
    OSError（目录建不了 / 磁盘满 → app.py 兜成 500 INTERNAL_ERROR——客户端据此弹
    「图片保存失败」，草稿与已有附图原样保留）。"""
    if not is_png(data):
        raise InvalidFieldError("body must be a PNG image", {"field": "body"})
    directory = paths.attachments_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{uuid.uuid4()}-1"
    final = directory / f"{stem}.png"
    tmp = directory / f".{stem}.png.tmp"  # 点开头 + .tmp：不像 .png，GC 把它当孤儿收
    try:
        _write_0600(tmp, data)
        os.replace(tmp, final)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return {"ok": True, "path": str(final), "bytes": len(data)}
