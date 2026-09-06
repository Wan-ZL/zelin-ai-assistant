import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app";
import { AppErrorBoundary } from "./components/shell/AppErrorBoundary";
import "./styles/tokens.css";
import "./styles/shell.css";
import "./styles/board.css";
import "./styles/animations.css";

// 顶层错误边界（§49 追记 `store-resilience-drawer`）：渲染期未捕获的异常 → 「看板渲染失败」+ 重试，而不是 React 卸载整棵树留白页
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </StrictMode>,
);
