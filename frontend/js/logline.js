/** Один вид журнала для всего кабинета.
 *
 *  До этого «Логи процесса» рисовали три файла по-своему: поиск ставил класс
 *  только для warn, офферы вообще красили ОШИБКИ как предупреждения (`level
 *  === 'error' → terminal-warn`), а рассылка просила `terminal-ok`, которого в
 *  стилях не было. Из-за этого журнал не отвечал на главный вопрос — всё идёт
 *  нормально или уже сломалось.
 *
 *  Здесь одна функция на всех: разбор уровня, время, метка уровня словом,
 *  склейка повторов и автоскролл, который не дёргает ленту, если человек
 *  отлистал наверх и читает.
 */

const LEVELS = {
  info: { cls: 'terminal-info', tag: '' },
  ok: { cls: 'terminal-ok', tag: 'готово' },
  warn: { cls: 'terminal-warn', tag: 'внимание' },
  err: { cls: 'terminal-err', tag: 'ошибка' },
};

const ALIASES = {
  '': 'info',
  info: 'info',
  log: 'info',
  ok: 'ok',
  success: 'ok',
  done: 'ok',
  warn: 'warn',
  warning: 'warn',
  err: 'err',
  error: 'err',
  fail: 'err',
  critical: 'err',
};

/** Уровень из любого написания, которое встречается в коде и в ответах API. */
export function normalizeLevel(level) {
  return ALIASES[String(level || '').toLowerCase()] || 'info';
}

function stamp(date) {
  return new Date(date).toLocaleTimeString('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

/**
 * Дописать строку в журнал.
 *
 * @param {string} boxId  id элемента `.terminal`
 * @param {string} text   сообщение (вставляется текстом, не HTML)
 * @param {string} level  info | ok | warn | err (плюс синонимы)
 */
export function logLine(boxId, text, level = 'info') {
  const box = document.getElementById(boxId);
  if (!box) return;
  const message = String(text == null ? '' : text).trim();
  if (!message) return;
  const kind = normalizeLevel(level);
  const meta = LEVELS[kind];

  box.querySelector('[data-placeholder]')?.remove();
  box.querySelector('.terminal-empty')?.remove();

  // Один и тот же текст подряд не плодит строки: у долгих процессов повтор
  // «ждём ответа Telegram» за минуту забивал весь экран.
  const last = box.lastElementChild;
  if (last && last.dataset.logText === message && last.dataset.logKind === kind) {
    const times = Number(last.dataset.logTimes || '1') + 1;
    last.dataset.logTimes = String(times);
    let badge = last.querySelector('.t-count');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 't-count';
      last.appendChild(badge);
    }
    badge.textContent = `×${times}`;
    return;
  }

  // Автоскролл только когда человек и так внизу: иначе лента уезжает из-под
  // курсора, пока он читает старую ошибку.
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;

  const line = document.createElement('div');
  line.className = `terminal-line ${meta.cls}`;
  line.dataset.logText = message;
  line.dataset.logKind = kind;
  line.dataset.logTimes = '1';

  const time = document.createElement('span');
  time.className = 't-ts';
  time.textContent = stamp(Date.now());
  line.appendChild(time);

  if (meta.tag) {
    const tag = document.createElement('span');
    tag.className = 't-tag';
    tag.textContent = meta.tag;
    line.appendChild(tag);
  }

  const body = document.createElement('span');
  body.className = 't-text';
  body.textContent = message;
  line.appendChild(body);

  box.appendChild(line);
  if (atBottom) box.scrollTop = box.scrollHeight;

  while (box.children.length > 300) box.firstElementChild.remove();
}

/** Пустой журнал с подсказкой, что тут вообще появится. */
export function resetLog(boxId, hint) {
  const box = document.getElementById(boxId);
  if (!box) return;
  box.innerHTML = '';
  const empty = document.createElement('div');
  empty.className = 'terminal-empty';
  empty.textContent = hint || 'Пока пусто — здесь появится ход процесса.';
  box.appendChild(empty);
}
