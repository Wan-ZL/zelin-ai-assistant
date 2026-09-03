// 脚手架冒烟测试：route 深链序列化（纯函数）。
import { describe, expect, it } from "vitest";
import { buildAppUrl, readCardId, readPage } from "./route";

describe("route", () => {
  it("reads the card deep link preserving case", () => {
    // SAFE_ID_RE 允许小写——id 按原样精确匹配，绝不 case 折叠
    expect(readCardId("?card=r-101")).toBe("r-101");
    expect(readCardId("?card=R-101")).toBe("R-101");
    expect(readCardId("?card=%20R-101%20")).toBe("R-101"); // trim 保留
    expect(readCardId("?card=")).toBeNull();
    expect(readCardId("")).toBeNull();
  });

  it("defaults to the board page and recognizes trash", () => {
    expect(readPage("")).toBe("board");
    expect(readPage("?page=trash")).toBe("trash");
    expect(readPage("?page=bogus")).toBe("board");
  });

  it("recognizes the settings page (§59) and round-trips it", () => {
    expect(readPage("?page=settings")).toBe("settings");
    const url = buildAppUrl("http://127.0.0.1:47820/?card=R-1", "settings", null);
    expect(url.searchParams.get("page")).toBe("settings");
    expect(url.searchParams.get("card")).toBeNull();
  });

  it("recognizes the recaps page (§63)", () => {
    expect(readPage("?page=recaps")).toBe("recaps");
    expect(buildAppUrl("http://127.0.0.1:47820/", "recaps", null).searchParams.get("page")).toBe("recaps");
  });

  it("round-trips page + card through buildAppUrl", () => {
    const url = buildAppUrl("http://127.0.0.1:47820/?page=trash", "board", "r-101");
    expect(url.searchParams.get("page")).toBeNull(); // board 是缺省，不落 query
    expect(url.searchParams.get("card")).toBe("r-101"); // 大小写原样保留
    expect(readPage(buildAppUrl(url.href, "trash", null).search)).toBe("trash");
  });
});
