// 笔记库路径的两条纯规则（CONTRACT §68.1 追记「vault 根」/ §68.5；原生 Settings.swift loadVault / commitVaultRoot 与
// SetupWizard.swift ObsidianVaultSetup.apply）：用户面对的是 **vault 根**（设置「Obsidian Vault 位置」、向导第 5 步），
// 而 override 键 `obsidian_raw` 存的是 `<根>/2 - raw`——act/lib/config.py 从它的父目录派生另外三个管线目录
// （1 - unprocessed / 3 - change-summary / 4 - wiki），所以显示 = 父目录，落盘 = 根 + "/2 - raw"。
// 两个函数互为逆运算（`vaultRootOf(rawDirOf(root)) === root`，去掉结尾 / 之后），FieldControl 靠这一点把用户敲的字与草稿对上。
export const RAW_SUBDIR = "2 - raw";
/** 目录字段的 wire key（settings_catalog 的 obsidian 区）——只有这一把键按 vault 根显示 */
export const VAULT_RAW_KEY = "obsidian_raw";
/** 原生 / act/lib/config.py DEFAULT_OBSIDIAN_VAULT 的默认根（Settings.swift:787「默认 ~/Documents/Obsidian Vault」） */
export const DEFAULT_VAULT_ROOT = "~/Documents/Obsidian Vault";

/** 去掉结尾的 /（根 "/" 本身保留） */
function stripTrailingSlashes(path: string): string {
  let out = path;
  while (out.length > 1 && out.endsWith("/")) out = out.slice(0, -1);
  return out;
}

function basename(path: string): string {
  const bare = stripTrailingSlashes(path);
  return bare.slice(bare.lastIndexOf("/") + 1);
}

/** raw 目录 → vault 根 = 父目录（原生 `deletingLastPathComponent`，不管叶子叫什么——叶子不是 "2 - raw" 是 config.yaml
 *  手工自定义的情形，原生只多亮一句「部分管线目录已在 config.yaml 自定义」）。空 / 没有父目录 → ""；"/x" → "/"。 */
export function vaultRootOf(raw: string): string {
  const bare = stripTrailingSlashes(raw.trim());
  if (!bare) return "";
  const cut = bare.lastIndexOf("/");
  if (cut < 0) return "";
  return cut === 0 ? "/" : bare.slice(0, cut);
}

/** vault 根 → 要落盘的 obsidian_raw = `<根>/2 - raw`（原生 ObsidianVaultSetup.apply 的 `root + "/2 - raw"`）；
 *  叶子已经是 "2 - raw"（用户在对话框里直接选到了 raw 目录 / 敲的就是完整 raw 路径）→ 原样，不再套一层；空 → ""（清键）。 */
export function rawDirOf(root: string): string {
  const bare = stripTrailingSlashes(root.trim());
  if (!bare) return "";
  if (basename(bare) === RAW_SUBDIR) return bare;
  return bare === "/" ? `/${RAW_SUBDIR}` : `${bare}/${RAW_SUBDIR}`;
}
