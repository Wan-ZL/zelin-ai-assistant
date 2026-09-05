// 笔记库路径的两条纯规则（CONTRACT §68.1 追记 vault 根；原生 Settings.swift loadVault `deletingLastPathComponent` /
// ObsidianVaultSetup.apply `root + "/2 - raw"`）：raw → 根 是取父目录（不管叶子叫什么），根 → raw 是接 "/2 - raw"
// （叶子已是 "2 - raw" 原样、空清键、结尾 / 去掉），两者互逆。
import { describe, expect, it } from "vitest";
import { DEFAULT_VAULT_ROOT, RAW_SUBDIR, rawDirOf, vaultRootOf } from "./vaultPaths";

describe("vaultRootOf", () => {
  it("is the parent of the raw dir, trailing slashes ignored", () => {
    expect(vaultRootOf("~/Notes/2 - raw")).toBe("~/Notes");
    expect(vaultRootOf("~/Notes/2 - raw/")).toBe("~/Notes");
    expect(vaultRootOf("  ~/Documents/Obsidian Vault/2 - raw  ")).toBe("~/Documents/Obsidian Vault");
  });

  it("takes the parent whatever the leaf is called (a hand-customised raw dir still shows its vault)", () => {
    expect(vaultRootOf("~/Custom/inbox")).toBe("~/Custom");
  });

  it("has no parent for an empty / bare / root-level path", () => {
    expect(vaultRootOf("")).toBe("");
    expect(vaultRootOf("2 - raw")).toBe("");
    expect(vaultRootOf("/2 - raw")).toBe("/");
  });
});

describe("rawDirOf", () => {
  it("appends the standard raw leaf to the root", () => {
    expect(rawDirOf("~/Notes")).toBe(`~/Notes/${RAW_SUBDIR}`);
    expect(rawDirOf("~/Notes/")).toBe("~/Notes/2 - raw");
    expect(rawDirOf("  ~/Notes//  ")).toBe("~/Notes/2 - raw");
    expect(rawDirOf("/")).toBe("/2 - raw");
  });

  it("leaves a path whose leaf already is the raw dir alone (no double nesting)", () => {
    expect(rawDirOf("~/Notes/2 - raw")).toBe("~/Notes/2 - raw");
    expect(rawDirOf("~/Notes/2 - raw/")).toBe("~/Notes/2 - raw");
  });

  it("maps an emptied field to the empty string (= clear the override)", () => {
    expect(rawDirOf("")).toBe("");
    expect(rawDirOf("   ")).toBe("");
  });

  it("round-trips with vaultRootOf", () => {
    for (const root of ["~/Notes", DEFAULT_VAULT_ROOT, "/Volumes/Data/Vault", "/"]) {
      expect(vaultRootOf(rawDirOf(root))).toBe(root);
    }
  });
});
