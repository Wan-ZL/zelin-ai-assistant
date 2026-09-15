// 用户面只显示 tag，`+N` 降级（CONTRACT §56.1 追记 2026-09-14；issue #309）。
// 生产机的 `1.0.23+92` 里的 92 数的是 release 分支上 ingest 的数据 commit——deploy_state 当时
// 说 `1.0.23+101`，同一台机器两个版本号。关于页与顶栏因此只渲染 tag，`+N` 变成一句辅助说明。
import { describe, expect, it } from "vitest";
import { aheadCommits, aheadNote, releaseVersion } from "./version";

const zh = (z: string, _e: string) => z;
const en = (_z: string, e: string) => e;

describe("releaseVersion / aheadCommits (§56.1 `X.Y.Z+N`)", () => {
  it("strips the build metadata and counts the local commits", () => {
    expect(releaseVersion("1.0.23+92")).toBe("1.0.23");
    expect(aheadCommits("1.0.23+92")).toBe(92);
    expect(releaseVersion("1.0.98")).toBe("1.0.98");
    expect(aheadCommits("1.0.98")).toBe(0);
    expect(releaseVersion("  1.0.98+1  ")).toBe("1.0.98");
    expect(aheadCommits("  1.0.98+1  ")).toBe(1);
  });

  it("tolerates every shape act.__version__ can degrade to (unknown / empty / non-string)", () => {
    for (const bad of [undefined, null, 42, {}, [], true]) {
      expect(releaseVersion(bad)).toBe("");
      expect(aheadCommits(bad)).toBe(0);
    }
    expect(releaseVersion("unknown")).toBe("unknown");
    expect(aheadCommits("unknown")).toBe(0);
    expect(releaseVersion("")).toBe("");
    // `+` 后面不是正整数 = 不是我们认识的 build metadata：版本照切，计数不猜
    expect(aheadCommits("1.0.23+")).toBe(0);
    expect(aheadCommits("1.0.23+abc")).toBe(0);
    expect(aheadCommits("1.0.23+0")).toBe(0);
    expect(aheadCommits("1.0.23+-3")).toBe(0);
    expect(releaseVersion("1.0.23+abc")).toBe("1.0.23");
  });
});

describe("aheadNote", () => {
  it("is null without a +N and names the count in both languages (singular in en)", () => {
    expect(aheadNote("1.0.98", zh)).toBeNull();
    expect(aheadNote(undefined, zh)).toBeNull();
    expect(aheadNote("1.0.23+92", zh)).toBe("本地领先 92 个提交，未发版");
    expect(aheadNote("1.0.23+92", en)).toBe("92 local commits ahead of the tag, unreleased");
    expect(aheadNote("1.0.23+1", en)).toBe("1 local commit ahead of the tag, unreleased");
  });
});
