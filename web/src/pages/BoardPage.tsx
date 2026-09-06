// 看板页（G2/A6）：五列 BoardLanes 装配。页面骨架关注点都在壳层——
// 顶栏/连接状态/离线横幅/整页加载与空态归 AppShell（G7），过滤 chips 归 FilterBar（G4）。
// 列与卡的全部逻辑在 components/board/ 下。
import { useEffect } from "react";
import { BoardLanes } from "../components/board/BoardLanes";
import { consumePendingFocus } from "../components/board/focusComposer";

export function BoardPage() {
  // ⌘L / quick_capture 从别的页过来（focusComposer 留下的 sessionStorage 接力棒，§54.4 2026-09-05 追记）：
  // 整页导航后由新文档在这里补上那一下聚焦。挂载时 composer 已在 DOM——AppShell 只在看板快照到了才渲染
  // children，effect 又在子树提交之后才跑。
  useEffect(() => {
    consumePendingFocus();
  }, []);
  return <BoardLanes />;
}
