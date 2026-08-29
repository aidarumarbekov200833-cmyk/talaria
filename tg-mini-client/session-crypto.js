"use strict";

/**
 * Шифрование строк сессий Telegram на диске.
 *
 * Зачем. Строка сессии — это полный доступ к аккаунту: без пароля, без кода из
 * SMS, с любого устройства. Всё остальное в системе (пароли, токены) можно
 * отозвать, а утёкшая строка сессии означает, что чужой человек читает и пишет
 * от имени владельца, пока тот не завершит сеанс вручную в самом Telegram.
 * Хранить её открытым текстом в файле — самый дорогой из возможных вариантов
 * утечки: снапшот тома, копия бэкапа, ошибочно открытый каталог.
 *
 * AES-256-GCM, а не просто AES: GCM даёт не только шифрование, но и проверку
 * целостности. Испорченный или подменённый файл не расшифруется молча в мусор,
 * который GramJS потом попытается использовать как сессию.
 *
 * Ключ. Берём TG_SESSION_KEY, если он задан. Если нет — выводим ключ из уже
 * существующего TG_CLIENT_SECRET через HKDF. Это сознательный компромисс:
 * шифрование обязано работать сразу после обновления, без новой переменной
 * окружения, которую легко забыть вписать. Забытая переменная означала бы
 * молчаливое хранение открытым текстом — то есть отсутствие защиты именно там,
 * где её считают включённой.
 *
 * Смена TG_CLIENT_SECRET (или TG_SESSION_KEY) делает старые файлы
 * нечитаемыми. Это не потеря доступа к Telegram: владелец просто подключает
 * аккаунт заново. Расшифровать «как-нибудь» и продолжить было бы хуже —
 * это ровно то поведение, ради отключения которого шифрование и вводится.
 */

const crypto = require("crypto");

// Префикс формата. Он же признак «файл зашифрован»: без него файл считается
// старым, ещё открытым, и подлежит перешифровке.
const PREFIX = "tgenc1";
const ALGO = "aes-256-gcm";
const IV_BYTES = 12; // рекомендованная длина nonce для GCM
const KEY_BYTES = 32;

/**
 * Получить ключ шифрования. Возвращает Buffer длиной 32 байта или null, если
 * взять ключ неоткуда — тогда вызывающий код обязан сказать об этом вслух.
 */
function resolveKey(env) {
  const source = env || process.env;
  const explicit = String(source.TG_SESSION_KEY || "").trim();
  if (explicit) {
    // Ключ принимаем и в hex, и в base64, и просто строкой: заставлять
    // человека угадывать кодировку — лишний способ сломать развёртывание.
    if (/^[0-9a-fA-F]{64}$/.test(explicit)) return Buffer.from(explicit, "hex");
    const decoded = Buffer.from(explicit, "base64");
    if (decoded.length === KEY_BYTES) return decoded;
    return crypto.createHash("sha256").update(explicit).digest();
  }

  const fallback = String(source.TG_CLIENT_SECRET || "").trim();
  if (!fallback) return null;
  // HKDF с фиксированной солью и назначением: из того же секрета для другой
  // задачи получится другой ключ, и подпись билетов с шифрованием сессий не
  // будут использовать одно и то же значение.
  return Buffer.from(
    crypto.hkdfSync("sha256", Buffer.from(fallback, "utf8"), Buffer.alloc(0), "talaria-session-v1", KEY_BYTES)
  );
}

/** Похоже ли содержимое файла на наш зашифрованный формат. */
function isEncrypted(raw) {
  return typeof raw === "string" && raw.startsWith(`${PREFIX}:`);
}

/**
 * Зашифровать строку сессии. Без ключа возвращает исходную строку: молча
 * потерять сессию хуже, чем сохранить её как раньше и громко предупредить.
 */
function encrypt(plain, key) {
  if (!key) return String(plain || "");
  const text = String(plain || "");
  if (!text) return "";
  const iv = crypto.randomBytes(IV_BYTES);
  const cipher = crypto.createCipheriv(ALGO, key, iv);
  const data = Buffer.concat([cipher.update(text, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return [PREFIX, iv.toString("base64"), tag.toString("base64"), data.toString("base64")].join(":");
}

/**
 * Расшифровать содержимое файла.
 *
 * Старый открытый файл возвращается как есть — иначе обновление сервиса
 * отключило бы всех, кто уже подключил аккаунт. Испорченный или зашифрованный
 * чужим ключом файл даёт исключение: пустая строка здесь означала бы «сессии
 * нет», и владелец получил бы предложение войти заново вместо понятной
 * ошибки в логе.
 */
function decrypt(raw, key) {
  const text = String(raw || "").trim();
  if (!text) return "";
  if (!isEncrypted(text)) return text;
  if (!key) {
    throw new Error("Файл сессии зашифрован, а ключ шифрования не настроен");
  }
  const parts = text.split(":");
  if (parts.length !== 4) {
    throw new Error("Файл сессии повреждён: неожиданный формат");
  }
  const [, ivPart, tagPart, dataPart] = parts;
  const decipher = crypto.createDecipheriv(ALGO, key, Buffer.from(ivPart, "base64"));
  decipher.setAuthTag(Buffer.from(tagPart, "base64"));
  return Buffer.concat([
    decipher.update(Buffer.from(dataPart, "base64")),
    decipher.final(),
  ]).toString("utf8");
}

module.exports = { resolveKey, encrypt, decrypt, isEncrypted, PREFIX };
