// 脚手架冒烟测试：route 深链序列化（纯函数）。
import { describe, expect, it } from "vitest";
import { buildAppUrl, readCardId, readPage } from "./route";

describe("route", () => {
  it("reads and normalizes the card deep link", () => {
    expect(readCardId("?card=r-101")).toBe("R-101");
    expect(readCardId("?card=")).toBeNull();
    expect(readCardId("")).toBeNull();
  });

  it("defaults to the board page and recognizes trash", () => {
    expect(readPage("")).toBe("board");
    expect(readPage("?page=trash")).toBe("trash");
    expect(readPage("?page=bogus")).toBe("board");
  });

  it("round-trips page + card through buildAppUrl", () => {
    const url = buildAppUrl("http://127.0.0.1:47820/?page=trash", "board", "r-101");
    expect(url.searchParams.get("page")).toBeNull(); // board 是缺省，不落 query
    expect(url.searchParams.get("card")).toBe("R-101");
    expect(readPage(buildAppUrl(url.href, "trash", null).search)).toBe("trash");
  });
});
