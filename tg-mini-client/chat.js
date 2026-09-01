"use strict";

/**
 * Чтение и отправка обычных диалогов Telegram для кабинета.
 *
 * Модуль отделён от server.js по одной причине: остальные ручки шлюза работают
 * с лидами (публичный username, проверка контакта, рассылка), а здесь человек
 * видит свою личную переписку целиком. Правила у этих двух режимов разные, и
 * держать их в одном файле — верный способ однажды применить проверку не к
 * тому режиму.
 *
 * Здесь нет ни авторизации, ни владельца: и то и другое проверяет backend до
 * вызова. Модуль занят только переводом ответов MTProto в плоские структуры,
 * которые переживут JSON.
 */

// Сколько диалогов и сообщений отдавать за раз. Значения совпадают с тем, что
// подгружает сам Telegram при прокрутке: больше — дольше ждать первый экран,
// меньше — заметны рывки на длинной истории.
const DIALOG_PAGE = 40;
const HISTORY_PAGE = 50;
// Верхняя граница на запрос от клиента. Без неё один запрос с limit=100000
// заставил бы шлюз тянуть всю переписку и упереться в флуд-лимит Telegram.
const MAX_PAGE = 100;

// Кеш аватаров и картинок: и то и другое неизменно для своего ключа, а
// повторное скачивание — это лишний раунд к серверам Telegram на каждый
// прокрут списка.
const MEDIA_CACHE_LIMIT = 300;
// Лимит только по числу записей не защищал память: 300 картинок по ~4 МБ
// (data-URL оригинала до 3 МБ + base64 на треть тяжелее) — это уже больше
// гигабайта в долгоживущем процессе с mem_limit 768 МБ. Считаем и байты.
const MEDIA_CACHE_BYTES = Math.max(
  8 * 1024 * 1024,
  Number(process.env.MEDIA_CACHE_BYTES) || 96 * 1024 * 1024
);

/** Маленький LRU: Map в JS хранит порядок вставки, этого достаточно. */
function createCache(limit = MEDIA_CACHE_LIMIT, byteLimit = MEDIA_CACHE_BYTES) {
  const store = new Map();
  let bytes = 0;
  // Строки в V8 — UTF-16, но data-URL целиком из ASCII, поэтому length ≈
  // байты полезной нагрузки; точность до множителя здесь не нужна.
  const weight = (value) => (typeof value === "string" ? value.length : 0);
  const evictOldest = () => {
    const oldest = store.keys().next();
    if (oldest.done) return false;
    bytes -= weight(store.get(oldest.value));
    store.delete(oldest.value);
    return true;
  };
  return {
    get(key) {
      if (!store.has(key)) return null;
      const value = store.get(key);
      // Перечитанное считается свежим: двигаем в конец, чтобы не вытеснить.
      store.delete(key);
      store.set(key, value);
      return value;
    },
    set(key, value) {
      if (store.has(key)) {
        bytes -= weight(store.get(key));
        store.delete(key);
      }
      store.set(key, value);
      bytes += weight(value);
      while ((store.size > limit || bytes > byteLimit) && evictOldest());
    },
    clear() {
      store.clear();
      bytes = 0;
    },
    get size() {
      return store.size;
    },
    get bytes() {
      return bytes;
    },
  };
}

/**
 * Ограничить размер страницы.
 *
 * Отрицательное, дробное и нечисловое значение — не ошибка запроса, а повод
 * взять значение по умолчанию: клиент мог просто не передать параметр.
 */
function pageSize(requested, fallback) {
  const value = Number(requested);
  if (!Number.isFinite(value) || value <= 0) return fallback;
  return Math.min(MAX_PAGE, Math.floor(value));
}

/**
 * «Меченый» id, каким его показывает Telegram: канал -100…, группа -…,
 * человек — положительный. Строкой, а не числом: id давно вышли за пределы
 * точности double, и 7 000 000 000 000 превратилось бы в другое число.
 */
function markedId(entity) {
  if (!entity) return "";
  const id = String(entity.id);
  const cls = entity.className || "";
  if (cls === "Channel" || cls === "ChannelForbidden") {
    return id.startsWith("-100") ? id : `-100${id}`;
  }
  if (cls === "Chat" || cls === "ChatForbidden") {
    return id.startsWith("-") ? id : `-${id}`;
  }
  return id;
}

/** Что это за собеседник — от этого зависят и иконка, и право писать. */
function peerKind(entity) {
  if (!entity) return "unknown";
  const cls = entity.className || "";
  if (cls === "User") return entity.bot ? "bot" : "user";
  if (cls === "Chat" || cls === "ChatForbidden") return "group";
  if (cls === "Channel" || cls === "ChannelForbidden") {
    return entity.megagroup || entity.gigagroup ? "group" : "channel";
  }
  return "unknown";
}

function displayTitle(entity) {
  if (!entity) return "Без имени";
  if (entity.className === "User" && entity.self) return "Избранное";
  if (entity.title) return String(entity.title);
  const name = [entity.firstName, entity.lastName].filter(Boolean).join(" ").trim();
  if (name) return name;
  if (entity.username) return `@${entity.username}`;
  if (entity.deleted) return "Удалённый аккаунт";
  return "Без имени";
}

/**
 * Дата из MTProto — секунды, а в JSON удобнее миллисекунды: клиенту не нужно
 * помнить про множитель, а Date его и так ждёт.
 */
function toMillis(value) {
  const seconds = Number(value || 0);
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 0;
}

/**
 * Описание вложения без самого файла.
 *
 * Картинки и файлы не грузим вместе с историей: сотня сообщений превратилась бы
 * в десятки мегабайт ответа. Клиент получает признак вложения и подтягивает
 * содержимое отдельным запросом, только для видимых сообщений.
 */
function mediaInfo(message) {
  const media = message && message.media;
  if (!media) return null;
  const cls = media.className || "";
  if (cls === "MessageMediaPhoto") {
    return { kind: "photo", loadable: true, name: "", size: 0 };
  }
  if (cls === "MessageMediaDocument") {
    const doc = media.document || {};
    const attributes = doc.attributes || [];
    const named = attributes.find((a) => a && a.className === "DocumentAttributeFilename");
    const video = attributes.find((a) => a && a.className === "DocumentAttributeVideo");
    const audio = attributes.find((a) => a && a.className === "DocumentAttributeAudio");
    const sticker = attributes.find((a) => a && a.className === "DocumentAttributeSticker");
    const animated = attributes.some((a) => a && a.className === "DocumentAttributeAnimated");
    let kind = "file";
    if (sticker) kind = "sticker";
    else if (animated) kind = "gif";
    else if (video) kind = "video";
    else if (audio) kind = audio.voice ? "voice" : "audio";
    return {
      kind,
      // Превью умеем показывать только для того, что рисуется картинкой.
      loadable: kind === "video" || kind === "gif" || kind === "sticker",
      name: named ? String(named.fileName || "") : "",
      size: Number(doc.size || 0),
      duration: video || audio ? Number((video || audio).duration || 0) : 0,
    };
  }
  const simple = {
    MessageMediaGeo: "geo",
    MessageMediaGeoLive: "geo",
    MessageMediaVenue: "geo",
    MessageMediaContact: "contact",
    MessageMediaPoll: "poll",
    MessageMediaWebPage: "link",
    MessageMediaDice: "dice",
    MessageMediaGame: "game",
    MessageMediaInvoice: "invoice",
  };
  return { kind: simple[cls] || "other", loadable: false, name: "", size: 0 };
}

/** Человеческая подпись вложения для строки предпросмотра в списке диалогов. */
const MEDIA_LABEL = {
  photo: "Фотография",
  video: "Видео",
  gif: "GIF",
  sticker: "Стикер",
  voice: "Голосовое сообщение",
  audio: "Аудио",
  file: "Файл",
  geo: "Геопозиция",
  contact: "Контакт",
  poll: "Опрос",
  link: "Ссылка",
  dice: "Игральная кость",
  game: "Игра",
  invoice: "Счёт",
  other: "Вложение",
};

/** Короткая строка «что было в последнем сообщении» для списка слева. */
function previewText(message) {
  if (!message) return "";
  if (message.className === "MessageService") return "Служебное сообщение";
  const text = String(message.message || "").replace(/\s+/g, " ").trim();
  if (text) return text.slice(0, 120);
  const media = mediaInfo(message);
  if (media) return MEDIA_LABEL[media.kind] || MEDIA_LABEL.other;
  return "";
}

/**
 * Список диалогов в том же порядке, в каком их показывает Telegram.
 *
 * Порядок не пересчитываем: у Telegram он учитывает закреплённые чаты, черновики
 * и «важность» папки, и самодельная сортировка по дате разошлась бы с тем, что
 * человек видит в телефоне.
 */
async function listDialogs(client, { limit, offsetDate = 0, offsetId = 0, offsetPeer = null } = {}) {
  const take = pageSize(limit, DIALOG_PAGE);
  const dialogs = await client.getDialogs({
    limit: take,
    offsetDate: offsetDate || undefined,
    offsetId: offsetId || undefined,
    offsetPeer: offsetPeer || undefined,
  });
  const items = [];
  for (const dialog of dialogs) {
    const entity = dialog && dialog.entity;
    if (!entity) continue;
    const last = dialog.message || null;
    items.push({
      peer_id: markedId(entity),
      kind: peerKind(entity),
      title: displayTitle(entity),
      username: String(entity.username || ""),
      // Себе писать можно всегда; в канал без права постить — нельзя, и лучше
      // спрятать поле ввода, чем получить ошибку после набранного текста.
      can_send: canSend(entity),
      verified: Boolean(entity.verified),
      is_self: Boolean(entity.self),
      unread: Number(dialog.unreadCount || 0),
      muted: Boolean(dialog.dialog && dialog.dialog.notifySettings && dialog.dialog.notifySettings.muteUntil),
      pinned: Boolean(dialog.pinned),
      last_message: previewText(last),
      last_out: Boolean(last && last.out),
      last_date: toMillis(last && last.date),
      has_photo: Boolean(entity.photo),
    });
  }
  const tail = dialogs.length ? dialogs[dialogs.length - 1] : null;
  return {
    items,
    // Курсор для «подгрузить ещё». Telegram листает диалоги тройкой
    // (дата, id сообщения, собеседник), одной даты недостаточно.
    next: dialogs.length >= take && tail && tail.message
      ? {
          offset_date: Number(tail.message.date || 0),
          offset_id: Number(tail.message.id || 0),
          offset_peer: tail.entity ? markedId(tail.entity) : "",
        }
      : null,
  };
}

/**
 * Можно ли писать в этот диалог.
 *
 * Права проверяем до показа поля ввода: набрать письмо и только потом узнать,
 * что канал только для чтения, — худший из возможных порядков.
 */
function canSend(entity) {
  if (!entity) return false;
  const cls = entity.className || "";
  if (cls === "ChannelForbidden" || cls === "ChatForbidden") return false;
  if (cls === "User") return !entity.deleted;
  if (cls === "Chat") return !entity.left && !(entity.defaultBannedRights && entity.defaultBannedRights.sendMessages);
  if (cls === "Channel") {
    if (entity.megagroup || entity.gigagroup) {
      if (entity.defaultBannedRights && entity.defaultBannedRights.sendMessages) {
        return Boolean(entity.creator || (entity.adminRights && entity.adminRights.postMessages));
      }
      return !entity.left;
    }
    return Boolean(entity.creator || (entity.adminRights && entity.adminRights.postMessages));
  }
  return false;
}

/**
 * История диалога, от новых к старым.
 *
 * Отдаём в хронологическом порядке (старые сверху): именно так её рисует любой
 * мессенджер, и разворачивать список на клиенте — лишний повод ошибиться при
 * подгрузке предыдущей страницы.
 */
async function loadHistory(client, peer, { limit, offsetId = 0 } = {}) {
  const take = pageSize(limit, HISTORY_PAGE);
  const messages = await client.getMessages(peer, {
    limit: take,
    maxId: offsetId ? Number(offsetId) : undefined,
  });
  const items = [];
  for (const message of messages) {
    if (!message) continue;
    const sender = message.sender || null;
    items.push({
      id: Number(message.id || 0),
      out: Boolean(message.out),
      date: toMillis(message.date),
      edited: toMillis(message.editDate),
      text: String(message.message || ""),
      service: message.className === "MessageService",
      media: mediaInfo(message),
      reply_to_id: Number((message.replyTo && message.replyTo.replyToMsgId) || 0),
      // Имя автора нужно только в группах, но считать его здесь дешевле, чем
      // потом отдельным запросом за участниками.
      sender_id: sender ? markedId(sender) : "",
      sender_name: sender ? displayTitle(sender) : "",
      views: Number(message.views || 0),
    });
  }
  items.reverse();
  return {
    items,
    // Курсор ведёт к более старым сообщениям, поэтому берём id самого верхнего.
    next_offset_id: messages.length >= take && items.length ? items[0].id : 0,
  };
}

/**
 * Закрепить или открепить диалог — по-настоящему, в самом Telegram.
 *
 * Порядок диалогов кабинет берёт у Telegram как есть, поэтому и закрепление
 * должно жить там же: локальная пометка разошлась бы с телефоном при первом
 * открытии приложения.
 */
async function setDialogPin(client, peer, pinned) {
  const { Api } = require("telegram");
  await client.invoke(
    new Api.messages.ToggleDialogPin({
      peer: new Api.InputDialogPeer({ peer }),
      pinned: pinned ? true : undefined,
    })
  );
}

// «Навсегда» в терминах Telegram: максимальный int32. Точно это же значение
// ставит официальный клиент при «Отключить уведомления → Навсегда».
const MUTE_FOREVER = 2147483647;

/** Выключить или включить уведомления диалога в самом Telegram. */
async function setDialogMute(client, peer, muted) {
  const { Api } = require("telegram");
  await client.invoke(
    new Api.account.UpdateNotifySettings({
      peer: new Api.InputNotifyPeer({ peer }),
      settings: new Api.InputPeerNotifySettings({
        muteUntil: muted ? MUTE_FOREVER : 0,
      }),
    })
  );
}

module.exports = {
  listDialogs,
  loadHistory,
  setDialogPin,
  setDialogMute,
  canSend,
  DIALOG_PAGE,
  HISTORY_PAGE,
  MAX_PAGE,
  MEDIA_CACHE_LIMIT,
  createCache,
  pageSize,
  markedId,
  peerKind,
  displayTitle,
  toMillis,
  mediaInfo,
  previewText,
  MEDIA_LABEL,
};
