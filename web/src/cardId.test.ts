// §60（D21）两段式编号的展示口径——纯函数判例。
//   display_id 优先；缺席按 work_id ?? id 回落（旧 server 投影）；
//   深链/占位行查找按主键或工作编号命中；legacy 判定只读 server 的 id_kind，不看前缀。
import { describe, expect, it } from "vitest";
import { displayId, isLegacyId, matchesCardRef } from "./cardId";

describe("displayId", () => {
  it("server 给了 display_id 就用它（work 卡显示工作编号，主键不露面）", () => {
    expect(displayId({ id: "P-012", work_id: "R-280", display_id: "R-280" })).toBe("R-280");
  });
  it("旧 server 缺 display_id：work_id 优先，再回落主键", () => {
    expect(displayId({ id: "P-012", work_id: "R-280" })).toBe("R-280");
    expect(displayId({ id: "P-012" })).toBe("P-012");
    expect(displayId({ id: "R-050", work_id: null })).toBe("R-050");
  });
});

describe("matchesCardRef", () => {
  it("主键或工作编号任一命中", () => {
    const row = { id: "P-012", work_id: "R-280" };
    expect(matchesCardRef(row, "P-012")).toBe(true);
    expect(matchesCardRef(row, "R-280")).toBe(true);
    expect(matchesCardRef(row, "R-012")).toBe(false);
  });
  it("无工作编号的卡只按主键命中", () => {
    expect(matchesCardRef({ id: "P-012" }, "R-012")).toBe(false);
  });
});

describe("isLegacyId", () => {
  it("只信 server 的 id_kind——R- 前缀本身不算 legacy（防腐 #10）", () => {
    expect(isLegacyId({ id_kind: "legacy" })).toBe(true);
    expect(isLegacyId({ id_kind: "work" })).toBe(false);
    expect(isLegacyId({ id_kind: "proposal" })).toBe(false);
    expect(isLegacyId({})).toBe(false);
  });
});
