/** Аккаунт-меню внизу бокового меню.
 *
 *  Зачем оно появилось. Тариф, квота API, тема, настройки и выход жили в пяти
 *  разных местах: тариф — плиткой в меню, квота — виджетом над ним, тема — в
 *  настройках, выход — внизу страницы настроек. Из-за этого боковое меню не
 *  вмещалось в экран и приходилось скроллить, а «где посмотреть, сколько
 *  осталось лимита» человек спрашивал отдельно. Теперь всё, что относится к
 *  самому аккаунту, лежит в одной выезжающей панели под карточкой профиля.
 *
 *  Данные панель берёт из тех же ручек, что и остальной кабинет
 *  (/api/subscription, /api/quota, /api/support/unread) — своих источников
 *  правды у неё нет, поэтому цифры в ней не могут разойтись с разделами.
 */
import { api, getToken } from './api.js';
import { goTo } from './nav.js?v=zephyr4';
import { setTheme, currentTheme } from './theme.js?v=zephyr3';
import { refreshQuota } from './quota.js';

// Что уже загружено. Панель открывают часто, а подписка и квота меняются редко:
// перезапрашиваем не чаще раза в 30 секунд, чтобы открытие было мгновенным.
let cache = { at: 0, sub: null };
const FRESH_MS = 30_000;

function panel() {
  return document.getElementById('accountMenu');
}

export function isAccountMenuOpen() {
  return Boolean(panel()?.classList.contains('show'));
}

function plural(n, one, few, many) {
  const value = Math.abs(Number(n) || 0) % 100;
  const last = value % 10;
  if (value > 10 && value < 20) return many;
  if (last === 1) return one;
  if (last >= 2 && last <= 4) return few;
  return many;
}

/** Строка «осталось N дней» или честное «тарифа нет». */
function planLine(sub) {
  if (!sub) return { name: 'Тариф не загрузился', sub: 'Откройте раздел «Тариф»', tone: '' };
  if (sub.blocked)
    return { name: 'Доступ приостановлен', sub: 'Напишите в поддержку', tone: 'err' };
  if (sub.is_trial) {
    const left = Number(sub.trial_days_left || 0);
    return {
      name: 'Пробный доступ',
      sub: `осталось ${left} ${plural(left, 'день', 'дня', 'дней')}`,
      tone: 'warn',
    };
  }
  if (sub.expired || sub.plan === 'none')
    return { name: 'Тариф не активен', sub: 'Рассылка выключена', tone: 'warn' };
  const until = sub.paid_until ? new Date(sub.paid_until) : null;
  const days = until ? Math.ceil((until - Date.now()) / 86_400_000) : 0;
  return {
    name: sub.plan_name || 'Тариф активен',
    sub: until
      ? `до ${until.toLocaleDateString('ru-RU')} · ${days} ${plural(days, 'день', 'дня', 'дней')}`
      : 'активен',
    tone: 'on',
  };
}

/** Одна полоса лимита. -1 в лимите означает «без ограничения». */
function limitRow(label, used, limit) {
  const unlimited = Number(limit) < 0;
  const total = Number(limit) || 0;
  const spent = Number(used) || 0;
  const pct = unlimited || total <= 0 ? 0 : Math.min(100, Math.round((spent / total) * 100));
  const right = unlimited
    ? 'без лимита'
    : `${spent.toLocaleString('ru-RU')} из ${total.toLocaleString('ru-RU')}`;
  const row = document.createElement('div');
  row.className = 'acc-limit';
  const top = document.createElement('div');
  top.className = 'acc-limit-top';
  const name = document.createElement('span');
  name.textContent = label;
  const value = document.createElement('b');
  value.textContent = right;
  top.append(name, value);
  const bar = document.createElement('div');
  bar.className = 'acc-limit-bar';
  const fill = document.createElement('i');
  fill.style.width = `${pct}%`;
  if (pct >= 90) fill.classList.add('is-full');
  bar.append(fill);
  row.append(top, bar);
  return row;
}

function paintIdentity() {
  const me = window.__me || {};
  const name = String(me.name || me.username || 'Пользователь').trim();
  const nameNode = document.getElementById('accMenuName');
  const userNode = document.getElementById('accMenuUsername');
  const avatar = document.getElementById('accMenuAvatar');
  if (nameNode) nameNode.textContent = name;
  if (userNode)
    userNode.textContent = me.username
      ? `@${String(me.username).replace(/^@/, '')}`
      : 'Telegram подключён';
  if (avatar) {
    // Заглушка — буква на акценте, а не тёмная буква на тёмном фоне: владелец
    // отдельно жаловался, что аккаунт без фотографии выглядит «фигово».
    avatar.textContent = (name[0] || 'T').toUpperCase();
    avatar.style.backgroundImage = '';
    avatar.classList.remove('has-photo');
    if (/^https:\/\//i.test(me.photo_url || '')) {
      avatar.style.backgroundImage = `url("${String(me.photo_url).replace(/["\\]/g, '')}")`;
      avatar.textContent = '';
      avatar.classList.add('has-photo');
    }
  }
}

function paintTheme() {
  const dark = currentTheme() === 'dark';
  document.querySelectorAll('#accMenuTheme [data-theme-choice]').forEach((button) => {
    const active = (button.dataset.themeChoice === 'dark') === dark;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

function paintPlan(sub) {
  const line = planLine(sub);
  const name = document.getElementById('accMenuPlan');
  const note = document.getElementById('accMenuPlanSub');
  if (name) name.textContent = line.name;
  if (note) note.textContent = line.sub;
  const badge = document.getElementById('accMenuPlanBadge');
  if (badge) badge.className = `acc-plan-dot ${line.tone}`;
}

function paintLimits(sub) {
  const box = document.getElementById('accMenuLimits');
  if (!box) return;
  box.textContent = '';
  const usage = sub?.usage;
  if (!usage) return;
  // Полосу квоты YouTube рисует quota.js по тем же id, что и раньше: два места,
  // считающие один и тот же процент, однажды разошлись бы в цифрах.
  // Строка одна (v43): «Лидов в базах» убрана — эта цифра и так живёт в разделе
  // «Базы лидов». Сообщения у тарифа Talaria без лимита; на пробном тарифе
  // отправка выключена вовсе, и рисовать «0 из 0» смысла нет.
  const messages = limitRow('Сообщения', usage.messages_used, usage.messages_limit);
  if (Number(usage.messages_limit) !== 0) box.append(messages);
}

/** Точка «есть непрочитанный ответ поддержки» на карточке аккаунта. */
export function paintSupportBadge(count) {
  const value = Number(count) || 0;
  const dot = document.getElementById('sessionSupportDot');
  if (dot) dot.hidden = value <= 0;
  const badge = document.getElementById('accMenuSupportCount');
  if (badge) {
    badge.hidden = value <= 0;
    badge.textContent = value > 9 ? '9+' : String(value);
  }
}

export async function refreshSupportBadge() {
  if (!getToken()) return;
  try {
    const data = await api('GET', '/api/support/unread');
    paintSupportBadge(data.unread);
  } catch (e) {
    // Точка — украшение: молчим, если ручка недоступна.
    console.warn('support unread:', e.message);
  }
}

async function loadState(force = false) {
  if (!getToken()) return;
  refreshQuota();
  const fresh = Date.now() - cache.at < FRESH_MS;
  if (fresh && !force && cache.sub) {
    paintPlan(cache.sub);
    paintLimits(cache.sub);
    return;
  }
  const sub = await api('GET', '/api/subscription').catch(() => null);
  cache = { at: Date.now(), sub: sub || cache.sub };
  paintPlan(cache.sub);
  paintLimits(cache.sub);
}

export function openAccountMenu() {
  const box = panel();
  if (!box) return;
  box.hidden = false;
  box.classList.add('show');
  box.setAttribute('aria-hidden', 'false');
  document.getElementById('sessionUserCard')?.setAttribute('aria-expanded', 'true');
  paintIdentity();
  paintTheme();
  loadState();
  refreshSupportBadge();
}

export function closeAccountMenu() {
  const box = panel();
  if (!box) return;
  box.classList.remove('show');
  box.setAttribute('aria-hidden', 'true');
  document.getElementById('sessionUserCard')?.setAttribute('aria-expanded', 'false');
  // Скрываем после анимации: hidden сразу оборвал бы выезд панели.
  setTimeout(() => {
    if (!box.classList.contains('show')) box.hidden = true;
  }, 220);
}

export function toggleAccountMenu() {
  if (isAccountMenuOpen()) closeAccountMenu();
  else openAccountMenu();
}

/** Пункт меню, который ведёт в раздел кабинета. */
export function accountGoTo(target) {
  closeAccountMenu();
  goTo(target);
}

export function setAccountTheme(value) {
  setTheme(value);
  paintTheme();
}

export function initAccountMenu() {
  const box = panel();
  if (!box) return;
  document.addEventListener('click', (event) => {
    if (!isAccountMenuOpen()) return;
    // Кнопка-переключатель обрабатывается своим обработчиком; если не исключить
    // её здесь, на телефоне панель закрывалась тем же нажатием, которым
    // открывалась: там кнопка живёт в нижней навигации, а не в карточке сессии.
    if (
      event.target.closest('#accountMenu') ||
      event.target.closest('[data-act="toggleAccountMenu"]')
    )
      return;
    closeAccountMenu();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && isAccountMenuOpen()) closeAccountMenu();
  });
  document.addEventListener('mf:themechange', paintTheme);
  // Тема и имя могут смениться до первого открытия панели.
  paintTheme();
}
