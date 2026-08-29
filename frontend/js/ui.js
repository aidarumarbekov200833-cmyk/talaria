/** Общие элементы интерфейса: тосты, модалки, дропдауны, AI-drawer. */
import { markNotificationsSeen, paintNotifBadge } from './notifications.js?v=tal32';

const TOAST_ICONS = {
  success: '<path d="M20 6L9 17l-5-5"/>',
  info: '<path d="M12 16v-4M12 8h.01"/><circle cx="12" cy="12" r="9"/>',
  warn: '<path d="M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  error: '<circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/>',
};

/** Тост.
 *
 *  Второй строкой можно дать пояснение: `toast('Вы вышли', 'info', 'Сессия
 *  закрыта')`. Движение — по references/transitions: появление 350ms,
 *  уход 250ms, класс `.on` ставится в следующем кадре, иначе браузер
 *  схлопнет начальное и конечное состояние в один стиль и анимации не будет.
 */
export function toast(msg, type = 'info', detail = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  const ic = document.createElement('div');
  ic.className = 'toast-ic';
  ic.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${TOAST_ICONS[type] || TOAST_ICONS.info}</svg>`;
  const body = document.createElement('div');
  body.className = 'toast-body';
  const title = document.createElement('b');
  title.textContent = msg; // textContent: сообщение может содержать данные пользователя
  body.append(title);
  if (detail) {
    const sub = document.createElement('small');
    sub.textContent = detail;
    body.append(sub);
  }
  el.append(ic, body);
  document.getElementById('toastWrap').appendChild(el);
  requestAnimationFrame(() => el.classList.add('on'));
  setTimeout(() => {
    el.classList.add('hide');
    setTimeout(() => el.remove(), 300);
  }, 3400);
}

/* ----------------------------- скелетоны -----------------------------
 *
 *  Раньше во время загрузки блоки были просто пустыми, и медленный запрос
 *  был неотличим от пустого списка — человек жал кнопку второй раз.
 *
 *  Скелетон — ровно заглушка разметки, никаких данных в нём нет, поэтому
 *  innerHTML безопасен: подставлять в него нечего.
 */

/** Показать скелетон в контейнере.
 *
 *  @param {string|Element} target  id или сам элемент
 *  @param {number} count           сколько заглушек
 *  @param {string} variant         'row' (таблица) | 'card' (сетка) | 'line' (текст)
 */
export function showSkeleton(target, count = 5, variant = 'row') {
  const el = typeof target === 'string' ? document.getElementById(target) : target;
  if (!el) return null;
  const box = document.createElement('div');
  box.className = 'sk-wrap';
  box.dataset.skeleton = '1';
  box.setAttribute('aria-hidden', 'true');
  let html = '';
  for (let i = 0; i < count; i += 1) html += `<div class="sk sk-${variant}"></div>`;
  box.innerHTML = html;
  el.replaceChildren(box);
  return el;
}

/** Убрать скелетон, если он ещё висит.
 *
 *  Проверка на data-skeleton важна: если два запроса гонкой успели отрисовать
 *  реальные данные, слепая очистка стерла бы их.
 */
export function hideSkeleton(target) {
  const el = typeof target === 'string' ? document.getElementById(target) : target;
  if (!el) return;
  el.querySelectorAll('[data-skeleton="1"]').forEach((s) => s.remove());
}

export function openModal(id) {
  const m = document.getElementById(id);
  if (!m) return;
  const first = m.querySelector('input:not([type="file"]),textarea');
  if (first) first.value = '';
  if (id === 'modalOffer') {
    const prev = document.getElementById('modalImgPreview');
    prev.classList.remove('has-img');
    prev.style.display = 'none';
    document.getElementById('offerImage').value = '';
  }
  m.classList.add('show');
}

function hideModal(m) {
  if (!m || !m.classList.contains('show')) return;
  m.classList.remove('show');
  m.dispatchEvent(new CustomEvent('mf:modal-closed'));
}

export function closeModal(id) {
  const m = document.getElementById(id);
  hideModal(m);
}

export function toggleDropdown(id) {
  document.querySelectorAll('.dropdown').forEach((d) => {
    if (d.id !== id) d.classList.remove('show');
  });
  const open = document.getElementById(id).classList.toggle('show');
  // Список открыли — значит, всё в нём человек увидел: точка перестаёт мигать.
  // Подсветка новых строк остаётся до следующей загрузки списка.
  if (open && id === 'ddNotif') markNotificationsSeen();
}

/** Отметить уведомления прочитанными, не открывая список. */
export function clearNotifs(e) {
  e?.stopPropagation?.();
  markNotificationsSeen();
  toast('Уведомления прочитаны', 'info');
}

export function openDrawer() {
  document.getElementById('aiDrawer').classList.add('show');
  document.getElementById('drawerOverlay').classList.add('show');
}

export function closeDrawer() {
  document.getElementById('aiDrawer').classList.remove('show');
  document.getElementById('drawerOverlay').classList.remove('show');
}

/** Слушатели, которые раньше висели прямо в inline-скрипте. */
export function initUi() {
  document.querySelectorAll('.modal-overlay').forEach((m) =>
    m.addEventListener('click', (e) => {
      if (e.target === m) hideModal(m);
    }),
  );

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    document.querySelectorAll('.modal-overlay.show').forEach(hideModal);
    closeDrawer();
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.rel'))
      document.querySelectorAll('.dropdown').forEach((d) => d.classList.remove('show'));
  });

  // Строки внутри дропдауна, которые куда-то ведут, закрывают его за собой:
  // клик внутри .rel сам по себе дропдаун не закрывает.
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-dd-close]'))
      document.querySelectorAll('.dropdown').forEach((d) => d.classList.remove('show'));
  });

  paintNotifBadge();
  document.getElementById('drawerOverlay').addEventListener('click', closeDrawer);
}

/* ------------------------- ПОДТВЕРЖДЕНИЕ -------------------------------- */

// Кто ждёт ответа модалки. Одна переменная, а не очередь: два подтверждения
// одновременно означали бы, что человек нажал две опасные кнопки за раз, —
// такого сценария нет, а очередь пришлось бы поддерживать вечно.
let pendingConfirm = null;

/**
 * Спросить подтверждение опасного действия.
 *
 * Возвращает промис с true/false. Заменяет системный confirm(): его нельзя
 * оформить, в нём не видно последствий, и браузер разрешает пользователю
 * отключить такие окна совсем — то есть подтверждение просто исчезает.
 */
export function confirmAction(
  text,
  { title = 'Подтвердите действие', okLabel = 'Подтвердить' } = {},
) {
  const modal = document.getElementById('modalConfirm');
  if (!modal) return Promise.resolve(window.confirm(text));

  const titleNode = document.getElementById('confirmTitle');
  const textNode = document.getElementById('confirmText');
  const okButton = document.getElementById('confirmOk');
  if (titleNode) titleNode.textContent = title;
  if (textNode) textNode.textContent = text;
  if (okButton) okButton.textContent = okLabel;

  // Предыдущий ждущий вызов закрываем отказом: иначе его промис никогда не
  // разрешится и вызвавший код зависнет навсегда.
  if (pendingConfirm) pendingConfirm(false);

  modal.classList.add('show');
  okButton?.focus();

  return new Promise((resolve) => {
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      pendingConfirm = null;
      modal.removeEventListener('mf:modal-closed', onClose);
      resolve(value);
    };
    // Закрытие крестиком, кнопкой «Отмена» или по фону — это отказ.
    const onClose = () => finish(false);
    modal.addEventListener('mf:modal-closed', onClose);
    pendingConfirm = finish;
  });
}

/** Кнопка «Подтвердить» в модалке. */
export function confirmAccept() {
  const finish = pendingConfirm;
  pendingConfirm = null;
  // Сначала разрешаем промис, потом закрываем. В обратном порядке закрытие
  // успело бы выстрелить событием «модалка закрыта», и подтверждение
  // превратилось бы в отказ.
  if (finish) finish(true);
  closeModal('modalConfirm');
}
