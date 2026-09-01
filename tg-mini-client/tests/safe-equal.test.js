"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { safeEqual } = require("../safe-equal");

// Регрессия: crypto.timingSafeEqual требует буферы РАВНОЙ длины в байтах.
// Старый код сравнивал длину в символах строки, и подпись из многобайтных
// символов роняла проверку RangeError'ом. В WebSocket-обработчике исключение
// уходило в unhandledRejection и оставляло неаутентифицированный сокет
// открытым навсегда.

test("safeEqual: равные строки — true", () => {
  assert.equal(safeEqual("a".repeat(64), "a".repeat(64)), true);
});

test("safeEqual: разные строки одной длины — false", () => {
  assert.equal(safeEqual("a".repeat(64), "b".repeat(64)), false);
});

test("safeEqual: многобайтные символы не бросают исключение", () => {
  // 64 символа «é» = 128 байт UTF-8 против 64 байт у hex-подписи: раньше
  // именно этот случай приводил к RangeError.
  assert.doesNotThrow(() => safeEqual("é".repeat(64), "a".repeat(64)));
  assert.equal(safeEqual("é".repeat(64), "a".repeat(64)), false);
});

test("safeEqual: равные многобайтные строки — true", () => {
  assert.equal(safeEqual("пароль-😀", "пароль-😀"), true);
});

test("safeEqual: не-строки — false без исключений", () => {
  assert.equal(safeEqual(null, "a"), false);
  assert.equal(safeEqual("a", undefined), false);
  assert.equal(safeEqual(123, 123), false);
});
