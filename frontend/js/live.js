/** Живая лента событий через WebSocket.
 *
 *  Канал /api/ws получает JWT через WebSocket-subprotocol и отдаёт события,
 *  которые уже записаны в БД
 *  (log_event) — сокет лишь доставляет их мгновенно, без опроса /api/events.
 *
 *  Разбор по типам:
 *   - event    — новая строка журнала: показываем в ленте активности и в
 *                колокольчике (та же разметка, что и в dashboard.js);
 *   - campaign — лёгкий апдейт прогресса рассылки: двигает процент на странице
 *                «Рассылка» без перезагрузки;
 *   - ping     — сердцебиение сервера, отвечаем автоматически;
 *   - hello    — сервер принял канал (гасим индикатор «подключение…»).
 *
 *  Переподключение с нарастающей паузой: короткий обрыв сети не рвёт ленту
 *  надолго, а при недоступном бэкенде не спамим консоль и не дёргаем сервер.
 *  Не настаиваемся при демо-входе: у демо-пользователя нет настоящего токена.
 */
import { getToken } from './api.js';
import { toast } from './ui.js?v=tal32';
import { loadNotifications, loadOfferStats } from './dashboard.js?v=tal33';
// Тот же URL модуля, что и в main.js: иначе query-string создаёт второй
// экземпляр campaigns.js со своим pendingTestReply, и реальный ответ не
// завершает таймер теста в первом экземпляре.
import { applyCampaignLiveUpdate, applyTestReplyUpdate } from './campaigns.js?v=zephyr3';

const ACTIVITY_COLOR = {
  success: 'var(--success)',
  warn: 'var(--warn)',
  error: 'var(--danger)',
  info: 'var(--accent)',
};

/** Относительное время, как в dashboard.js («5 мин назад»). */
function timeAgo(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  const sec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (sec < 60) return 'только что';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} мин назад`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} ч назад`;
  return `${Math.floor(hr / 24)} дн назад`;
}

let socket = null; // текущее соединение
let closed = false; // false — ждём переподключения (вкладка открыта)
let retryTimer = null;
let retryDelay = 1000; // стартовая пауза перед повторной попыткой

function scheduleReconnect() {
  if (closed) return;
  if (retryTimer) return;
  retryTimer = setTimeout(() => {
    retryTimer = null;
    retryDelay = Math.min(retryDelay * 2, 15000); // максимум 15 с
    connectLive();
  }, retryDelay);
}

function wsUrl() {
  return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/ws';
}

/** Вставить новое событие в ленту активности (свежие сверху). */
function pushActivity(ev) {
  const feed = document.getElementById('activityFeed');
  if (!feed) return;
  const empty = feed.querySelector('.activity-empty');
  if (empty) empty.remove();
  const color = ACTIVITY_COLOR[ev.level] || ACTIVITY_COLOR.info;
  const el = document.createElement('div');
  el.className = 'activity-item';
  el.innerHTML = `<div class="dot" style="background:${color}"></div><div><p></p><span>${timeAgo(ev.created_at)}</span></div>`;
  el.querySelector('p').textContent = ev.message; // текст события — не через innerHTML
  feed.prepend(el);
  // Ограничиваем длину живой ленты, чтобы она не росла бесконечно.
  while (feed.children.length > 30) feed.lastChild.remove();
}

/** Держать колокольчик и его список в актуальном состоянии.
 *  Индикатор непрочитанного считает notifications.js — руками его больше не
 *  включаем, иначе точка мигала бы и после того, как список уже прочитали. */
function refreshNotifications() {
  loadNotifications().catch(() => {});
}

function handleEvent(ev) {
  pushActivity(ev);
  refreshNotifications();
}

/** Обновить процент и статус кампании на странице «Рассылка». */
function handleCampaign(msg) {
  applyCampaignLiveUpdate(msg.campaign_id, msg.progress, msg.status);
}

/** Ответ лида: громкий тост + обновлённая лента и отклик по офферам. */
function handleReply(msg) {
  const who = msg.lead_name || msg.username || 'Лид';
  toast(`${who} ответил в Telegram`, 'success');
  loadNotifications().catch(() => {});
  loadOfferStats().catch(() => {});
  applyTestReplyUpdate(msg);
}

function handleMessage(raw) {
  let msg;
  try {
    msg = JSON.parse(raw.data);
  } catch (_) {
    return;
  }
  if (msg.type === 'hello' || msg.type === 'ping') return;
  if (msg.type === 'event') handleEvent(msg.event);
  else if (msg.type === 'campaign') handleCampaign(msg);
  else if (msg.type === 'reply') handleReply(msg);
}

export function connectLive() {
  const token = getToken();
  // Демо-вход: токена нет (и не будет) — канал не открываем.
  if (!token) return;

  closed = false;
  if (
    socket &&
    (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)
  )
    return;

  try {
    // Keep the JWT out of the URL and proxy access logs. Browsers expose no
    // Authorization-header API for WebSocket, so it is negotiated as a protocol.
    socket = new WebSocket(wsUrl(), ['zephyr.jwt', token]);
  } catch (_) {
    scheduleReconnect();
    return;
  }

  socket.onopen = () => {
    retryDelay = 1000;
  };
  socket.onmessage = handleMessage;
  socket.onclose = () => {
    socket = null;
    scheduleReconnect();
  };
  socket.onerror = () => {
    try {
      socket.close();
    } catch (_) {}
  };
}

/** Закрыть канал и перестать переподключаться (logout). */
export function disconnectLive() {
  closed = true;
  if (retryTimer) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
  if (socket) {
    try {
      socket.close();
    } catch (_) {}
    socket = null;
  }
}
