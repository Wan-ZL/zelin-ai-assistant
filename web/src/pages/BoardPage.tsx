// 看板页（G2/A6）：五列 BoardLanes 装配。页面骨架关注点都在壳层——
// 顶栏/连接状态/离线横幅/整页加载与空态归 AppShell（G7），过滤 chips 归 FilterBar（G4）。
// 列与卡的全部逻辑在 components/board/ 下。
import { BoardLanes } from "../components/board/BoardLanes";

export function BoardPage() {
  return <BoardLanes />;
}
