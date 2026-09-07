// 贴图的纯逻辑（CONTRACT §29ter 认领规则 / §10bis 上限；owner 决策 D41；原生 PastedImages.isImagePaste / isCompanionURLText）：
//   · 剪贴板图片 flavor 的提取（items 优先、files 兜底、非 image/* 不算）；
//   · ⌘V 认领真值表：纯位图认领；位图 + 单个 URL / 路径 token 认领（scheme 大小写不敏感、≤2048、不含空白）；
//     位图 + 实质文本（空白 / 多行 / Excel 单元格）让路；Finder 文件 + 恰为文件名的文本认领；没有图永远是文本；
//   · 常量与 server / 原生同值（4 张、2560px、8 MiB）。
import { describe, expect, it } from "vitest";
import {
  IMAGES_MAX,
  IMAGE_MAX_BYTES,
  IMAGE_MAX_EDGE,
  imageFilesFromClipboard,
  isCompanionUrlText,
  pasteClaim,
} from "./pastedImages";

const png = (name = "image.png") => new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], name, { type: "image/png" });

const item = (file: File | null, kind = "file", type = "image/png") => ({ kind, type, getAsFile: () => file });

describe("pastedImages — constants mirror the native model and the server cap", () => {
  it("4 images, 2560px longest edge, 8 MiB per PNG", () => {
    expect(IMAGES_MAX).toBe(4);
    expect(IMAGE_MAX_EDGE).toBe(2560);
    expect(IMAGE_MAX_BYTES).toBe(8 * 1024 * 1024);
  });
});

describe("imageFilesFromClipboard", () => {
  it("takes kind=file image/* items and skips text items and non-images", () => {
    const a = png("a.png");
    const b = new File(["x"], "b.jpg", { type: "image/jpeg" });
    const files = imageFilesFromClipboard({
      items: [item(null, "string", "text/plain"), item(a), item(new File(["t"], "t.txt", { type: "text/plain" }), "file", "text/plain"), item(b)],
    });
    expect(files).toEqual([a, b]);
  });

  it("falls back to .files when items carry nothing (older Safari)", () => {
    const a = png();
    expect(imageFilesFromClipboard({ items: [], files: [a, new File(["t"], "t.txt", { type: "text/plain" })] })).toEqual([a]);
  });

  it("empty / missing clipboard → []", () => {
    expect(imageFilesFromClipboard(null)).toEqual([]);
    expect(imageFilesFromClipboard({})).toEqual([]);
    expect(imageFilesFromClipboard({ items: [item(null)] })).toEqual([]);
  });
});

describe("isCompanionUrlText (native isCompanionURLText)", () => {
  it("accepts a single URL / file / absolute-path token, scheme case-insensitive", () => {
    for (const t of ["https://x.example/a.png", "http://x", "FILE:///Users/demo/a.png", "/Users/demo/Shot.png"]) {
      expect(isCompanionUrlText(t), t).toBe(true);
    }
  });

  it("rejects whitespace, multi-line, substantive text, over-long tokens and empty", () => {
    for (const t of ["", "hello", "https://x.example/a.png\tnote", "a\nb", "ftp://x", `https://x/${"a".repeat(2048)}`]) {
      expect(isCompanionUrlText(t), JSON.stringify(t)).toBe(false);
    }
  });
});

describe("pasteClaim — ⌘V claim rules (§29ter)", () => {
  it("no image flavor → text, whatever the text says", () => {
    expect(pasteClaim([], "")).toBe("text");
    expect(pasteClaim([], "https://x.example/a.png")).toBe("text");
  });

  it("pure bitmap (screenshot) → image", () => {
    expect(pasteClaim([png()], "")).toBe("image");
    expect(pasteClaim([png()], "  \n ")).toBe("image");
  });

  it("bitmap + companion URL token → image (browser 「拷贝图像」)", () => {
    expect(pasteClaim([png()], " https://cdn.example/shot.png ")).toBe("image");
    expect(pasteClaim([png()], "/Users/demo/Desktop/shot.png")).toBe("image");
  });

  it("bitmap + substantive text → text wins (Excel / Numbers cells)", () => {
    expect(pasteClaim([png()], "Q3 revenue\t1200")).toBe("text");
    expect(pasteClaim([png()], "https://x.example/a.png 备注")).toBe("text");
    expect(pasteClaim([png()], "line one\nline two")).toBe("text");
  });

  it("Finder-copied file whose text flavor is exactly the file name → image (even with spaces)", () => {
    expect(pasteClaim([png("Screenshot 2026-09-06 at 10.00.png")], "Screenshot 2026-09-06 at 10.00.png")).toBe("image");
    expect(pasteClaim([png("shot.png")], "shot.png ")).toBe("image");
    expect(pasteClaim([png("shot.png")], "other.png")).toBe("text");
    expect(pasteClaim([png("")], "")).toBe("image"); // 空名 + 空文本走纯位图那条，不靶空名等空串
  });
});
