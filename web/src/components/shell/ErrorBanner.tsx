// 离线横幅（G7 shell，自写非 fork）：server 连不上时的诚实降级——
// 有旧数据 → 顶部横幅声明"下面是最后一次成功加载的快照，正在自动重连"；
// 完全没有数据 → 本组件不渲染（整页空态由 AppShell 负责，同一信息绝不双份，
// 语义对齐 Mac 版 PipelineHealthBanner/PipelineEmptyStateView 的分工）。
// 触发条件：board 读失败（api 合成 READ_FAILED）或 SSE 处于 reconnecting。
// 第二种话（§49 追记 `store-resilience-drawer`）：server 答了、dashboard.json 却解不出来（写了一半 / 损坏）——
// 原生 Kanban.swift:60-66 在 header 下给一行橙色 loadError、看板照常渲染上一版；这里是同一横幅的 warning 变体，
// 不借离线文案（server 明明在跑），健康横幅也不因它闭嘴（它们只看 boardError）。离线优先：两句话不同时说。
// 第三种话（§49 追记 2026-09-18，issue #423）：dashboard.json 在、但 server 这个进程读不动（503 BOARD_UNREADABLE）——
// 同一个 warning 变体，标题带 errno，细节**不承诺自动重连**（api.request 只重试抛错的 fetch，503 是一次真回答，
// 而看板并不轮询）。PipelineBanner 刻意不长 dashboard_error 分支，就是为了这句话不出现两遍（§47.4 读者 3）。
import { useI18n } from "../../i18n";
import { useAppState } from "../../store";

export function ErrorBanner() {
  const { text } = useI18n();
  const { board, boardError, boardDecodeError, boardUnreadable, connection } = useAppState();

  if (!board) return null; // 无快照：AppShell 整页空态接管
  const offline = boardError != null || connection === "reconnecting";
  if (!offline && !boardDecodeError && !boardUnreadable) return null;

  // 离线优先，其次读不动，最后解不出来——三态在 store 里本就互斥，这里的顺序只为「同一时刻一句话」兜底
  const title = offline
    ? text("连不上本地服务", "Can't reach the local server")
    : (boardUnreadable ?? boardDecodeError);
  const detail = offline
    ? text(
        "正在自动重连；下面显示的是最后一次成功加载的看板快照。",
        "Reconnecting automatically — the board below is the last successfully loaded snapshot.",
      )
    : boardUnreadable
      // 刻意不承诺自动重连：api.request 只重试**抛错**的 fetch，503 是一次真回答；看板也不轮询（只有 /api/health 轮）。
      // 也**不说「文件在」**（server 那侧刻意不断言存在），也**不说「点重试」**——本横幅没有按钮，
      // 有快照时整页是正常看板；实际让它恢复的是下一次成功的读。
      ? text(
          "下面显示的是最后一次成功加载的看板快照；后台服务这个进程读不动看板文件，修好访问权限后下一次读成功即恢复。",
          "The board below is the last successfully loaded snapshot; this server process could not read the board file, and it recovers on the next successful read once access is fixed.",
        )
      : text(
          "下面显示的是最后一次成功加载的看板快照；后台服务下一次写出看板后自动恢复。",
          "The board below is the last successfully loaded snapshot; it recovers when the background service next writes the board.",
        );

  return (
    <div className={`shell-banner${offline ? "" : " is-warning"}`} role="alert">
      <svg className="shell-banner-icon" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M12 2.8 22.6 21H1.4L12 2.8Zm0 6.2a1 1 0 0 0-1 1v4a1 1 0 1 0 2 0v-4a1 1 0 0 0-1-1Zm0 8a1.2 1.2 0 1 0 0 2.4 1.2 1.2 0 0 0 0-2.4Z"
          fill="currentColor"
        />
      </svg>
      <strong className="shell-banner-title">{title}</strong>
      <span className="shell-banner-detail">{detail}</span>
    </div>
  );
}
