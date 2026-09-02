/** Настройки: ключи YouTube API и Telegram-аккаунты.
 *
 *  Всё берётся и сохраняется через бэкенд. Ничего не выдумываем:
 *  - ключи показываем только замаскированными (сервер иначе их не отдаёт);
 *  - расход квоты — наш счётчик по цене вызовов (у YouTube нет метода
 *    «сколько осталось»), об этом прямо написано в подсказке;
 *  - «настоящий» тариф/биллинг не показываем — в системе оплат нет.
 *
 *  Провайдер ИИ задаётся переменными окружения сервера и не редактируется из
 *  браузера. Продуктовых месячных лимитов у тарифа Talaria нет.
 */
import { api, esc, getToken } from './api.js';
import { toast, openModal, closeModal, confirmAction } from './ui.js?v=tal32';
import { currentTheme } from './theme.js?v=zephyr3';
import { renderQuotaHint } from './quota.js';

let _settings = null; // последний ответ GET /api/settings

/* ----------------------------- квота сайдбара ----------------------------- */

/** Обновить виджет квоты в сайдбаре из /api/quota. */
export async function refreshQuota() {
  const bar = document.getElementById('usageBar');
  const text = document.getElementById('usageText');
  const hint = document.getElementById('usageHint');
  if (!bar || !text || !getToken()) return;
  try {
    const q = await api('GET', '/api/quota');
    const pct =
      q.total_daily > 0 ? Math.min(100, Math.round((q.used_today / q.total_daily) * 100)) : 0;
    bar.style.width = pct + '%';
    text.textContent = `${q.used_today.toLocaleString('ru-RU')} / ${q.total_daily.toLocaleString('ru-RU')}`;
    renderQuotaHint(hint, q);
  } catch (e) {
    console.warn('Не удалось обновить квоту:', e.message);
  }
}

/* ------------------------------- настройки -------------------------------- */

/** Прочитать настройки и заполнить раздел. Лимит — только для чтения. */
export async function loadSettings() {
  if (!getToken()) return;
  try {
    _settings = await api('GET', '/api/settings');
  } catch (e) {
    console.warn('Не удалось загрузить настройки:', e.message);
    return;
  }
  const s = _settings;

  // Панель статуса ИИ убрана из настроек: она ничего не настраивала,
  // а иконка ИИ теперь висит в углу на каждой странице.
  paintReplyNotify(s.reply_notify !== false);
  paintSendWindow(s);
  await loadNotifyTargets();
  await loadReplyHours();
}

/* ---------------------- окно отправки ---------------------------------- */

/** Заполнить оба списка часов и показать текущее окно. Списки строим кодом:
 *  48 одинаковых <option> в разметке — это шум, который никто не читает. */
function paintSendWindow(s) {
  const on = s.send_window_enabled !== false;
  const button = document.getElementById('sendWindowToggle');
  if (button) {
    button.setAttribute('aria-checked', on ? 'true' : 'false');
    button.classList.toggle('on', on);
  }
  const sub = document.getElementById('sendWindowSub');
  if (sub) {
    sub.textContent = on
      ? 'Включено — ночью письма не уходят'
      : 'Выключено — рассылка идёт в любое время';
  }
  const start = Number(s.send_window_start ?? 10);
  const end = Number(s.send_window_end ?? 20);
  fillHours('sendWindowStart', start, on);
  fillHours('sendWindowEnd', end, on);
}

function fillHours(id, selected, enabled) {
  const select = document.getElementById(id);
  if (!select) return;
  select.innerHTML = Array.from({ length: 24 }, (_, hour) => {
    const label = `${String(hour).padStart(2, '0')}:00`;
    return `<option value="${hour}"${hour === selected ? ' selected' : ''}>${label}</option>`;
  }).join('');
  select.disabled = !enabled;
}

export async function toggleSendWindow() {
  const button = document.getElementById('sendWindowToggle');
  if (!button || button.disabled) return;
  const next = button.getAttribute('aria-checked') !== 'true';
  button.disabled = true;
  try {
    const fresh = await api('PATCH', '/api/settings', { send_window_enabled: next });
    _settings = { ..._settings, ...fresh };
    paintSendWindow(_settings);
    toast(
      next ? 'Окно отправки включено' : 'Окно отправки выключено',
      'success',
      next
        ? 'Ночью письма уходить не будут.'
        : 'Рассылка пойдёт в любое время суток — риск жалоб выше.',
    );
  } catch (e) {
    toast('Не удалось сохранить настройку', 'error', e.message || 'Повторите попытку.');
  } finally {
    button.disabled = false;
  }
}

/** Сохранить границы окна. Отправляем обе сразу: сервер проверяет их парой,
 *  и по одному полю «начало позже конца» не отловить. */
export async function saveSendWindow() {
  const start = Number(document.getElementById('sendWindowStart')?.value ?? 10);
  const end = Number(document.getElementById('sendWindowEnd')?.value ?? 20);
  try {
    const fresh = await api('PATCH', '/api/settings', {
      send_window_start: start,
      send_window_end: end,
    });
    _settings = { ..._settings, ...fresh };
    paintSendWindow(_settings);
    toast('Окно отправки сохранено', 'success', `Письма уходят с ${start}:00 до ${end}:00.`);
  } catch (e) {
    // Возвращаем поля к сохранённому состоянию: иначе на экране осталось бы
    // окно, которого на сервере нет.
    paintSendWindow(_settings || {});
    toast('Окно не сохранено', 'error', e.message || 'Начало должно быть раньше конца.');
  }
}

/** Показатель «когда чаще отвечают» — по местному времени получателя. */
async function loadReplyHours() {
  const note = document.getElementById('replyHoursNote');
  const bars = document.getElementById('replyHoursBars');
  if (!note || !bars) return;
  let data;
  try {
    data = await api('GET', '/api/campaigns/reply-hours');
  } catch (e) {
    note.textContent = 'Показатель недоступен';
    return;
  }
  note.textContent = data.note || '';
  const peak = Math.max(1, ...data.hours.map((h) => h.sent));
  // Рисуем по объёму отправок, а долю ответов показываем цветом и подсказкой:
  // столбик высотой в один ответ из одного письма иначе выглядел бы рекордом.
  bars.innerHTML = data.hours
    .map((h) => {
      const height = Math.round((h.sent / peak) * 100);
      const best = data.best_hours.includes(h.hour);
      const tip = h.sent
        ? `${h.hour}:00 — писем ${h.sent}, ответов ${h.replied} (${h.rate}%)`
        : `${h.hour}:00 — отправок не было`;
      return (
        `<i class="reply-hour${best ? ' best' : ''}" style="--h:${height}%" ` +
        `data-tip="${esc(tip)}"></i>`
      );
    })
    .join('');
}

/* ------------- главный аккаунт, куда приходят уведомления ---------------- */

const TARGET_KIND = { login: 'аккаунт входа', sender: 'подключённый' };

/** Заполнить список аккаунтов и подписать состояние проверки. */
async function loadNotifyTargets() {
  const select = document.getElementById('notifyTargetSelect');
  if (!select) return;
  let targets = [];
  try {
    targets = await api('GET', '/api/settings/notify-targets');
  } catch (e) {
    console.warn('Не удалось загрузить список аккаунтов:', e.message);
    return;
  }
  select.innerHTML = targets
    .map(
      (t) =>
        `<option value="${esc(String(t.tg_id))}"${t.selected ? ' selected' : ''}>` +
        `${esc(t.label)} — ${esc(TARGET_KIND[t.kind] || t.kind)}</option>`,
    )
    .join('');
  // Выбирать не из чего — прятать поле честнее, чем показывать список из одной
  // строки, который ничего не решает.
  const wrap = select.closest('.notify-target');
  if (wrap) wrap.hidden = targets.length < 2;
  paintTargetHint();
}

/** Подпись под списком: проверяли ли доставку и чем закончилось. */
function paintTargetHint(override) {
  const hint = document.getElementById('notifyTargetHint');
  if (!hint) return;
  if (override) {
    hint.textContent = override;
    return;
  }
  const ok = _settings?.notify_target_ok;
  hint.textContent =
    ok === true
      ? 'Проверено — сообщения доходят на этот аккаунт'
      : ok === false
        ? 'Проверка не прошла: откройте бота с этого аккаунта и нажмите Start'
        : 'Уведомления приходят только сюда, а не на каждый подключённый аккаунт';
}

/** Сохранить выбранный аккаунт. */
export async function saveNotifyTarget() {
  const select = document.getElementById('notifyTargetSelect');
  if (!select) return;
  const chosen = parseInt(select.value, 10);
  if (!Number.isFinite(chosen)) return;
  try {
    const fresh = await api('PATCH', '/api/settings', { notify_target_tg_id: chosen });
    _settings = { ..._settings, ...fresh };
    paintTargetHint();
    toast(
      'Главный аккаунт сохранён',
      'success',
      'Проверьте доставку — бот пишет только тем, кто нажал у него Start.',
    );
  } catch (e) {
    toast('Не удалось сохранить аккаунт', 'error', e.message || 'Повторите попытку.');
    await loadNotifyTargets();
  }
}

/** Отправить проверочное сообщение на выбранный аккаунт. */
export async function testNotifyTarget() {
  const button = document.getElementById('notifyTargetTest');
  if (button?.disabled) return;
  if (button) {
    button.disabled = true;
    button.textContent = 'Проверяем…';
  }
  try {
    const result = await api('POST', '/api/settings/notify-target/test', {});
    if (_settings) _settings.notify_target_ok = result.delivered;
    paintTargetHint(result.detail);
    toast(
      result.delivered ? 'Сообщение доставлено' : 'Сообщение не дошло',
      result.delivered ? 'success' : 'warn',
      result.detail,
    );
  } catch (e) {
    toast('Не удалось выполнить проверку', 'error', e.message || 'Повторите попытку.');
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = 'Проверить';
    }
  }
}

/** Отрисовать переключатель уведомлений об ответе лида. */
function paintReplyNotify(on) {
  const button = document.getElementById('replyNotifyToggle');
  if (button) {
    button.setAttribute('aria-checked', on ? 'true' : 'false');
    button.classList.toggle('on', on);
  }
  const sub = document.getElementById('replyNotifySub');
  if (sub) {
    sub.textContent = on
      ? 'Включено — приходит сразу, как лид ответил'
      : 'Выключено — сообщений не будет, ответы смотрите в кабинете';
  }
}

/** Переключить уведомление об ответе лида.
 *
 * Рисуем сразу, не дожидаясь сервера: переключатель должен отзываться мгновенно.
 * Если запрос не прошёл — возвращаем на место и говорим об этом, иначе человек
 * будет уверен, что настройка сохранилась.
 */
export async function toggleReplyNotify() {
  const button = document.getElementById('replyNotifyToggle');
  if (!button || button.disabled) return;
  const next = button.getAttribute('aria-checked') !== 'true';
  paintReplyNotify(next);
  button.disabled = true;
  try {
    const fresh = await api('PATCH', '/api/settings', { reply_notify: next });
    _settings = { ..._settings, ...fresh };
    paintReplyNotify(fresh.reply_notify !== false);
    toast(
      next ? 'Уведомления об ответах включены' : 'Уведомления об ответах выключены',
      'success',
      next
        ? 'Сообщение придёт сразу, как лид ответит.'
        : 'Ответы по-прежнему видны в кабинете и в ленте событий.',
    );
  } catch (e) {
    paintReplyNotify(!next);
    toast('Не удалось сохранить настройку', 'error', e.message || 'Повторите попытку.');
  } finally {
    button.disabled = false;
  }
}

function intVal(id, fallback) {
  const n = parseInt((document.getElementById(id)?.value || '').replace(/\D/g, ''), 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

/* --------------------------- ключи YouTube API ---------------------------- */

const KEY_STATUS = {
  ok: { label: 'Работает', cls: 'ok' },
  quota_exceeded: { label: 'Квота исчерпана', cls: 'warn' },
  invalid: { label: 'Недействителен', cls: 'err' },
  error: { label: 'Ошибка проверки', cls: 'err' },
  unchecked: { label: 'Не проверен', cls: 'muted' },
};

function keyRowHtml(k) {
  const st = KEY_STATUS[k.status] || KEY_STATUS.unchecked;
  const checked = k.last_checked_at
    ? new Date(k.last_checked_at).toLocaleString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—';
  const detail = k.status_detail ? `<div class="key-detail">${esc(k.status_detail)}</div>` : '';
  return `<div class="key-row" data-key-id="${k.id}">
    <div class="key-main">
      <div class="key-top">
        <span class="key-label">${esc(k.label || 'Без названия')}</span>
        <span class="key-status ${st.cls}">${st.label}</span>
        ${k.enabled ? '' : '<span class="key-status muted">Выключен</span>'}
      </div>
      <div class="key-sub"><code>${esc(k.key_masked)}</code></div>
      <div class="key-sub">Сегодня: ${k.used_today.toLocaleString('ru-RU')} / ${(k.daily_quota || 0).toLocaleString('ru-RU')} · осталось ${k.left_today.toLocaleString('ru-RU')} · проверен ${checked}</div>
      ${detail}
    </div>
    <div class="key-actions">
      <button class="btn-mini" data-key-check="${k.id}">Проверить</button>
      <button class="btn-mini ghost" data-key-del="${k.id}">Удалить</button>
    </div>
  </div>`;
}

/** Загрузить и отрисовать список ключей (GET /api/keys). Обновляет и сайдбар. */
export async function loadKeys() {
  const wrap = document.getElementById('keysList');
  if (!wrap || !getToken()) return;
  try {
    const data = await api('GET', '/api/keys');
    renderKeys(data);
  } catch (e) {
    wrap.innerHTML = `<div class="dd-empty">Не удалось загрузить ключи: ${esc(e.message)}</div>`;
  }
}

function renderKeys(data) {
  const wrap = document.getElementById('keysList');
  if (!wrap) return;
  const keys = data.keys || [];
  wrap.innerHTML = keys.length
    ? keys.map(keyRowHtml).join('')
    : '<div class="dd-empty">Ключей пока нет. Добавьте первый — без него парсинг YouTube не работает.</div>';

  // Сводка квоты под списком + сайдбар.
  const q = data.quota;
  const sum = document.getElementById('keysQuota');
  if (sum && q) {
    sum.textContent = `Итого за сегодня: ${q.used_today.toLocaleString('ru-RU')} / ${q.total_daily.toLocaleString('ru-RU')} · ключей ${q.keys_enabled}/${q.keys_total} · проектов ${q.projects}`;
  }
  const note = document.getElementById('keysNote');
  if (note && q) note.textContent = q.source_note || '';
  if (q) applyQuota(q);
}

/** Отрисовать сайдбар из уже полученной сводки квоты (без лишнего запроса). */
function applyQuota(q) {
  const bar = document.getElementById('usageBar');
  const text = document.getElementById('usageText');
  const hint = document.getElementById('usageHint');
  if (!bar || !text) return;
  const pct =
    q.total_daily > 0 ? Math.min(100, Math.round((q.used_today / q.total_daily) * 100)) : 0;
  bar.style.width = pct + '%';
  // Без ключей «0 / 0» выглядит как поломка — так и пишем, что ключей нет.
  text.textContent =
    q.total_daily > 0
      ? `${q.used_today.toLocaleString('ru-RU')} / ${q.total_daily.toLocaleString('ru-RU')}`
      : 'ключей нет';
  renderQuotaHint(hint, q);
}

/** Добавить ключ (POST /api/keys). */
export async function addKey() {
  if (!getToken()) {
    toast('Сначала войдите', 'warn');
    return;
  }
  const keyInput = document.getElementById('newKeyValue');
  const key = (keyInput?.value || '').trim();
  if (!key) {
    toast('Вставьте ключ', 'warn');
    return;
  }
  // Лёгкая проверка формата до похода на сервер: ключ Google API — это ровно
  // 39 символов, начинающихся на «AIza». Так человек сразу видит, что вставил
  // не тот текст (например, Client ID или ссылку из консоли), и не ждёт ответа
  // «недействительный ключ» после запроса к YouTube.
  if (!/^AIza[0-9A-Za-z_-]{35}$/.test(key)) {
    toast(
      'Это не похоже на ключ YouTube API: он начинается с «AIza» и содержит 39 символов без пробелов',
      'warn',
    );
    return;
  }
  const body = {
    key,
    label: (document.getElementById('newKeyLabel')?.value || '').trim(),

    daily_quota: intVal('newKeyQuota', 10000),
  };
  try {
    const data = await api('POST', '/api/keys', body);
    ['newKeyValue', 'newKeyLabel'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    renderKeys(data);
    toast('Ключ добавлен', 'success');
  } catch (e) {
    toast(`Не удалось добавить ключ: ${e.message}`, 'warn');
  }
}

/** Проверить ключ настоящим запросом к YouTube (POST /api/keys/{id}/check). */
async function checkKey(id) {
  const btn = document.querySelector(`[data-key-check="${id}"]`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Проверка…';
  }
  try {
    const data = await api('POST', `/api/keys/${id}/check`);
    renderKeys(data);
    toast('Ключ проверен', 'info');
  } catch (e) {
    toast(`Проверка не удалась: ${e.message}`, 'warn');
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Проверить';
    }
  }
}

/** Удалить ключ (DELETE /api/keys/{id}). */
async function removeKey(id) {
  if (!(await confirmAction('Удалить этот ключ? Парсинг с ним прекратится.'))) return;
  try {
    const data = await api('DELETE', `/api/keys/${id}`);
    renderKeys(data);
    toast('Ключ удалён', 'success');
  } catch (e) {
    toast(`Не удалось удалить: ${e.message}`, 'warn');
  }
}

/* ------------------- Telegram-аккаунты и прогрев ------------------------- */
// Пароли/2FA не хранятся в БД; здесь план, пределы отправки и назначение слота.

const WARMUP_STATUS = {
  active: { label: 'Идёт', cls: 'ok' },
  paused: { label: 'На паузе', cls: 'warn' },
  done: { label: 'Завершён', cls: 'muted' },
};

const ACCOUNT_STATE = {
  ready: { label: 'В ротации', cls: 'ok' },
  cooldown: { label: 'Пауза Telegram', cls: 'warn' },
  limited: { label: 'Ограничен', cls: 'err' },
  banned: { label: 'Заблокирован', cls: 'err' },
  reauth: { label: 'Нужен новый вход', cls: 'warn' },
};

const SPAMBOT_STATE = {
  unknown: 'не проверен',
  clear: 'ограничений нет',
  limited: 'есть ограничение',
  appeal_available: 'можно обжаловать',
  appeal_sent: 'запрос отправлен',
  error: 'ошибка проверки',
};

let _warmupTicker = null;
let pendingTgAccountId = null;

function durationLabel(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  if (days) return `${days} д ${hours} ч`;
  if (hours) return `${hours} ч ${minutes} мин`;
  if (minutes) return `${minutes} мин ${rest} с`;
  return `${rest} с`;
}

function updateWarmupCountdowns() {
  document.querySelectorAll('[data-cooldown-until]').forEach((el) => {
    const until = new Date(el.dataset.cooldownUntil || '').getTime();
    if (!Number.isFinite(until)) return;
    const left = Math.max(0, Math.ceil((until - Date.now()) / 1000));
    el.textContent = left
      ? `Вернётся в ротацию через ${durationLabel(left)}`
      : 'Пауза завершена · обновите список';
  });
}

/** Загрузить и отрисовать аккаунты прогрева (GET /api/warmup). */
export async function loadWarmup() {
  const wrap = document.getElementById('warmupList');
  if (!wrap) return;
  if (!getToken()) {
    wrap.innerHTML =
      '<div class="dd-empty">Войдите через Telegram, чтобы увидеть подключённые аккаунты.</div>';
    renderAccountHealth([]);
    return;
  }
  try {
    const data = await api('GET', '/api/warmup');
    renderWarmup(data);
  } catch (e) {
    wrap.innerHTML = `<div class="dd-empty">Не удалось загрузить аккаунты: ${esc(e.message)}</div>`;
    renderAccountHealthError(e.message);
  }
}

/** Инициалы для кружка: из ника Telegram, иначе из названия слота. */
function accInitials(a) {
  const src = String(a.tg_username || a.label || '?')
    .replace(/^@/, '')
    .trim();
  const parts = src
    .split(/[\s_.\-]+/)
    .filter(Boolean)
    .slice(0, 2);
  return (parts.map((x) => x[0]).join('') || '?').toUpperCase();
}

/** Карточка аккаунта: профиль из Telegram и один переключатель прогрева. */
function warmupCardHtml(a) {
  const on = a.status === 'active';
  const username = a.tg_username ? '@' + a.tg_username : '';
  const identity = [username, a.tg_phone || ''].filter(Boolean).join(' · ');
  const state = ACCOUNT_STATE[a.operational_status] || ACCOUNT_STATE.ready;
  const sub = a.connected
    ? a.operational_status === 'ready'
      ? on
        ? 'Прогрев включён · параметры назначены автоматически'
        : 'Аккаунт подключён и участвует в ротации'
      : 'Аккаунт временно исключён из рассылки и верификации'
    : 'нажмите «Подключить» и войдите по номеру телефона';
  const health = Math.max(0, Math.min(100, Number(a.health_score || 0)));
  const healthClass = health >= 85 ? 'ok' : health >= 55 ? 'warn' : 'err';
  const healthTip = (a.health_reasons || []).join(' · ') || a.ready_reason || '';
  const cooldown = a.cooldown_until
    ? `<div class="acc-cooldown" data-cooldown-until="${esc(a.cooldown_until)}">Вернётся в ротацию через ${esc(durationLabel(a.cooldown_seconds))}</div>`
    : '';
  const lastError = a.last_tg_error
    ? `<div class="acc-last-error" title="${esc(a.last_tg_error)}">${esc(a.last_tg_error_code || 'Telegram')}: ${esc(a.last_tg_error)}</div>`
    : '';
  const spamDetail = a.spambot_detail ? ` title="${esc(a.spambot_detail)}"` : '';
  const appeal = ['limited', 'appeal_available', 'appeal_sent'].includes(a.spambot_status)
    ? `<button class="btn-mini ghost" data-wm-appeal="${a.id}">Обжаловать</button>`
    : '';
  const primaryWarning = a.is_primary
    ? '<div class="acc-primary-warning"><b>Не используйте основной личный аккаунт для холодных рассылок.</b><span>Подключите отдельный рабочий аккаунт. Уведомления об ответах всё равно придут в Telegram через бота Talaria.</span></div>'
    : '';

  return `<div class="acc-card" data-wm-id="${a.id}">
    <div class="acc-av">${esc(accInitials(a))}</div>
    <div class="acc-body">
      <div class="acc-name">${esc(a.label)}${a.is_primary ? '<span class="acc-primary">Аккаунт входа</span>' : ''}<span class="acc-state ${state.cls}">${esc(a.connected ? state.label : 'Не подключён')}</span><span class="acc-dot ${a.connected ? 'on' : ''}"></span></div>
      <div class="acc-title">${esc(identity || (a.connected ? 'Telegram подключён' : 'Профиль появится после входа'))}</div>
      <div class="acc-sub">${esc(sub)}</div>
      ${cooldown}
      ${lastError}
      ${primaryWarning}
      ${on ? '<div class="acc-auto-note">Автоматические параметры прогрева применены</div>' : ''}
      <div class="acc-stats">
        <span><b>${Number(a.sent_today || 0)}</b> отправлено сегодня</span>
        <span><b>${Number(a.verified_today || 0)}</b> проверено</span>
        <span><b>${Number(a.daily_left || 0)}</b> осталось</span>
        <span><b>${Number(a.failed_today || 0)}</b> ошибок</span>
      </div>
      <div class="acc-health-row">
        <span class="account-health ${healthClass}" title="${esc(healthTip)}">Здоровье · ${health}/100</span>
        <span class="account-health ${Number(a.unread_growth || 0) >= 10 ? 'warn' : 'ok'}" title="Фактический снимок непрочитанных диалогов из Telegram">Непрочитано · ${Number(a.unread_count || 0)}</span>
        ${a.spambot_status && a.spambot_status !== 'unknown' ? `<span class="account-health ${a.spambot_status === 'clear' ? 'ok' : 'err'}"${spamDetail}>Официальная проверка · ${esc(SPAMBOT_STATE[a.spambot_status] || a.spambot_status)}</span>` : ''}
      </div>
    </div>
    <div class="acc-actions">
      <div class="warmup-control">
        <span><b>Прогрев</b><small>${on ? 'Настроен автоматически' : 'Включить при необходимости'}</small></span>
        <label class="switch" data-tip="Параметры прогрева настроятся автоматически">
          <input type="checkbox" data-wm-toggle="${a.id}"${on ? ' checked' : ''}${a.connected ? '' : ' disabled'}>
          <span class="switch-k"></span>
        </label>
      </div>
      <div class="warmup-control sending-control">
        <span><b>Рассылки</b><small>${a.sending_enabled === false ? 'Аккаунт исключён из пула' : 'Аккаунт участвует в ротации'}</small></span>
        <label class="switch" data-tip="Включить или исключить этот аккаунт из отправки">
          <input type="checkbox" data-wm-sending="${a.id}"${a.sending_enabled === false ? '' : ' checked'}${a.connected ? '' : ' disabled'}>
          <span class="switch-k"></span>
        </label>
      </div>
      <div class="acc-action-buttons">
        ${a.connected ? `<button class="btn-mini ghost" data-wm-spambot="${a.id}">Проверить ограничения</button>${appeal}` : ''}
        <button class="btn-mini" data-wm-connect="${a.id}">${a.connected ? 'Переподключить' : a.is_primary ? 'Подключить для работы' : 'Подключить'}</button>
        ${a.is_primary ? '' : `<button class="btn-mini ghost" data-wm-del="${a.id}">Удалить</button>`}
      </div>
    </div>
  </div>`;
}

function renderWarmup(data) {
  const wrap = document.getElementById('warmupList');
  if (!wrap) return;
  const accounts = data.accounts || [];
  wrap.innerHTML = accounts.length
    ? accounts.map(warmupCardHtml).join('')
    : '<div class="dd-empty">Аккаунтов пока нет. Нажмите «Добавить аккаунт» и пройдите вход по номеру телефона.</div>';
  const disc = document.getElementById('warmupDisclaimer');
  if (disc) disc.textContent = data.disclaimer || '';
  const count = document.querySelector('.health-management-count');
  if (count)
    count.textContent = `${accounts.length} ${accounts.length === 1 ? 'аккаунт' : accounts.length > 1 && accounts.length < 5 ? 'аккаунта' : 'аккаунтов'}`;
  renderAccountHealth(accounts);
  updateWarmupCountdowns();
}

/** Добавить аккаунт одной кнопкой; профиль приедет из Telegram после входа. */
export async function addWarmupAccount() {
  if (!getToken()) {
    toast('Сначала войдите', 'warn');
    return;
  }
  try {
    const acc = await api('POST', '/api/warmup/quick', {});
    await loadWarmup();
    // Сразу открываем окно входа: отдельный шаг «найди слот и нажми
    // Подключить» люди пропускали, и слот оставался пустым.
    if (acc && acc.id) await connectTgAccount(acc.id, true);
  } catch (e) {
    toast(`Не удалось добавить: ${e.message}`, 'warn');
  }
}

/** Переключатель прогрева (POST /api/warmup/{id}/toggle). */
async function toggleWarmup(id, on) {
  try {
    await api('POST', `/api/warmup/${id}/toggle`, { on });
    toast(on ? 'Прогрев включён' : 'Прогрев выключен', 'success');
  } catch (e) {
    toast(`Не удалось переключить: ${e.message}`, 'warn');
  }
  // Перерисовываем всегда: если сервер отказал, галка вернётся на место.
  loadWarmup();
}

/** Отдельный от прогрева переключатель участия аккаунта в рассылках. */
async function toggleSending(id, on) {
  try {
    await api('PATCH', `/api/warmup/${id}`, { sending_enabled: on });
    toast(on ? 'Аккаунт добавлен в ротацию' : 'Рассылки с аккаунта выключены', 'success');
  } catch (e) {
    toast(`Не удалось изменить ротацию: ${e.message}`, 'warn');
  }
  loadWarmup();
}

/** Удалить аккаунт прогрева вместе с журналом (DELETE /api/warmup/{id}). */
async function removeWarmup(id) {
  if (!(await confirmAction('Удалить аккаунт и его план прогрева?'))) return;
  try {
    await api('DELETE', `/api/warmup/${id}`);
    toast('Аккаунт удалён', 'success');
    loadWarmup();
  } catch (e) {
    toast(`Не удалось удалить: ${e.message}`, 'warn');
  }
}

async function checkSpamBot(id) {
  const btn = document.querySelector(`[data-wm-spambot="${id}"]`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Проверяем…';
  }
  try {
    const result = await api('POST', `/api/warmup/${id}/spambot/check`, {});
    toast(
      result.status === 'clear' ? 'SpamBot: ограничений нет' : 'SpamBot сообщил об ограничении',
      result.status === 'clear' ? 'success' : 'warn',
    );
  } catch (e) {
    toast(`Проверка SpamBot не удалась: ${e.message}`, 'warn');
  }
  loadWarmup();
}

async function appealSpamBot(id) {
  if (
    !(await confirmAction(
      'Отправить SpamBot запрос на пересмотр ограничения? Talaria нажмёт только доступную кнопку обжалования; дополнительные вопросы нужно подтвердить лично.',
    ))
  )
    return;
  try {
    const result = await api('POST', `/api/warmup/${id}/spambot/appeal`, {});
    if (result.action === 'manual') {
      const manualUrl =
        String(result.manual_url || '')
          .toLowerCase()
          .replace(/\/$/, '') === 'https://t.me/spambot'
          ? result.manual_url
          : 'https://t.me/SpamBot';
      window.open(manualUrl, '_blank', 'noopener,noreferrer');
      toast('SpamBot требует продолжить диалог лично', 'info');
    } else {
      toast(
        result.status === 'clear' ? 'Ограничений уже нет' : 'Запрос на пересмотр передан SpamBot',
        'success',
      );
    }
  } catch (e) {
    toast(`Обжалование не удалось: ${e.message}`, 'warn');
  }
  loadWarmup();
}

/* ------------------------------- сессии ------------------------------------ */
// Блок удалён целиком. Он ходил в GET/DELETE /api/auth/sessions, которых
// в бэкенде никогда не было: в auth.py есть только /auth/telegram, /me и
// /auth/logout. Миграция таблицу user_sessions создала, а роутер к ней — нет,
// поэтому панель всегда показывала «Not Found». Если сессии понадобятся —
// сначала пишется роутер, потом возвращается интерфейс.

/** Подключить (или переподключить) Telegram для слота — прямо в настройках.
 *
 *  Окно входа получает короткий подписанный билет и открывается только в
 *  модалке настроек. Диалоги и история сообщений в сайт не загружаются.
 */
export async function connectTgAccount(id, pending = false) {
  const host = document.getElementById('tgConnectWrap');
  const modal = document.getElementById('modalTgConnect');
  if (!host || !modal) return;
  pendingTgAccountId = pending ? Number(id) : null;
  host.innerHTML = '<div class="tg-connect-placeholder">Окно входа загружается…</div>';
  openModal('modalTgConnect');
  try {
    const res = await api(
      'GET',
      `/api/telegram-accounts/ticket?account_id=${encodeURIComponent(id)}`,
    );
    const params = new URLSearchParams({ t: res.ticket, theme: currentTheme() });
    const frame = document.createElement('iframe');
    frame.id = 'tgConnectFrame';
    frame.src = '/tg-connect/?' + params.toString();
    frame.title = 'Подключение Telegram';
    host.innerHTML = '';
    host.appendChild(frame);
  } catch (e) {
    host.innerHTML = `<div class="tg-connect-placeholder">${esc(e.message)}</div>`;
  }
}

/** После входа закрываем окно и перечитываем список: появится @ник. */
export function finishTgConnect() {
  pendingTgAccountId = null;
  closeModal('modalTgConnect');
  const wrap = document.getElementById('tgConnectWrap');
  if (wrap) wrap.innerHTML = '';
  loadWarmup();
}

async function discardPendingTgAccount() {
  const id = pendingTgAccountId;
  pendingTgAccountId = null;
  if (!id) return;
  try {
    await api('DELETE', `/api/warmup/${id}/pending`);
  } catch (e) {
    // 409 означает, что identity callback уже успел подключить аккаунт. Это не
    // ошибка отмены: перечитываем список и сохраняем реальную карточку.
    if (!String(e.message || '').includes('уже подключён')) {
      toast(`Не удалось убрать незавершённый аккаунт: ${e.message}`, 'warn');
    }
  }
  loadWarmup();
}

/** Закрыть форму подключения; новый пустой слот при этом не сохраняется. */
export function cancelTgConnect() {
  const id = pendingTgAccountId;
  pendingTgAccountId = null;
  closeModal('modalTgConnect');
  const wrap = document.getElementById('tgConnectWrap');
  if (wrap) wrap.innerHTML = '';
  if (id) {
    pendingTgAccountId = id;
    discardPendingTgAccount();
  } else {
    loadWarmup();
  }
}

/* --------------------- центр здоровья: только реальные данные API ---------------- */

let healthAccounts = new Map();

function accountName(a) {
  return a.tg_username ? `@${a.tg_username}` : a.label || 'Telegram-аккаунт';
}

function formatAccountTime(value) {
  if (!value) return 'нет данных';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return 'нет данных';
  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function rotationReady(a) {
  return (
    !!a.connected && a.sending_enabled !== false && (a.operational_status || 'ready') === 'ready'
  );
}

function accountStatusView(a) {
  const status = a.operational_status || 'ready';
  if (!a.connected)
    return { label: 'Не подключён', reason: 'Telegram-сессия не подключена', cls: 'attention' };
  if (status === 'cooldown') {
    const left = a.cooldown_seconds
      ? `Осталось ${durationLabel(a.cooldown_seconds)}`
      : 'Дождитесь окончания паузы';
    return { label: 'Пауза Telegram', reason: a.last_tg_error || left, cls: 'attention' };
  }
  if (status === 'limited')
    return {
      label: 'Ограничен',
      reason: a.spambot_detail || a.last_tg_error || 'Telegram ограничил отправку',
      cls: 'attention',
    };
  if (status === 'banned')
    return {
      label: 'Заблокирован',
      reason: a.last_tg_error || 'Сессия исключена из ротации',
      cls: 'attention',
    };
  if (status === 'reauth')
    return {
      label: 'Нужен вход',
      reason: a.last_tg_error || 'Переподключите Telegram-сессию',
      cls: 'attention',
    };
  if (a.sending_enabled === false)
    return {
      label: 'Отправка выключена',
      reason: 'Аккаунт исключён из ротации настройкой',
      cls: 'attention',
    };
  const reason = a.safety_checked_at
    ? `Авторизация подтверждена · непрочитано ${Number(a.unread_count || 0)}`
    : a.ready_reason || 'Сессия подтверждена, safety-снимок обновляется';
  return { label: 'В ротации', reason, cls: 'ready' };
}

function healthAccountRowHtml(a) {
  const view = accountStatusView(a);
  const ready = rotationReady(a);
  const sent = Math.max(0, Number(a.sent_today || 0));
  const limit = Math.max(0, Number(a.daily_send_limit || sent + Number(a.daily_left || 0)));
  const used = limit ? Math.min(100, Math.round((sent / limit) * 100)) : 0;
  const identity = a.is_primary ? 'Аккаунт входа' : 'Дополнительный аккаунт';
  const plan =
    a.status === 'active'
      ? `прогрев: день ${Number(a.current_day || 0)} из ${Number(a.plan_days || 0)}`
      : a.status === 'done'
        ? 'прогрев завершён'
        : 'прогрев на паузе';
  return `<article class="health-account-row${ready ? '' : ' needs-attention'}" data-health-account data-state="${ready ? 'ready' : 'attention'}">
    <div class="health-account-id"><span class="health-avatar">${esc(accInitials(a))}</span><div><b>${esc(accountName(a))}</b><small>${esc(identity)} · ${esc(plan)}</small></div></div>
    <div class="health-state"><span class="health-pill ${view.cls}"><i></i> ${esc(view.label)}</span><small title="${esc(view.reason)}">${esc(view.reason)}</small></div>
    <div class="health-limit"><span><b>${sent}</b> / ${limit || '—'} сегодня</span><i><b style="width:${used}%"></b></i></div>
    <div class="health-account-stats"><span><b>${Number(a.failed_today || 0)}</b> ошибок</span><span><b>${Number(a.unread_count || 0)}</b> непрочитано</span><span><b>${esc(formatAccountTime(a.connected_at))}</b> сессия</span></div>
    <button type="button" class="health-row-menu" aria-label="Подробнее" data-health-details="${Number(a.id)}">•••</button>
  </article>`;
}

function recommendationHtml(item) {
  const action = `<button type="button" data-health-manage="${Number(item.id)}">Открыть аккаунт</button>`;
  return `<article class="health-recommendation${item.important ? ' important' : ''}" data-health-recommendation>
    <span class="recommend-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3 5 6v5c0 4.7 2.8 8.2 7 10 4.2-1.8 7-5.3 7-10V6l-7-3Z"/><path d="m9 12 2 2 4-4"/></svg></span>
    <div><b>${esc(item.title)}</b><p>${esc(item.text)}</p>${action}</div>
  </article>`;
}

function buildHealthRecommendations(accounts) {
  const items = [];
  accounts.forEach((a) => {
    const name = accountName(a);
    const view = accountStatusView(a);
    if (!a.connected) {
      items.push({
        id: a.id,
        title: `${name}: подключите Telegram`,
        text: 'Без активной пользовательской сессии отправка и проверка контактов недоступны.',
        important: true,
      });
      return;
    }
    if ((a.operational_status || 'ready') !== 'ready') {
      items.push({
        id: a.id,
        title: `${name}: ${view.label.toLowerCase()}`,
        text: view.reason,
        important: true,
      });
      return;
    }
    if (a.sending_enabled === false) {
      items.push({
        id: a.id,
        title: `${name}: отправка выключена`,
        text: 'Аккаунт подключён, но сервер не включает его в пул отправителей.',
      });
    }
    if (Number(a.unread_growth || 0) >= 10) {
      items.push({
        id: a.id,
        title: `${name}: автопауза из-за непрочитанных`,
        text: `Рост непрочитанных диалогов: +${Number(a.unread_growth || 0)}. Отправка возобновится после контрольной паузы.`,
        important: true,
      });
    }
    if (Number(a.consecutive_errors || 0) > 0 || Number(a.failed_today || 0) > 0) {
      items.push({
        id: a.id,
        title: `${name}: проверьте ошибки`,
        text: `Ошибок подряд: ${Number(a.consecutive_errors || 0)} · сегодня: ${Number(a.failed_today || 0)}. ${a.last_tg_error || 'Откройте карточку для подробностей.'}`,
        important: true,
      });
    }
  });
  return items.slice(0, 4);
}

function applyHealthFilter() {
  const center = document.getElementById('accountHealthCenter');
  if (!center) return;
  const state = center.querySelector('[data-health-filter].active')?.dataset.healthFilter || 'all';
  center.querySelectorAll('[data-health-account]').forEach((row) => {
    row.hidden = state !== 'all' && row.dataset.state !== state;
  });
}

function renderAccountHealth(accounts) {
  healthAccounts = new Map(accounts.map((a) => [Number(a.id), a]));
  const list = document.getElementById('healthAccountList');
  if (list) {
    list.innerHTML = accounts.length
      ? accounts.map(healthAccountRowHtml).join('')
      : '<div class="dd-empty">Подключённых аккаунтов пока нет. Добавьте Telegram-аккаунт ниже — выдуманные отправители здесь не показываются.</div>';
  }

  const ready = accounts.filter(rotationReady).length;
  const attention = Math.max(0, accounts.length - ready);
  const score = accounts.length
    ? Math.round(
        accounts.reduce((sum, a) => sum + Number(a.health_score || 0), 0) / accounts.length,
      )
    : 0;
  const sent = accounts.reduce((sum, a) => sum + Number(a.sent_today || 0), 0);
  const limit = accounts.reduce((sum, a) => sum + Number(a.daily_send_limit || 0), 0);
  const failed = accounts.reduce((sum, a) => sum + Number(a.failed_today || 0), 0);
  const verified = accounts.reduce((sum, a) => sum + Number(a.verified_today || 0), 0);
  const put = (id, value) => {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
  };
  const ring = document.getElementById('healthScoreRing');
  if (ring) {
    ring.style.setProperty('--score', String(score));
    // Цвет круга дублирует вывод словами: зелёный — пул готов, жёлтый — есть
    // что поправить, красный — пул не настроен или всё плохо.
    ring.classList.remove('is-ok', 'is-warn', 'is-bad');
    ring.classList.add(
      !accounts.length || score < 40 ? 'is-bad' : score < 75 ? 'is-warn' : 'is-ok',
    );
  }
  put('healthScoreValue', String(score));
  put(
    'healthScoreTitle',
    !accounts.length
      ? 'Пул не настроен'
      : attention
        ? `${attention} требуют внимания`
        : 'Пул готов к работе',
  );
  put(
    'healthScoreSummary',
    !accounts.length
      ? 'Добавьте Telegram-аккаунт. После подключения здесь появятся авторизация, паузы, ошибки и непрочитанные диалоги.'
      : `Сводка по ${accounts.length} аккаунтам: авторизация, возраст сессии, лимиты, ошибки, паузы и непрочитанные.`,
  );
  put('healthReadyValue', `${ready} из ${accounts.length}`);
  put(
    'healthReadyMeta',
    accounts.length ? `${Math.round((ready / accounts.length) * 100)}% пула` : 'аккаунтов нет',
  );
  put('healthSentValue', sent.toLocaleString('ru-RU'));
  put('healthSentMeta', `лимит пула: ${limit.toLocaleString('ru-RU')}`);
  put(
    'healthErrorsValue',
    `${failed.toLocaleString('ru-RU')} ${failed === 1 ? 'ошибка' : 'ошибок'}`,
  );
  put('healthErrorsMeta', `${verified.toLocaleString('ru-RU')} проверок контактов`);

  const recommendations = buildHealthRecommendations(accounts);
  put('healthRecommendationCount', String(recommendations.length));
  const recommendationBox = document.getElementById('healthRecommendations');
  if (recommendationBox) {
    recommendationBox.innerHTML = recommendations.length
      ? recommendations.map(recommendationHtml).join('')
      : `<div class="dd-empty">${accounts.length ? 'По данным Talaria действий сейчас не требуется.' : 'Сначала добавьте Telegram-аккаунт.'}</div>`;
  }

  const chart = document.getElementById('healthLoadChart');
  if (chart) {
    const shown = accounts.slice(0, 7);
    chart.style.gridTemplateColumns = '';
    chart.innerHTML = shown.length
      ? shown
          .map((a, index) => {
            const sentToday = Number(a.sent_today || 0);
            const dailyLimit = Number(a.daily_send_limit || 0);
            const used = dailyLimit
              ? Math.max(0, Math.min(100, Math.round((sentToday / dailyLimit) * 100)))
              : 0;
            const name = esc(accountName(a));
            return `<div class="health-load-row${index === 0 ? ' today' : ''}" title="${name}: ${sentToday} / ${dailyLimit}"><div class="health-load-meta"><span>${name}</span><b>${sentToday.toLocaleString('ru-RU')} / ${dailyLimit.toLocaleString('ru-RU')}</b></div><div class="health-load-track"><i style="width:${used}%"></i></div></div>`;
          })
          .join('')
      : '<div class="dd-empty">Нет аккаунтов</div>';
  }
  put(
    'healthLoadTitle',
    accounts.length ? `Нагрузка ${accounts.length} отправителей` : 'Отправители не подключены',
  );
  put(
    'healthLoadSummary',
    accounts.length
      ? `${sent.toLocaleString('ru-RU')} отправлено из общего дневного лимита ${limit.toLocaleString('ru-RU')}.`
      : 'Столбцы появятся после подключения реальных Telegram-аккаунтов.',
  );
  const sync = document.getElementById('healthSyncState');
  if (sync)
    sync.innerHTML = `<i></i> обновлено ${new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`;
  applyHealthFilter();
}

function renderAccountHealthError(message) {
  healthAccounts = new Map();
  const list = document.getElementById('healthAccountList');
  if (list)
    list.innerHTML = `<div class="dd-empty">Не удалось загрузить состояние: ${esc(message)}</div>`;
  const recs = document.getElementById('healthRecommendations');
  if (recs) recs.innerHTML = '<div class="dd-empty">Рекомендации недоступны без данных API.</div>';
  const chart = document.getElementById('healthLoadChart');
  if (chart) chart.innerHTML = '<div class="dd-empty">Нет данных</div>';
  const sync = document.getElementById('healthSyncState');
  if (sync) sync.innerHTML = '<i></i> ошибка обновления';
}

function initAccountHealthCenter() {
  const center = document.getElementById('accountHealthCenter');
  if (!center) return;
  center.addEventListener('click', (e) => {
    const filter = e.target.closest('[data-health-filter]');
    if (filter) {
      center
        .querySelectorAll('[data-health-filter]')
        .forEach((button) => button.classList.toggle('active', button === filter));
      const state = filter.dataset.healthFilter;
      center.querySelectorAll('[data-health-account]').forEach((row) => {
        row.hidden = state !== 'all' && row.dataset.state !== state;
      });
      return;
    }

    const refresh = e.target.closest('[data-health-refresh]');
    if (refresh) {
      refresh.disabled = true;
      refresh.textContent = 'Обновляем…';
      center.classList.add('is-refreshing');
      loadWarmup().then(() => {
        center.classList.remove('is-refreshing');
        refresh.disabled = false;
        refresh.textContent = 'Обновить';
        toast('Состояние аккаунтов обновлено', 'success');
      });
      return;
    }

    const spam = e.target.closest('[data-health-spambot]');
    if (spam) {
      checkSpamBot(Number(spam.dataset.healthSpambot));
      return;
    }

    const manage = e.target.closest('[data-health-manage]');
    if (manage) {
      const management = document.getElementById('healthAccountManagement');
      if (management) management.open = true;
      const row = document.querySelector(`[data-wm-id="${Number(manage.dataset.healthManage)}"]`);
      (row || document.getElementById('warmupList'))?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
      return;
    }

    const details = e.target.closest('[data-health-details]');
    if (details) {
      const data = healthAccounts.get(Number(details.dataset.healthDetails));
      if (!data) return;
      const pop = document.getElementById('healthDetailsPop');
      const title = document.getElementById('healthDetailsTitle');
      const text = document.getElementById('healthDetailsText');
      const view = accountStatusView(data);
      if (title) title.textContent = accountName(data);
      if (text)
        text.textContent = [
          `Статус: ${view.label}. Причина: ${view.reason}.`,
          `Отправлено сегодня: ${Number(data.sent_today || 0)} из ${Number(data.daily_send_limit || 0)}; ошибок: ${Number(data.failed_today || 0)}.`,
          `Риск: ${Number(data.risk_score || 0)}/100. SpamBot: ${SPAMBOT_STATE[data.spambot_status] || 'не проверен'}; проверка: ${formatAccountTime(data.spambot_checked_at)}.`,
          `Последняя успешная отправка: ${formatAccountTime(data.last_success_at)}.`,
        ].join(' ');
      if (pop) pop.hidden = false;
      return;
    }

    if (e.target.closest('[data-health-details-close]')) {
      const pop = document.getElementById('healthDetailsPop');
      if (pop) pop.hidden = true;
    }
  });
}

/* -------------------------------- init ------------------------------------ */

// --- Входы в аккаунт -------------------------------------------------------

/** Дата в списке входов: короткая и без секунд — точность здесь не нужна. */
function sessionMoment(value) {
  if (!value) return 'неизвестно';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'неизвестно';
  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export async function loadSessions() {
  const box = document.getElementById('sessionsList');
  if (!box) return;
  try {
    const body = await api('GET', '/api/auth/sessions');
    const items = body.items || [];
    if (!items.length) {
      box.innerHTML = '<div class="row-sub">Активных входов не найдено</div>';
      return;
    }
    box.innerHTML = items
      .map(
        (item) => `
      <div class="session-row${item.current ? ' current' : ''}">
        <div class="session-text">
          <div class="session-title">
            ${esc(item.device)}${item.current ? '<span class="session-now">этот браузер</span>' : ''}
          </div>
          <div class="row-sub">
            вход ${esc(sessionMoment(item.created_at))}
            · активность ${esc(sessionMoment(item.last_seen_at))}
            ${item.ip ? `· ${esc(item.ip)}` : ''}
          </div>
        </div>
        ${
          item.current
            ? ''
            : `<button class="btn ghost small" data-act="revokeSession"
                 data-args='["${esc(item.jti)}"]'>Закрыть</button>`
        }
      </div>`,
      )
      .join('');
  } catch (error) {
    box.innerHTML = `<div class="row-sub">${esc(
      error && error.message ? error.message : 'Не удалось загрузить список входов',
    )}</div>`;
  }
}

export async function revokeSession(jti) {
  if (!jti) return;
  if (!(await confirmAction('Закрыть этот вход? На том устройстве попросят войти заново.'))) {
    return;
  }
  try {
    await api('POST', '/api/auth/sessions/revoke', { jti });
    toast('Вход закрыт', 'success');
    loadSessions();
  } catch (error) {
    toast(error && error.message ? error.message : 'Не удалось закрыть вход', 'err');
  }
}

export async function revokeOtherSessions() {
  const ok = await confirmAction(
    'Выйти на всех других устройствах? Текущий браузер останется в системе.',
    { title: 'Закрыть остальные входы', okLabel: 'Выйти везде' },
  );
  if (!ok) return;
  try {
    await api('POST', '/api/auth/sessions/revoke-others');
    toast('Другие входы закрыты', 'success');
    loadSessions();
  } catch (error) {
    toast(error && error.message ? error.message : 'Не получилось', 'err');
  }
}

// --- Риск блокировки аккаунта ---------------------------------------------

const RISK_TONE = { blocked: 'bad', danger: 'bad', watch: 'warn', ok: 'good' };

export async function loadAccountRisk() {
  const box = document.getElementById('accountRiskList');
  if (!box) return;
  try {
    const body = await api('GET', '/api/warmup/risk');
    const items = body.items || [];
    if (!items.length) {
      box.innerHTML =
        '<div class="row-sub">Подключите Telegram-аккаунт — тогда здесь появится оценка.</div>';
      return;
    }
    box.innerHTML = items
      .map(
        (item) => `
      <div class="risk-card ${RISK_TONE[item.level] || 'good'}">
        <div class="risk-head">
          <b>${esc(item.label)}</b>
          <span class="risk-badge">${esc(item.title)}</span>
        </div>
        <ul class="risk-reasons">
          ${item.reasons.map((line) => `<li>${esc(line)}</li>`).join('')}
        </ul>
        ${
          item.advice.length
            ? `<div class="risk-advice">${item.advice
                .map((line) => `<div>${esc(line)}</div>`)
                .join('')}</div>`
            : ''
        }
      </div>`,
      )
      .join('');
  } catch (error) {
    box.innerHTML = `<div class="row-sub">${esc(
      error && error.message ? error.message : 'Не удалось оценить риск',
    )}</div>`;
  }
}

export function initSettings() {
  initAccountHealthCenter();
  // loadSessions() и loadAccountRisk() отсюда убраны: init идёт при загрузке
  // страницы, ещё до входа, и каждый посетитель лендинга получал два 401 в
  // консоль и в access-лог. Их зовёт loadUserData() в auth.js после входа.
  // Окно подключения сообщает об удачном входе (mf:tg-account-connected из
  // mini-client): закрываем модалку сами и сразу показываем @ник в карточке —
  // раньше человек оставался наедине с формой и не знал, что уже всё.
  window.addEventListener('message', (e) => {
    if (e.origin !== location.origin) return;
    if (!e.data || e.data.type !== 'mf:tg-account-connected') return;
    const modal = document.getElementById('modalTgConnect');
    if (modal && modal.classList.contains('show')) {
      toast('Telegram подключён', 'success');
      finishTgConnect();
    }
  });

  // Окно входа само сообщает свою высоту (mf:tg-height): iframe без этого имел
  // дефолтные 300x150 и рисовал внутри модалки две полосы прокрутки.
  window.addEventListener('message', (e) => {
    if (e.origin !== location.origin) return;
    if (!e.data || e.data.type !== 'mf:tg-height') return;
    const frame = document.getElementById('tgConnectFrame');
    if (!frame) return;
    const max = Math.round(window.innerHeight * 0.6);
    const height = Math.min(Math.max(Number(e.data.height) || 0, 220), max);
    frame.style.height = `${height}px`;
  });

  const connectModal = document.getElementById('modalTgConnect');
  if (connectModal) {
    // Закрытие по фону или Escape проходит через ui.js и тоже обязано убрать
    // временный слот, а не оставлять пустую карточку «Аккаунт 2».
    connectModal.addEventListener('mf:modal-closed', () => {
      const wrap = document.getElementById('tgConnectWrap');
      if (wrap) wrap.innerHTML = '';
      discardPendingTgAccount();
    });
  }

  const wrap = document.getElementById('keysList');
  if (wrap) {
    // Делегирование: строки перерисовываются, повесить слушатели на контейнер надёжнее.
    wrap.addEventListener('click', (e) => {
      const c = e.target.closest('[data-key-check]');
      const d = e.target.closest('[data-key-del]');
      if (c) checkKey(Number(c.dataset.keyCheck));
      else if (d) removeKey(Number(d.dataset.keyDel));
    });
  }

  const warm = document.getElementById('warmupList');
  if (warm) {
    if (!_warmupTicker) _warmupTicker = window.setInterval(updateWarmupCountdowns, 1000);
    warm.addEventListener('click', (e) => {
      const conn = e.target.closest('[data-wm-connect]');
      const del = e.target.closest('[data-wm-del]');
      const spam = e.target.closest('[data-wm-spambot]');
      const appeal = e.target.closest('[data-wm-appeal]');
      if (spam) checkSpamBot(Number(spam.dataset.wmSpambot));
      else if (appeal) appealSpamBot(Number(appeal.dataset.wmAppeal));
      else if (conn) connectTgAccount(Number(conn.dataset.wmConnect));
      else if (del) removeWarmup(Number(del.dataset.wmDel));
    });

    // Переключатель слушаем по change, а не по click: клик по <label> пришёл бы
    // дважды — и прогрев тут же выключался бы обратно.
    warm.addEventListener('change', (e) => {
      const sending = e.target.closest('[data-wm-sending]');
      if (sending) {
        toggleSending(Number(sending.dataset.wmSending), sending.checked);
        return;
      }
      const sw = e.target.closest('[data-wm-toggle]');
      if (sw) toggleWarmup(Number(sw.dataset.wmToggle), sw.checked);
    });
  }
}
