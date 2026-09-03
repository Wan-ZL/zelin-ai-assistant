// 自动草稿 PR 通道横幅（CONTRACT §65.4）：敏感路径护栏把通道挂起时说话——
// 数据源 = board.self_improve（dashboard.json 顶层 add-only 键，老 daemon 无此键 → 不渲染）。
// 只在 paused 时出现：点名被标记的 PR（needs-owner-eyes）与触碰的受保护路径，给一个
// 「恢复通道」按钮（POST /api/self-improve/resume → 下一 pass 免批派发继续）。owner 处理完
// 该 PR（合并/关闭）actd 巡检也会自动清——两条出口都在文案里。enabled=false 不渲染
// （那是配置，不是事故）。与 ErrorBanner 互斥：server 连不上时它闭嘴。
import { useState } from "react";
import { ApiError, postSelfImproveResume } from "../../api";
import { useI18n } from "../../i18n";
import { refreshBoard, useAppState } from "../../store";
import type { SelfImproveState } from "../../types";

/** 是否该显示 + 文案；null = 不渲染。导出供测试直测。 */
export function describeSelfImprove(
  state: SelfImproveState | undefined,
  text: (zh: string, en: string) => string,
): { title: string; detail: string } | null {
  if (!state || !state.paused) return null;
  const pr = state.paused_pr != null ? `#${state.paused_pr}` : text("（未知 PR）", "(unknown PR)");
  const paths = (state.paused_paths ?? []).join(", ") || text("受保护路径", "protected paths");
  return {
    title: text("自我改进通道已暂停", "Self-improve lane paused"),
    detail: text(
      `PR ${pr} 触碰了 ${paths}，已打 needs-owner-eyes 标签。处理该 PR（合并/关闭）后自动恢复，或现在点「恢复通道」。`,
      `PR ${pr} touches ${paths}; it is labelled needs-owner-eyes. The lane resumes when you merge/close that PR, or press Resume now.`,
    ),
  };
}

export function SelfImproveBanner() {
  const { text } = useI18n();
  const { board, boardError, connection } = useAppState();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!board) return null;
  if (boardError != null || connection === "reconnecting") return null; // ErrorBanner 在说话
  const state = board.self_improve;
  const described = describeSelfImprove(state, text);
  if (!described || !state) return null;

  const resume = async () => {
    setPending(true);
    setError(null);
    try {
      await postSelfImproveResume();
      await refreshBoard();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="shell-banner is-warning" role="alert" data-lane="self_improve">
      <svg className="shell-banner-icon" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M12 2.8 22.6 21H1.4L12 2.8Zm0 6.2a1 1 0 0 0-1 1v4a1 1 0 1 0 2 0v-4a1 1 0 0 0-1-1Zm0 8a1.2 1.2 0 1 0 0 2.4 1.2 1.2 0 0 0 0-2.4Z"
          fill="currentColor"
        />
      </svg>
      <strong className="shell-banner-title">{described.title}</strong>
      <span className="shell-banner-detail">
        {described.detail}
        {state.paused_pr_url && (
          <>
            {" "}
            <a href={state.paused_pr_url} target="_blank" rel="noreferrer">
              {text("打开 PR", "Open PR")}
            </a>
          </>
        )}
      </span>
      <button type="button" className="shell-button" disabled={pending} onClick={() => void resume()}>
        {pending ? text("恢复中…", "Resuming…") : text("恢复通道", "Resume lane")}
      </button>
      {error && <span className="shell-banner-detail">{error}</span>}
    </div>
  );
}
