// Small 24px line icons (stroke = currentColor). Decorative only: every use sits next to a text label.

const PATHS = {
  overview: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.6"/>',
  today: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.2"/><path d="M3.5 10h17M8.5 3v4M15.5 3v4M9 15l2 2 4-4"/>',
  stock: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
  prices: '<path d="M3.5 12.2V4.5a1 1 0 011-1h7.7l8.3 8.3a1.4 1.4 0 010 2l-6.7 6.7a1.4 1.4 0 01-2 0z"/><circle cx="8" cy="8" r="1.6"/>',
  orders: '<path d="M2.5 6h11.5v10H2.5zM14 9.5h4l3.5 3.5v3H14"/><circle cx="6.5" cy="18" r="1.8"/><circle cx="17.5" cy="18" r="1.8"/>',
  live: '<circle cx="12" cy="12" r="2.2"/><path d="M8 8a5.6 5.6 0 000 8M16 16a5.6 5.6 0 000-8M5.2 5.2a9.6 9.6 0 000 13.6M18.8 18.8a9.6 9.6 0 000-13.6"/>',
  rupee: '<path d="M7 4.5h10M7 9h10M7 4.5h3a4.5 4.5 0 010 9H7l7.5 6"/>',
  cart: '<path d="M3 4h2.2l2.2 10.2a1.2 1.2 0 001.2 1H17a1.2 1.2 0 001.2-.9L19.8 8H6.2"/><circle cx="9.5" cy="19" r="1.3"/><circle cx="16.5" cy="19" r="1.3"/>',
  trend: '<path d="M3.5 16.5l5.5-5.5 3.5 3.5 7.5-7.5"/><path d="M15 7h5v5"/>',
  star: '<path d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15.5" rx="2.2"/><path d="M3.5 10h17M8.5 3v4M15.5 3v4"/>',
  scan: '<path d="M4 8V5a1 1 0 011-1h3M16 4h3a1 1 0 011 1v3M20 16v3a1 1 0 01-1 1h-3M8 20H5a1 1 0 01-1-1v-3"/><circle cx="12" cy="12" r="3.2"/>',
  doc: '<path d="M14 3H6.5a1.5 1.5 0 00-1.5 1.5v15A1.5 1.5 0 006.5 21h11a1.5 1.5 0 001.5-1.5V8z"/><path d="M14 3v5h5M8.5 13h7M8.5 16.5h4.5"/>',
  left: '<path d="M14.5 6l-6 6 6 6"/>',
  right: '<path d="M9.5 6l6 6-6 6"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
} as const;

export type IconName = keyof typeof PATHS;

export function icon(name: IconName, size = 20, width = 1.6): SVGSVGElement {
  const tpl = document.createElement('template');
  tpl.innerHTML = `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${PATHS[name]}</svg>`;
  return tpl.content.firstElementChild as SVGSVGElement;
}
