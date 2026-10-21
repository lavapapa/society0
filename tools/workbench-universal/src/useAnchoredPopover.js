import { useLayoutEffect, useRef, useState } from 'react';

/**
 * 弹层定位：默认贴在触发点下方并夹在窗口内；窄屏（<900）交给 CSS 变为底部弹层。
 * 返回 ref 与内联样式，触发元素和弹层分别挂到 anchorRef / popoverRef。
 */
export function useAnchoredPopover(open, width = 320) {
  const anchorRef = useRef(null);
  const popoverRef = useRef(null);
  const [style, setStyle] = useState({});

  useLayoutEffect(() => {
    if (!open) return undefined;
    const place = () => {
      const anchor = anchorRef.current;
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      if (window.innerWidth <= 900) {
        setStyle({});
        return;
      }
      const w = Math.min(width, window.innerWidth - 16);
      const left = Math.max(8, Math.min(rect.left + rect.width / 2 - w / 2, window.innerWidth - w - 8));
      setStyle({ position: 'fixed', left, top: rect.bottom + 4, width: w });
    };
    place();
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [open, width]);

  return { anchorRef, popoverRef, style };
}
