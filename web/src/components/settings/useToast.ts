// 设置页内 toast（页内提示，非系统通知）：6s 自动消失；kind 决定 role（error → alert）。
// ModelsSection 先行内联了一份同款瞬态；后续 section 统一用这个 hook。
import { useEffect, useState } from "react";
import { ApiError } from "../../api";

export interface Toast {
  kind: "ok" | "error";
  message: string;
  /** 前缀独立节点（原生「保存设置失败: 」+ 原句 / 「已保存 」+ 时刻）：探针按节点直接文本判短标签 */
  prefix?: string;
}

const TOAST_MS = 6000;

export function useToast(): [Toast | null, (toast: Toast | null) => void] {
  const [toast, setToast] = useState<Toast | null>(null);
  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast]);
  return [toast, setToast];
}

/** 错误 → toast 文案（ApiError 用 server 整句原文，其它 String()） */
export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error);
}
