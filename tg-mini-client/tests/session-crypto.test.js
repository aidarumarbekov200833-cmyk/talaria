"use strict";

const test = require("node:test");
const assert = require("node:assert");

const { resolveKey, encrypt, decrypt, isEncrypted } = require("../session-crypto");

const SAMPLE = "1BQANOTEuMTA4LjU2LjE0MAG7fake-session-string==";

test("ключ берётся из TG_SESSION_KEY в любом виде", () => {
  const hex = "a".repeat(64);
  assert.strictEqual(resolveKey({ TG_SESSION_KEY: hex }).length, 32);

  const base64 = Buffer.alloc(32, 7).toString("base64");
  assert.deepStrictEqual(resolveKey({ TG_SESSION_KEY: base64 }), Buffer.alloc(32, 7));

  // Произвольная строка тоже принимается: заставлять человека угадывать
  // кодировку — лишний способ сломать развёртывание.
  assert.strictEqual(resolveKey({ TG_SESSION_KEY: "просто пароль" }).length, 32);
});

test("без своего ключа он выводится из общего секрета", () => {
  const derived = resolveKey({ TG_CLIENT_SECRET: "shared-secret" });
  assert.strictEqual(derived.length, 32);
  // Тот же секрет даёт тот же ключ — иначе после перезапуска сессии стали бы
  // нечитаемыми.
  assert.deepStrictEqual(derived, resolveKey({ TG_CLIENT_SECRET: "shared-secret" }));
  // И ключ не равен самому секрету: подпись билетов и шифрование сессий не
  // должны использовать одно значение.
  assert.notStrictEqual(derived.toString("utf8"), "shared-secret");
});

test("совсем без секретов ключа нет", () => {
  assert.strictEqual(resolveKey({}), null);
});

test("зашифрованное расшифровывается обратно", () => {
  const key = resolveKey({ TG_CLIENT_SECRET: "secret" });
  const cipher = encrypt(SAMPLE, key);
  assert.ok(isEncrypted(cipher));
  assert.ok(!cipher.includes("fake-session-string"), "строка сессии не должна быть видна");
  assert.strictEqual(decrypt(cipher, key), SAMPLE);
});

test("каждый раз получается новый шифротекст", () => {
  const key = resolveKey({ TG_CLIENT_SECRET: "secret" });
  // Одинаковый шифротекст на одинаковых данных выдал бы, что две сессии
  // совпадают, и позволил бы подменять файлы местами.
  assert.notStrictEqual(encrypt(SAMPLE, key), encrypt(SAMPLE, key));
});

test("старый открытый файл читается как есть", () => {
  const key = resolveKey({ TG_CLIENT_SECRET: "secret" });
  // Иначе обновление сервиса отключило бы всех, кто уже подключил аккаунт.
  assert.strictEqual(decrypt(SAMPLE, key), SAMPLE);
});

test("чужой ключ не расшифровывает, а падает", () => {
  const mine = resolveKey({ TG_CLIENT_SECRET: "secret-a" });
  const other = resolveKey({ TG_CLIENT_SECRET: "secret-b" });
  const cipher = encrypt(SAMPLE, mine);
  // Молча вернуть пустую строку нельзя: это выглядело бы как «сессии нет»,
  // и настоящая причина потерялась бы.
  assert.throws(() => decrypt(cipher, other));
});

test("подмена содержимого обнаруживается", () => {
  const key = resolveKey({ TG_CLIENT_SECRET: "secret" });
  const cipher = encrypt(SAMPLE, key);
  const parts = cipher.split(":");
  const data = Buffer.from(parts[3], "base64");
  data[0] ^= 0xff;
  parts[3] = data.toString("base64");
  assert.throws(() => decrypt(parts.join(":"), key));
});

test("повреждённый формат не проходит", () => {
  const key = resolveKey({ TG_CLIENT_SECRET: "secret" });
  assert.throws(() => decrypt("tgenc1:мусор", key));
});

test("без ключа зашифрованный файл прочитать нельзя", () => {
  const key = resolveKey({ TG_CLIENT_SECRET: "secret" });
  assert.throws(() => decrypt(encrypt(SAMPLE, key), null));
});

test("пустая сессия остаётся пустой", () => {
  const key = resolveKey({ TG_CLIENT_SECRET: "secret" });
  assert.strictEqual(encrypt("", key), "");
  assert.strictEqual(decrypt("", key), "");
});
