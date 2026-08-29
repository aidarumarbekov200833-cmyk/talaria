require("dotenv").config();

const fs = require("fs");
const { classifyIncoming } = require("./reply-filter");
const sessionCrypto = require("./session-crypto");
const chat = require("./chat");
const path = require("path");
const crypto = require("crypto");
const express = require("express");
const http = require("http");
const QRCode = require("qrcode");
const { WebSocketServer } = require("ws");
const { TelegramClient, Api } = require("telegram");
const { CustomFile } = require("telegram/client/uploads");
const { StringSession } = require("telegram/sessions");
const { NewMessage, Raw } = require("telegram/events");
const { spamBotState } = require("./spambot");

const API_ID = Number(process.env.API_ID);
const API_HASH = process.env.API_HASH;
const PORT = process.env.PORT || 3000;
const SESSIONS_DIR = path.join(__dirname, "sessions");

// Общий секрет с бэкендом. Роли две: (1) серверные ручки требуют его в
// X-Send-Secret, (2) им подписаны билеты — по ним форма подключения понимает,
// чью сессию открыть.
const SEND_SECRET = process.env.TG_CLIENT_SECRET || "";
// Куда сообщать, кто именно вошёл: бэкенд подпишет аккаунт к карточке в
// настройках, чтобы человек не вводил ник руками.
const SITE_API_URL = (process.env.SITE_API_URL || "http://backend:8000").replace(/\/+$/, "");

// Через сколько после закрытия последней вкладки гасить MTProto-соединение.
// Раньше оно не закрывалось никогда: каждое открытие раздела оставляло в
// Telegram ещё один живой сокет.
const IDLE_DISCONNECT_MS = 2 * 60 * 1000;
const INPUT_TIMEOUT_MS = 5 * 60 * 1000;
// GramJS распознаёт RESTART_AUTH внутри phoneCode и повторно вызывает sendCode.
// Значение живёт только в памяти процесса и никогда не отправляется в Telegram.
const RESEND_CODE = "__OUTLY_RESEND_CODE__";
const QR_LOGIN = "__OUTLY_QR_LOGIN__";
// Разовая миграция самой первой версии: сессия была одна на всех.
const LEGACY_OWNER_ID = String(process.env.TG_LEGACY_OWNER_ID || "").trim();

if (!API_ID || !API_HASH) {
  console.error(
    "Не заданы API_ID / API_HASH. Скопируй .env.example в .env и впиши значения с my.telegram.org"
  );
  process.exit(1);
}

if (!SEND_SECRET) {
  console.error("TG_CLIENT_SECRET обязателен: без него server API и билеты не защищены");
  process.exit(1);
}

const app = express();
app.disable("x-powered-by");
app.use((_req, res, next) => {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "SAMEORIGIN");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()");
  res.setHeader(
    "Content-Security-Policy",
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
      "connect-src 'self' ws: wss:; img-src 'self' data:; frame-ancestors 'self'; " +
      "base-uri 'self'; form-action 'self'; object-src 'none'"
  );
  next();
});
// Оффер может содержать до 800 КБ изображения в data-URI (~1.07 МБ base64).
// Сам /send дополнительно валидирует формат и реальный размер буфера.
app.use(express.json({ limit: "1200kb" }));
app.use(express.static(path.join(__dirname, "public"), { maxAge: "5m" }));

const server = http.createServer(app);
// В WebSocket ходят только номер, код и 2FA; ограничиваем тело формы 64 KiB.
const wss = new WebSocketServer({
  server,
  maxPayload: 64 * 1024,
});

// --- Сессии: по одной на аккаунт -------------------------------------------
//
// Раньше файл был один на пользователя сайта (user-<id>.txt), то есть один
// Telegram на человека. Аккаунтов бывает несколько, и в настройках сайта у
// каждого есть своя карточка, поэтому ключ теперь «владелец + аккаунт».
// В имени файла только цифры, так что подставить в путь лишнее нельзя.

function sessionFile(ownerId, accountId) {
  return path.join(SESSIONS_DIR, `user-${ownerId}-acc-${accountId}.txt`);
}

function legacyFile(ownerId) {
  return path.join(SESSIONS_DIR, `user-${ownerId}.txt`);
}

/** Все аккаунты владельца, у которых есть сохранённая сессия. */
function ownerAccounts(ownerId) {
  if (!fs.existsSync(SESSIONS_DIR)) return [];
  const prefix = `user-${ownerId}-acc-`;
  return fs
    .readdirSync(SESSIONS_DIR)
    .filter((name) => name.startsWith(prefix) && name.endsWith(".txt"))
    .map((name) => name.slice(prefix.length, -4))
    .filter((id) => /^\d+$/.test(id))
    .sort((a, b) => Number(a) - Number(b));
}

/**
 * Перенести старую «одну на человека» сессию на конкретный аккаунт.
 * Бэкенд помечает в билете флагом, что это первый (главный) аккаунт — только
 * ему достаётся старая сессия, иначе второй слот открыл бы тот же Telegram.
 */
function adoptLegacySession(ownerId, accountId) {
  const legacy = legacyFile(ownerId);
  if (!fs.existsSync(legacy)) return false;
  const target = sessionFile(ownerId, accountId);
  if (fs.existsSync(target)) return false;
  fs.mkdirSync(SESSIONS_DIR, { recursive: true });
  fs.renameSync(legacy, target);
  console.log(`Сессия user-${ownerId} закреплена за аккаунтом ${accountId}.`);
  return true;
}

// Ключ шифрования сессий. Считаем один раз при старте: переменные окружения
// в работающем процессе не меняются, а искать ключ на каждое чтение файла —
// лишняя работа на горячем пути.
const SESSION_KEY = sessionCrypto.resolveKey(process.env);
if (!SESSION_KEY) {
  console.warn(
    "Строки сессий Telegram сохраняются БЕЗ шифрования: не задан ни TG_SESSION_KEY, " +
      "ни TG_CLIENT_SECRET. Строка сессии — полный доступ к аккаунту, задайте ключ."
  );
}

function loadSession(ownerId, accountId) {
  const file = sessionFile(ownerId, accountId);
  if (!fs.existsSync(file)) return "";
  const raw = fs.readFileSync(file, "utf8").trim();
  try {
    return sessionCrypto.decrypt(raw, SESSION_KEY);
  } catch (err) {
    // Ключ сменили или файл повреждён. Возвращаем пустую строку — сервис
    // предложит подключить аккаунт заново; продолжать с мусором нельзя.
    console.error(
      `Не удалось прочитать сессию user-${ownerId}-acc-${accountId}: ${err.message}. ` +
        "Подключите аккаунт заново."
    );
    return "";
  }
}

function saveSession(ownerId, accountId, sessionString) {
  fs.mkdirSync(SESSIONS_DIR, { recursive: true });
  // 0600 остаётся и после шифрования: права на файл — вторая линия, а не
  // замена первой.
  fs.writeFileSync(
    sessionFile(ownerId, accountId),
    sessionCrypto.encrypt(sessionString, SESSION_KEY),
    { encoding: "utf8", mode: 0o600 }
  );
}

/**
 * Разово перешифровать файлы, оставшиеся с прошлой версии открытым текстом.
 *
 * Делается при старте, а не «когда-нибудь при следующем сохранении»: сессия
 * может не переписываться месяцами, и всё это время самый ценный секрет лежал
 * бы на диске открытым. Ошибка по одному файлу не останавливает остальные.
 */
function encryptExistingSessions() {
  if (!SESSION_KEY || !fs.existsSync(SESSIONS_DIR)) return 0;
  let converted = 0;
  for (const name of fs.readdirSync(SESSIONS_DIR)) {
    if (!name.endsWith(".txt")) continue;
    const file = path.join(SESSIONS_DIR, name);
    try {
      const raw = fs.readFileSync(file, "utf8").trim();
      if (!raw || sessionCrypto.isEncrypted(raw)) continue;
      fs.writeFileSync(file, sessionCrypto.encrypt(raw, SESSION_KEY), {
        encoding: "utf8",
        mode: 0o600,
      });
      converted += 1;
    } catch (err) {
      console.error(`Не удалось зашифровать ${name}: ${err.message}`);
    }
  }
  if (converted) console.log(`Зашифровано сохранённых сессий: ${converted}.`);
  return converted;
}

function dropSession(ownerId, accountId) {
  const file = sessionFile(ownerId, accountId);
  if (fs.existsSync(file)) fs.unlinkSync(file);
}

/** Самая старая миграция: sessions/session.txt был общим на всю установку. */
function migrateSharedSession() {
  const shared = path.join(SESSIONS_DIR, "session.txt");
  if (!fs.existsSync(shared)) return;
  if (!/^\d+$/.test(LEGACY_OWNER_ID)) {
    console.warn(
      "Найдена старая общая сессия sessions/session.txt. Укажите TG_LEGACY_OWNER_ID=<id пользователя>, " +
        "чтобы закрепить её за владельцем, либо удалите файл и войдите заново."
    );
    return;
  }
  const target = legacyFile(LEGACY_OWNER_ID);
  if (fs.existsSync(target)) return;
  fs.renameSync(shared, target);
  console.log(`Общая сессия перенесена пользователю ${LEGACY_OWNER_ID}.`);
}

migrateSharedSession();
encryptExistingSessions();

// --- Подписанные токены -------------------------------------------------
//
// Билет (выдаёт бэкенд):  <owner>.<account>.<flags>.<exp>.<sig>
// flags — битовая маска: 1 = главный аккаунт (ему можно отдать старую сессию).

function sign(payload) {
  return crypto.createHmac("sha256", SEND_SECRET).update(payload).digest("hex");
}

/**
 * Разобрать и проверить токен. Возвращает null на любом сомнении:
 * нет секрета, чужая подпись, истёк срок, мусор вместо цифр.
 */
function readToken(token) {
  if (!SEND_SECRET || typeof token !== "string") return null;
  const parts = token.split(".");
  // Старый трёхчастный билет <owner>.<exp>.<sig> — вкладка, открытая до
  // обновления. Приводим к главному аккаунту, чтобы не выкидывать человека.
  if (parts.length === 3) {
    const [ownerId, exp, sig] = parts;
    if (!/^\d+$/.test(ownerId) || !/^\d+$/.test(exp)) return null;
    if (!sigOk(`${ownerId}.${exp}`, sig)) return null;
    if (Number(exp) < Math.floor(Date.now() / 1000)) return null;
    return { ownerId, accountId: "0", primary: true };
  }
  if (parts.length !== 5) return null;
  const [ownerId, accountId, flags, exp, sig] = parts;
  if (![ownerId, accountId, flags, exp].every((v) => /^\d+$/.test(v))) return null;
  if (!sigOk(`${ownerId}.${accountId}.${flags}.${exp}`, sig)) return null;
  if (Number(exp) < Math.floor(Date.now() / 1000)) return null;
  return { ownerId, accountId, primary: (Number(flags) & 1) === 1 };
}

function sigOk(payload, sig) {
  const expected = sign(payload);
  if (typeof sig !== "string" || sig.length !== expected.length) return false;
  // timingSafeEqual — чтобы подпись нельзя было угадать по времени ответа.
  return crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected));
}

/** Проверка серверного секрета для ручек, которые зовёт бэкенд. */
function secretOk(req) {
  if (!SEND_SECRET) return false;
  const got = req.get("X-Send-Secret") || "";
  if (got.length !== SEND_SECRET.length) return false;
  return crypto.timingSafeEqual(Buffer.from(got), Buffer.from(SEND_SECRET));
}

/** Браузерный WebSocket принимаем только со страницы на том же host.
 * Подписанный билет остаётся основной авторизацией, проверка Origin закрывает
 * cross-site WebSocket hijacking, если ссылка с билетом случайно утекла. */
function websocketOriginOk(req) {
  const origin = String((req.headers && req.headers.origin) || "").trim();
  const expectedHost = String(
    (req.headers && (req.headers["x-forwarded-host"] || req.headers.host)) || ""
  ).trim();
  if (!origin || !expectedHost) return false;
  try {
    return new URL(origin).host.toLowerCase() === expectedHost.toLowerCase();
  } catch (_) {
    return false;
  }
}

// --- Ошибки Telegram по-русски -------------------------------------------
//
// GramJS отдаёт сырые коды (PHONE_CODE_INVALID, FLOOD_WAIT_42). Показывать их
// человеку бесполезно: по такому тексту непонятно, что делать дальше.

const TG_ERRORS = {
  PHONE_NUMBER_INVALID: "Номер неверный. Формат: +79001234567",
  PHONE_CODE_INVALID: "Код неверный. Проверьте цифры и попробуйте снова",
  PHONE_CODE_EXPIRED: "Код устарел. Запросите новый вход",
  PHONE_NUMBER_BANNED: "Номер заблокирован в Telegram",
  PASSWORD_HASH_INVALID: "Неверный пароль двухшаговой проверки",
  SESSION_PASSWORD_NEEDED: "Нужен пароль двухшаговой проверки",
  AUTH_KEY_UNREGISTERED: "Сессия больше не действует. Войдите заново",
  AUTH_KEY_DUPLICATED: "Сессия сброшена Telegram (вход с другого устройства). Войдите заново",
  USER_DEACTIVATED_BAN: "Аккаунт заблокирован Telegram",
  USER_PRIVACY_RESTRICTED: "Настройки приватности получателя запрещают писать ему",
  USER_IS_BLOCKED: "Получатель заблокировал аккаунт",
  PEER_FLOOD: "Telegram ограничил аккаунт за спам. Пауза на сутки и мягче темп",
  CHAT_WRITE_FORBIDDEN: "В этом чате нельзя писать",
  CHANNEL_PRIVATE: "Канал закрыт или аккаунт из него исключён",
  MESSAGE_ID_INVALID: "Сообщение не найдено или уже удалено",
  MESSAGE_NOT_MODIFIED: "Текст не изменился",
  MESSAGE_EDIT_TIME_EXPIRED: "Время на редактирование истекло",
  MEDIA_CAPTION_TOO_LONG: "Подпись к файлу слишком длинная",
  FILE_PARTS_INVALID: "Telegram не принял файл. Попробуйте другой",
};

function errText(err) {
  const raw = String((err && (err.errorMessage || err.message)) || err || "");
  const flood = raw.match(/FLOOD_WAIT_(\d+)/);
  if (flood) {
    const sec = Number(flood[1]);
    const human = sec >= 3600 ? `${Math.ceil(sec / 3600)} ч` : sec >= 60 ? `${Math.ceil(sec / 60)} мин` : `${sec} с`;
    return `Telegram просит подождать ${human} — слишком частые запросы`;
  }
  for (const [code, text] of Object.entries(TG_ERRORS)) {
    if (raw.includes(code)) return text;
  }
  return raw || "Неизвестная ошибка Telegram";
}

function tgErrorInfo(err) {
  const raw = String((err && (err.errorMessage || err.message)) || err || "").toUpperCase();
  const flood = raw.match(/\b(FLOOD(?:_PREMIUM)?_WAIT)_(\d+)\b/);
  if (flood) {
    return { error_code: flood[1], retry_after: Number(flood[2]) };
  }
  const known = Object.keys(TG_ERRORS).find((code) => raw.includes(code));
  return { error_code: known || "", retry_after: null };
}

function errorPayload(err) {
  return { ok: false, error: errText(err), ...tgErrorInfo(err) };
}

// --- Реестр клиентов -----------------------------------------------------
//
// На один аккаунт — ровно один TelegramClient, сколько бы вкладок его ни
// смотрело. Два клиента на одной строке сессии Telegram считает угрозой и
// гасит сессию целиком (AUTH_KEY_DUPLICATED) — поэтому счётчик ссылок, а не
// клиент на каждое соединение.

/** key = "<owner>:<account>" → { client, refs, idleTimer, listeners, bound } */
const registry = new Map();
/** Обещания старта: два одновременных захода не должны создать два клиента. */
const starting = new Map();

function entryKey(ownerId, accountId) {
  return `${ownerId}:${accountId}`;
}

function cancelIdle(entry) {
  if (entry.idleTimer) {
    clearTimeout(entry.idleTimer);
    entry.idleTimer = null;
  }
}

async function shutdownEntry(key) {
  const entry = registry.get(key);
  if (!entry) return;
  registry.delete(key);
  cancelIdle(entry);
  try {
    await entry.client.disconnect();
  } catch (_) {}
  try {
    // destroy закрывает сокеты и снимает таймеры GramJS; без него процесс
    // постепенно накапливал мёртвые подключения.
    await entry.client.destroy();
  } catch (_) {}
}

/** Никто не смотрит и ничего не делает — гасим соединение через паузу. */
function scheduleIdle(key) {
  const entry = registry.get(key);
  if (!entry) return;
  cancelIdle(entry);
  if (entry.refs > 0 || entry.listeners.size > 0) return;
  const holdMs = Math.max(0, Number(entry.holdUntil || 0) - Date.now());
  entry.idleTimer = setTimeout(() => {
    const still = registry.get(key);
    if (!still || still.refs > 0 || still.listeners.size > 0) return;
    if (Number(still.holdUntil || 0) > Date.now()) {
      scheduleIdle(key);
      return;
    }
    shutdownEntry(key).then(
      () => console.log(`Соединение ${key} закрыто за ненадобностью.`),
      () => {}
    );
  }, Math.max(IDLE_DISCONNECT_MS, holdMs));
  // unref: подвисший таймер не должен держать процесс при остановке.
  if (typeof entry.idleTimer.unref === "function") entry.idleTimer.unref();
}

/**
 * Получить (или создать) запись реестра с живым подключением.
 * requireSession: для серверных ручек — без файла сессии смысла нет.
 */
async function getEntry(ownerId, accountId, { requireSession = false } = {}) {
  const key = entryKey(ownerId, accountId);
  const existing = registry.get(key);
  if (existing) {
    cancelIdle(existing);
    if (!existing.client.connected) await existing.client.connect();
    return existing;
  }
  if (starting.has(key)) return starting.get(key);

  const stored = loadSession(ownerId, accountId);
  if (requireSession && !stored) {
    throw new Error("Аккаунт не подключён: подключите его в настройках");
  }

  const promise = (async () => {
    const client = new TelegramClient(new StringSession(stored), API_ID, API_HASH, {
      connectionRetries: 3,
      autoReconnect: true,
      // Без этого GramJS сыплет в stdout служебные логи на каждый пакет.
      baseLogger: undefined,
    });
    client.setLogLevel("error");
    await client.connect();
    const entry = {
      key,
      ownerId: String(ownerId),
      accountId: String(accountId),
      client,
      refs: 0,
      idleTimer: null,
      holdUntil: 0,
      // Telegram user id -> { expiresAt, username }. Заполняется только после
      // успешного /send; обычные личные диалоги владельца сюда не попадают.
      replyWatches: new Map(),
      bound: false,
      listeners: new Set(),
    };
    registry.set(key, entry);
    return entry;
  })();

  starting.set(key, promise);
  try {
    return await promise;
  } finally {
    starting.delete(key);
  }
}

/** Обёртка для разовых операций: держит ссылку, потом отдаёт на погашение. */
async function withClient(ownerId, accountId, fn, opts) {
  const entry = await getEntry(ownerId, accountId, opts);
  entry.refs += 1;
  cancelIdle(entry);
  try {
    return await fn(entry.client, entry);
  } finally {
    entry.refs = Math.max(0, entry.refs - 1);
    scheduleIdle(entry.key);
  }
}

// --- Публичные Telegram-сущности ------------------------------------------
//
// Ключ сущности — «меченый» id в том виде, в каком его знает сам Telegram:
// канал — -100…, группа — -…, человек — положительный. Строкой, а не BigInt:
// через JSON всё равно уйдёт текстом, а точности number на больших id не хватает.

function peerKey(entity) {
  if (!entity) return "";
  const id = String(entity.id);
  if (entity.className === "Channel" || entity.className === "ChannelForbidden") {
    return id.startsWith("-100") ? id : `-100${id}`;
  }
  if (entity.className === "Chat" || entity.className === "ChatForbidden") {
    return id.startsWith("-") ? id : `-${id}`;
  }
  return id;
}

function displayName(entity) {
  if (!entity) return "Без имени";
  if (entity.title) return entity.title;
  const name = [entity.firstName, entity.lastName].filter(Boolean).join(" ").trim();
  if (name) return name;
  if (entity.username) return `@${entity.username}`;
  return "Без имени";
}

function pruneReplyWatches(entry) {
  const now = Date.now();
  for (const [peerId, watch] of entry.replyWatches.entries()) {
    if (!watch || Number(watch.expiresAt || 0) <= now) entry.replyWatches.delete(peerId);
  }
}

function watchReplyPeer(entry, peer, username, seconds) {
  const peerId = String((peer && peer.id) || "");
  const ttl = Math.max(0, Math.min(3600, Number(seconds) || 0));
  if (!peerId || !ttl) return;
  pruneReplyWatches(entry);
  const expiresAt = Date.now() + ttl * 1000;
  entry.replyWatches.set(peerId, {
    expiresAt,
    username: String((peer && peer.username) || username || "").toLowerCase(),
  });
  entry.holdUntil = Math.max(Number(entry.holdUntil || 0), expiresAt);
}

function watchedReply(entry, peerId) {
  pruneReplyWatches(entry);
  const watch = entry.replyWatches.get(String(peerId || ""));
  if (!watch || Number(watch.expiresAt || 0) <= Date.now()) return null;
  return watch;
}

/** Найти публичную сущность по username для серверной операции. */
async function resolvePeer(entry, key) {
  const id = String(key == null ? "" : key).trim();
  if (!id) throw new Error("Не указан username");
  return entry.client.getEntity(id.replace(/^@/, ""));
}

// --- Серверные ручки для бэкенда ------------------------------------------

/**
 * Какой слот брать для серверного вызова. Бэкенд может не передать account_id
 * (так было до многоаккаунтности) — тогда берём первую живую сессию владельца.
 */
function pickAccountId(ownerId, requested) {
  const want = String(requested == null ? "" : requested).trim();
  if (/^\d+$/.test(want)) {
    if (loadSession(ownerId, want)) return want;
    // Старую единственную сессию можно привязать только если других слотов нет.
    if (!ownerAccounts(ownerId).length && adoptLegacySession(ownerId, want)) return want;
    throw new Error("Запрошенный Telegram-аккаунт не подключён");
  }
  const existing = ownerAccounts(ownerId);
  if (existing.length) return existing[0];
  // Сессия с прошлой версии без явного номера получает служебный слот 0.
  if (adoptLegacySession(ownerId, "0")) return "0";
  throw new Error("Аккаунт не подключён: подключите его в настройках");
}

function publicUsername(value) {
  const username = String(value || "").trim().replace(/^@/, "");
  return /^[A-Za-z0-9_]{4,32}$/.test(username) ? username : "";
}

/** Общая часть серверных ручек: секрет, owner_id, выбор слота. */
function serverContext(req, res) {
  if (!secretOk(req)) {
    res.status(403).json({ ok: false, error: "Неверный секрет" });
    return null;
  }
  const ownerId = String((req.body && req.body.owner_id) || "").trim();
  if (!/^\d+$/.test(ownerId)) {
    res.status(400).json({ ok: false, error: "Не указан owner_id" });
    return null;
  }
  try {
    return { ownerId, accountId: pickAccountId(ownerId, req.body && req.body.account_id) };
  } catch (err) {
    res.status(409).json(errorPayload(err));
    return null;
  }
}

/** Контекст для управления конкретной сессией без автоматического выбора слота. */
function exactServerContext(req, res) {
  if (!secretOk(req)) {
    res.status(403).json({ ok: false, error: "Неверный секрет" });
    return null;
  }
  const ownerId = String((req.body && req.body.owner_id) || "").trim();
  const accountId = String((req.body && req.body.account_id) || "").trim();
  if (!/^\d+$/.test(ownerId) || !/^\d+$/.test(accountId)) {
    res.status(400).json({ ok: false, error: "Нужны owner_id и account_id" });
    return null;
  }
  return { ownerId, accountId };
}

app.get("/health", (_req, res) => {
  res.json({ ok: true, sessions: registry.size });
});

app.post("/session/status", async (req, res) => {
  const ctx = exactServerContext(req, res);
  if (!ctx) return;
  const key = entryKey(ctx.ownerId, ctx.accountId);
  if (!loadSession(ctx.ownerId, ctx.accountId)) {
    return res.json({
      ok: true,
      authorized: false,
      error_code: "AUTH_KEY_UNREGISTERED",
      reason: "Сессия Telegram не найдена. Подключите аккаунт заново.",
    });
  }
  try {
    const authorized = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client) => Boolean(await client.isUserAuthorized()),
      { requireSession: true }
    );
    if (!authorized) {
      dropSession(ctx.ownerId, ctx.accountId);
      await shutdownEntry(key);
      return res.json({
        ok: true,
        authorized: false,
        error_code: "AUTH_KEY_UNREGISTERED",
        reason: "Сессия завершена в Telegram. Подключите аккаунт заново.",
      });
    }
    return res.json({ ok: true, authorized: true });
  } catch (err) {
    const info = tgErrorInfo(err);
    if (["AUTH_KEY_UNREGISTERED", "AUTH_KEY_DUPLICATED"].includes(info.error_code)) {
      dropSession(ctx.ownerId, ctx.accountId);
      await shutdownEntry(key);
      return res.json({ ok: true, authorized: false, reason: errText(err), ...info });
    }
    console.error("session-status:", err && err.message);
    return res.status(502).json(errorPayload(err));
  }
});

app.post("/session/drop", async (req, res) => {
  const ctx = exactServerContext(req, res);
  if (!ctx) return;
  dropSession(ctx.ownerId, ctx.accountId);
  await shutdownEntry(entryKey(ctx.ownerId, ctx.accountId));
  res.json({ ok: true });
});

app.post("/account/safety", async (req, res) => {
  const ctx = exactServerContext(req, res);
  if (!ctx) return;
  try {
    const result = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client) => {
        const dialogs = await client.getDialogs({ limit: 100 });
        let unreadCount = 0;
        let unreadDialogs = 0;
        for (const dialog of dialogs || []) {
          const count = Math.max(0, Number(dialog.unreadCount || 0));
          if (count > 0 || dialog.unreadMark) unreadDialogs += 1;
          unreadCount += count;
        }
        return { unread_count: unreadCount, unread_dialogs: unreadDialogs };
      },
      { requireSession: true }
    );
    res.json({ ok: true, ...result });
  } catch (err) {
    console.error("account-safety:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

app.post("/send", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const username = publicUsername(req.body && req.body.username);
  const text = String((req.body && req.body.text) || "");
  const image = String((req.body && req.body.image) || "");
  const watchSeconds = Math.max(0, Math.min(3600, Number((req.body && req.body.watch_seconds) || 0)));
  if (!username || (!text.trim() && !image)) {
    return res.status(400).json({ ok: false, error: "Нужны username и сообщение" });
  }
  if (text.length > 4096) {
    return res.status(400).json({ ok: false, error: "Сообщение длиннее 4096 символов" });
  }
  try {
    const result = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client, entry) => {
        const peer = await resolvePeer(entry, username);
        if (
          peer.className !== "User" || peer.bot || peer.deleted || peer.scam || peer.fake
        ) {
          throw new Error("Получатель не является доступным личным аккаунтом");
        }
        let sent;
        if (image) {
          const match = /^data:image\/(png|jpeg|webp|gif);base64,([A-Za-z0-9+/=]+)$/.exec(image);
          if (!match) throw new Error("Некорректный формат изображения оффера");
          const buffer = Buffer.from(match[2], "base64");
          if (!buffer.length || buffer.length > 800000) {
            throw new Error("Изображение оффера должно быть не больше 800 КБ");
          }
          const extension = match[1] === "jpeg" ? "jpg" : match[1];
          const file = new CustomFile(`zephyr-offer.${extension}`, buffer.length, "", buffer);
          const media = await client.sendFile(peer, {
            file,
            caption: text.length <= 1024 ? text : "",
            forceDocument: false,
          });
          // Telegram ограничивает подпись изображения сильнее обычного
          // сообщения. Длинный оффер отправляем сразу следом целиком, не
          // обрезая пользовательский текст и не теряя медиа.
          sent = text.length > 1024
            ? await client.sendMessage(peer, { message: text })
            : media;
        } else {
          sent = await client.sendMessage(peer, { message: text });
        }
        // Только подтверждённая Telegram отправка открывает точное окно ответа.
        // До этого момента обработчик не имеет права трактовать личные диалоги
        // владельца как лидов Zephyr.
        if (watchSeconds) {
          watchReplyPeer(entry, peer, username, watchSeconds);
          bindHandlers(entry);
        }
        return {
          message_id: sent && sent.id ? sent.id : null,
          peer_tg_id: String(peer.id || ""),
          peer_username: String(peer.username || username || ""),
        };
      },
      { requireSession: true }
    );
    res.json({ ok: true, ...result });
  } catch (err) {
    console.error("send:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

async function readSpamBot(client) {
  const bot = await client.getEntity("SpamBot");
  await client.sendMessage(bot, { message: "/start" });
  await new Promise((resolve) => setTimeout(resolve, 1400));
  const messages = await client.getMessages(bot, { limit: 6 });
  const incoming = messages.find((message) => message && !message.out && message.message);
  if (!incoming) throw new Error("SpamBot не ответил. Повторите проверку позже");
  return { bot, message: incoming, detail: String(incoming.message || "").slice(0, 2000) };
}

app.post("/spambot/status", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  try {
    const result = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client) => {
        const answer = await readSpamBot(client);
        return { status: spamBotState(answer.detail), detail: answer.detail };
      },
      { requireSession: true }
    );
    res.json({ ok: true, ...result, manual_url: "https://t.me/SpamBot" });
  } catch (err) {
    console.error("spambot-status:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

app.post("/spambot/appeal", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  try {
    const result = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client) => {
        const answer = await readSpamBot(client);
        if (spamBotState(answer.detail) === "clear") {
          return { status: "clear", detail: answer.detail, action: "not_needed" };
        }
        const rows = (answer.message.replyMarkup && answer.message.replyMarkup.rows) || [];
        const buttons = rows.flatMap((row) => row.buttons || []);
        const appeal = buttons.find((button) =>
          /mistake|appeal|review|ошиб|обжал|пересмотр/i.test(String(button.text || ""))
        );
        if (!appeal || !appeal.data) {
          return { status: "appeal_available", detail: answer.detail, action: "manual" };
        }
        await client.invoke(
          new Api.messages.GetBotCallbackAnswer({
            peer: answer.bot,
            msgId: answer.message.id,
            data: appeal.data,
          })
        );
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const followUp = await client.getMessages(answer.bot, { limit: 4 });
        const incoming = followUp.find((message) => message && !message.out && message.message);
        return {
          status: "appeal_sent",
          detail: String((incoming && incoming.message) || answer.detail).slice(0, 2000),
          action: "callback_sent",
        };
      },
      { requireSession: true }
    );
    res.json({ ok: true, ...result, manual_url: "https://t.me/SpamBot" });
  } catch (err) {
    console.error("spambot-appeal:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

app.post("/channel-posts", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const username = publicUsername(req.body && req.body.username);
  const limit = Math.max(1, Math.min(20, Number((req.body && req.body.limit) || 5)));
  if (!username) return res.status(400).json({ ok: false, error: "Не указан username" });
  try {
    const posts = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client, entry) => {
        const peer = await resolvePeer(entry, username);
        const messages = await client.getMessages(peer, { limit });
        return messages
          .filter((m) => m && (m.message || m.media))
          .map((m) => ({
            id: m.id,
            text: m.message || "",
            date: m.date ? m.date * 1000 : null,
            views: m.views || null,
          }));
      },
      { requireSession: true }
    );
    res.json({ ok: true, posts });
  } catch (err) {
    console.error("channel-posts:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

app.post("/resolve", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const username = publicUsername(req.body && req.body.username);
  if (!username) return res.status(400).json({ ok: false, error: "Не указан username" });
  try {
    const profile = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client, entry) => {
        const entity = await resolvePeer(entry, username);
        return {
          id: peerKey(entity),
          title: displayName(entity),
          username: entity.username || "",
          first_name: entity.firstName || "",
          last_name: entity.lastName || "",
          kind: entity.className === "User" ? (entity.bot ? "bot" : "user") : entity.megagroup ? "group" : "channel",
          verified: Boolean(entity.verified),
          scam: Boolean(entity.scam),
          fake: Boolean(entity.fake),
          participants: entity.participantsCount || null,
          about: "",
        };
      },
      { requireSession: true }
    );
    res.json({ ok: true, profile });
  } catch (err) {
    console.error("resolve:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

/** Найти личные контакты, которые публично указаны у канала/бота.
 *
 * Telegram не публикует поле «владелец канала». Корректный источник — только
 * то, что сам автор оставил в about или последних постах: @username/t.me и
 * публичный автор сообщения. Ручка возвращает кандидатов, а backend затем
 * отдельно resolve-ит каждого и принимает только kind=user.
 */
function publicHandles(text) {
  const value = String(text || "");
  const out = [];
  const patterns = [
    /(?:https?:\/\/)?(?:t\.me|telegram\.me|telegram\.dog)\/(?:s\/)?([A-Za-z0-9_]{4,32})/gi,
    /(^|[^A-Za-z0-9_])@([A-Za-z0-9_]{4,32})/g,
  ];
  for (const pattern of patterns) {
    for (const match of value.matchAll(pattern)) {
      const handle = (match[2] || match[1] || "").toLowerCase();
      if (handle && !out.includes(handle)) out.push(handle);
    }
  }
  return out;
}

async function publicAbout(client, entity) {
  try {
    if (entity.className === "User") {
      const full = await client.invoke(new Api.users.GetFullUser({ id: entity }));
      return (full.fullUser && full.fullUser.about) || "";
    }
    if (entity.className === "Channel") {
      const full = await client.invoke(new Api.channels.GetFullChannel({ channel: entity }));
      return (full.fullChat && full.fullChat.about) || "";
    }
  } catch (err) {
    console.warn("contact-candidates about:", err && err.message);
  }
  return "";
}

app.post("/contact-candidates", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const username = publicUsername(req.body && req.body.username);
  if (!username) return res.status(400).json({ ok: false, error: "Не указан username" });
  try {
    const result = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client, entry) => {
        const entity = await resolvePeer(entry, username);
        const found = new Map();
        const add = (handle, source) => {
          const clean = String(handle || "").trim().replace(/^@/, "").toLowerCase();
          if (!/^[a-z0-9_]{4,32}$/.test(clean) || clean === username.toLowerCase()) return;
          if (!found.has(clean)) found.set(clean, { username: clean, source });
        };

        const about = await publicAbout(client, entity);
        publicHandles(about).forEach((handle) => add(handle, "about"));

        if (entity.className === "Channel" || entity.className === "Chat") {
          const messages = await client.getMessages(entity, { limit: 12 });
          for (const message of messages) {
            publicHandles(message && message.message).forEach((handle) => add(handle, "post"));
            try {
              const sender = await message.getSender();
              if (sender && sender.className === "User" && !sender.bot && sender.username) {
                add(sender.username, "post_author");
              }
            } catch (_) {}
          }
        }
        return { about: String(about || "").slice(0, 1000), candidates: [...found.values()].slice(0, 30) };
      },
      { requireSession: true }
    );
    res.json({ ok: true, ...result });
  } catch (err) {
    console.error("contact-candidates:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

// --- Личная переписка для кабинета ----------------------------------------
//
// Отдельно от ручек про лидов. Там действует правило «только публичный
// username и только живой человек», здесь человек смотрит собственный
// Telegram целиком, и та проверка была бы бессмысленной. Право доступа
// проверяет backend: сюда запрос приходит уже с owner_id, которому этот
// аккаунт принадлежит.

// Кеш аватаров и превью на весь процесс. Ключ включает владельца и аккаунт,
// поэтому пересечь чужие картинки нельзя даже при совпадении id собеседника.
const mediaCache = chat.createCache();

/**
 * Найти собеседника по «меченому» id.
 *
 * GramJS держит соответствие id → access_hash в памяти сессии, и после
 * перезапуска шлюза оно пустое: getEntity по одному id тогда падает. Поэтому
 * при промахе один раз прогреваем кеш списком диалогов — именно там Telegram
 * отдаёт access_hash для всех, с кем человек переписывается.
 */
async function chatPeer(client, peerId) {
  const id = String(peerId == null ? "" : peerId).trim();
  if (!/^-?\d{1,20}$/.test(id)) throw new Error("Некорректный идентификатор диалога");
  const asBig = BigInt(id);
  try {
    return await client.getInputEntity(asBig);
  } catch (_) {
    // Прогрев: 200 диалогов покрывают активную переписку любого живого
    // аккаунта, а полный проход по архиву стоил бы минуты и флуд-лимита.
    await client.getDialogs({ limit: 200 });
    return client.getInputEntity(asBig);
  }
}

app.post("/chat/dialogs", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const body = req.body || {};
  try {
    const result = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client) => {
        const offsetPeerId = String(body.offset_peer || "").trim();
        const offsetPeer = offsetPeerId ? await chatPeer(client, offsetPeerId).catch(() => null) : null;
        return chat.listDialogs(client, {
          limit: body.limit,
          offsetDate: Number(body.offset_date || 0),
          offsetId: Number(body.offset_id || 0),
          offsetPeer,
        });
      },
      { requireSession: true }
    );
    res.json({ ok: true, ...result });
  } catch (err) {
    console.error("chat-dialogs:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

app.post("/chat/history", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const body = req.body || {};
  try {
    const result = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client) => {
        const peer = await chatPeer(client, body.peer_id);
        return chat.loadHistory(client, peer, {
          limit: body.limit,
          offsetId: Number(body.offset_id || 0),
        });
      },
      { requireSession: true }
    );
    res.json({ ok: true, ...result });
  } catch (err) {
    console.error("chat-history:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

app.post("/chat/send", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const body = req.body || {};
  const text = String(body.text || "");
  if (!text.trim()) return res.status(400).json({ ok: false, error: "Пустое сообщение" });
  if (text.length > 4096) {
    return res.status(400).json({ ok: false, error: "Сообщение длиннее 4096 символов" });
  }
  try {
    const result = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client) => {
        const peer = await chatPeer(client, body.peer_id);
        const replyTo = Number(body.reply_to || 0) || undefined;
        const sent = await client.sendMessage(peer, { message: text, replyTo });
        return {
          message_id: sent && sent.id ? Number(sent.id) : 0,
          date: sent && sent.date ? Number(sent.date) * 1000 : Date.now(),
        };
      },
      { requireSession: true }
    );
    res.json({ ok: true, ...result });
  } catch (err) {
    console.error("chat-send:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

app.post("/chat/read", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const body = req.body || {};
  try {
    await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client) => {
        const peer = await chatPeer(client, body.peer_id);
        // Отметка о прочтении уходит в Telegram по-настоящему: иначе счётчик
        // непрочитанных в кабинете и в телефоне разошлись бы навсегда.
        await client.markAsRead(peer);
      },
      { requireSession: true }
    );
    res.json({ ok: true });
  } catch (err) {
    console.error("chat-read:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

/**
 * Аватар собеседника.
 *
 * Отдаём отдельным запросом, а не внутри списка: аватары есть не у всех, весят
 * заметно больше самого списка и нужны только для видимой части экрана.
 */
app.post("/chat/avatar", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const peerId = String((req.body && req.body.peer_id) || "").trim();
  const cacheKey = `avatar:${ctx.ownerId}:${ctx.accountId}:${peerId}`;
  const cached = mediaCache.get(cacheKey);
  if (cached !== null) return res.json({ ok: true, image: cached });
  try {
    const image = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client) => {
        const peer = await chatPeer(client, peerId);
        const buffer = await client.downloadProfilePhoto(peer, { isBig: false });
        if (!buffer || !buffer.length) return "";
        return `data:image/jpeg;base64,${Buffer.from(buffer).toString("base64")}`;
      },
      { requireSession: true }
    );
    // Пустую строку тоже кешируем: «аватара нет» — такой же ответ, и
    // переспрашивать его при каждом прокруте списка незачем.
    mediaCache.set(cacheKey, image);
    res.json({ ok: true, image });
  } catch (err) {
    console.error("chat-avatar:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

/** Картинка из сообщения: грузим только когда сообщение попало на экран. */
app.post("/chat/media", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const body = req.body || {};
  const peerId = String(body.peer_id || "").trim();
  const messageId = Number(body.message_id || 0);
  if (!messageId) return res.status(400).json({ ok: false, error: "Не указано сообщение" });
  const cacheKey = `media:${ctx.ownerId}:${ctx.accountId}:${peerId}:${messageId}`;
  const cached = mediaCache.get(cacheKey);
  if (cached !== null) return res.json({ ok: true, image: cached });
  try {
    const image = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client) => {
        const peer = await chatPeer(client, peerId);
        const [message] = await client.getMessages(peer, { ids: [messageId] });
        if (!message || !message.media) return "";
        // Только уменьшенная копия. Оригинал видео или скана на несколько
        // мегабайт превратил бы прокрутку переписки в загрузку файлов.
        const buffer = await client.downloadMedia(message, { thumb: -1 });
        if (!buffer || !buffer.length) return "";
        return `data:image/jpeg;base64,${Buffer.from(buffer).toString("base64")}`;
      },
      { requireSession: true }
    );
    mediaCache.set(cacheKey, image);
    res.json({ ok: true, image });
  } catch (err) {
    console.error("chat-media:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

// --- Нейрокомментинг ------------------------------------------------------
//
// Отдельный блок ручек, потому что комментирование живёт по другим правилам,
// чем рассылка: пишем в публичные площадки, где мы участник, и ни при каких
// условиях не в личку незнакомцу.
//
// Вступление в чат вынесено в отдельную ручку и НЕ делается автоматически
// внутри поиска: joinChannel — самое «дорогое» действие для репутации
// аккаунта, и решение о нём принимает человек в интерфейсе.

/** Тип площадки в терминах бэкенда: канал (посты + чат обсуждений) или группа. */
function targetKind(entity) {
  if (!entity) return "";
  if (entity.className === "Chat" || entity.className === "ChatForbidden") return "group";
  if (entity.className === "Channel" || entity.className === "ChannelForbidden") {
    return entity.megagroup ? "group" : "channel";
  }
  return "";
}

/** Поиск публичных каналов и групп по теме. */
app.post("/neuro/search", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const query = String((req.body && req.body.query) || "").trim().slice(0, 100);
  const limit = Math.max(1, Math.min(50, Number((req.body && req.body.limit) || 20)));
  if (query.length < 2) return res.status(400).json({ ok: false, error: "Слишком короткий запрос" });
  try {
    const found = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client) => {
        const result = await client.invoke(new Api.contacts.Search({ q: query, limit }));
        const chats = [...(result.chats || [])];
        return chats
          .map((chat) => ({
            id: peerKey(chat),
            username: chat.username || "",
            title: displayName(chat),
            kind: targetKind(chat),
            participants: chat.participantsCount || null,
            // Уже вступили — значит комментировать можно без нового joinChannel.
            joined: Boolean(chat.left === false),
            verified: Boolean(chat.verified),
            scam: Boolean(chat.scam),
            fake: Boolean(chat.fake),
          }))
          // Без публичного username цель бесполезна: её не открыть по ссылке.
          .filter((item) => item.username && item.kind && !item.scam && !item.fake);
      },
      { requireSession: true }
    );
    res.json({ ok: true, results: found });
  } catch (err) {
    console.error("neuro-search:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

/** Всё, что нужно знать перед комментированием: слоумод, чат обсуждений, участие. */
app.post("/neuro/info", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const username = publicUsername(req.body && req.body.username);
  if (!username) return res.status(400).json({ ok: false, error: "Не указан username" });
  try {
    const info = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client, entry) => {
        const entity = await resolvePeer(entry, username);
        const kind = targetKind(entity);
        const base = {
          id: peerKey(entity),
          username: entity.username || "",
          title: displayName(entity),
          kind,
          participants: entity.participantsCount || null,
          joined: entity.left === false,
          slowmode_seconds: 0,
          linked_chat_id: "",
          // Канал без чата обсуждений комментировать физически негде.
          can_comment: false,
          restricted: Boolean(entity.restricted),
        };
        if (!kind) throw new Error("Это не канал и не группа");
        let full = null;
        try {
          full = await client.invoke(new Api.channels.GetFullChannel({ channel: entity }));
        } catch (_) {
          // Приватные площадки полную карточку не отдают: работаем по тому,
          // что видно снаружи, а не падаем.
          full = null;
        }
        const fullChat = full && full.fullChat;
        if (fullChat) {
          base.slowmode_seconds = Number(fullChat.slowmodeSeconds || 0) || 0;
          base.participants = Number(fullChat.participantsCount || base.participants || 0) || null;
          if (fullChat.linkedChatId) {
            const raw = String(fullChat.linkedChatId);
            base.linked_chat_id = raw.startsWith("-100") ? raw : `-100${raw}`;
          }
        }
        base.can_comment = kind === "group" ? true : Boolean(base.linked_chat_id);
        return base;
      },
      { requireSession: true }
    );
    res.json({ ok: true, info });
  } catch (err) {
    console.error("neuro-info:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

/** Вступление в чат или канал. Вызывается только по явной команде из интерфейса. */
app.post("/neuro/join", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const username = publicUsername(req.body && req.body.username);
  if (!username) return res.status(400).json({ ok: false, error: "Не указан username" });
  try {
    const result = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client, entry) => {
        const entity = await resolvePeer(entry, username);
        if (entity.left === false) return { joined: true, already: true };
        await client.invoke(new Api.channels.JoinChannel({ channel: entity }));
        return { joined: true, already: false };
      },
      { requireSession: true }
    );
    res.json({ ok: true, ...result });
  } catch (err) {
    console.error("neuro-join:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

/** Последние посты площадки — сырьё для генерации комментария. */
app.post("/neuro/posts", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const username = publicUsername(req.body && req.body.username);
  const limit = Math.max(1, Math.min(20, Number((req.body && req.body.limit) || 5)));
  if (!username) return res.status(400).json({ ok: false, error: "Не указан username" });
  try {
    const posts = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client, entry) => {
        const peer = await resolvePeer(entry, username);
        const messages = await client.getMessages(peer, { limit });
        return messages
          .filter((m) => m && m.id && (m.message || "").trim())
          .map((m) => ({
            id: Number(m.id),
            text: m.message || "",
            date: m.date ? m.date * 1000 : null,
            views: m.views || null,
            // Сколько уже комментариев: под постом без обсуждения писать нечего,
            // а под постом с сотней комментариев наш будет незаметен.
            replies: m.replies ? Number(m.replies.replies || 0) : 0,
            // Своё сообщение исключаем на стороне бэкенда по журналу, но если
            // пост наш собственный — комментировать его бессмысленно.
            out: Boolean(m.out),
          }));
      },
      { requireSession: true }
    );
    res.json({ ok: true, posts });
  } catch (err) {
    console.error("neuro-posts:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

/** Отправка комментария.
 *
 * Для канала используем commentTo: GramJS сам находит привязанный чат
 * обсуждений и служебное сообщение поста, поэтому нам не нужно вручную
 * пересчитывать идентификаторы между каналом и чатом (это классический
 * источник ошибки «комментарий улетел не туда»).
 *
 * Для группы это обычная реплика в потоке.
 */
app.post("/neuro/comment", async (req, res) => {
  const ctx = serverContext(req, res);
  if (!ctx) return;
  const body = req.body || {};
  const username = publicUsername(body.username);
  const text = String(body.text || "").trim();
  const postId = Number(body.post_id || 0) || 0;
  const kind = String(body.kind || "channel");
  if (!username) return res.status(400).json({ ok: false, error: "Не указан username" });
  if (!text) return res.status(400).json({ ok: false, error: "Пустой комментарий" });
  // Верхняя граница ниже лимита Telegram намеренно: длинный комментарий в чате
  // выглядит машинным независимо от содержания.
  if (text.length > 1000) {
    return res.status(400).json({ ok: false, error: "Комментарий длиннее 1000 символов" });
  }
  if (kind === "channel" && !postId) {
    return res.status(400).json({ ok: false, error: "Не указан пост для комментария" });
  }
  try {
    const result = await withClient(
      ctx.ownerId,
      ctx.accountId,
      async (client, entry) => {
        const peer = await resolvePeer(entry, username);
        const params = { message: text };
        if (kind === "channel") params.commentTo = postId;
        else if (postId) params.replyTo = postId;
        const sent = await client.sendMessage(peer, params);
        return {
          message_id: sent && sent.id ? Number(sent.id) : 0,
          date: sent && sent.date ? Number(sent.date) * 1000 : Date.now(),
        };
      },
      { requireSession: true }
    );
    res.json({ ok: true, ...result });
  } catch (err) {
    console.error("neuro-comment:", err && err.message);
    res.status(502).json(errorPayload(err));
  }
});

// --- События Telegram → вкладки ------------------------------------------
//
// Обработчики вешаются РОВНО ОДИН РАЗ на клиента. Раньше их добавляли на
// каждое соединение, и после пары перезагрузок страницы одно сообщение
// приходило по пять раз.

function bindHandlers(entry) {
  if (entry.bound) return;
  entry.bound = true;
  entry.client.addEventHandler((event) => {
    const msg = event.message;
    if (!msg) return;
    // Веб-чата нет: слушатель нужен только для фиксации ответа лида на сайте.
    reportReply(entry, msg);
  }, new NewMessage({}));
  entry.client.addEventHandler((update) => {
    if (!update || !["UpdateReadHistoryOutbox", "UpdateReadChannelOutbox"].includes(update.className)) return;
    reportRead(entry, update);
  }, new Raw({}));
}

async function reportRead(entry, update) {
  if (!SEND_SECRET) return;
  const peer = update.peer || {};
  const peerId = peer.userId || peer.channelId || peer.chatId || "";
  const maxId = Number(update.maxId || update.max_id || 0);
  if (!peerId || !Number.isInteger(maxId) || maxId <= 0) return;
  try {
    const res = await fetch(`${SITE_API_URL}/api/campaigns/read`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Send-Secret": SEND_SECRET },
      body: JSON.stringify({
        owner_id: Number(entry.ownerId),
        account_id: /^\d+$/.test(entry.accountId) ? Number(entry.accountId) : 0,
        peer_tg_id: String(peerId),
        max_message_id: maxId,
      }),
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) console.warn(`read: сайт ответил ${res.status}`);
  } catch (err) {
    console.warn("read:", err && err.message);
  }
}

/**
 * Сообщить сайту, что лид ответил.
 *
 * Ловим только входящие личные сообщения: свои (out) и каналы/группы не нужны —
 * ответом считается сообщение от человека, которому мы писали оффер. Сопоставляет
 * лида сам сайт (/api/campaigns/reply): здесь нет ни базы, ни истории отправок.
 * Ошибка callback не должна ронять gateway — уведомление важное, но не критичное.
 */
async function reportReply(entry, msg) {
  if (!SEND_SECRET) return;
  // Правило «что считать ответом лида» живёт в reply-filter.js и покрыто
  // тестами: ложный ответ гасит окно ожидания, и настоящий после этого
  // теряется, поэтому проверять его на живом Telegram нельзя.
  let sender = null;
  try {
    sender = await msg.getSender();
  } catch (_) {
    return;
  }
  let senderId = "";
  try {
    senderId = msg.senderId ? String(msg.senderId) : "";
  } catch (_) {}
  if (!senderId && sender) senderId = String(sender.id || "");
  const watch = watchedReply(entry, senderId);
  const verdict = classifyIncoming(msg, sender, Boolean(watch));
  if (!verdict.ok) return;
  const username = sender.username || "";
  const senderName = displayName(sender);
  const body = {
    owner_id: Number(entry.ownerId),
    account_id: /^\d+$/.test(entry.accountId) ? Number(entry.accountId) : 0,
    tg_id: senderId,
    username,
    sender_name: senderName,
    text: (msg.message || "").slice(0, 500),
  };
  try {
    const res = await fetch(`${SITE_API_URL}/api/campaigns/reply`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Send-Secret": SEND_SECRET },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) {
      console.warn(`reply: сайт ответил ${res.status}`);
      return;
    }
    const result = await res.json().catch(() => ({}));
    if (result && result.matched && entry.replyWatches.get(senderId) === watch) {
      // Один успешный ответ поглощает окно. Повторные сообщения в обычном
      // диалоге остаются в Telegram и больше не становятся уведомлениями Zephyr.
      entry.replyWatches.delete(senderId);
      pruneReplyWatches(entry);
      entry.holdUntil = Math.max(
        0,
        ...[...entry.replyWatches.values()].map((item) => Number(item.expiresAt || 0))
      );
      scheduleIdle(entry.key);
    }
  } catch (err) {
    console.warn("reply:", err && err.message);
  }
}

/**
 * Сообщить сайту, какой Telegram вошёл в этот слот. Именно поэтому в
 * настройках не нужно вводить ник руками: карточка подпишется сама.
 * Ошибка здесь не должна ломать вход — это всего лишь подпись.
 */
async function reportIdentity(entry, me) {
  if (!SEND_SECRET || !me) return;
  if (!/^\d+$/.test(entry.accountId) || entry.accountId === "0") return;
  const body = {
    owner_id: Number(entry.ownerId),
    account_id: Number(entry.accountId),
    tg_id: String(me.id || ""),
    name: displayName(me),
    username: me.username || "",
    phone: me.phone ? `+${String(me.phone).replace(/^\+/, "")}` : "",
  };
  try {
    const res = await fetch(`${SITE_API_URL}/api/warmup/identity`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Send-Secret": SEND_SECRET },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) console.warn(`identity: сайт ответил ${res.status}`);
  } catch (err) {
    console.warn("identity:", err && err.message);
  }
}

// --- WebSocket: одна вкладка = один зритель --------------------------------

wss.on("connection", async (ws, req) => {
  if (!websocketOriginOk(req)) {
    ws.close(4003, "bad origin");
    return;
  }
  const url = new URL(req.url || "/", "http://localhost");
  const info = readToken(url.searchParams.get("t"));
  if (!info) {
    try {
      ws.send(JSON.stringify({ type: "error", error: "Билет подключения не принят или истёк. Откройте настройки заново" }));
    } catch (_) {}
    ws.close(4003, "bad ticket");
    return;
  }

  const { ownerId, accountId, primary } = info;
  // Старая «одна на человека» сессия достаётся только главному аккаунту, иначе
  // второй слот открыл бы тот же Telegram и человек решил, что переключение не работает.
  if (primary && !loadSession(ownerId, accountId)) adoptLegacySession(ownerId, accountId);

  const send = (payload) => {
    if (ws.readyState === 1) {
      try {
        ws.send(JSON.stringify(payload));
      } catch (_) {}
    }
  };

  let entry = null;
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    if (entry) {
      entry.listeners.delete(ws);
      // Никто больше не смотрит — соединение погаснет само через паузу.
      scheduleIdle(entry.key);
    }
  };
  ws.on("close", release);
  ws.on("error", release);

  try {
    entry = await getEntry(ownerId, accountId);
  } catch (err) {
    send({ type: "error", error: errText(err) });
    ws.close(1011, "connect failed");
    return;
  }
  entry.listeners.add(ws);
  cancelIdle(entry);
  bindHandlers(entry);

  // Операции формы выстраиваются в цепочку: несколько быстрых сообщений не
  // должны перемешать этапы авторизации, а GramJS не любит параллельный вход.
  let chain = Promise.resolve();
  const serial = (fn) => {
    chain = chain.then(fn).catch((err) => {
      console.error("ws:", err && err.message);
      send({ type: "error", error: errText(err) });
    });
    return chain;
  };

  const pending = { code: null, password: null };
  let resendRequested = false;
  let forceSmsRequested = false;
  let qrRequested = false;
  let loginPhone = "";
  const waitForInput = (field) =>
    new Promise((resolve, reject) => {
      // Без таймера брошенный вход висел бы вечно и держал соединение.
      const timer = setTimeout(() => {
        pending[field] = null;
        reject(new Error("Время ожидания истекло — начните вход заново"));
      }, INPUT_TIMEOUT_MS);
      pending[field] = (value) => {
        clearTimeout(timer);
        pending[field] = null;
        resolve(value);
      };
    });

  /** Общий финал входа: и после кода, и при автовходе с сохранённой сессией. */
  const afterLogin = async () => {
    const me = await entry.client.getMe();
    send({
      type: "me",
      me: {
        id: String(me.id),
        name: displayName(me),
        username: me.username || "",
        phone: me.phone ? `+${String(me.phone).replace(/^\+/, "")}` : "",
      },
    });
    send({ type: "logged_in" });
    // Веб-чата больше нет: после авторизации форме достаточно идентичности.
    reportIdentity(entry, me);
  };

  const sendQrCode = async ({ token, expires }) => {
    const encoded = Buffer.from(token)
      .toString("base64")
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    const dataUrl = await QRCode.toDataURL(`tg://login?token=${encoded}`, {
      errorCorrectionLevel: "M",
      margin: 1,
      width: 256,
      color: { dark: "#111827", light: "#ffffff" },
    });
    send({ type: "qr_code", image: dataUrl, expires: Number(expires || 0) });
  };

  const authParams = {
    phoneNumber: async () => {
      if (qrRequested) {
        qrRequested = false;
        const switchToQr = new Error("RESTART_AUTH_WITH_QR");
        switchToQr.errorMessage = "RESTART_AUTH_WITH_QR";
        throw switchToQr;
      }
      return loginPhone;
    },
    get forceSMS() {
      return forceSmsRequested;
    },
    phoneCode: async (isCodeViaApp) => {
      const resent = resendRequested;
      resendRequested = false;
      forceSmsRequested = false;
      const delivery = isCodeViaApp ? "app" : "sms";
      console.log(
        `Код входа запрошен: owner=${ownerId} account=${accountId} delivery=${delivery}${resent ? " resend" : ""}`
      );
      send({ type: "need_code", delivery, resent });
      const code = await waitForInput("code");
      if (code === RESEND_CODE) {
        const restart = new Error("RESTART_AUTH");
        restart.errorMessage = "RESTART_AUTH";
        throw restart;
      }
      return code;
    },
    qrCode: sendQrCode,
    password: async () => {
      send({ type: "need_password" });
      return waitForInput("password");
    },
    onError: (err) => {
      send({ type: "error", error: errText(err) });
      const raw = String((err && (err.errorMessage || err.message)) || err || "").toUpperCase();
      // В GramJS false продолжает текущий вход и снова ждёт значение,
      // true превращается в AUTH_USER_CANCEL. Ошибку кода/2FA можно
      // исправить в той же форме, не запрашивая новый билет.
      return ![
        "PHONE_CODE_INVALID",
        "PHONE_CODE_EMPTY",
        "PHONE_CODE_EXPIRED",
        "PASSWORD_HASH_INVALID",
      ].some((code) => raw.includes(code));
    },
  };

  const runLogin = async () => {
    await entry.client.start(authParams);
    saveSession(ownerId, accountId, entry.client.session.save());
    await afterLogin();
  };

  // Главное отличие от прошлой версии: если сессия есть, человек сразу видит
  // сессию — повторный вход при каждом открытии настроек не нужен.
  const stored = loadSession(ownerId, accountId);
  send({ type: "session_status", hasSession: Boolean(stored) });
  if (stored) {
    serial(async () => {
      const authorized = await entry.client.isUserAuthorized();
      if (!authorized) {
        // Сессию отозвали с телефона — держать мёртвый файл нет смысла.
        dropSession(ownerId, accountId);
        send({ type: "session_status", hasSession: false });
        send({ type: "error", error: TG_ERRORS.AUTH_KEY_UNREGISTERED });
        return;
      }
      await afterLogin();
    });
  }

  ws.on("message", (raw) => {
    let msg = null;
    try {
      msg = JSON.parse(String(raw));
    } catch (_) {
      return;
    }
    if (!msg || typeof msg.type !== "string") return;
    // Браузерная поверхность ограничена подключением аккаунта. Диалоги,
    // история и ручная отправка доступны только в нативном Telegram; рабочие
    // resolve/send/channel-posts вызываются backend по защищённому HTTP API.
    if (!["login_start", "login_qr", "code", "resend", "password", "logout"].includes(msg.type)) {
      send({ type: "error", error: "Операция недоступна в форме подключения" });
      return;
    }

    switch (msg.type) {
      case "login_start":
        serial(async () => {
          const phone = String(msg.phone || "").trim();
          if (!phone) throw new Error("Введите номер телефона");
          loginPhone = phone;
          qrRequested = false;
          forceSmsRequested = false;
          send({ type: "status", status: "Подключаюсь к Telegram…" });
          await runLogin();
        });
        break;

      case "login_qr":
        qrRequested = true;
        forceSmsRequested = false;
        send({ type: "status", status: "Создаю безопасный QR-код…" });
        if (pending.code) {
          pending.code(RESEND_CODE);
        } else {
          // QR-вход запускается тем же обработчиком авторизации, но phoneNumber
          // сразу переключает GramJS на auth.exportLoginToken.
          loginPhone = "";
          serial(runLogin);
        }
        break;

      case "code":
        if (pending.code) pending.code(String(msg.code || "").trim());
        break;

      case "resend":
        if (pending.code) {
          resendRequested = true;
          // GramJS сначала получает новый hash, а если Telegram снова выбрал
          // доставку в приложение — вызывает auth.resendCode для SMS/fallback.
          forceSmsRequested = true;
          pending.code(RESEND_CODE);
        } else {
          send({ type: "error", error: "Сначала запросите код для этого номера" });
        }
        break;

      case "password":
        if (pending.password) pending.password(String(msg.password || ""));
        break;

      case "logout":
        serial(async () => {
          try {
            await entry.client.invoke(new Api.auth.LogOut());
          } catch (_) {
            // Сессия могла уже быть недействительна — файл всё равно убираем.
          }
          dropSession(ownerId, accountId);
          send({ type: "logged_out" });
          const key = entry.key;
          entry.listeners.delete(ws);
          released = true;
          await shutdownEntry(key);
          ws.close(1000, "logged out");
        });
        break;

      default:
        break;
    }
  });
});

// --- Запуск и аккуратное выключение ----------------------------------------

server.listen(PORT, () => {
  console.log(`tg-mini-client на порту ${PORT}`);
});

let closing = false;

async function shutdown(signal) {
  if (closing) return;
  closing = true;
  console.log(`выключение (${signal})`);
  try {
    server.close();
  } catch (_) {}
  for (const ws of wss.clients) {
    try {
      ws.close(1012, "restart");
    } catch (_) {}
  }
  // Клиенты MTProto гасим явно: иначе Telegram считает сеанс живым
  // и при быстром перезапуске можно получить FLOOD_WAIT.
  await Promise.all([...registry.keys()].map((key) => shutdownEntry(key)));
  process.exit(0);
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

process.on("unhandledRejection", (err) => {
  // GramJS при обрыве сети роняет одиночные промисы — гасить из-за этого
  // всю службу и рвать чужие сеансы нельзя.
  console.error("unhandledRejection:", err && err.message ? err.message : err);
});
