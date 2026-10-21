import React from 'react';

/** 配置分区图标：16px 线性图标，跟随文字颜色。 */
export function SectionIcon({ name }) {
  const common = { width: 14, height: 14, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.3, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true };
  switch (name) {
    case 'grid':
      return <svg {...common}><rect x="2.5" y="2.5" width="4.5" height="4.5" rx="1" /><rect x="9" y="2.5" width="4.5" height="4.5" rx="1" /><rect x="2.5" y="9" width="4.5" height="4.5" rx="1" /><rect x="9" y="9" width="4.5" height="4.5" rx="1" /></svg>;
    case 'branch':
      return <svg {...common}><circle cx="4" cy="3.5" r="1.5" /><circle cx="4" cy="12.5" r="1.5" /><circle cx="12" cy="8" r="1.5" /><path d="M4 5v6M5.5 8h5" /></svg>;
    case 'clock':
      return <svg {...common}><circle cx="8" cy="8" r="5.5" /><path d="M8 4.8V8l2.2 1.4" /></svg>;
    case 'user':
      return <svg {...common}><circle cx="8" cy="5.5" r="2.4" /><path d="M3.4 13c.6-2.4 2.4-3.7 4.6-3.7s4 1.3 4.6 3.7" /></svg>;
    case 'chart':
      return <svg {...common}><path d="M2.5 13V3M2.5 13h11" /><path d="M5 10.5V7M8 10.5V4.5M11 10.5V6" /></svg>;
    case 'layers':
      return <svg {...common}><path d="M8 2.5 14 6l-6 3.5L2 6l6-3.5Z" /><path d="M2 9.5 8 13l6-3.5" /></svg>;
    case 'cpu':
      return <svg {...common}><rect x="4" y="4" width="8" height="8" rx="1.5" /><path d="M6.5 1.8v2M9.5 1.8v2M6.5 12.2v2M9.5 12.2v2M1.8 6.5h2M1.8 9.5h2M12.2 6.5h2M12.2 9.5h2" /></svg>;
    default:
      return <svg {...common}><circle cx="8" cy="8" r="2" /><circle cx="8" cy="8" r="5.5" strokeDasharray="1.6 2.2" /></svg>;
  }
}
