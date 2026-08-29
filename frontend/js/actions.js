/** Единый обработчик действий разметки вместо inline-атрибутов.
 *
 *  Раньше разметка вызывала функции через onclick="createOffer()". Это требует
 *  разрешения 'unsafe-inline' в script-src, а с ним CSP почти не защищает от XSS:
 *  любой вставленный в страницу <script> выполнится. Поэтому все вызовы
 *  переехали в data-атрибуты, а здесь один делегированный слушатель на document.
 *
 *  Формат:
 *    <button data-act="goTo" data-args='["settings"]">
 *    <input data-change-act="searchClamp" data-args='["__EL__", 1, 1000]'>
 *
 *  Строка "__EL__" в аргументах заменяется на сам элемент — это прямая замена
 *  бывшему this в inline-атрибутах.
 *
 *  Имена берутся из window — туда их публикует main.js. Никакого eval:
 *  вызывается только то, что действительно оказалось функцией.
 */

import { resetLog } from './logline.js?v=tal33';

const EL_MARKER = '__EL__';

/** Разобрать data-args. Нет атрибута — вызов без аргументов. */
function parseArgs(el) {
  const raw = el.getAttribute('data-args');
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    const list = Array.isArray(parsed) ? parsed : [parsed];
    return list.map((v) => (v === EL_MARKER ? el : v));
  } catch (e) {
    console.warn('Не разобрал data-args:', raw, e.message);
    return [];
  }
}

function runAction(el, name) {
  const fn = window[name];
  if (typeof fn !== 'function') {
    console.warn(`Действие «${name}» не найдено`);
    return;
  }
  fn(...parseArgs(el));
}

export function initActions() {
  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-act]');
    if (!el) return;
    // Ссылки-кнопки (<a href="#">) раньше не перезагружали страницу только
    // потому, что рядом стоял href="javascript:void(0)" или пустой якорь.
    if (el.tagName === 'A' && (el.getAttribute('href') || '').startsWith('#')) e.preventDefault();
    runAction(el, el.getAttribute('data-act'));
  });

  document.addEventListener('change', (e) => {
    const el = e.target.closest('[data-change-act]');
    if (!el) return;
    runAction(el, el.getAttribute('data-change-act'));
  });

  document.addEventListener('input', (e) => {
    const el = e.target.closest('[data-input-act]');
    if (!el) return;
    runAction(el, el.getAttribute('data-input-act'));
  });

  // Enter в поле ввода. Раньше это было onkeydown="if(event.key==='Enter')..." —
  // тот же inline-скрипт, из-за которого приходилось держать 'unsafe-inline' в CSP.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    const el = e.target.closest('[data-enter-act]');
    if (!el) return;
    e.preventDefault();
    runAction(el, el.getAttribute('data-enter-act'));
  });
}

/** Очистить терминал парсера. Раньше это был кусок JS прямо в атрибуте onclick. */
export function clearParseLogs() {
  resetLog('parseLogs', 'Пока пусто — здесь появится ход поиска: запросы, каналы, отказы.');
}
