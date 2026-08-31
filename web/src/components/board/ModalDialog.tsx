// 原生 <dialog> 外壳（§41：网页 fork/确认弹窗用原生 <dialog>）。
// 挂载即 showModal；Esc/backdrop 关闭统一走 onCancel。调用方按条件渲染（open 即挂载）。
import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

interface ModalDialogProps {
  title: string;
  onCancel: () => void;
  children: ReactNode;
}

export function ModalDialog({ title, onCancel, children }: ModalDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  return (
    <dialog
      ref={ref}
      className="zai-dialog"
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
      onClick={(e) => {
        // 点击 backdrop（target 是 dialog 本体而非内容）= 取消
        if (e.target === ref.current) onCancel();
      }}
    >
      <h2>{title}</h2>
      {children}
    </dialog>
  );
}
