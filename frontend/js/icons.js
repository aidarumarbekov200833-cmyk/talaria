/** Иконки для плиток ниш.
 *
 *  Бэкенд (backend/app/niches.py) отдаёт у каждой ниши только имя иконки —
 *  сами SVG живут здесь, чтобы не гонять разметку по сети. Ключи должны
 *  совпадать с полем `icon` в NICHES; для неизвестного имени отдаём точку,
 *  а не пустоту — плитка не должна «ломаться» из-за новой ниши на бэкенде.
 *
 *  Внутри — только содержимое <svg>: обёртку с viewBox добавляет icon().
 */

const PATHS = {
  briefcase:
    '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M8 7V5a2 2 0 012-2h4a2 2 0 012 2v2"/><path d="M2 13h20"/>',
  megaphone: '<path d="M3 11l14-6v14L3 13z"/><path d="M8 12v6a2 2 0 004 0v-2"/>',
  coins:
    '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/>',
  code: '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
  sparkles:
    '<path d="M12 3l1.8 4.8L18.6 9.6l-4.8 1.8L12 16.2l-1.8-4.8L5.4 9.6l4.8-1.8z"/><path d="M19 15l.9 2.4 2.4.9-2.4.9-.9 2.4-.9-2.4-2.4-.9 2.4-.9z"/>',
  book: '<path d="M4 4a2 2 0 012-2h13v18H6a2 2 0 00-2 2z"/><path d="M4 19h15"/>',
  globe:
    '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 010 18 14 14 0 010-18z"/>',
  dumbbell: '<path d="M6 8v8M18 8v8M3 10v4M21 10v4"/><path d="M6 12h12"/>',
  heart:
    '<path d="M20.8 5.6a5 5 0 00-7.1 0L12 7.3l-1.7-1.7a5 5 0 10-7.1 7.1L12 21l8.8-8.3a5 5 0 000-7.1z"/>',
  sparkle: '<path d="M12 2l2.2 5.9L20 10l-5.8 2.1L12 18l-2.2-5.9L4 10l5.8-2.1z"/>',
  shirt: '<path d="M8 2l4 3 4-3 5 4-2.5 3v11a1 1 0 01-1 1H6.5a1 1 0 01-1-1V9L3 6z"/>',
  plane:
    '<path d="M21 15l-9-4V4a1.5 1.5 0 00-3 0v7l-6 3v2l6-1.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L12 19v-4.5l9 2.5z"/>',
  utensils:
    '<path d="M4 2v7a3 3 0 003 3v10M7 2v7"/><path d="M17 2c-1.7 1.7-2 3-2 5s.3 3.3 2 5v10"/>',
  gamepad:
    '<rect x="2" y="7" width="20" height="11" rx="4"/><path d="M7 11v3M5.5 12.5h3"/><circle cx="16" cy="11.5" r="1"/><circle cx="18.5" cy="14" r="1"/>',
  car: '<path d="M5 13l1.6-4.6A2 2 0 018.5 7h7a2 2 0 011.9 1.4L19 13"/><path d="M3 13h18v4a1 1 0 01-1 1h-1.5"/><path d="M5.5 18H4a1 1 0 01-1-1v-4"/><circle cx="7.5" cy="18" r="1.8"/><circle cx="16.5" cy="18" r="1.8"/>',
  home: '<path d="M3 10l9-7 9 7v10a1 1 0 01-1 1h-5v-7H9v7H4a1 1 0 01-1-1z"/>',
  music: '<circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/><path d="M9 18V6l12-2v12"/>',
  brain:
    '<path d="M9.5 3A3.5 3.5 0 006 6.5 3 3 0 004 9.4 3 3 0 005.6 15 3.5 3.5 0 009 20a3 3 0 003-2.8V4.9A2 2 0 009.5 3z"/><path d="M14.5 3A3.5 3.5 0 0118 6.5 3 3 0 0120 9.4 3 3 0 0118.4 15 3.5 3.5 0 0115 20a3 3 0 01-3-2.8"/>',
  baby: '<circle cx="12" cy="11" r="7"/><path d="M9 10h.01M15 10h.01"/><path d="M9.5 14.5a3.5 2.5 0 005 0"/>',
};

/* Показываем что-то нейтральное, а не ничего: ниша на бэкенде может появиться
   раньше, чем иконка здесь, и плитка всё равно должна выглядеть целой. */
const FALLBACK = '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.5"/>';

export function icon(name, extraClass = '') {
  const body = PATHS[name] || FALLBACK;
  const cls = extraClass ? ` class="${extraClass}"` : '';
  return (
    `<svg${cls} viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
    `stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`
  );
}

export function hasIcon(name) {
  return Object.prototype.hasOwnProperty.call(PATHS, name);
}
