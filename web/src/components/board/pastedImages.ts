// 列顶输入框的贴图（CONTRACT §10bis 追记 / §29ter 认领规则；owner 决策 D41；原生 mac/Sources/PastedImages.swift 的 web 版）。
// 纯逻辑住这里，LaneComposer 只管状态与渲染：
//   · imageFilesFromClipboard：粘贴事件里的图片 flavor（kind=file 且 image/*）；
//   · pasteClaim：⌘V 认领规则（原生 isImagePaste）——纯位图认领；位图 + 文本双 flavor 时，文本 trim 后是单个 URL /
//     文件路径 token（浏览器「拷贝图像」旁带的地址、聊天工具截图旁的文件引用；不含空白、≤2048、scheme 大小写不敏感）
//     = 图片的伴生元数据，认领；含空白 / 多行 / 实质文本（Excel 复制单元格连图带字）不认领，文本粘贴优先。
//     原生「图片文件 URL 无条件认领」在浏览器里的对应物：Finder 拷来的文件旁带的文本恰是它的文件名 → 认领
//     （文件名含空格也算，否则 Finder 拷贝的截图永远贴不进来）。
//     一旦认领，粘贴事件必被吃掉——绝不回退文本粘贴（图片文件的文本形态是本机路径 / 文件名，不该进正文）。
//   · encodePng：canvas 降采样到最长边 IMAGE_MAX_EDGE（原生 maxPixelDimension = 2560）并转 PNG；产物超过
//     IMAGE_MAX_BYTES（server 上限）就把边长减半再编一次（照片级噪点的 PNG 才会触发；截图远在其下）。
//     只在真浏览器里跑（createImageBitmap + canvas.toBlob，jsdom 没有），判例整个 mock 掉它。
// 上限 4 张（原生 maxCount，与 server/inbox_writer._CAPTURE_IMAGES_MAX 同值）；上传时机 = 贴进来那一刻
// （POST /api/attachments → 回绝对路径），提交时把路径原样塞进 capture 的 images[]。草稿丢弃留下的孤儿 PNG
// 由 actd 的日频附件 GC（§10bis，无引用且 > 30 天）收走——不加 DELETE 路由、不另起台账。

/** 一次捕获最多几张（原生 PastedImagesModel.maxCount；server 侧 inbox_writer 同值 fail-closed） */
export const IMAGES_MAX = 4;
/** 降采样上限：最长边像素（原生 PastedImages.maxPixelDimension） */
export const IMAGE_MAX_EDGE = 2560;
/** 单张 PNG 上限（truth = server/attachments.py MAX_BYTES；超了 server 413） */
export const IMAGE_MAX_BYTES = 8 * 1024 * 1024;
/** 减半降采样的下限——再小就不像截图了，宁可让 server 413 报「太大」 */
const MIN_EDGE = 640;
/** 伴生 URL / 路径 token 的长度上限（原生 isCompanionURLText） */
const COMPANION_MAX_CHARS = 2048;

/** 剪贴板 / 文件选择器给的图片文件；判例里是鸭子类型，所以只读需要的字段 */
export interface ClipboardLike {
  items?: ArrayLike<{ kind: string; type: string; getAsFile(): File | null }> | null;
  files?: ArrayLike<File> | null;
}

/** 粘贴事件里的图片 flavor：优先 items（kind=file 且 image/*），退到 files（Safari 老版只填 files） */
export function imageFilesFromClipboard(data: ClipboardLike | null | undefined): File[] {
  if (!data) return [];
  const out: File[] = [];
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      const file = item.getAsFile();
      if (file) out.push(file);
    }
  }
  if (out.length > 0) return out;
  return Array.from(data.files ?? []).filter((f) => f.type.startsWith("image/"));
}

/** 位图旁带文本的判别（原生 isCompanionURLText）：trim 后恰好一个 URL / 文件路径 token 才算伴生元数据 */
export function isCompanionUrlText(trimmed: string): boolean {
  if (trimmed.length === 0 || trimmed.length > COMPANION_MAX_CHARS || /\s/.test(trimmed)) return false;
  if (trimmed.startsWith("/")) return true;
  const lower = trimmed.toLowerCase();
  return lower.startsWith("http://") || lower.startsWith("https://") || lower.startsWith("file://");
}

/** ⌘V 认领规则（原生 PastedImages.isImagePaste）：image = 吃掉事件、收图；text = 不拦，浏览器照常粘贴文本 */
export function pasteClaim(files: File[], text: string): "image" | "text" {
  if (files.length === 0) return "text";
  const trimmed = text.trim();
  if (trimmed === "" || isCompanionUrlText(trimmed)) return "image";
  // Finder 拷贝的图片文件：文本 flavor 就是文件名（可能含空格）——原生「文件 URL 无条件认领」的浏览器对应物
  if (files.some((f) => f.name !== "" && f.name === trimmed)) return "image";
  return "text";
}

/** 把位图画到 ≤ edge 的 canvas 上转 PNG；toBlob 给 null（画布被污染 / 内存不足）按失败抛 */
async function drawPng(bitmap: ImageBitmap, edge: number): Promise<Blob> {
  const scale = Math.min(1, edge / Math.max(bitmap.width, bitmap.height, 1));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(bitmap.width * scale));
  canvas.height = Math.max(1, Math.round(bitmap.height * scale));
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas 2d context unavailable");
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("PNG encode failed"))), "image/png");
  });
}

/** 降采样 + 转 PNG（原生 downsampled + representation(using: .png)）：最长边 ≤ maxEdge；产物超 maxBytes 就减半再编 */
export async function encodePng(source: Blob, maxEdge = IMAGE_MAX_EDGE, maxBytes = IMAGE_MAX_BYTES): Promise<Blob> {
  if (typeof createImageBitmap !== "function") throw new Error("createImageBitmap unavailable");
  const bitmap = await createImageBitmap(source);
  try {
    let edge = maxEdge;
    for (;;) {
      const png = await drawPng(bitmap, edge);
      if (png.size <= maxBytes || edge <= MIN_EDGE) return png;
      edge = Math.max(MIN_EDGE, Math.floor(edge / 2));
    }
  } finally {
    bitmap.close?.();
  }
}
