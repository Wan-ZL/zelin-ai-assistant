// 脚手架冒烟测试：route 深链序列化（纯函数）。v2.0.0：缺省页 = 会议纪要；?card= 随卡片退役。
import { describe, expect, it } from "vitest";
import { buildAppUrl, buildSettingsUrl, DEFAULT_PAGE, DEPS_ANCHOR, hasExplicitRoute, isDepsPage, readPage, readSettingsAnchor, withoutSettingsAnchor } from "./route";

describe("route", () => {
  it("defaults to the recaps page; unknown and retired pages fall back to it", () => {
    expect(DEFAULT_PAGE).toBe("recaps");
    expect(readPage("")).toBe("recaps");
    expect(readPage("?page=bogus")).toBe("recaps");
    // 退役页（看板 / 回收站 / 永久性完成 / 技能 / 样式指南 / 问问助手）：旧深链回落会议纪要，不崩
    for (const retired of ["board", "trash", "archive", "skills", "styleguide", "ask"]) {
      expect(readPage(`?page=${retired}`)).toBe("recaps");
    }
  });

  it("recognizes the surviving pages and round-trips them", () => {
    for (const page of ["settings", "ingest", "about", "permissions", "setup"] as const) {
      expect(readPage(`?page=${page}`)).toBe(page);
      expect(buildAppUrl("http://127.0.0.1:47820/", page).searchParams.get("page")).toBe(page);
    }
  });

  it("the default page is not written into the query, and a stale ?card= is dropped on navigation", () => {
    const url = buildAppUrl("http://127.0.0.1:47820/?page=settings&card=R-1", "recaps");
    expect(url.searchParams.get("page")).toBeNull();
    expect(url.searchParams.get("card")).toBeNull();
    expect(readPage(buildAppUrl(url.href, "about").search)).toBe("about");
  });

  it("hasExplicitRoute only counts ?page= (cold start may restore the last rail page otherwise)", () => {
    expect(hasExplicitRoute("?page=settings")).toBe(true);
    expect(hasExplicitRoute("?card=R-1")).toBe(false);
    expect(hasExplicitRoute("")).toBe(false);
  });

  it("依赖检查是设置页的一区（D30）：deps / diagnostics 旧深链仍认、都滚到 anchor=deps；buildSettingsUrl 给出新形", () => {
    expect(isDepsPage("deps")).toBe(true);
    expect(isDepsPage("diagnostics")).toBe(true);
    expect(isDepsPage("settings")).toBe(false);
    expect(readSettingsAnchor("?page=deps&log=engine.log")).toBe(DEPS_ANCHOR);
    expect(readSettingsAnchor("?page=settings&anchor=recording")).toBe("recording");
    const url = buildSettingsUrl("http://127.0.0.1:47820/?page=deps", DEPS_ANCHOR);
    expect(url.searchParams.get("page")).toBe("settings");
    expect(url.searchParams.get("anchor")).toBe("deps");
  });

  it("withoutSettingsAnchor 摘掉 ?anchor= 与 #settings-<id>（D44：设置页读过一次就 replaceState），其余 query / hash 原样", () => {
    expect(withoutSettingsAnchor("http://127.0.0.1:47820/?page=settings&anchor=recap").toString()).toBe("http://127.0.0.1:47820/?page=settings");
    expect(withoutSettingsAnchor("http://127.0.0.1:47820/?page=settings#settings-updates").toString()).toBe("http://127.0.0.1:47820/?page=settings");
    expect(withoutSettingsAnchor("http://127.0.0.1:47820/?page=deps&log=engine.log&anchor=deps#settings-deps").toString())
      .toBe("http://127.0.0.1:47820/?page=deps&log=engine.log");
    expect(withoutSettingsAnchor("http://127.0.0.1:47820/?page=settings#other").toString()).toBe("http://127.0.0.1:47820/?page=settings#other");
    expect(withoutSettingsAnchor("http://127.0.0.1:47820/?page=settings").toString()).toBe("http://127.0.0.1:47820/?page=settings");
  });
});
