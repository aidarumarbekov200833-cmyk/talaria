/** Колокольчик: непрочитанные события и подсказки настройки.
 *
 *  Раньше индикатор `.ping` вообще ничего не значил: его показывали, если в
 *  /api/events есть хоть одна запись, и гасили руками в трёх разных местах.
 *  Теперь «прочитано» — это отметка в localStorage: время самого свежего
 *  увиденного события и список уже показанных подсказок настройки. Пока есть
 *  непрочитанное, точка на колокольчике мигает и показывает число.
 */

const SEEN_KEY = 'talaria_notif_seen';

function readSeen() {
  try {
    const raw = JSON.parse(localStorage.getItem(SEEN_KEY) || '{}');
    return {
      ts: typeof raw.ts === 'string' ? raw.ts : '',
      setup: Array.isArray(raw.setup) ? raw.setup.filter((id) => typeof id === 'string') : [],
    };
  } catch (_) {
    return { ts: '', setup: [] };
  }
}

function writeSeen(state) {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify(state));
  } catch (_) {
    /* приватный режим: просто не запоминаем отметку */
  }
}

function stamp(iso) {
  const value = new Date(iso || 0).getTime();
  return Number.isFinite(value) ? value : 0;
}

// Последние данные колокольчика: нужны, чтобы отметить прочитанным ровно то,
// что человек увидел, без повторного запроса к серверу.
let snapshot = { events: [], setup: [] };

/** Событие новее последней отметки «прочитано». */
export function isUnreadEvent(event) {
  const seen = readSeen();
  return !seen.ts || stamp(event?.created_at) > stamp(seen.ts);
}

/** Сколько непрочитанного: свежие события + непоказанные подсказки настройки. */
export function unreadCount() {
  const seen = readSeen();
  const events = snapshot.events.filter(
    (event) => !seen.ts || stamp(event.created_at) > stamp(seen.ts),
  );
  const setup = snapshot.setup.filter((item) => !seen.setup.includes(item.id));
  return events.length + setup.length;
}

function plural(count) {
  const tail = count % 100;
  if (tail > 10 && tail < 20) return 'новых';
  const last = count % 10;
  if (last === 1) return 'новое';
  if (last >= 2 && last <= 4) return 'новых';
  return 'новых';
}

/** Перерисовать точку на колокольчике и счётчик в шапке списка. */
export function paintNotifBadge() {
  const count = unreadCount();
  const ping = document.getElementById('notifPing');
  if (ping) {
    ping.classList.toggle('is-on', count > 0);
    ping.textContent = count > 9 ? '9+' : count > 0 ? String(count) : '';
  }
  const badge = document.getElementById('notifCount');
  if (badge) {
    badge.hidden = count === 0;
    badge.textContent = count ? `${count} ${plural(count)}` : '';
  }
}

/** Запомнить текущие данные колокольчика и обновить индикатор. */
export function setNotifSnapshot(events, setup) {
  snapshot = {
    events: Array.isArray(events) ? events : [],
    setup: Array.isArray(setup) ? setup : [],
  };
  paintNotifBadge();
}

/** Человек открыл список — всё, что в нём лежит, считается прочитанным. */
export function markNotificationsSeen() {
  // Счётчик в шапке списка не гасим сразу: человек должен видеть, сколько
  // записей было новыми, пока список открыт. Он исчезнет при следующей загрузке.
  const wasUnread = unreadCount();
  const seen = readSeen();
  const newest = snapshot.events.reduce(
    (top, event) => (stamp(event.created_at) > stamp(top) ? event.created_at : top),
    seen.ts,
  );
  const setup = [...new Set([...seen.setup, ...snapshot.setup.map((item) => item.id)])];
  writeSeen({ ts: newest || '', setup });
  paintNotifBadge();
  const badge = document.getElementById('notifCount');
  if (badge && wasUnread > 0) {
    badge.hidden = false;
    badge.textContent = `${wasUnread} ${plural(wasUnread)}`;
  }
}
