"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { classifyIncoming } = require("../reply-filter");

const human = { className: "User", id: 555, username: "client", bot: false };
const privateMsg = { isPrivate: true, senderId: 555, message: "Интересно" };

test("настоящий ответ в личке от человека, которому мы писали, проходит", () => {
  assert.equal(classifyIncoming(privateMsg, human, true).ok, true);
});

test("своё исходящее не считается ответом", () => {
  const got = classifyIncoming({ ...privateMsg, out: true }, human, true);
  assert.equal(got.ok, false);
  assert.match(got.reason, /исходящее/);
});

test("служебное событие не считается ответом", () => {
  const got = classifyIncoming({ ...privateMsg, className: "MessageService" }, human, true);
  assert.equal(got.ok, false);
});

test("сообщение из группы отбрасывается", () => {
  // Главный источник «левых» сообщений: реклама и болтовня в чатах.
  const group = { isPrivate: false, peerId: { className: "PeerChat" }, senderId: 555 };
  const got = classifyIncoming(group, human, true);
  assert.equal(got.ok, false);
  assert.match(got.reason, /не личный/);
});

test("пост из канала отбрасывается", () => {
  const channel = { isPrivate: false, peerId: { className: "PeerChannel" }, senderId: 777 };
  assert.equal(classifyIncoming(channel, { className: "Channel", id: 777 }, true).ok, false);
});

test("сообщение бота отбрасывается", () => {
  const got = classifyIncoming(privateMsg, { ...human, bot: true }, true);
  assert.equal(got.ok, false);
  assert.match(got.reason, /бот/);
});

test("личка от того, кому мы не писали, не становится ответом лида", () => {
  // Иначе сообщение знакомого гасило бы окно ожидания настоящего лида.
  const got = classifyIncoming(privateMsg, human, false);
  assert.equal(got.ok, false);
  assert.match(got.reason, /не писали/);
});

test("отправителя без id и без username опознать нельзя", () => {
  const anon = { className: "User", id: 0, username: "", bot: false };
  const got = classifyIncoming({ isPrivate: true, senderId: "" }, anon, true);
  assert.equal(got.ok, false);
});

test("peerId в snake_case тоже распознаётся как личный диалог", () => {
  // GramJS отдаёт оба варианта в зависимости от источника события.
  const msg = { peer_id: { className: "PeerUser" }, senderId: 555 };
  assert.equal(classifyIncoming(msg, human, true).ok, true);
});
