/** Навигация по разделам. */
import { get, set } from './shared/storage.js';

const ACTIVE_SECTION_KEY = 'zephyr_active_section';

export function goTo(target, options = {}) {
  const section = document.getElementById(target);
  if (!section?.classList.contains('section')) return;
  // Подсветка идёт по всем элементам с data-target, а не только по .nav-item:
  // «Настройки» и «Тариф» с v40 живут в меню аккаунта, и без этого ни один
  // пункт навигации не отмечался бы как открытый.
  document
    .querySelectorAll('[data-target]')
    .forEach((item) => item.classList.toggle('active', item.dataset.target === target));
  document
    .querySelectorAll('.section')
    .forEach((s) => s.classList.toggle('active', s.id === target));
  if (options.persist !== false) set(ACTIVE_SECTION_KEY, target);
  // Страница прокручивается окном, а не блоком #main, поэтому одного
  // main.scrollTop мало: при переходе из подвала одного раздела человек
  // оказывался в середине другого.
  const main = document.getElementById('main');
  if (main) main.scrollTop = 0;
  document.querySelector('.app-content, .content')?.scrollTo?.({ top: 0 });
  window.scrollTo({ top: 0, behavior: 'auto' });
  // Разделы могут лениво загружать данные после фактического открытия.
  window.dispatchEvent(new CustomEvent('mf:navigate', { detail: { target } }));
}

export function restoreLastSection() {
  const saved = get(ACTIVE_SECTION_KEY, 'dashboard');
  const target = saved === 'admin' && !window.__isAdmin ? 'dashboard' : saved;
  if (document.getElementById(target)?.classList.contains('section')) {
    goTo(target, { persist: false });
  } else {
    goTo('dashboard', { persist: false });
  }
}

export function initNav() {
  document
    .querySelectorAll('.nav-item')
    .forEach((item) => item.addEventListener('click', () => goTo(item.dataset.target)));
}
