// 「筛选」popover 外壳（§49 追记 2026-09-04：顶栏 compact / tight 档把 chips + 排序 + 清除收进这里）。
// role="dialog" + aria-label；打开即把焦点放进面板、Tab 在面板内循环、关闭把焦点还给触发按钮；
// ⎋ / 点外面 / 视口变化 关闭。定位跟 TaskPropertyPicker 同法：fixed，挂在触发按钮下方、贴视口边裁。
// 面板里的 TaskPropertyPicker 会把自己的 listbox portal 进最近的 [role='dialog']——即本面板——
// 所以「点外面」的判定天然把子弹层算作里面。
import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

interface FilterPopoverProps {
  anchorRef: RefObject<HTMLElement | null>;
  ariaLabel: string;
  onClose: () => void;
  children: ReactNode;
}

const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])";

export function FilterPopover({ anchorRef, ariaLabel, onClose, children }: FilterPopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 0, top: 0 });

  function focusables(): HTMLElement[] {
    return Array.from(panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
  }

  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    const panel = panelRef.current;
    if (!anchor || !panel) return;
    const anchorRect = anchor.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const gap = 4;
    const edge = 8;
    const left = Math.max(edge, Math.min(anchorRect.left, window.innerWidth - panelRect.width - edge));
    setPosition({ left, top: Math.max(edge, anchorRect.bottom + gap) });
  }, [anchorRef]);

  // 打开：焦点进面板第一个可聚焦项；关闭：还给触发按钮——除非关闭的那一下已经把焦点送去了别处
  // （⌘F 关面板并聚焦搜索框、点了面板外的另一个控件），那就不抢
  useEffect(() => {
    const anchor = anchorRef.current;
    requestAnimationFrame(() => focusables()[0]?.focus({ preventScroll: true }));
    return () => {
      const active = document.activeElement;
      if (!active || active === document.body) anchor?.focus({ preventScroll: true });
    };
  }, [anchorRef]);

  useEffect(() => {
    function closeFromOutside(event: PointerEvent) {
      const target = event.target as Node;
      if (panelRef.current?.contains(target) || anchorRef.current?.contains(target)) return;
      onClose();
    }
    function closeFromViewportChange(event: Event) {
      if (event.type === "scroll" && panelRef.current?.contains(event.target as Node)) return;
      onClose();
    }
    document.addEventListener("pointerdown", closeFromOutside);
    window.addEventListener("resize", closeFromViewportChange);
    window.addEventListener("scroll", closeFromViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", closeFromOutside);
      window.removeEventListener("resize", closeFromViewportChange);
      window.removeEventListener("scroll", closeFromViewportChange, true);
    };
  }, [anchorRef, onClose]);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      // 面板里开着 listbox 时让事件继续冒泡：TaskPropertyPicker 的 window 监听负责收它，这一下不关面板；
      // 没有子弹层才关面板，并 stopPropagation 让 FilterBar 的 window 监听（清词 / 退出多选）这一下不动
      if (panelRef.current?.querySelector("[role='listbox']")) return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusables();
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return createPortal(
    <div
      ref={panelRef}
      className="chrome-filter-panel"
      role="dialog"
      aria-label={ariaLabel}
      style={{ position: "fixed", left: position.left, top: position.top }}
      onKeyDown={handleKeyDown}
    >
      {children}
    </div>,
    document.body,
  );
}
