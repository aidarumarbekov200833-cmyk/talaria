"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const chat = require("../chat");

// --- Идентификаторы --------------------------------------------------------
//
// Ошибка в знаке или в префиксе -100 означает не «не тот чат», а «сообщение
// ушло не тому человеку», поэтому проверяем каждый вид отдельно.

test("markedId: человек — положительный id", () => {
  assert.equal(chat.markedId({ className: "User", id: 777000 }), "777000");
});

test("markedId: канал получает префикс -100", () => {
  assert.equal(chat.markedId({ className: "Channel", id: 1234567890 }), "-1001234567890");
});

test("markedId: уже помеченный канал не помечается второй раз", () => {
  assert.equal(chat.markedId({ className: "Channel", id: "-1001234567890" }), "-1001234567890");
});

test("markedId: старая группа получает минус", () => {
  assert.equal(chat.markedId({ className: "Chat", id: 42 }), "-42");
});

test("markedId: большой id не теряет точность", () => {
  // 7 000 000 000 000 больше Number.MAX_SAFE_INTEGER после умножения на 10^4,
  // и любая арифметика через number испортила бы последние цифры.
  const id = "7284591063847";
  assert.equal(chat.markedId({ className: "User", id }), id);
});

// --- Вид собеседника -------------------------------------------------------

test("peerKind: супергруппа — это группа, а не канал", () => {
  assert.equal(chat.peerKind({ className: "Channel", megagroup: true }), "group");
});

test("peerKind: обычный канал", () => {
  assert.equal(chat.peerKind({ className: "Channel" }), "channel");
});

test("peerKind: бот отличается от человека", () => {
  assert.equal(chat.peerKind({ className: "User", bot: true }), "bot");
  assert.equal(chat.peerKind({ className: "User" }), "user");
});

// --- Право писать ----------------------------------------------------------

test("canSend: в канал без прав писать нельзя", () => {
  assert.equal(chat.canSend({ className: "Channel" }), false);
});

test("canSend: администратор канала писать может", () => {
  assert.equal(
    chat.canSend({ className: "Channel", adminRights: { postMessages: true } }),
    true
  );
});

test("canSend: в супергруппе с запретом на отправку — только админ", () => {
  const banned = { className: "Channel", megagroup: true, defaultBannedRights: { sendMessages: true } };
  assert.equal(chat.canSend(banned), false);
  assert.equal(chat.canSend({ ...banned, creator: true }), true);
});

test("canSend: удалённому аккаунту писать нельзя", () => {
  assert.equal(chat.canSend({ className: "User", deleted: true }), false);
});

// --- Заголовки -------------------------------------------------------------

test("displayTitle: свой аккаунт называется «Избранное»", () => {
  assert.equal(chat.displayTitle({ className: "User", self: true, firstName: "Самат" }), "Избранное");
});

test("displayTitle: без имени и фамилии показываем username", () => {
  assert.equal(chat.displayTitle({ className: "User", username: "samat" }), "@samat");
});

test("displayTitle: удалённый аккаунт подписан явно", () => {
  assert.equal(chat.displayTitle({ className: "User", deleted: true }), "Удалённый аккаунт");
});

// --- Вложения --------------------------------------------------------------

test("mediaInfo: голосовое отличается от обычного аудио", () => {
  const voice = {
    media: {
      className: "MessageMediaDocument",
      document: { attributes: [{ className: "DocumentAttributeAudio", voice: true, duration: 7 }] },
    },
  };
  assert.equal(chat.mediaInfo(voice).kind, "voice");
});

test("mediaInfo: файл отдаёт имя и размер", () => {
  const doc = {
    media: {
      className: "MessageMediaDocument",
      document: {
        size: 2048,
        attributes: [{ className: "DocumentAttributeFilename", fileName: "смета.pdf" }],
      },
    },
  };
  const info = chat.mediaInfo(doc);
  assert.equal(info.kind, "file");
  assert.equal(info.name, "смета.pdf");
  assert.equal(info.size, 2048);
  // Превью у pdf нет: пытаться его скачать — потерянный запрос.
  assert.equal(info.loadable, false);
});

test("mediaInfo: у фотографии есть превью", () => {
  assert.equal(chat.mediaInfo({ media: { className: "MessageMediaPhoto" } }).loadable, true);
});

test("mediaInfo: сообщение без вложения даёт null", () => {
  assert.equal(chat.mediaInfo({ message: "привет" }), null);
});

// --- Предпросмотр ----------------------------------------------------------

test("previewText: перевод строки не ломает строку списка", () => {
  assert.equal(chat.previewText({ message: "первая\nвторая" }), "первая вторая");
});

test("previewText: длинный текст обрезается", () => {
  const long = "я".repeat(400);
  assert.equal(chat.previewText({ message: long }).length, 120);
});

test("previewText: у картинки без подписи — название вложения", () => {
  assert.equal(chat.previewText({ media: { className: "MessageMediaPhoto" } }), "Фотография");
});

test("previewText: подпись к картинке важнее слова «Фотография»", () => {
  const withCaption = { message: "смотри", media: { className: "MessageMediaPhoto" } };
  assert.equal(chat.previewText(withCaption), "смотри");
});

// --- Размер страницы -------------------------------------------------------

test("pageSize: мусор и пустое дают значение по умолчанию", () => {
  assert.equal(chat.pageSize(undefined, 40), 40);
  assert.equal(chat.pageSize("много", 40), 40);
  assert.equal(chat.pageSize(-5, 40), 40);
  assert.equal(chat.pageSize(0, 40), 40);
});

test("pageSize: запрос сверх предела срезается", () => {
  assert.equal(chat.pageSize(100000, 40), chat.MAX_PAGE);
});

// --- Кеш -------------------------------------------------------------------

test("кеш вытесняет самую старую запись", () => {
  const cache = chat.createCache(2);
  cache.set("a", "1");
  cache.set("b", "2");
  cache.set("c", "3");
  assert.equal(cache.get("a"), null);
  assert.equal(cache.get("c"), "3");
  assert.equal(cache.size, 2);
});

test("кеш считает перечитанную запись свежей", () => {
  const cache = chat.createCache(2);
  cache.set("a", "1");
  cache.set("b", "2");
  cache.get("a"); // «a» снова нужна — вытеснить должно «b»
  cache.set("c", "3");
  assert.equal(cache.get("a"), "1");
  assert.equal(cache.get("b"), null);
});

test("кеш отличает пустую строку от отсутствия записи", () => {
  // «Аватара нет» — полноценный ответ, и переспрашивать его нельзя.
  const cache = chat.createCache(2);
  cache.set("empty", "");
  assert.equal(cache.get("empty"), "");
  assert.equal(cache.get("missing"), null);
});

// --- Списки через поддельный клиент ---------------------------------------

function fakeDialog(overrides = {}) {
  return {
    entity: { className: "User", id: 5, firstName: "Иван" },
    message: { id: 10, date: 1700000000, message: "привет", out: false },
    unreadCount: 0,
    pinned: false,
    dialog: { notifySettings: {} },
    ...overrides,
  };
}

test("listDialogs: даты переводятся в миллисекунды", async () => {
  const client = { getDialogs: async () => [fakeDialog()] };
  const { items } = await chat.listDialogs(client, {});
  assert.equal(items[0].last_date, 1700000000000);
});

test("listDialogs: неполная страница означает конец списка", async () => {
  const client = { getDialogs: async () => [fakeDialog()] };
  const { next } = await chat.listDialogs(client, { limit: 40 });
  assert.equal(next, null);
});

test("listDialogs: полная страница отдаёт курсор из трёх частей", async () => {
  const client = { getDialogs: async () => [fakeDialog(), fakeDialog()] };
  const { next } = await chat.listDialogs(client, { limit: 2 });
  assert.deepEqual(next, { offset_date: 1700000000, offset_id: 10, offset_peer: "5" });
});

test("listDialogs: диалог без сущности пропускается, а не роняет список", async () => {
  const client = { getDialogs: async () => [{ entity: null }, fakeDialog()] };
  const { items } = await chat.listDialogs(client, {});
  assert.equal(items.length, 1);
});

test("listDialogs: приглушённый чат помечен", async () => {
  const muted = fakeDialog({ dialog: { notifySettings: { muteUntil: 2000000000 } } });
  const client = { getDialogs: async () => [muted] };
  const { items } = await chat.listDialogs(client, {});
  assert.equal(items[0].muted, true);
});

test("loadHistory: сообщения отдаются от старых к новым", async () => {
  // getMessages возвращает от новых к старым — на экране порядок обратный.
  const client = {
    getMessages: async () => [
      { id: 3, date: 300, message: "третье" },
      { id: 2, date: 200, message: "второе" },
      { id: 1, date: 100, message: "первое" },
    ],
  };
  const { items } = await chat.loadHistory(client, {}, { limit: 50 });
  assert.deepEqual(items.map((m) => m.id), [1, 2, 3]);
});

test("loadHistory: курсор ведёт к более старым сообщениям", async () => {
  const client = {
    getMessages: async () => [
      { id: 3, date: 300, message: "c" },
      { id: 2, date: 200, message: "b" },
    ],
  };
  const { next_offset_id: next } = await chat.loadHistory(client, {}, { limit: 2 });
  assert.equal(next, 2);
});

test("loadHistory: конец переписки — курсора нет", async () => {
  const client = { getMessages: async () => [{ id: 1, date: 100, message: "a" }] };
  const { next_offset_id: next } = await chat.loadHistory(client, {}, { limit: 50 });
  assert.equal(next, 0);
});

test("loadHistory: имя автора берётся из отправителя", async () => {
  const client = {
    getMessages: async () => [
      { id: 1, date: 100, message: "в группе", sender: { className: "User", id: 9, firstName: "Пётр" } },
    ],
  };
  const { items } = await chat.loadHistory(client, {}, {});
  assert.equal(items[0].sender_name, "Пётр");
  assert.equal(items[0].sender_id, "9");
});
