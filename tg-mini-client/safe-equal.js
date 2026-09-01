"use strict";

const crypto = require("crypto");

/**
 * Сравнение секретов за постоянное время.
 *
 * Длину сравниваем в БАЙТАХ, а не в символах строки: строка из 64 символов
 * «é» даёт 128 байт, и crypto.timingSafeEqual в этом случае бросал RangeError.
 * В WebSocket-обработчике такое исключение уходило в unhandledRejection и
 * оставляло сокет открытым навсегда — неаутентифицированный клиент мог так
 * копить соединения. Здесь любое расхождение — просто false, без исключений.
 */
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const left = Buffer.from(a, "utf8");
  const right = Buffer.from(b, "utf8");
  if (left.length !== right.length) return false;
  return crypto.timingSafeEqual(left, right);
}

module.exports = { safeEqual };
