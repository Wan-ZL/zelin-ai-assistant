// 顶层错误边界（§49 追记 `store-resilience-drawer`）：React 对渲染期未捕获的异常是**卸载整棵树**——白页、没有任何字、
// 没有重试。这里接住它：一行「页面渲染失败」+ 异常原话 + 「重试」（重挂子树 + 重拉会议纪要）。语言读 store（LanguageContext
// 住在 App 里、边界在它外面），文案仍是 text(zh, en) 内联对——没有第二套 i18n。
// 诚实注：边界不知道崩的原因——坏响应进不了 store（api.request 拒非 JSON、refreshRecaps 验顶层形状），所以走到这里的多半是
// 别的渲染 bug；文案只说「渲染失败」+ 原话，不冒认某个文件读失败（宪法第 3 条）。
// 类组件是 React 错误边界的唯一写法（getDerivedStateFromError 没有 hook 版）；本仓库其余组件一律函数式。
import { Component, type ErrorInfo, type ReactNode } from "react";
import { getI18n } from "../../i18n";
import { getState, refreshRecaps } from "../../store";
import { EmptyState } from "./EmptyState";

interface AppErrorBoundaryProps {
  children: ReactNode;
}

interface AppErrorBoundaryState {
  error: Error | null;
}

/** 异常原话（Error → message；非 Error 的 throw 值 → String） */
export function renderErrorText(error: unknown): string {
  if (error instanceof Error) return error.message || error.name;
  return String(error);
}

export class AppErrorBoundary extends Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): AppErrorBoundaryState {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    // 控制台留原话 + 组件栈（DevTools 能追）；不上传、不进任何日志文件（宪法第 9 条）
    console.error("[zai] render crashed", error, info.componentStack);
  }

  retry = () => {
    this.setState({ error: null }); // 重挂子树
    void refreshRecaps();           // 同一条 refreshRecaps 通道：拉到新快照才有机会不再崩
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const { text } = getI18n(getState().language);
    // 唯一的 live region 是 EmptyState 自己（role=alert，assertive）——外层不再套 role，嵌套 alert>status 会让辅助技术读两遍
    return (
      <div className="shell-center" style={{ minHeight: "100vh" }} data-testid="app-error-boundary">
        <EmptyState
          role="alert"
          title={text("页面渲染失败", "The page failed to render")}
          hint={renderErrorText(error)}
          action={
            <button type="button" className="shell-button" onClick={this.retry}>
              {text("重试", "Retry")}
            </button>
          }
        />
      </div>
    );
  }
}
