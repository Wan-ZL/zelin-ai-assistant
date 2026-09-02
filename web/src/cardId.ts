// 卡片编号的展示口径（CONTRACT §60，D21 两段式编号）。
//   id          主键，终身不变：新卡 P-xxx；存量卡 legacy R-xxx。动作回传永远用它。
//   work_id     工作编号 R-xxx：卡进入 approved 时 server 端分配；提案/备选/回收站卡没有。
//   display_id  server 算好的展示名（= work_id ?? id）；旧 server 缺席时客户端同式回落。
//   id_kind     work | legacy | proposal —— server 给的分类，客户端**不按前缀猜**（防腐 #10）。
// 卡面/抽屉/对话框标题一律走 displayId()；cardAction() 继续送 id。

/** 展示编号：优先 server 的 display_id，缺席（旧投影）按同一公式回落 */
export function displayId(row: { id: string; display_id?: unknown; work_id?: unknown }): string {
  if (typeof row.display_id === "string" && row.display_id) return row.display_id;
  if (typeof row.work_id === "string" && row.work_id) return row.work_id;
  return row.id;
}

/** 该行是否被 ref（主键或工作编号）指到——深链 ?card=R-280 / 抽屉占位行查找用 */
export function matchesCardRef(row: { id: string; work_id?: unknown }, ref: string): boolean {
  return row.id === ref || (typeof row.work_id === "string" && row.work_id === ref);
}

/** 存量 R- 主键且未获工作编号 = 「检测即分号」的旧卡：卡面灰显编号 */
export function isLegacyId(row: { id_kind?: unknown }): boolean {
  return row.id_kind === "legacy";
}
