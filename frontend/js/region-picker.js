/** Выбор региона: список всех стран с поиском.
 *
 *  Раньше это был обычный <select> с пятью странами. Всплывающее меню
 *  нативного селекта рисует браузер, а цвет текста наследуется от страницы —
 *  на тёмной теме получался белый список с белым текстом. Поэтому список свой:
 *  кнопка + панель с полем поиска, а сам <select> остаётся в разметке скрытым
 *  и по-прежнему хранит значение (js/search.js читает `.value`).
 */

import { REGIONS, REGION_PINNED } from './regions.js';

const CHEVRON =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';

/** Часто нужные страны — вверху, остальные по алфавиту. */
function ordered() {
  const pinned = REGION_PINNED.map((code) => REGIONS.find((r) => r.code === code)).filter(Boolean);
  const rest = REGIONS.filter((r) => !REGION_PINNED.includes(r.code));
  return { pinned, rest, all: [...pinned, ...rest] };
}

function matches(region, query) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return region.name.toLowerCase().includes(q) || region.code.toLowerCase().startsWith(q);
}

export function initRegionPicker(selectId = 'trendsRegion') {
  const select = document.getElementById(selectId);
  if (!select || select.dataset.regionPicker === 'on') return;
  select.dataset.regionPicker = 'on';

  const groups = ordered();
  const initial = groups.all.some((r) => r.code === select.value) ? select.value : 'KZ';

  // В самом <select> держим полный список: значение читают другие модули, а
  // при отключённом JS-виджете остаётся рабочий нативный выбор.
  select.innerHTML = groups.all.map((r) => `<option value="${r.code}">${r.name}</option>`).join('');
  select.value = initial;
  select.hidden = true;
  select.removeAttribute('style');

  const wrap = document.createElement('div');
  wrap.className = 'region-picker';
  wrap.innerHTML = `
    <button type="button" class="region-picker-btn" aria-haspopup="listbox" aria-expanded="false">
      <span class="region-picker-code"></span><span class="region-picker-name"></span>${CHEVRON}
    </button>
    <div class="region-picker-panel" hidden>
      <input type="search" class="region-picker-search" placeholder="Страна или код: KZ" autocomplete="off" aria-label="Поиск региона">
      <div class="region-picker-list" role="listbox" tabindex="-1"></div>
    </div>`;
  select.insertAdjacentElement('afterend', wrap);

  const button = wrap.querySelector('.region-picker-btn');
  const codeLabel = wrap.querySelector('.region-picker-code');
  const nameLabel = wrap.querySelector('.region-picker-name');
  const panel = wrap.querySelector('.region-picker-panel');
  const search = wrap.querySelector('.region-picker-search');
  const list = wrap.querySelector('.region-picker-list');

  const syncButton = () => {
    const region = groups.all.find((r) => r.code === select.value) || groups.all[0];
    codeLabel.textContent = region.code;
    nameLabel.textContent = region.name;
  };

  const row = (region) =>
    `<button type="button" class="region-picker-item${region.code === select.value ? ' active' : ''}" role="option" aria-selected="${region.code === select.value}" data-code="${region.code}"><span>${region.code}</span>${region.name}</button>`;

  const renderList = (query = '') => {
    const q = query.trim();
    if (!q) {
      list.innerHTML =
        `<div class="region-picker-group">Часто выбирают</div>${groups.pinned.map(row).join('')}` +
        `<div class="region-picker-group">Все регионы</div>${groups.rest.map(row).join('')}`;
      return;
    }
    const found = groups.all.filter((r) => matches(r, q));
    list.innerHTML = found.length
      ? found.map(row).join('')
      : '<div class="region-picker-empty">Ничего не найдено</div>';
  };

  const close = () => {
    panel.hidden = true;
    button.setAttribute('aria-expanded', 'false');
  };

  const open = () => {
    renderList(search.value);
    panel.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    search.focus();
    search.select();
    const active = list.querySelector('.region-picker-item.active');
    if (active) active.scrollIntoView({ block: 'nearest' });
  };

  const choose = (code) => {
    if (!code) return;
    select.value = code;
    // Другие модули могут слушать change у селекта — событие обязательно.
    select.dispatchEvent(new Event('change', { bubbles: true }));
    syncButton();
    close();
  };

  button.addEventListener('click', () => (panel.hidden ? open() : close()));
  search.addEventListener('input', () => renderList(search.value));
  search.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      choose(list.querySelector('.region-picker-item')?.dataset.code);
    }
    if (event.key === 'Escape') close();
  });
  list.addEventListener('click', (event) => {
    const item = event.target.closest('.region-picker-item');
    if (item) choose(item.dataset.code);
  });
  document.addEventListener('click', (event) => {
    if (!panel.hidden && !wrap.contains(event.target)) close();
  });

  syncButton();
  renderList();
}
