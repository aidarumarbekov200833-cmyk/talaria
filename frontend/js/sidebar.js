/** Сворачивание бокового меню.
 *
 *  Кабинет часто открыт рядом с Telegram или таблицей, и 260 пикселей меню
 *  забирают заметный кусок экрана. Свёрнутый режим оставляет только иконки, а
 *  выбор запоминается: заново складывать меню при каждом входе — раздражает.
 */

const KEY = 'talaria_sidebar_collapsed';

function shell() {
  return document.querySelector('#appWrapper .app');
}

function paint(collapsed) {
  const app = shell();
  if (app) app.classList.toggle('sidebar-collapsed', collapsed);
  const button = document.querySelector('.sidebar-collapse');
  if (button) {
    button.setAttribute('aria-label', collapsed ? 'Развернуть меню' : 'Свернуть меню');
    button.title = collapsed ? 'Развернуть меню' : 'Свернуть меню';
    button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }
}

export function toggleSidebar() {
  let collapsed = false;
  try {
    collapsed = localStorage.getItem(KEY) === '1';
    localStorage.setItem(KEY, collapsed ? '0' : '1');
  } catch (_) {
    // Приватный режим без localStorage: свернём на один сеанс, и хватит.
  }
  paint(!collapsed);
}

export function initSidebar() {
  let collapsed = false;
  try {
    collapsed = localStorage.getItem(KEY) === '1';
  } catch (_) {
    collapsed = false;
  }
  // На узком экране меню и так превращается в нижнюю навигацию — сворачивать
  // там нечего, а восстановленный класс ломал бы разметку.
  paint(collapsed && window.innerWidth > 900);
}
