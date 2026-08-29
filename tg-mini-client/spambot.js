"use strict";

/**
 * Классифицировать текст официального @SpamBot.
 *
 * Важен порядок: в положительном ответе тоже есть слово «ограничений»
 * («свободен от каких-либо ограничений»), поэтому одиночные слова никогда
 * не считаются доказательством блока. Неизвестная формулировка остаётся
 * unknown и не исключает аккаунт из ротации.
 */
function spamBotState(text) {
  const value = String(text || "")
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/[\u00a0\s]+/g, " ")
    .trim();

  const clearPatterns = [
    /free as a bird/,
    /\bno (?:limits?|restrictions?)(?: are)?(?: currently)? (?:applied|placed|active)/,
    /(?:account|you).{0,45}(?:is not|isn't|not currently).{0,25}(?:limited|restricted)/,
    /свобод[а-я]* от (?:каких[- ]?либо )?ограничен[а-я]*/,
    /(?:никак[а-я]* )?ограничен[а-я]* (?:на (?:ваш )?аккаунт )?не (?:наложен[а-я]*|применен[а-я]*)/,
    /на (?:ваш )?аккаунт не (?:наложен[а-я]*|применен[а-я]*).{0,30}ограничен[а-я]*/,
    /ограничен[а-я]* (?:сейчас )?нет/,
  ];
  if (clearPatterns.some((pattern) => pattern.test(value))) return "clear";

  const limitedPatterns = [
    /your account.{0,35}(?:has been|is currently|is) (?:limited|restricted)/,
    /some users reported.{0,80}(?:messages|spam)/,
    /you (?:cannot|can't|won't be able to).{0,45}(?:send|message)/,
    /к сожалению.{0,100}(?:пожаловал|жалоб|огранич)/,
    /(?:ваш )?(?:аккаунт|учетн[а-я]* запись).{0,35}(?:ограничен|заблокирован)/,
    /на (?:ваш )?аккаунт (?:были )?наложен[а-я]*.{0,30}ограничен[а-я]*/,
    /(?:не можете|нельзя|невозможно).{0,45}(?:отправлять|писать).{0,35}сообщен[а-я]*/,
    /ограничен[а-я]*.{0,45}(?:будут сняты|действуют до|снимутся)/,
  ];
  return limitedPatterns.some((pattern) => pattern.test(value)) ? "limited" : "unknown";
}

module.exports = { spamBotState };
