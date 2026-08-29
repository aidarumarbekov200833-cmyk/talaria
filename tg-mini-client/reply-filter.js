"use strict";

/**
 * Решение «это настоящий ответ лида или посторонний шум».
 *
 * Вынесено из reportReply отдельной чистой функцией по одной причине: раньше
 * правило было вплетено в функцию, которая ходит в сеть, и проверить его можно
 * было только живым Telegram. Цена ошибки здесь высокая — ложный «ответ лида»
 * это уведомление клиенту и погашенное окно ожидания, то есть настоящий ответ
 * после этого потеряется.
 *
 * Что отсекаем и почему:
 *  - свои же исходящие: это не ответ, а наше сообщение;
 *  - служебные события («вошёл в чат», «сменил фото»): не текст человека;
 *  - всё, что не личный диалог: группы, каналы, обсуждения. Рассылка идёт в
 *    личку, поэтому ответ может прийти только из лички;
 *  - отправитель-не-человек: посты каналов и боты;
 *  - тот, кому мы не писали: без окна ожидания это просто чужая переписка.
 */

/** @returns {{ok: true}|{ok: false, reason: string}} */
function classifyIncoming(msg, sender, hasWatch) {
  if (!msg) return { ok: false, reason: "нет сообщения" };
  if (msg.out) return { ok: false, reason: "своё исходящее" };
  if (msg.className === "MessageService") return { ok: false, reason: "служебное событие" };

  const peer = msg.peerId || msg.peer_id || null;
  const privateChat = msg.isPrivate === true || (peer && peer.className === "PeerUser");
  if (!privateChat) return { ok: false, reason: "не личный диалог" };

  if (!sender) return { ok: false, reason: "отправитель неизвестен" };
  if (sender.className !== "User") return { ok: false, reason: "отправитель не человек" };
  if (sender.bot) return { ok: false, reason: "отправитель бот" };

  const senderId = String(msg.senderId || sender.id || "");
  const username = sender.username || "";
  if (!senderId && !username) return { ok: false, reason: "нечем опознать отправителя" };

  if (!hasWatch) return { ok: false, reason: "мы этому человеку не писали" };
  return { ok: true };
}

module.exports = { classifyIncoming };
