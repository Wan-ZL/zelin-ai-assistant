// 列顶输入框的贴图（CONTRACT §10bis 追记 / §29ter 认领规则；owner 决策 D41；原生 PastedImages.swift 的 web 版）：
//   1) 粘贴截图 → 认领：canvas 转 PNG（mock）→ POST /api/attachments（mock）→ 缩略图行一张 + ✕；提交时 body 带 images:[路径]，
//      成功后草稿与附图一起清；
//   2) 位图 + 实质文本 → 让路：不上传、缩略图行亮 3 s 提示后消失；
//   3) ✕ 移除后 body 不带 images 键；没有附图时 body 也不带（inbox 形不变）；
//   4) 上限 4：多贴的不收 + 提示；满员 📎 禁点；
//   5) 上传 / 编码失败 → 弹窗「图片保存失败」+ 好；文字草稿与已加的图原样保留；
//   6) 斜杠命令不消费附图；capture 被拒不丢附图；Esc / blur 不丢附图；
//   7) 📎 选文件 = 同一条入口（非图片文件过滤掉）；上传在途时「捕获」禁点。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreForTests } from "../../store";
import { LaneComposer } from "./LaneComposer";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  postAction: vi.fn().mockResolvedValue({ ok: true, file: "capture-1.json" }),
  postAttachment: vi.fn(),
}));
vi.mock("./pastedImages", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./pastedImages")>()),
  encodePng: vi.fn(),
}));
import { postAction, postAttachment } from "../../api";
import { encodePng } from "./pastedImages";

const ENCODED = new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3])], { type: "image/png" });

function pngFile(name = "image.png"): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type: "image/png" });
}

/** 假剪贴板：items（kind=file image/*）+ text/plain */
function clipboard(files: File[], text = "") {
  return {
    items: files.map((f) => ({ kind: "file", type: f.type, getAsFile: () => f })),
    files,
    getData: (type: string) => (type === "text/plain" ? text : ""),
  };
}

let pathSeq = 0;
function mount(buildBody: (t: string) => Record<string, unknown> = (t) => ({ action: "capture", text: t })) {
  render(<LaneComposer placeholder="One sentence…" submitLabel="Capture" buildBody={buildBody} />);
  return {
    field: screen.getByPlaceholderText("One sentence…") as HTMLTextAreaElement,
    submit: screen.getByRole("button", { name: "Capture" }) as HTMLButtonElement,
    attach: screen.getByRole("button", { name: /Attach images/ }) as HTMLButtonElement,
  };
}

const thumbs = () => Array.from(document.querySelectorAll<HTMLElement>(".composer-image"));
const removeButtons = () => screen.queryAllByRole("button", { name: "Remove this image" });

async function pasteAndSettle(field: HTMLTextAreaElement, files: File[], text = "") {
  await act(async () => {
    fireEvent.paste(field, { clipboardData: clipboard(files, text) });
  });
}

beforeEach(() => {
  // jsdom 没有 <dialog>.showModal（ModalDialog 挂载即调用）——其余弹窗判例同一桩
  if (typeof HTMLDialogElement.prototype.showModal !== "function") {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) { this.open = true; };
  }
  resetStoreForTests();
  window.localStorage.clear();
  pathSeq = 0;
  vi.mocked(postAction).mockClear();
  vi.mocked(postAction).mockResolvedValue({ ok: true, file: "capture-1.json" });
  vi.mocked(encodePng).mockReset();
  vi.mocked(encodePng).mockResolvedValue(ENCODED);
  vi.mocked(postAttachment).mockReset();
  vi.mocked(postAttachment).mockImplementation(async () => {
    pathSeq += 1;
    return { ok: true, path: `/home/demo/state/attachments/uuid-${pathSeq}-1.png`, bytes: 7 };
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("LaneComposer — pasted screenshots ride the capture as attachments (D41)", () => {
  it("paste a bitmap → encode → upload → thumbnail with ✕; the capture body carries images:[path]; success clears both", async () => {
    const { field, submit } = mount();
    const shot = pngFile("shot.png");
    await pasteAndSettle(field, [shot]);

    await waitFor(() => expect(thumbs()).toHaveLength(1));
    expect(encodePng).toHaveBeenCalledWith(shot);
    expect(postAttachment).toHaveBeenCalledWith(ENCODED, expect.any(AbortSignal)); // 第二参 = 上传超时信号
    expect(thumbs()[0].dataset.imagePath).toBe("/home/demo/state/attachments/uuid-1-1.png");
    expect(screen.getByAltText("Image 1")).toBeTruthy();
    expect(removeButtons()).toHaveLength(1);

    fireEvent.change(field, { target: { value: "look at this error" } });
    await act(async () => {
      fireEvent.click(submit);
    });
    await waitFor(() => expect(postAction).toHaveBeenCalledTimes(1));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({
      action: "capture",
      text: "look at this error",
      images: ["/home/demo/state/attachments/uuid-1-1.png"],
    });
    await waitFor(() => expect(thumbs()).toHaveLength(0));
    expect(field.value).toBe("");
  });

  it("direct-run composer: images ride next to mode:\"run\"; wire keys stay exactly capture/text/mode/images", async () => {
    const { field, submit } = mount((t) => ({ action: "capture", text: t, mode: "run" }));
    await pasteAndSettle(field, [pngFile()]);
    await waitFor(() => expect(thumbs()).toHaveLength(1));
    fireEvent.change(field, { target: { value: "run it" } });
    await act(async () => {
      fireEvent.click(submit);
    });
    await waitFor(() => expect(postAction).toHaveBeenCalledTimes(1));
    expect(Object.keys(vi.mocked(postAction).mock.calls[0][0] as object).sort()).toEqual(["action", "images", "mode", "text"]);
  });

  it("no attachments → the body has no images key at all (inbox byte shape unchanged)", async () => {
    const { field, submit } = mount();
    fireEvent.change(field, { target: { value: "plain" } });
    await act(async () => {
      fireEvent.click(submit);
    });
    await waitFor(() => expect(postAction).toHaveBeenCalledTimes(1));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "capture", text: "plain" });
  });

  it("bitmap + companion URL token → claimed (browser 「拷贝图像」)", async () => {
    const { field } = mount();
    await pasteAndSettle(field, [pngFile()], "https://cdn.example/shot.png");
    await waitFor(() => expect(thumbs()).toHaveLength(1));
  });

  it("bitmap + substantive text → text wins: nothing uploads, a 3 s hint offers a path that works, then fades", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { field } = mount();
    await pasteAndSettle(field, [pngFile()], "Q3 revenue\t1200");
    expect(encodePng).not.toHaveBeenCalled();
    expect(postAttachment).not.toHaveBeenCalled();
    expect(thumbs()).toHaveLength(0);
    const hint = screen.getByRole("status");
    // 剪贴板里的位图不在磁盘上，📎 选不到它——提示必须先给「只复制图片再贴」这条走得通的路（review of PR #272）
    expect(hint.textContent).toBe(
      "The clipboard also holds an image — copy the image alone and paste again, or click 📎 to pick a file",
    );
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("plain text paste is not intercepted (no image flavor)", async () => {
    const { field } = mount();
    await pasteAndSettle(field, [], "just words");
    expect(encodePng).not.toHaveBeenCalled();
    expect(document.querySelector(".composer-images")).toBeNull();
  });

  it("✕ removes the image; the next capture carries no images key", async () => {
    const { field, submit } = mount();
    await pasteAndSettle(field, [pngFile("a.png"), pngFile("b.png")]);
    await waitFor(() => expect(thumbs()).toHaveLength(2));
    fireEvent.click(removeButtons()[0]);
    expect(thumbs()).toHaveLength(1);
    expect(thumbs()[0].dataset.imagePath).toBe("/home/demo/state/attachments/uuid-2-1.png");
    fireEvent.click(removeButtons()[0]);
    expect(document.querySelector(".composer-images")).toBeNull();

    fireEvent.change(field, { target: { value: "text only after all" } });
    await act(async () => {
      fireEvent.click(submit);
    });
    await waitFor(() => expect(postAction).toHaveBeenCalledTimes(1));
    expect(vi.mocked(postAction).mock.calls[0][0]).toEqual({ action: "capture", text: "text only after all" });
  });

  it("✕ keeps keyboard focus in place: the ✕ at the same slot, then the previous one, finally the textarea (review of PR #272)", async () => {
    const { field } = mount();
    await pasteAndSettle(field, [pngFile("a.png"), pngFile("b.png"), pngFile("c.png")]);
    await waitFor(() => expect(thumbs()).toHaveLength(3));
    // 中间那颗 ✕（b）：焦点落到接替同位的 ✕（c）
    removeButtons()[1].focus();
    fireEvent.click(removeButtons()[1]);
    expect(thumbs().map((t) => t.dataset.imagePath)).toEqual([
      "/home/demo/state/attachments/uuid-1-1.png",
      "/home/demo/state/attachments/uuid-3-1.png",
    ]);
    expect(document.activeElement).toBe(removeButtons()[1]);
    // 最后一颗（c）：没有同位的了，退到前一颗（a）
    fireEvent.click(removeButtons()[1]);
    expect(document.activeElement).toBe(removeButtons()[0]);
    // 唯一一颗：回输入框，绝不掉到 <body>
    fireEvent.click(removeButtons()[0]);
    expect(document.querySelector(".composer-images")).toBeNull();
    expect(document.activeElement).toBe(field);
  });

  it("a hung upload times out via AbortSignal.timeout(30 s): the failure dialog shows and Capture is re-enabled (review of PR #272)", async () => {
    // AbortSignal.timeout 桩成手动控制器（jsdom 的内部计时器不受 fake timers 管）：钉住调用方要的是 30 s，并在这里亲手触发
    const controller = new AbortController();
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout").mockReturnValue(controller.signal);
    // postAttachment 桩：模拟 api.request 在 signal 触发时的行为（TimeoutError → SERVICE_UNAVAILABLE 一句）
    vi.mocked(postAttachment).mockImplementationOnce((_png, signal) => new Promise((_resolve, reject) => {
      signal!.addEventListener("abort", () => reject(new Error("The local service is temporarily unavailable. Try again later.")));
    }));
    const { field, submit } = mount();
    fireEvent.change(field, { target: { value: "still here" } });
    await pasteAndSettle(field, [pngFile()]);
    expect(timeoutSpy).toHaveBeenCalledWith(30_000);
    expect(vi.mocked(postAttachment).mock.calls[0][1]).toBe(controller.signal);
    expect(submit.disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toBe("Uploading image…");
    await act(async () => {
      controller.abort();
    });
    timeoutSpy.mockRestore();
    const dialog = await screen.findByRole("dialog");
    expect(dialog.querySelector("h2")?.textContent).toBe("Images Could Not Be Saved");
    expect(thumbs()).toHaveLength(0);
    expect(field.value).toBe("still here");
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(submit.disabled).toBe(false);
  });

  it("caps at 4: the fifth is not uploaded, a hint says so, and 📎 is disabled while full", async () => {
    const { field, attach } = mount();
    expect(attach.disabled).toBe(false);
    await pasteAndSettle(field, [1, 2, 3, 4, 5].map((n) => pngFile(`${n}.png`)));
    await waitFor(() => expect(thumbs()).toHaveLength(4));
    expect(postAttachment).toHaveBeenCalledTimes(4);
    expect(screen.getByRole("status").textContent).toBe("Up to 4 images");
    expect(attach.disabled).toBe(true);
    // 满员再贴：一张都不收（原生 beep）
    await pasteAndSettle(field, [pngFile("6.png")]);
    expect(postAttachment).toHaveBeenCalledTimes(4);
    fireEvent.click(removeButtons()[0]);
    expect(attach.disabled).toBe(false);
  });

  it("upload failure → 「图片保存失败」dialog with the server reason + OK; draft and other images are kept", async () => {
    vi.mocked(postAttachment)
      .mockImplementationOnce(async () => ({ ok: true, path: "/home/demo/state/attachments/ok-1.png", bytes: 7 }))
      .mockImplementationOnce(async () => {
        throw new Error("disk full (ENOSPC)");
      });
    const { field } = mount();
    fireEvent.change(field, { target: { value: "keep me" } });
    await pasteAndSettle(field, [pngFile("ok.png"), pngFile("bad.png")]);
    const dialog = await screen.findByRole("dialog");
    expect(dialog.querySelector("h2")?.textContent).toBe("Images Could Not Be Saved");
    expect(dialog.textContent).toMatch(/disk full \(ENOSPC\)/);
    expect(dialog.textContent).toMatch(/your text and the other images are kept/);
    expect(thumbs()).toHaveLength(1);
    expect(field.value).toBe("keep me");
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(thumbs()).toHaveLength(1);
  });

  it("encode failure (canvas) → the same dialog, nothing uploaded", async () => {
    vi.mocked(encodePng).mockRejectedValueOnce(new Error("PNG encode failed"));
    const { field } = mount();
    await pasteAndSettle(field, [pngFile()]);
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toMatch(/PNG encode failed/);
    expect(postAttachment).not.toHaveBeenCalled();
  });

  it("slash commands never consume attachments (native Composer.swift:211-219)", async () => {
    const { field, submit } = mount();
    await pasteAndSettle(field, [pngFile()]);
    await waitFor(() => expect(thumbs()).toHaveLength(1));
    fireEvent.change(field, { target: { value: "/lang en" } });
    await act(async () => {
      fireEvent.click(submit);
    });
    await waitFor(() => expect(field.value).toBe(""));
    expect(postAction).not.toHaveBeenCalled();
    expect(thumbs()).toHaveLength(1);
  });

  it("a rejected capture keeps text and attachments (§41 draft retention)", async () => {
    vi.mocked(postAction).mockRejectedValueOnce(new Error("inbox not writable (EACCES)"));
    const { field, submit } = mount();
    await pasteAndSettle(field, [pngFile()]);
    await waitFor(() => expect(thumbs()).toHaveLength(1));
    fireEvent.change(field, { target: { value: "hold" } });
    await act(async () => {
      fireEvent.click(submit);
    });
    await waitFor(() => expect(document.querySelector(".composer-error")).not.toBeNull());
    expect(field.value).toBe("hold");
    expect(thumbs()).toHaveLength(1);
  });

  it("Esc / blur leave the attachments alone, like the text draft", async () => {
    const { field } = mount();
    await pasteAndSettle(field, [pngFile()]);
    await waitFor(() => expect(thumbs()).toHaveLength(1));
    fireEvent.change(field, { target: { value: "draft" } });
    field.focus();
    fireEvent.keyDown(field, { key: "Escape" });
    fireEvent.blur(field);
    expect(field.value).toBe("draft");
    expect(thumbs()).toHaveLength(1);
  });

  it("📎 opens the file picker; picked files go through the same pipeline, non-images filtered out", async () => {
    const { attach } = mount();
    const input = document.querySelector<HTMLInputElement>('.lane-composer input[type="file"]')!;
    expect(input.accept).toBe("image/*");
    expect(input.multiple).toBe(true);
    const click = vi.spyOn(input, "click");
    fireEvent.click(attach);
    expect(click).toHaveBeenCalledTimes(1);
    const shot = pngFile("picked.png");
    await act(async () => {
      fireEvent.change(input, { target: { files: [shot, new File(["t"], "notes.txt", { type: "text/plain" })] } });
    });
    await waitFor(() => expect(thumbs()).toHaveLength(1));
    expect(encodePng).toHaveBeenCalledTimes(1);
    expect(encodePng).toHaveBeenCalledWith(shot);
  });

  it("Capture is disabled while an upload is in flight and re-enabled once it lands", async () => {
    let finish: (v: { ok: boolean; path: string; bytes: number }) => void = () => undefined;
    vi.mocked(postAttachment).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const { field, submit } = mount();
    fireEvent.change(field, { target: { value: "wait for it" } });
    expect(submit.disabled).toBe(false);
    await pasteAndSettle(field, [pngFile()]);
    expect(screen.getByRole("status").textContent).toBe("Uploading image…");
    expect(submit.disabled).toBe(true);
    await act(async () => {
      finish({ ok: true, path: "/home/demo/state/attachments/late-1.png", bytes: 7 });
    });
    await waitFor(() => expect(thumbs()).toHaveLength(1));
    expect(submit.disabled).toBe(false);
  });

  it("zh copy: 📎 title / ✕ / dialog title / OK are the native strings", async () => {
    vi.mocked(postAttachment).mockRejectedValueOnce(new Error("boom"));
    const { LanguageContext } = await import("../../i18n");
    render(
      <LanguageContext.Provider value="zh">
        <LaneComposer placeholder="p" submitLabel="捕获" buildBody={(t) => ({ action: "capture", text: t })} />
      </LanguageContext.Provider>,
    );
    expect(screen.getByRole("button", { name: "添加图片（最多 4 张，仅保存在本机）" })).toBeTruthy();
    const field = screen.getByPlaceholderText("p") as HTMLTextAreaElement;
    await pasteAndSettle(field, [pngFile()]);
    const dialog = await screen.findByRole("dialog");
    expect(dialog.querySelector("h2")?.textContent).toBe("图片保存失败");
    fireEvent.click(screen.getByRole("button", { name: "好" }));
    await pasteAndSettle(field, [pngFile()]);
    await waitFor(() => expect(screen.getByRole("button", { name: "移除这张图" })).toBeTruthy());
  });
});
