/** Переписка Telegram в кабинете.
 *
 *  Раздел устроен как обычный мессенджер: слева диалоги, справа лента, внизу
 *  поле ввода. На телефоне колонки не помещаются рядом, поэтому список и чат
 *  сменяют друг друга — так же ведёт себя сам Telegram.
 *
 *  Ничего не кешируется между сессиями: переписка живёт в Telegram, а не у нас,
 *  и хранить её копию в localStorage значило бы оставить личные сообщения на
 *  чужом компьютере после выхода из аккаунта.
 */
import { api, esc } from './api.js';
import { toast } from './ui.js?v=tal32';

// Как часто подтягивать новые сообщения открытого чата. Постоянное соединение
// здесь избыточно: раздел смотрят минутами, а не часами, и каждый лишний сокет
// к шлюзу — это ещё одно живое подключение к Telegram.
const POLL_MS = 6000;
// Список диалогов меняется медленнее ленты, обновлять его так же часто незачем.
const DIALOG_POLL_MS = 20000;

const state = {
  accountId: null,
  dialogs: [],
  cursor: null,
  peerId: '',
  peer: null,
  messages: [],
  oldestCursor: 0,
  query: '',
  loading: false,
  pollTimer: null,
  dialogTimer: null,
  // Слоты Telegram-аккаунтов владельца целиком, включая отключённые: по ним
  // строится понятное объяснение, ПОЧЕМУ переписки нет и что нажать.
  rows: [],
  // Аватары и картинки за сеанс: один и тот же собеседник встречается в списке
  // и в шапке, а перезапрашивать картинку на каждую перерисовку — расточительно.
  images: new Map(),
  initialized: false,
  // Что показывать в списке: группы/каналы и боты по умолчанию скрыты — в
  // рабочем кабинете ищут человека, а не подписки.
  showGroups: false,
  showBots: false,
  // На какое сообщение отвечаем. Telegram без ответов читается плохо: в длинной
  // переписке непонятно, к какой строке относится «да, давайте».
  replyTo: null,
};

// --- Мелкие помощники ------------------------------------------------------

function el(id) {
  return document.getElementById(id);
}

/**
 * Время как в мессенджере: сегодня — часы, на этой неделе — день недели,
 * дальше — дата. Абсолютная дата у каждого сообщения превратила бы ленту в
 * таблицу, а «5 минут назад» бесполезно, когда листаешь переписку за год.
 */
function shortTime(ms) {
  if (!ms) return '';
  const date = new Date(ms);
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) {
    return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }
  const days = (now - date) / 86400000;
  if (days < 7) return date.toLocaleDateString('ru-RU', { weekday: 'short' });
  return date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
}

function fullTime(ms) {
  if (!ms) return '';
  return new Date(ms).toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Разделитель дат между сообщениями разных дней. */
function dayLabel(ms) {
  const date = new Date(ms);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) return 'Сегодня';
  const yesterday = new Date(now.getTime() - 86400000);
  if (date.toDateString() === yesterday.toDateString()) return 'Вчера';
  return date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
}

/**
 * Цвет заглушки аватара выводим из идентификатора, а не из случайного числа:
 * иначе один и тот же человек менял бы цвет при каждой перерисовке списка.
 */
function avatarTone(peerId) {
  let sum = 0;
  for (const ch of String(peerId)) sum = (sum + ch.charCodeAt(0)) % 997;
  return (sum % 6) + 1;
}

function initials(title) {
  const words = String(title || '?')
    .replace(/^@/, '')
    .split(/\s+/)
    .filter(Boolean);
  const first = words[0] ? words[0][0] : '?';
  const second = words[1] ? words[1][0] : '';
  return (first + second).toUpperCase();
}

function avatarHtml(dialog, extraClass = '') {
  const tone = avatarTone(dialog.peer_id);
  const cached = state.images.get(`a:${dialog.peer_id}`);
  const inner = cached
    ? `<img src="${esc(cached)}" alt="" />`
    : `<span>${esc(initials(dialog.title))}</span>`;
  return `<div class="chat-ava ${extraClass}" data-tone="${tone}">${inner}</div>`;
}

const KIND_LABEL = {
  user: '',
  bot: 'бот',
  group: 'группа',
  channel: 'канал',
};

// Мелкие значки вместо текстовых плашек: в Telegram тип чата и «без звука»
// показаны иконкой, а строка списка остаётся в две строки, а не в три.
const SVG = {
  group:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M3.5 19a5.5 5.5 0 0 1 11 0"/><path d="M16 6.4a3 3 0 0 1 0 5.5M18.5 19a5 5 0 0 0-2.2-3.6"/></svg>',
  channel:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10.5v3a1 1 0 0 0 1 1h2l5 3.5V6L7 9.5H5a1 1 0 0 0-1 1Z"/><path d="M16.5 9a4.5 4.5 0 0 1 0 6"/></svg>',
  bot: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 4v4"/><circle cx="9.2" cy="13" r="1.1"/><circle cx="14.8" cy="13" r="1.1"/></svg>',
  mute: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 8.5A3.5 3.5 0 0 1 15.5 10v3l1.5 2.5H7.5"/><path d="M4 4l16 16"/></svg>',
  pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4h6l-1 6 3 3H7l3-3z"/><path d="M12 13v7"/></svg>',
  verified:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2.2 1.6 2.7-.2 1 2.5 2.3 1.4-.7 2.6.7 2.6-2.3 1.4-1 2.5-2.7-.2L12 21l-2.2-1.6-2.7.2-1-2.5L3.8 15l.7-2.6-.7-2.6 2.3-1.4 1-2.5 2.7.2z"/><path d="M9.5 12.2l1.8 1.8 3.4-3.6"/></svg>',
  sent: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12.5l4.5 4.5L19 6.5"/></svg>',
  reply:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 8H6l4-4"/><path d="M6 8h8a4 4 0 0 1 4 4v6"/></svg>',
  unmute:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 10a4 4 0 0 1 8 0v3l1.5 2.5h-11L8 13z"/><path d="M10.5 18.5a1.8 1.8 0 0 0 3 0"/></svg>',
  unpin:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4h6l-1 6 3 3H7l3-3z"/><path d="M12 13v7"/><path d="M4 4l16 16"/></svg>',
  read: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 12.5l4 4L15 8"/><path d="M11 15.5l2 2L21.5 9"/></svg>',
};

// --- Загрузка данных -------------------------------------------------------

function accountQuery() {
  return state.accountId ? `&account_id=${encodeURIComponent(state.accountId)}` : '';
}

async function loadAccounts() {
  let rows = [];
  try {
    rows = await api('GET', '/api/chat/accounts');
  } catch (_) {
    rows = [];
  }
  state.rows = rows;
  const connected = rows.filter((row) => row.connected);
  if (connected.length && !state.accountId) state.accountId = connected[0].id;

  // Скрытый select остаётся: на нём висит проверка вёрстки и он же нужен тем,
  // кто ходит по странице с клавиатуры через форму.
  const select = el('chatAccount');
  if (select) {
    select.innerHTML = connected
      .map(
        (row) => `<option value="${row.id}">${esc(row.label || row.username || row.id)}</option>`,
      )
      .join('');
    if (state.accountId) select.value = String(state.accountId);
  }
  renderAccountSwitcher();
  return connected;
}

/**
 * С какого аккаунта читаем переписку.
 *
 * Раньше здесь стоял системный `<select>`, который вообще прятался, если
 * подключён один аккаунт: человек не видел, ЧЕЙ Telegram он читает, а при двух
 * аккаунтах получал серый список без подписей и статусов. Теперь это кнопка с
 * аватаром и @username, а в меню — все слоты, включая отвалившиеся, с прямой
 * кнопкой «Подключить».
 */
function accountLabel(row) {
  return row.label || (row.username ? `@${row.username}` : `Аккаунт ${row.id}`);
}

function renderAccountSwitcher() {
  const box = el('chatAccountBox');
  if (!box) return;
  const rows = Array.isArray(state.rows) ? state.rows : [];
  const current = rows.find((row) => Number(row.id) === Number(state.accountId));
  if (!rows.length) {
    box.innerHTML =
      '<button type="button" class="chat-acc" data-act="goTo" data-args=\'["settings"]\'>' +
      '<span class="chat-acc-ava">+</span><span class="chat-acc-text"><b>Нет аккаунта</b>' +
      '<small>Подключить в настройках</small></span></button>';
    return;
  }
  const initial = current ? initials(accountLabel(current)).slice(0, 1) : '?';
  const menu = rows
    .map((row) => {
      const active = Number(row.id) === Number(state.accountId);
      const action = row.connected
        ? `data-act="switchChatAccount" data-args='[${Number(row.id)}]'`
        : `data-act="connectTgAccount" data-args='[${Number(row.id)}]'`;
      return `<button type="button" class="chat-acc-item${active ? ' active' : ''}" ${action}>
        <span class="chat-acc-ava">${esc(initials(accountLabel(row)).slice(0, 1))}</span>
        <span class="chat-acc-text"><b>${esc(accountLabel(row))}</b><small>${
          row.connected ? (active ? 'читаем этот' : 'подключён') : 'сессия завершена — подключить'
        }</small></span>
        <i class="chat-acc-state${row.connected ? ' is-live' : ''}"></i>
      </button>`;
    })
    .join('');
  box.innerHTML = `
    <button type="button" class="chat-acc" data-act="toggleChatAccounts" aria-haspopup="true">
      <span class="chat-acc-ava">${esc(initial)}</span>
      <span class="chat-acc-text"><b>${esc(current ? accountLabel(current) : 'Выберите аккаунт')}</b>
      <small>${rows.length > 1 ? `аккаунтов: ${rows.length}` : 'Telegram для переписки'}</small></span>
      <i class="chat-acc-chev" aria-hidden="true"></i>
    </button>
    <div class="chat-acc-menu" id="chatAccountMenu" hidden>${menu}</div>`;
}

export function toggleChatAccounts() {
  const menu = el('chatAccountMenu');
  if (!menu) return;
  menu.hidden = !menu.hidden;
}

export function switchChatAccount(id) {
  const next = Number(id) || null;
  const menu = el('chatAccountMenu');
  if (menu) menu.hidden = true;
  if (next === Number(state.accountId)) return;
  state.accountId = next;
  const select = el('chatAccount');
  if (select) select.value = String(next);
  // Аккаунт другой — и диалоги, и картинки другие. Оставить старый кеш значило
  // бы показать чужие аватары рядом с чужими же именами.
  state.images.clear();
  closeChatRoom();
  state.cursor = null;
  renderAccountSwitcher();
  loadDialogs({});
}

async function loadDialogs({ append = false } = {}) {
  if (state.loading) return;
  state.loading = true;
  try {
    const cursor = append && state.cursor ? state.cursor : null;
    const params = new URLSearchParams({ limit: '40' });
    if (cursor) {
      params.set('offset_date', String(cursor.offset_date || 0));
      params.set('offset_id', String(cursor.offset_id || 0));
      params.set('offset_peer', String(cursor.offset_peer || ''));
    }
    if (state.accountId) params.set('account_id', String(state.accountId));
    const body = await api('GET', `/api/chat/dialogs?${params.toString()}`);
    state.accountId = body.account_id || state.accountId;
    state.dialogs = append ? state.dialogs.concat(body.items || []) : body.items || [];
    state.cursor = body.next || null;
    renderDialogs();
    autoOpenFirstDialog();
    loadAvatars(body.items || []);
  } catch (error) {
    renderDialogsError(error);
  } finally {
    state.loading = false;
  }
}

/**
 * Общая очередь на картинки: аватары и вложения ходят в один и тот же шлюз.
 *
 * Было: последовательный await в цикле — каждая следующая картинка ждала
 * предыдущую. Двадцать вложений выстраивались в очередь, и последнее
 * появлялось через десятки секунд — со стороны это выглядит как «чат виснет».
 * Стало: несколько запросов одновременно. Полный Promise.all по всей ленте не
 * годится: каждое вложение — отдельный поход в Telegram, и сорок параллельных
 * запросов дают флуд-лимит вместо картинок. Поэтому ровно пул.
 */
const MEDIA_CONCURRENCY = 4;
const mediaQueue = [];
let mediaWorkers = 0;

function pumpMediaQueue() {
  while (mediaWorkers < MEDIA_CONCURRENCY && mediaQueue.length) {
    const job = mediaQueue.shift();
    mediaWorkers += 1;
    job().finally(() => {
      mediaWorkers -= 1;
      pumpMediaQueue();
    });
  }
}

function enqueueMedia(job) {
  mediaQueue.push(job);
  pumpMediaQueue();
}

/** Аватары — только для новых собеседников, через тот же пул. */
function loadAvatars(dialogs) {
  for (const dialog of dialogs) {
    if (!dialog.has_photo) continue;
    const key = `a:${dialog.peer_id}`;
    if (state.images.has(key)) continue;
    state.images.set(key, '');
    enqueueMedia(async () => {
      try {
        const body = await api(
          'GET',
          `/api/chat/avatar?peer_id=${encodeURIComponent(dialog.peer_id)}${accountQuery()}`,
        );
        if (body.image) {
          state.images.set(key, body.image);
          paintAvatar(dialog.peer_id, body.image);
        }
      } catch (_) {
        // Нет аватара — остаются инициалы. Ради картинки шуметь ошибкой незачем.
      }
    });
  }
}

/** Точечно заменить инициалы на картинку, не перерисовывая весь список. */
function paintAvatar(peerId, image) {
  document
    .querySelectorAll(`[data-peer="${CSS.escape(String(peerId))}"] .chat-ava`)
    .forEach((node) => {
      node.innerHTML = `<img src="${esc(image)}" alt="" />`;
    });
  const head = el('chatHeadAva');
  if (head && state.peerId === String(peerId)) {
    head.innerHTML = `<img src="${esc(image)}" alt="" />`;
  }
}

async function loadHistory({ older = false } = {}) {
  if (!state.peerId) return;
  const requestedPeer = state.peerId;
  const params = new URLSearchParams({ peer_id: state.peerId, limit: '50' });
  if (older && state.oldestCursor) params.set('offset_id', String(state.oldestCursor));
  if (state.accountId) params.set('account_id', String(state.accountId));
  const body = await api('GET', `/api/chat/history?${params.toString()}`);
  // Гонка диалогов: пока ждали сеть, человек открыл другой чат. Дорисовать
  // сюда чужую историю — значит показать переписку A в окне B и дать ответить
  // «по контексту A» не тому человеку. Опоздавший ответ просто выбрасываем
  // (тот же приём, что previewSeq в offers.js).
  if (state.peerId !== requestedPeer) return [];
  const items = body.items || [];
  if (older) {
    state.messages = items.concat(state.messages);
    state.oldestCursor = body.next_offset_id || 0;
  } else {
    state.messages = items;
    state.oldestCursor = body.next_offset_id || 0;
  }
  return items;
}

// --- Отрисовка -------------------------------------------------------------

function visibleDialogs() {
  const query = state.query.trim().toLowerCase();
  const byKind = state.dialogs.filter((dialog) => {
    if (dialog.kind === 'bot') return state.showBots;
    if (dialog.kind === 'group' || dialog.kind === 'channel') return state.showGroups;
    return true;
  });
  if (!query) return byKind;
  return byKind.filter(
    (dialog) =>
      dialog.title.toLowerCase().includes(query) ||
      String(dialog.username || '')
        .toLowerCase()
        .includes(query),
  );
}

function dialogSort(a, b) {
  // Закреплённые сверху — как в Telegram; внутри групп по времени сообщения.
  if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
  return (b.last_date || 0) - (a.last_date || 0);
}

function renderDialogs() {
  const box = el('chatDialogs');
  if (!box) return;
  const items = visibleDialogs().slice().sort(dialogSort);
  if (!items.length) {
    box.innerHTML = `<div class="chat-none">${
      state.query ? 'Ничего не нашлось' : 'Диалогов пока нет'
    }</div>`;
    return;
  }
  box.innerHTML =
    items
      .map((dialog) => {
        const kindIcon = SVG[dialog.kind] ? `<i class="cd-kind-ico">${SVG[dialog.kind]}</i>` : '';
        // «Вы:» в Telegram — галочка перед своим последним сообщением.
        const mine = dialog.last_out ? `<i class="cd-sent">${SVG.sent}</i>` : '';
        const unread = Number(dialog.unread || 0);
        return `
      <div class="chat-dialog${state.peerId === dialog.peer_id ? ' active' : ''}${
        unread ? ' is-unread' : ''
      }"
           data-peer="${esc(dialog.peer_id)}" data-act="openChatDialog"
           data-args='["${esc(dialog.peer_id)}"]'>
        ${avatarHtml(dialog)}
        <div class="cd-text">
          <div class="cd-top">
            <span class="cd-title">${kindIcon}<span class="cd-name">${esc(dialog.title)}</span>${
              dialog.verified ? `<i class="cd-badge-ico">${SVG.verified}</i>` : ''
            }${dialog.lead_id ? '<span class="cd-lead">лид</span>' : ''}</span>
            <span class="cd-meta">${dialog.muted ? `<i class="cd-mute">${SVG.mute}</i>` : ''}${
              dialog.pinned ? `<i class="cd-pin">${SVG.pin}</i>` : ''
            }<span class="cd-time">${esc(shortTime(dialog.last_date))}</span></span>
          </div>
          <div class="cd-bottom">
            <span class="cd-preview">${mine}<span class="cd-prev-text">${esc(
              dialog.last_message,
            )}</span></span>
            ${unread ? `<span class="cd-badge${dialog.muted ? ' is-muted' : ''}">${unread > 999 ? '999+' : unread}</span>` : ''}
          </div>
        </div>
      </div>`;
      })
      .join('') +
    (state.cursor
      ? '<button class="chat-more" data-act="loadMoreDialogs">Показать ещё</button>'
      : '');
}

/** Раздел просит «войти», хотя человек уже в кабинете.
 *
 *  Так выглядит любая проблема с ПОДКЛЮЧЁННЫМ АККАУНТОМ РАССЫЛКИ: своего входа
 *  у переписки нет, она читает Telegram через MTProto-сессию слота. Если слота
 *  нет или Telegram отозвал сессию (вышли с телефона, «завершить все сеансы»),
 *  в списке появлялась красная строка «Подключите аккаунт заново» — и её
 *  невозможно было отличить от требования войти на сайт. Теперь тут написано,
 *  какой именно аккаунт отвалился, и стоит кнопка, открывающая окно
 *  подключения — тот же путь, что в настройках.
 */
function connectStateHtml() {
  const rows = Array.isArray(state.rows) ? state.rows : [];
  const dead = rows.filter((row) => !row.connected);
  const target = dead[0];
  const name = target ? target.label || (target.username ? `@${target.username}` : '') : '';
  if (!rows.length) {
    return `
      <div class="chat-none chat-none-cta">
        <b>Нет Telegram-аккаунта для переписки</b>
        <span>Переписка читается через ваш Telegram-аккаунт. Добавьте его в настройках —
        это тот же аккаунт, с которого уходят сообщения кампаний.</span>
        <button class="btn" data-act="goTo" data-args='["settings"]'>Открыть настройки</button>
      </div>`;
  }
  return `
    <div class="chat-none chat-none-cta">
      <b>Нужно подключить Telegram${name ? `: ${esc(name)}` : ''}</b>
      <span>Сессия аккаунта завершена в Telegram (или он ещё не подключён). Это вход
      аккаунта рассылки, а не вход на сайт: телефон и код нужны один раз.</span>
      <button class="btn" data-act="connectTgAccount" data-args='[${Number(target.id)}]'>
        Подключить Telegram
      </button>
    </div>`;
}

function renderConnectState() {
  const box = el('chatDialogs');
  if (box) box.innerHTML = connectStateHtml();
  const empty = el('chatEmpty');
  const title = empty?.querySelector('.ce-title');
  const sub = empty?.querySelector('.ce-sub');
  if (title) title.textContent = 'Переписка недоступна';
  if (sub) sub.textContent = 'Подключите Telegram-аккаунт — слева кнопка';
}

/** Ошибка шлюза про мёртвую сессию читается как «войдите заново». */
function isConnectProblem(error) {
  const text = String((error && error.message) || '').toLowerCase();
  return (
    text.includes('не подключ') ||
    text.includes('подключите') ||
    text.includes('сессия') ||
    text.includes('войдите')
  );
}

function renderDialogsError(error) {
  const box = el('chatDialogs');
  if (!box) return;
  if (isConnectProblem(error)) {
    renderConnectState();
    return;
  }
  const message = error && error.message ? error.message : 'Не удалось загрузить диалоги';
  box.innerHTML = `<div class="chat-none chat-none-err">${esc(message)}</div>`;
}

/** Одно сообщение. Служебные отделены: это не переписка, а отметки Telegram. */
function messageHtml(message, previous, next) {
  const chunks = [];
  const prevDay = previous ? new Date(previous.date).toDateString() : '';
  const day = new Date(message.date).toDateString();
  if (day !== prevDay) {
    chunks.push(`<div class="chat-day">${esc(dayLabel(message.date))}</div>`);
  }
  if (message.service) {
    chunks.push(`<div class="chat-service">${esc(message.text || 'Служебное сообщение')}</div>`);
    return chunks.join('');
  }
  const media = message.media;
  const mediaHtml = media
    ? media.loadable
      ? `<div class="cm-media" data-media="${esc(message.id)}"><span class="cm-load">Загрузка…</span></div>`
      : `<div class="cm-file">${esc(media.name || mediaLabel(media.kind))}${
          media.size ? ` · ${fileSize(media.size)}` : ''
        }</div>`
    : '';
  // Имя автора показываем только в группах: в личном чате оно очевидно.
  const author =
    !message.out && state.peer && (state.peer.kind === 'group' || state.peer.kind === 'channel')
      ? `<div class="cm-author">${esc(message.sender_name)}</div>`
      : '';

  // Цитата: без неё ответ выглядит как обычная реплика не по теме.
  const parent = message.reply_to_id
    ? state.messages.find((item) => item.id === message.reply_to_id)
    : null;
  const quote = message.reply_to_id
    ? `<div class="cm-quote"><b>${esc(
        parent
          ? parent.out
            ? 'Вы'
            : parent.sender_name || state.peer?.title || 'Собеседник'
          : 'Сообщение',
      )}</b><span>${esc(parent ? quoteText(parent) : 'выше в переписке')}</span></div>`
    : '';

  // Группировка: подряд идущие сообщения одного автора в пределах пяти минут
  // Telegram склеивает в блок — хвостик и время только у последнего.
  const sameAuthor = (a, b) =>
    a && b && !a.service && !b.service && !!a.out === !!b.out && Math.abs(a.date - b.date) < 300000;
  const stacked = sameAuthor(previous, message) && day === prevDay;
  const last = !sameAuthor(message, next) || new Date(next?.date || 0).toDateString() !== day;

  chunks.push(`
    <div class="chat-msg${message.out ? ' out' : ''}${stacked ? ' is-stacked' : ''}${
      last ? ' is-last' : ''
    }" data-msg="${esc(message.id)}">
      <div class="cm-bubble">
        ${author}
        ${quote}
        ${mediaHtml}
        ${message.text ? `<div class="cm-text">${esc(message.text)}</div>` : ''}
        <div class="cm-meta" title="${esc(fullTime(message.date))}">
          ${message.edited ? '<span class="cm-edited">изменено</span>' : ''}
          <span>${esc(shortTime(message.date))}</span>
          ${message.out ? `<i class="cm-tick">${SVG.sent}</i>` : ''}
        </div>
      </div>
      <button type="button" class="cm-reply" title="Ответить" aria-label="Ответить"
              data-act="replyToChatMessage" data-args='[${Number(message.id)}]'>${SVG.reply}</button>
    </div>`);
  return chunks.join('');
}

/** Короткая версия текста для цитаты: пузырь ответа не должен быть больше самого ответа. */
function quoteText(message) {
  const raw = (message.text || (message.media ? mediaLabel(message.media.kind) : '') || '').replace(
    /\s+/g,
    ' ',
  );
  return raw.length > 90 ? `${raw.slice(0, 90)}…` : raw;
}

const MEDIA_LABEL = {
  photo: 'Фотография',
  video: 'Видео',
  gif: 'GIF',
  sticker: 'Стикер',
  voice: 'Голосовое сообщение',
  audio: 'Аудио',
  file: 'Файл',
  geo: 'Геопозиция',
  contact: 'Контакт',
  poll: 'Опрос',
  link: 'Ссылка',
};

function mediaLabel(kind) {
  return MEDIA_LABEL[kind] || 'Вложение';
}

function fileSize(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} Б`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} КБ`;
  return `${(value / 1024 / 1024).toFixed(1)} МБ`;
}

function renderFeed({ keepScroll = false } = {}) {
  const feed = el('chatFeed');
  if (!feed) return;
  const bottomBefore = feed.scrollHeight - feed.scrollTop;
  const parts = [];
  if (state.oldestCursor) {
    parts.push('<button class="chat-more" data-act="loadOlderMessages">Показать раньше</button>');
  }
  state.messages.forEach((message, index) => {
    parts.push(
      messageHtml(message, index ? state.messages[index - 1] : null, state.messages[index + 1]),
    );
  });
  feed.innerHTML = parts.join('');
  if (keepScroll) {
    // При подгрузке старых сообщений держим взгляд на том же месте: иначе
    // лента прыгает вверх и человек теряет, что читал.
    feed.scrollTop = feed.scrollHeight - bottomBefore;
  } else {
    feed.scrollTop = feed.scrollHeight;
  }
  loadMediaThumbs();
  paintToBottom();
}

/** Кнопка «вниз» появляется, только когда лента действительно отлистана. */
function paintToBottom() {
  const feed = el('chatFeed');
  const button = el('chatToBottom');
  if (!feed || !button) return;
  const away = feed.scrollHeight - feed.scrollTop - feed.clientHeight;
  button.classList.toggle('is-on', away > 240);
}

export function scrollChatToBottom() {
  const feed = el('chatFeed');
  if (!feed) return;
  feed.scrollTo({ top: feed.scrollHeight, behavior: 'smooth' });
}

/** Ответить на сообщение: показываем цитату над полем ввода. */
export function replyToChatMessage(id) {
  const message = state.messages.find((item) => item.id === Number(id));
  if (!message) return;
  state.replyTo = message;
  const bar = el('chatReplyBar');
  const text = el('chatReplyText');
  const who = el('chatReplyWho');
  if (who) {
    who.textContent = message.out ? 'Вы' : message.sender_name || state.peer?.title || 'Собеседник';
  }
  if (text) text.textContent = quoteText(message);
  if (bar) bar.hidden = false;
  el('chatInput')?.focus();
}

export function cancelChatReply() {
  state.replyTo = null;
  const bar = el('chatReplyBar');
  if (bar) bar.hidden = true;
}

/** Вставить картинку и сделать её открываемой на полный экран. */
function paintMedia(node, image, id) {
  node.innerHTML = `<img src="${esc(image)}" alt="Изображение из переписки" loading="lazy" decoding="async" />`;
  node.classList.add('is-ready');
  node.setAttribute('role', 'button');
  node.setAttribute('tabindex', '0');
  node.setAttribute('aria-label', 'Открыть изображение');
  // Через data-act, а не addEventListener: лента перерисовывается целиком через
  // innerHTML, и личные слушатели умирали бы на каждом опросе сервера.
  node.dataset.act = 'openChatImage';
  node.dataset.enterAct = 'openChatImage';
  node.dataset.args = JSON.stringify([String(id)]);
}

async function fetchMediaThumb(node, id, peerId) {
  const key = `m:${peerId}:${id}`;
  try {
    const body = await api(
      'GET',
      `/api/chat/media?peer_id=${encodeURIComponent(peerId)}&message_id=${encodeURIComponent(id)}${accountQuery()}`,
    );
    // Пока ждали сеть, человек мог открыть другой чат: дорисовывать туда
    // картинку из прошлой переписки нельзя — тот же приём, что в loadHistory.
    if (state.peerId !== peerId) return;
    if (body.image) {
      state.images.set(key, body.image);
      paintMedia(node, body.image, id);
    } else {
      node.innerHTML = '<span class="cm-load">Без превью</span>';
    }
  } catch (_) {
    if (state.peerId !== peerId) return;
    node.innerHTML = '<span class="cm-load">Не загрузилось</span>';
    // Ключ снимаем: иначе одна сетевая осечка навсегда оставит плашку.
    state.images.delete(key);
  }
}

function queueMediaNode(node) {
  const id = node.dataset.media;
  const peerId = state.peerId;
  const key = `m:${peerId}:${id}`;
  if (state.images.has(key)) return;
  state.images.set(key, '');
  enqueueMedia(() => fetchMediaThumb(node, id, peerId));
}

let mediaObserver = null;

/**
 * Грузим только то, что вот-вот покажется. Запас в 600px — картинка
 * успевает приехать до того, как до неё долистали, и лента не моргает
 * пустыми плашками. Без этого открытие чата на тысячу сообщений тянуло бы
 * сотни картинок, которых никто не увидит.
 */
function ensureMediaObserver() {
  if (mediaObserver || typeof IntersectionObserver !== 'function') return mediaObserver;
  mediaObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        mediaObserver.unobserve(entry.target);
        queueMediaNode(entry.target);
      }
    },
    { root: el('chatFeed'), rootMargin: '600px 0px' },
  );
  return mediaObserver;
}

function loadMediaThumbs() {
  const nodes = [...document.querySelectorAll('#chatFeed [data-media]')];
  const observer = ensureMediaObserver();
  for (const node of nodes) {
    const id = node.dataset.media;
    const key = `m:${state.peerId}:${id}`;
    const cached = state.images.get(key);
    if (cached) {
      paintMedia(node, cached, id);
      continue;
    }
    if (state.images.has(key)) continue;
    // Без IntersectionObserver (старый браузер) грузим сразу — пул всё равно
    // не даст завалить шлюз.
    if (observer) observer.observe(node);
    else queueMediaNode(node);
  }
}

/** Просмотр картинки целиком: в мессенджере вложение обязано открываться. */
export function openChatImage(id) {
  const image = state.images.get(`m:${state.peerId}:${id}`);
  if (!image) return;
  const box = el('chatLightbox');
  const img = el('chatLightboxImg');
  if (!box || !img) return;
  img.src = image;
  img.classList.remove('is-zoomed');
  if (!box.open) box.showModal();
}

export function closeChatImage() {
  const box = el('chatLightbox');
  if (box?.open) box.close();
}

/** Клик по открытой картинке — приблизить или вернуть в размер экрана. */
export function toggleChatImageZoom() {
  el('chatLightboxImg')?.classList.toggle('is-zoomed');
}

function renderHead() {
  const dialog = state.peer;
  if (!dialog) return;
  const title = el('chatHeadTitle');
  const sub = el('chatHeadSub');
  const ava = el('chatHeadAva');
  const lead = el('chatHeadLead');
  if (title) title.textContent = dialog.title;
  if (sub) {
    const bits = [];
    if (dialog.username) bits.push(`@${dialog.username}`);
    if (KIND_LABEL[dialog.kind]) bits.push(KIND_LABEL[dialog.kind]);
    if (dialog.muted) bits.push('без звука');
    sub.textContent = bits.join(' · ');
  }
  if (ava) {
    const image = state.images.get(`a:${dialog.peer_id}`);
    ava.innerHTML = image
      ? `<img src="${esc(image)}" alt="" />`
      : `<span>${esc(initials(dialog.title))}</span>`;
    ava.dataset.tone = String(avatarTone(dialog.peer_id));
  }
  if (lead) lead.hidden = !dialog.lead_id;
  const compose = el('chatCompose');
  const readonly = el('chatReadonly');
  // Поле ввода прячем заранее: набрать сообщение и узнать о запрете после
  // отправки — худший из возможных порядков.
  if (compose) compose.hidden = !dialog.can_send;
  if (readonly) readonly.hidden = dialog.can_send;
}

// --- Меню диалога ------------------------------------------------------------
//
// Как в Telegram: правый клик по строке списка (на телефоне — долгое нажатие,
// его браузер сам превращает в contextmenu) или кнопка «⋯» в шапке открытого
// диалога. Пункты — закрепить, без звука, прочитано. И pin, и mute уходят в
// настоящий Telegram через шлюз, поэтому телефон и кабинет показывают одно.

function dialogByPeer(peerId) {
  return state.dialogs.find((item) => item.peer_id === String(peerId));
}

function closeChatCtx() {
  document.getElementById('chatCtxMenu')?.remove();
}

function openChatCtx(peerId, x, y) {
  closeChatCtx();
  const dialog = dialogByPeer(peerId);
  if (!dialog) return;
  const menu = document.createElement('div');
  menu.className = 'chat-ctx';
  menu.id = 'chatCtxMenu';
  const items = [
    {
      act: 'toggleChatPin',
      icon: dialog.pinned ? SVG.unpin : SVG.pin,
      label: dialog.pinned ? 'Открепить' : 'Закрепить',
    },
    {
      act: 'toggleChatMute',
      icon: dialog.muted ? SVG.unmute : SVG.mute,
      label: dialog.muted ? 'Включить звук' : 'Без звука',
    },
  ];
  if (dialog.unread) items.push({ act: 'markChatDialogRead', icon: SVG.read, label: 'Прочитано' });
  // Обработчики вешаются напрямую, без data-act: меню живёт вне разметки
  // страницы, и контракту действий нечего проверять в динамическом HTML.
  items.forEach((item) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'chat-ctx-item';
    button.innerHTML = `<i>${item.icon}</i>${item.label}`;
    button.addEventListener('click', () => window[item.act]?.(dialog.peer_id));
    menu.appendChild(button);
  });
  // В кабинет, а не в body: цветовые токены кабинета объявлены на #appWrapper,
  // и меню на body красилось бы в цвета лендинга (тёмные на светлой теме).
  (document.getElementById('appWrapper') || document.body).appendChild(menu);
  // Меню не должно вылезать за экран: прижимаем к краю, если клик у границы.
  const rect = menu.getBoundingClientRect();
  const left = Math.min(x, window.innerWidth - rect.width - 8);
  const top = Math.min(y, window.innerHeight - rect.height - 8);
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.top = `${Math.max(8, top)}px`;
}

export function openChatHeadMenu() {
  if (!state.peerId) return;
  const existing = document.getElementById('chatCtxMenu');
  if (existing) {
    existing.remove();
    return;
  }
  const button = el('chatHeadMenu');
  const rect = button ? button.getBoundingClientRect() : { right: 0, bottom: 0 };
  // Меню шириной ~200px выравниваем по правому краю кнопки.
  openChatCtx(state.peerId, Math.max(8, rect.right - 200), rect.bottom + 6);
}

export async function toggleChatPin(peerId) {
  const dialog = dialogByPeer(peerId);
  if (!dialog) return;
  closeChatCtx();
  const next = !dialog.pinned;
  // Список меняется сразу, а не после ответа сервера: закрепление в Telegram
  // занимает секунду-две, и без мгновенной реакции кажется, что клик не понят.
  dialog.pinned = next;
  renderDialogs();
  try {
    await api('POST', '/api/chat/pin', {
      peer_id: dialog.peer_id,
      account_id: state.accountId,
      pinned: next,
    });
  } catch (error) {
    dialog.pinned = !next;
    renderDialogs();
    toast(error && error.message ? error.message : 'Не удалось закрепить', 'err');
  }
}

export async function toggleChatMute(peerId) {
  const dialog = dialogByPeer(peerId);
  if (!dialog) return;
  closeChatCtx();
  const next = !dialog.muted;
  dialog.muted = next;
  renderDialogs();
  if (state.peerId === dialog.peer_id) renderHead();
  try {
    await api('POST', '/api/chat/mute', {
      peer_id: dialog.peer_id,
      account_id: state.accountId,
      muted: next,
    });
    toast(next ? 'Диалог без звука: Telegram и Talaria молчат' : 'Уведомления включены', 'info');
  } catch (error) {
    dialog.muted = !next;
    renderDialogs();
    if (state.peerId === dialog.peer_id) renderHead();
    toast(error && error.message ? error.message : 'Не удалось изменить уведомления', 'err');
  }
}

export async function markChatDialogRead(peerId) {
  const dialog = dialogByPeer(peerId);
  closeChatCtx();
  if (!dialog || !dialog.unread) return;
  await markRead(dialog);
}

// --- Действия --------------------------------------------------------------

export async function openChatDialog(peerId) {
  const dialog = state.dialogs.find((item) => item.peer_id === String(peerId));
  if (!dialog) return;
  state.peerId = dialog.peer_id;
  state.peer = dialog;
  state.messages = [];
  state.oldestCursor = 0;
  el('chatEmpty')?.setAttribute('hidden', '');
  el('chatRoom')?.removeAttribute('hidden');
  el('chatWrap')?.classList.add('room-open');
  renderHead();
  const feed = el('chatFeed');
  if (feed) feed.innerHTML = '<div class="chat-none">Загрузка…</div>';
  try {
    await loadHistory({});
    // Пока грузили историю, человек мог кликнуть следующий диалог — рисовать
    // и отмечать прочитанным тогда нужно уже его, а не этот.
    if (state.peerId !== dialog.peer_id) return;
    renderFeed();
  } catch (error) {
    if (state.peerId !== dialog.peer_id) return;
    if (feed) {
      feed.innerHTML = `<div class="chat-none chat-none-err">${esc(
        error && error.message ? error.message : 'Не удалось открыть диалог',
      )}</div>`;
    }
    return;
  }
  renderDialogs();
  markRead(dialog);
  startPolling();
}

/** Отметка о прочтении уходит в настоящий Telegram, иначе счётчики разойдутся. */
async function markRead(dialog) {
  if (!dialog || !dialog.peer_id) return;
  if (!dialog.unread) return;
  try {
    await api('POST', '/api/chat/read', {
      peer_id: dialog.peer_id,
      account_id: state.accountId,
    });
    dialog.unread = 0;
    renderDialogs();
  } catch (_) {
    // Не удалось — счётчик просто останется. Ошибку показывать не за что.
  }
}

/**
 * Уйти к базе лидов, где лежит собеседник.
 *
 * Открывать карточку прямо в чате не стали: она большая, а нужна не для
 * переписки, а для работы с базой — там же фильтры, статусы и заметки.
 */
export function openChatLead() {
  if (!state.peer || !state.peer.lead_id) return;
  window.goTo?.('databases');
}

export function closeChatRoom() {
  state.peerId = '';
  state.peer = null;
  cancelChatReply();
  stopPolling();
  el('chatRoom')?.setAttribute('hidden', '');
  el('chatEmpty')?.removeAttribute('hidden');
  el('chatWrap')?.classList.remove('room-open');
  renderDialogs();
}

export async function sendChatMessage() {
  const input = el('chatInput');
  if (!input || !state.peerId) return;
  const text = input.value.trim();
  if (!text) return;
  const button = document.querySelector('[data-act="sendChatMessage"]');
  if (button) button.disabled = true;
  try {
    const replyTo = state.replyTo ? Number(state.replyTo.id) : 0;
    const body = await api('POST', '/api/chat/send', {
      peer_id: state.peerId,
      text,
      account_id: state.accountId,
      reply_to: replyTo,
    });
    input.value = '';
    input.style.height = '';
    // Дорисовываем сразу, не дожидаясь опроса: пауза между нажатием и
    // появлением своего же сообщения читается как «не отправилось».
    state.messages.push({
      id: body.message_id || Date.now(),
      out: true,
      date: body.date || Date.now(),
      text,
      media: null,
      service: false,
      reply_to_id: replyTo,
    });
    cancelChatReply();
    renderFeed();
    const dialog = state.dialogs.find((item) => item.peer_id === state.peerId);
    if (dialog) {
      dialog.last_message = text;
      dialog.last_out = true;
      dialog.last_date = body.date || Date.now();
      renderDialogs();
    }
  } catch (error) {
    toast(error && error.message ? error.message : 'Не отправилось', 'err');
  } finally {
    if (button) button.disabled = false;
    input.focus();
  }
}

export async function loadMoreDialogs() {
  await loadDialogs({ append: true });
}

export async function loadOlderMessages() {
  if (!state.oldestCursor) return;
  try {
    await loadHistory({ older: true });
    renderFeed({ keepScroll: true });
  } catch (error) {
    toast(error && error.message ? error.message : 'Не удалось загрузить историю', 'err');
  }
}

/**
 * Снять непрочитанные во всех загруженных диалогах.
 *
 * Владелец просил, чтобы непрочитанные не накапливались: их рост поднимает
 * оценку риска аккаунта и мешает прогреву. Идём по одному с паузой — сорок
 * одновременных read-запросов Telegram считает флудом.
 */
export async function markAllChatsRead() {
  const targets = state.dialogs.filter((dialog) => Number(dialog.unread || 0) > 0);
  const button = el('chatReadAllBtn');
  if (!targets.length) {
    toast('Непрочитанных нет', 'info');
    return;
  }
  if (button) {
    button.disabled = true;
    button.textContent = `Читаем 0/${targets.length}`;
  }
  let done = 0;
  for (const dialog of targets) {
    try {
      await api('POST', '/api/chat/read', {
        peer_id: dialog.peer_id,
        account_id: state.accountId,
      });
      dialog.unread = 0;
      done += 1;
      if (button) button.textContent = `Читаем ${done}/${targets.length}`;
      renderDialogs();
    } catch (_) {
      // Один недоступный диалог не должен останавливать остальные.
    }
    await new Promise((resolve) => window.setTimeout(resolve, 120));
  }
  if (button) {
    button.disabled = false;
    button.textContent = 'Прочитать всё';
  }
  toast(
    done ? `Отмечено прочитанными: ${done}` : 'Не удалось снять непрочитанные',
    done ? 'success' : 'warn',
  );
}

/** Открыть первый диалог сам, если ни один не выбран (на широком экране). */
function autoOpenFirstDialog() {
  if (state.peerId) return;
  if (window.matchMedia('(max-width: 900px)').matches) return;
  const first = visibleDialogs().slice().sort(dialogSort)[0];
  if (first) openChatDialog(first.peer_id);
}

export async function reloadChat() {
  state.cursor = null;
  await loadDialogs({});
  if (state.peerId) {
    await loadHistory({});
    renderFeed();
  }
}

// --- Опрос -----------------------------------------------------------------

function startPolling() {
  stopPolling();
  state.pollTimer = window.setInterval(async () => {
    if (document.hidden || !state.peerId) return;
    try {
      const before = state.messages.length ? state.messages[state.messages.length - 1].id : 0;
      const cursor = state.oldestCursor;
      await loadHistory({});
      // Курсор «вглубь истории» опрос менять не должен: иначе кнопка «показать
      // раньше» исчезала бы при каждом обновлении ленты.
      state.oldestCursor = cursor || state.oldestCursor;
      const after = state.messages.length ? state.messages[state.messages.length - 1].id : 0;
      if (after !== before) {
        renderFeed();
        const dialog = state.dialogs.find((item) => item.peer_id === state.peerId);
        if (dialog) markRead({ ...dialog, unread: 1 });
      }
    } catch (_) {
      // Обрыв связи не должен сыпать всплывашками каждые шесть секунд.
    }
  }, POLL_MS);
}

function stopPolling() {
  if (state.pollTimer) window.clearInterval(state.pollTimer);
  state.pollTimer = null;
}

// После выхода фоновые опросы чата обязаны замолчать: токена больше нет, и
// каждый тик превращался бы в тихий 401 к API до перезагрузки страницы.
window.addEventListener('talaria:logout', () => {
  stopPolling();
  if (state.dialogTimer) window.clearInterval(state.dialogTimer);
  state.dialogTimer = null;
});

function startDialogPolling() {
  if (state.dialogTimer) return;
  state.dialogTimer = window.setInterval(() => {
    if (document.hidden) return;
    if (!document.getElementById('messages')?.classList.contains('active')) return;
    state.cursor = null;
    loadDialogs({});
  }, DIALOG_POLL_MS);
}

// --- Инициализация ---------------------------------------------------------

export async function initChat() {
  if (state.initialized) {
    // Аккаунт могли подключить (или потерять) с прошлого открытия раздела.
    const ready = await loadAccounts();
    if (!ready.length) {
      renderConnectState();
      return;
    }
    await loadDialogs({});
    return;
  }
  state.initialized = true;

  const search = el('chatSearch');
  if (search) {
    search.addEventListener('input', () => {
      state.query = search.value;
      renderDialogs();
    });
  }

  const select = el('chatAccount');
  if (select) {
    select.addEventListener('change', () => switchChatAccount(select.value));
  }

  // Галочки «группы и каналы» / «боты» помнятся между входами.
  try {
    const saved = JSON.parse(localStorage.getItem('talaria.chat-filters.v1') || 'null');
    if (saved) {
      state.showGroups = !!saved.groups;
      state.showBots = !!saved.bots;
    }
  } catch (_) {
    /* нет сохранённых настроек — остаются значения по умолчанию */
  }
  const groupsBox = el('chatShowGroups');
  const botsBox = el('chatShowBots');
  if (groupsBox) groupsBox.checked = state.showGroups;
  if (botsBox) botsBox.checked = state.showBots;
  [
    [groupsBox, 'showGroups'],
    [botsBox, 'showBots'],
  ].forEach(([box, key]) => {
    if (!box) return;
    box.addEventListener('change', () => {
      state[key] = box.checked;
      try {
        localStorage.setItem(
          'talaria.chat-filters.v1',
          JSON.stringify({ groups: state.showGroups, bots: state.showBots }),
        );
      } catch (_) {
        /* приватный режим — настройка не запомнится */
      }
      renderDialogs();
    });
  });

  const feed = el('chatFeed');
  if (feed) feed.addEventListener('scroll', paintToBottom, { passive: true });

  // Меню аккаунтов закрывается кликом мимо — как любое выпадающее меню.
  document.addEventListener('click', (event) => {
    const box = el('chatAccountBox');
    const menu = el('chatAccountMenu');
    if (!box || !menu || menu.hidden) return;
    if (!box.contains(event.target)) menu.hidden = true;
  });

  // Правый клик (на телефоне — долгое нажатие) по строке диалога открывает
  // меню «закрепить / без звука / прочитано» — как в настольном Telegram.
  const dialogsBox = el('chatDialogs');
  if (dialogsBox) {
    dialogsBox.addEventListener('contextmenu', (event) => {
      const row = event.target.closest('.chat-dialog');
      if (!row) return;
      event.preventDefault();
      openChatCtx(row.dataset.peer, event.clientX, event.clientY);
    });
  }

  // Меню диалога закрывается кликом мимо, прокруткой и клавишей Esc: висящее
  // после прокрутки меню указывало бы на строку, которой уже нет на экране.
  document.addEventListener('click', (event) => {
    const menu = document.getElementById('chatCtxMenu');
    if (!menu) return;
    if (!menu.contains(event.target) && !event.target.closest('#chatHeadMenu')) closeChatCtx();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeChatCtx();
  });
  document.addEventListener('scroll', closeChatCtx, { capture: true, passive: true });

  const input = el('chatInput');
  if (input) {
    input.addEventListener('keydown', (event) => {
      // Enter отправляет, Shift+Enter переносит строку — как в Telegram.
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendChatMessage();
      }
    });
    input.addEventListener('input', () => {
      // Поле растёт под текст, но не бесконечно: иначе длинное письмо съест
      // всю ленту и человек перестанет видеть, кому он пишет.
      input.style.height = 'auto';
      input.style.height = `${Math.min(160, input.scrollHeight)}px`;
    });
  }

  const connected = await loadAccounts();
  // Без подключённого слота запрос диалогов вернёт 400 и красную строку.
  // Объясняем сразу и не ходим в шлюз впустую.
  if (!connected.length) {
    renderConnectState();
    return;
  }
  await loadDialogs({});
  startDialogPolling();
}

// Раздел грузится лениво: тянуть чужую переписку при каждом входе в кабинет
// незачем, а первый запрос к Telegram не самый быстрый.
window.addEventListener('mf:navigate', (event) => {
  if (event.detail && event.detail.target === 'messages') initChat();
});
