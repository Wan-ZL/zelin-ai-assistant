// 「筛选」popover 外壳（§49 追记 2026-09-04：顶栏 compact / tight 档把 chips + 排序 + 清除收进这里；
// ⎋ 与焦点的交付路径见 §49 追记 2026-09-18 / issue #420）。
// role="dialog" + aria-label；打开即把焦点放进面板、Tab 在面板内循环、关闭把焦点还给触发按钮；
// ⎋ / 点外面 / 视口变化 关闭——这三条**都不看焦点在哪儿**：三个监听一起挂在 window / document 上。
// 定位跟 TaskPropertyPicker 同法：fixed，挂在触发按钮下方、贴视口边裁。
// 面板里的 TaskPropertyPicker 会把自己的 listbox portal 进最近的 [role='dialog']——即本面板——
// 所以「点外面」的判定天然把子弹层算作里面；⎋ 的子弹层让位有两道（defaultPrevented + querySelector），
// 因为两个监听同挂 window、谁先跑不由我们定，见下面 closeFromEscape 的注释。
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
  // （⌘F 关面板并聚焦搜索框、点了面板外的另一个控件），那就不抢。
  // 聚焦这一下**同步做**（#420）：此前排在 requestAnimationFrame 里，帧没来之前焦点还停在 body
  // （tight 档开面板那一下尤其：pointerdown 被 preventDefault 不给按钮焦点、click 又把展开的搜索框
  // 卸掉），于是「Tab 在面板内循环」在那段时间里也是空的——与下面 ⎋ 那条同一个道理：焦点在 body 上
  // 时 Tab 的 target 就是 body，下面挂在面板上的 handleKeyDown（React 委托）根本不跑，焦点按文档顺序
  // 直接走出面板。同步聚焦不引起滚动跳动的理由是 preventScroll + 面板 position: fixed，不是定位先后。
  // 位置在 style 上落定得比这一下晚也无妨：滚不动的元素聚焦不会把视口拽走。
  useEffect(() => {
    const anchor = anchorRef.current;
    focusables()[0]?.focus({ preventScroll: true });
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
    // ⎋ 关面板（issue #420）。**挂 window，不挂面板的 onKeyDown**：React 的 onKeyDown 只在原生事件的
    // target 落在面板子树里才跑，而「面板开着」与「焦点在面板里」是两回事——tight 档点「筛选」开面板的
    // 那一下，pointerdown 被 keepSearchFocus 拦下（按钮不拿焦点）、click 又把正聚焦的搜索框卸掉，
    // activeElement 掉回 body；这一下 ⎋ 送到 body，面板的 React 监听收不到，FilterBar 的 window 监听又
    // 因 panelOpen 让位（FilterBar.tsx「弹窗 / 筛选面板 / 详情侧栏开着时不插手」），于是没有任何人关它，
    // 面板永久卡开。判据不该是焦点，而是「面板开着」——所以跟「点外面」「视口变化」一样挂全局。
    // 兄弟件 TaskPropertyPicker.closeFromEscape 早就是同一套 window 监听，这里跟它同法。
    // 冒泡相（非 capture）：列顶输入框 / 改名框靠 React 的 stopPropagation 把自己的 ⎋ 就地吃掉
    //（§34 追记 2026-09-05 的双保险之一），capture 会抢在它们前面，把那一半静默退役。
    function closeFromEscape(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (event.isComposing || event.keyCode === 229) return; // §15 IME 红线：候选期间的 ⎋ 归输入法
      // 别人已经认领了这一下就不抢——**顺序无关**的那半边让位判据。window 上的监听谁先注册谁先跑，
      // 而顺序不由我们说了算：React 的 passive effect 是**子先于父**，chips 作为 children 挂在本面板
      // 底下，所以面板与子弹层在**同一次 commit** 里出生时（档位变窄前就点开过 chip，openChip 还留着
      // 的那种），TaskPropertyPicker 的监听反而排在前面。它认领 ⎋ 时一定先 preventDefault，据此让位就
      // 不必赌顺序。同一道门顺带盖住只 preventDefault、不 stopPropagation 的别人家输入框（设置页搜索框
      // 那类）——⌘L 之类的快捷键能把焦点直接送进面板外的文字框，不经 pointerdown 也不经 Tab。
      if (event.defaultPrevented) return;
      // 面板里开着 listbox 时这一下归它：本监听先跑的那一半靠这句让位——只让位、不
      // stopImmediatePropagation，那会把子弹层一起锁死在开着的状态
      if (panelRef.current?.querySelector("[role='listbox']")) return;
      // 上面还压着模态时这一下归模态（§49 追记 2026-09-04 D34「侧栏开着时 ⎋ 只关侧栏」）：与
      // FilterBar 用的是同一句判据。本面板是 role="dialog" 但**没有** aria-modal，所以选不中自己。
      if (document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return;
      // 不 stopPropagation：window 是冒泡路的最后一站，对同挂 window 的听众也无效（那得
      // stopImmediatePropagation）。真正让 FilterBar 的两段 ⎋ 站住不动的是它自己的 panelOpen 让位。
      event.preventDefault();
      onClose();
    }
    function closeFromViewportChange(event: Event) {
      if (event.type === "scroll" && panelRef.current?.contains(event.target as Node)) return;
      onClose();
    }
    document.addEventListener("pointerdown", closeFromOutside);
    window.addEventListener("keydown", closeFromEscape);
    window.addEventListener("resize", closeFromViewportChange);
    window.addEventListener("scroll", closeFromViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", closeFromOutside);
      window.removeEventListener("keydown", closeFromEscape);
      window.removeEventListener("resize", closeFromViewportChange);
      window.removeEventListener("scroll", closeFromViewportChange, true);
    };
  }, [anchorRef, onClose]);

  // 只管 Tab 的环：⎋ 归上面的 window 监听（#420——挂在这里的话，焦点还没进面板的那一下就收不到）
  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
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
