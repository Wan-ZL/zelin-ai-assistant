pr: `fix/parity-vault-root-field-semantics`（无版本 bump，版本由 tag 派生；behaviour-parity 程序 catalog-client 链第 2 批，接 #225）
phase: P4 余量（D3：web 看板是产品，`mac/Sources` 是只读规格——找回移植时丢掉的原生行为；无新 owner 决策，plan 决策表不加行）
law: §68.1 追记（「Obsidian Vault 位置」显示 vault 根、落 `<根>/2 - raw`）

**一条 gap（`settings-obsidian-vault-root-semantics`），wire 零变化，语义在客户端换算。** 原生 Settings.swift:740-792 是 v0.15 owner 决策的**一格 vault 根字段**：`loadVault` 显示 effective `obsidian_raw` 的父目录，`commitVaultRoot` → `ObsidianVaultSetup.apply(root)` 落 `root + "/2 - raw"`，`act/lib/config.py` 再从父目录派生 `1 - unprocessed` / `3 - change-summary` / `4 - wiki`。web 的 `FieldControl` 把它当普通目录字段逐字存取：用户按标签「Obsidian Vault 位置」挑了根 → 雷达扫根、另外三个目录派生到根的上一级；向导第 5 步 `VaultStep` 本就接 `/2 - raw` 还说「之后可在 设置 → 笔记库 修改」，两处自相矛盾。

修法：键仍是 `obsidian_raw`，草稿 / PUT / diff-write / `path_exists` / 打开 / 创建 全部仍作用于 raw 目录，server 不长新键（审计给的「`vault_root` 投影 + PUT 别名」那条路没有必要——两个纯函数就够）。`web/src/vaultPaths.ts`：`vaultRootOf`（父目录，不管叶子叫什么——原生同式）/ `rawDirOf`（去结尾 `/` 接 `/2 - raw`；叶子已是 `2 - raw` 原样不套层；空 = 清键）互逆，`RAW_SUBDIR` 单源（向导 `VaultStep` 改 import，行为不变）。`FolderControls.VaultRootField`：框里显示根、「选择…」的 `current` 与浏览器路径框预填都是根、敲字与选中落草稿为 raw。框里的字是本地态——第一版直接 `value={vaultRootOf(raw)}` 反向派生，`~/Notes/` 刚敲的 `/` 立刻被抹掉、下一个字接成 `~/NotesSub`；改成敲字只正向换算、外部换草稿（保存对齐 / 目录合并 / 选中）才重派生显示；选中一次性的完整路径显示派生出的根（选到 `2 - raw` 本身也显示父目录——第一版会把 raw 路径留在框里）。

`settings_catalog` 的字段文案随语义回到原生逐字（:750 副标题 + :787 页脚；placeholder = 默认根 `~/Documents/Obsidian Vault`，浏览器路径框共用，其它目录字段不再借用这句），fixture 重铸。**未做**：原生「⚙︎ 部分管线目录已在 config.yaml 自定义，不跟随这里的 vault 根目录」需要 server 侧 add-only `customized` 投影（判据镜像 `config._OBSIDIAN_DIR_NAMES`；server/ 不 import act），另 PR。

**判例**（新文件，防腐 #7）：`vaultPaths.test.ts` 8 条、`VaultRootField.test.tsx` 13 条（显示父目录 / 自定义叶子 / 空值 + placeholder / 老 server 回落 / 桥选中从根出发存 raw / 叶子已 raw 不套层 / 路径框预填根 + 共用 placeholder / 敲字留结尾 `/` / 敲完整 raw 原样 / 清空清键 / 外部换草稿重派生 / dirty 按 raw 判 / 工作目录仍逐字）；`FolderControls.test.tsx` 的通用目录判例改用 `default_target_repo`（它原本拿 `obsidian_raw` 钉「逐字存取」——正是这条 gap）。视觉 golden 零 diff（笔记库区在首屏之下）。
