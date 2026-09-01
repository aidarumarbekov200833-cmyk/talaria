/** Рассылки (кампании). Работают с бэкендом по /api/campaigns.
 *
 *  Что здесь честно, а что нет — важно понимать сразу:
 *  кампания фиксирует, КОМУ и ЧТО отправлять (база спарсенных каналов + оффер).
 *  Сообщения уходят через отдельный Telegram-gateway с MTProto-сессиями
 *  подключённых аккаунтов; интерфейс показывает только состояние кампании.
 *
 *  Поэтому убрана simulateCampaignProgress(): она двигала прогресс-бар и
 *  писала «Отправлено 1568/4128» при нуле отправленных сообщений. Прогресс
 *  теперь берётся только из полей sent_count/total_recipients, которые
 *  меняются, когда отправку действительно кто-то выполнил.
 */
import { api, esc, getToken } from './api.js';
import { toast, confirmAction } from './ui.js?v=tal32';
import { logLine, resetLog } from './logline.js?v=tal33';

const ENVELOPE_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>';
const TRASH_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>';

const STATUS_TEXT = {
  running: 'Идёт',
  paused: 'На паузе',
  completed: 'Завершена',
  error: 'Ошибка',
  interrupted: 'Прервана',
  cancel_requested: 'Останавливается',
  cancelled: 'Остановлена',
};
const STATUS_CLASS = {
  running: 'on',
  paused: 'pending',
  completed: 'off',
  error: 'pending',
  interrupted: 'pending',
  cancel_requested: 'pending',
  cancelled: 'off',
};

let bases = []; // базы лидов для селекта получателей

function group(n) {
  if (n === null || n === undefined) return '0';
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

/* ---------- ЖУРНАЛ ---------- */

/** Строка в панель журнала справа от формы. */
export function campaignLog(message, level = 'info') {
  logLine('campaignLogs', message, level);
}

export function clearCampaignLog() {
  resetLog(
    'campaignLogs',
    'Пока пусто — здесь появится ход отправки: кто, когда и с каким результатом.',
  );
}

/* ---------- ВЫБОР БАЗЫ ---------- */

/** Базы получателей из /api/leads/databases. Раньше в селекте лежали две
 *  придуманные базы, и кампания создавалась на несуществующих людей. */
export async function loadCampaignBases() {
  const sel = document.getElementById('campaignBaseInput');
  if (!sel || !getToken()) return;
  try {
    const data = await api('GET', '/api/leads/databases');
    bases = data.databases || [];
    const prev = sel.value;
    sel.innerHTML = '';

    if (!bases.length) {
      sel.innerHTML = '<option value="">Баз пока нет — соберите на странице «Поиск лидов»</option>';
      return;
    }
    bases.forEach((b) => {
      const opt = document.createElement('option');
      opt.value = String(b.id);
      opt.textContent = `${b.name} (${group(b.leads_count)})`;
      sel.appendChild(opt);
    });
    if (prev && bases.some((b) => String(b.id) === prev)) sel.value = prev;
  } catch (e) {
    console.log('Не удалось загрузить базы:', e.message);
  }
}

/* ---------- СОЗДАНИЕ ---------- */

/* ---------- ЗАПУСК ---------- */

// Состояние переключателей формы держим в модуле, а не в DOM-атрибутах:
// так логика и вид читают одно и то же состояние.
let aiMode = false;
let spintaxOn = false;
let aiAllowed = false; // разрешает ли тариф ИИ-оффер
let aiPlanNames = '';
let aiTemplate = ''; // сохранённый скелет ИИ-оффера (только для показа)
let aiProviderReady = false;
let offersCount = null; // сколько офферов у человека — для подсказки

/** Подключённые аккаунты — сервер сам выбирает здоровый пул и ротирует нагрузку. */
export async function loadCampaignAccounts() {
  const sel = document.getElementById('campaignAccounts');
  if (!sel) return;
  const count = document.getElementById('campaignAccountPoolCount');
  const list = document.getElementById('campaignAccountPoolList');
  if (!getToken()) {
    sel.innerHTML = '<option value="" disabled>Войдите через Telegram</option>';
    if (count) count.textContent = 'Войдите, чтобы загрузить пул';
    if (list) list.innerHTML = '<span>Данные отправителей недоступны без входа</span>';
    return;
  }
  try {
    const data = await api('GET', '/api/warmup');
    const items = (data.accounts || data.items || []).filter(
      (a) => a.tg_id || a.tg_username || a.connected,
    );
    sel.innerHTML = items.length
      ? items
          .map(
            (a) =>
              `<option value="${a.id}">${esc(a.tg_username ? '@' + a.tg_username : a.label)}</option>`,
          )
          .join('')
      : '<option value="" disabled>Нет подключённых аккаунтов — подключите в настройках</option>';
    // Select остаётся служебным для совместимости с текущей формой, но выбор
    // человеку больше не показываем: пустое selected = безопасная авторотация.
    Array.from(sel.options).forEach((option) => {
      option.selected = false;
    });
    const ready = items.filter(
      (a) =>
        a.connected !== false &&
        a.sending_enabled !== false &&
        (a.operational_status || 'ready') === 'ready',
    );
    if (count) {
      count.textContent = items.length
        ? `${ready.length} готово к ротации · ${Math.max(0, items.length - ready.length)} исключено`
        : 'Нет подключённых Telegram-аккаунтов';
    }
    if (list) {
      list.innerHTML = ready.length
        ? ready
            .slice(0, 4)
            .map((a) => {
              const name = a.tg_username ? '@' + a.tg_username : a.label || 'Telegram';
              const sent = Number(a.sent_today || 0);
              const limit = Number(a.daily_send_limit || sent + Number(a.daily_left || 0));
              return `<span>${esc(name)} · ${sent}/${limit || '—'}</span>`;
            })
            .join('')
        : `<span>${items.length ? 'Все аккаунты сейчас исключены из ротации' : 'Подключите аккаунт в «Настройках»'}</span>`;
    }
  } catch (e) {
    sel.innerHTML = `<option value="" disabled>${esc(e.message)}</option>`;
    if (count) count.textContent = 'Не удалось загрузить пул';
    if (list) list.innerHTML = `<span>${esc(e.message)}</span>`;
  }
}

/** Доступен ли AI-оффер на текущем тарифе.
 *
 *  Сервер всё равно проверяет это сам, здесь — только подсказка, чтобы человек
 *  не писал шаблон впустую и только потом узнавал про тариф.
 */
export async function refreshAiAvailability() {
  const notice = document.getElementById('aiPlanNotice');
  if (!getToken()) {
    if (notice) notice.hidden = true;
    return;
  }
  try {
    // Один источник правды — настройки ИИ-оффера: там и доступность по тарифу,
    // и названия тарифов, и сам скелет. Раньше здесь читались поля, которых в
    // ответах /api/subscription и /api/plans нет (sub.plan.ai_offer,
    // plans.items), поэтому переключатель считался недоступным всегда.
    const s = await api('GET', '/api/offers/ai-settings');
    aiAllowed = !!s.allowed;
    aiPlanNames = s.plan_names || '';
    aiTemplate = s.template || '';
    aiProviderReady = !!s.provider_ready;
  } catch {
    aiAllowed = false;
  }
  if (notice) {
    const text = aiAllowed
      ? ''
      : `ИИ-оффер входит в тариф ${aiPlanNames || 'повыше'}. На текущем тарифе доступны обычные офферы со спинтаксом.`;
    notice.textContent = text;
    // Пустую плашку не показываем: без текста это просто жёлтая полоса ни о чём
    // (так она и выглядела до входа, когда тарифов ещё не знаем).
    notice.hidden = !text;
  }
  if (!aiAllowed && aiMode) {
    aiMode = false;
  }
  paintModes();
}

/** Скелет ИИ-оффера в форме рассылки — только для чтения: настраивается он
 *  на странице «Офферы», и второе поле для того же текста разошлось бы с ним. */
function paintAiTemplate() {
  const box = document.getElementById('campaignAiTemplatePreview');
  if (!box) return;
  if (aiTemplate) {
    box.textContent = aiTemplate;
    box.classList.remove('empty');
  } else {
    box.textContent =
      'Скелет ещё не настроен. Откройте «Офферы» → «ИИ-оффер» и опишите, как примерно должен выглядеть оффер.';
    box.classList.add('empty');
  }
}

/** Сколько офферов уйдёт в дело: выбирать их поштучно больше не нужно. */
function paintOffersHint(count) {
  const hint = document.getElementById('campaignOffersHint');
  if (!hint) return;
  if (offersCount === null && count === undefined) return;
  if (count !== undefined) offersCount = count;
  hint.textContent = offersCount
    ? `В дело идут все ваши офферы (${offersCount}) — они чередуются по лидам. Управление офферами — в разделе «Офферы».`
    : 'Офферов пока нет. Создайте хотя бы один в разделе «Офферы» — в рассылке они чередуются автоматически.';
}

/** Число офферов для подсказки. Зовётся из offers.js после загрузки списка. */
export function setCampaignOffersCount(count) {
  paintOffersHint(Number(count) || 0);
}

/** Один источник списка офферов для подсказки и тестовой отправки. */
export function setCampaignOffers(offers) {
  const rows = Array.isArray(offers) ? offers : [];
  setCampaignOffersCount(rows.length);
  const sel = document.getElementById('testSendOffer');
  if (!sel) return;
  const selected = sel.value;
  sel.innerHTML = rows.length
    ? '<option value="">Свой текст ниже</option>' +
      rows.map((o) => `<option value="${Number(o.id)}">${esc(o.name)}</option>`).join('')
    : '<option value="">Офферов пока нет</option>';
  if (rows.some((o) => String(o.id) === selected)) sel.value = selected;
}

/** Перерисовать переключатели и показать ровно одну ветку. */
function paintModes() {
  const aiBtn = document.getElementById('campaignAiToggle');
  const spinBtn = document.getElementById('campaignSpintaxToggle');
  const aiPane = document.getElementById('campaignAiPane');
  const regPane = document.getElementById('campaignRegularPane');
  if (aiBtn) {
    aiBtn.classList.toggle('on', aiMode);
    aiBtn.setAttribute('aria-checked', String(aiMode));
  }
  if (spinBtn) {
    spinBtn.classList.toggle('on', spintaxOn);
    spinBtn.setAttribute('aria-checked', String(spintaxOn));
  }
  if (aiPane) aiPane.hidden = !aiMode;
  if (regPane) regPane.hidden = aiMode;
  paintAiTemplate();
  paintOffersHint();
}

export function toggleCampaignAi() {
  if (!aiMode && !aiAllowed) {
    toast(`AI-оффер доступен в тарифе ${aiPlanNames || 'повыше'}`, 'warn');
    return;
  }
  aiMode = !aiMode;
  paintModes();
}

export function toggleCampaignSpintax() {
  spintaxOn = !spintaxOn;
  paintModes();
}

/** Собрать тело запроса запуска из формы — общее для запуска и предпросмотра.
 *  Одна функция, чтобы предпросмотр никогда не расходился с реальным запуском. */
function collectLaunchBody(baseId) {
  return {
    database_id: Number(baseId),
    name: (document.getElementById('campaignNameInput')?.value || '').trim(),
    offer_mode: aiMode ? 'ai' : 'regular',
    // Пустой список = «все мои офферы, по кругу». Скелет ИИ-оффера сервер тоже
    // берёт из настроек сам — передавать его копию отсюда незачем.
    offer_ids: [],
    spintax: !aiMode && spintaxOn,
    // Пустой список — сервер использует все здоровые аккаунты, соблюдает их
    // дневные лимиты, cooldown и автоматически исключает рискованные сессии.
    account_ids: [],
    min_score: scoreFilterValue(),
    skip_contacted: skipContactedValue(),
    limit: Math.max(
      1,
      Math.min(200, Number(document.getElementById('campaignLimit')?.value) || 50),
    ),
  };
}

/** Предпросмотр: 2–3 готовых текста на реальных лидах — до запуска, без отправки. */
export async function previewCampaign() {
  if (!getToken()) {
    toast('Войдите, чтобы посмотреть примеры', 'warn');
    return;
  }
  const baseId = document.getElementById('campaignBaseInput')?.value || '';
  if (!baseId) {
    toast('Выберите базу лидов', 'warn');
    return;
  }
  if (aiMode && !aiTemplate) {
    toast('Скелет ИИ-оффера не настроен — откройте «Офферы» → «ИИ-оффер»', 'warn');
    return;
  }
  if (!aiMode && offersCount === 0) {
    toast('Создайте хотя бы один оффер в разделе «Офферы»', 'warn');
    return;
  }

  const box = document.getElementById('campaignPreviewBox');
  const btn = document.getElementById('campaignPreviewBtn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Готовим…';
  }
  try {
    const res = await api('POST', '/api/campaigns/launch-preview', collectLaunchBody(baseId));
    if (!box) return;
    box.hidden = false;
    const head =
      `<div class="preview-meta">Получателей: <b>${group(res.recipients)}</b>` +
      (res.skipped_contacted ? ` · уже писали: ${group(res.skipped_contacted)}` : '') +
      (res.skipped_no_telegram ? ` · без Telegram: ${group(res.skipped_no_telegram)}` : '') +
      '</div>';
    const cards = (res.samples || [])
      .map(
        (s) => `
      <div class="preview-card">
        <div class="preview-card-head">
          <span class="preview-lead">${esc(s.title || 'Лид #' + s.lead_id)}</span>
          ${s.username ? `<span class="preview-user">@${esc(s.username)}</span>` : ''}
          <span class="preview-offer">${esc(s.offer_name)}</span>
        </div>
        <div class="preview-text">${esc(s.text)}</div>
      </div>`,
      )
      .join('');
    box.innerHTML =
      '<div class="preview-head"><b>Предпросмотр</b><span class="link" data-act="hideCampaignPreview">Скрыть</span></div>' +
      head +
      (cards || '<div class="dd-empty">Примеров нет.</div>') +
      (res.note ? `<div class="field-hint" style="margin-top:8px">${esc(res.note)}</div>` : '');
  } catch (e) {
    toast(`Предпросмотр не получился: ${e.message}`, 'warn');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Предпросмотр';
    }
  }
}

export function hideCampaignPreview() {
  const box = document.getElementById('campaignPreviewBox');
  if (box) {
    box.hidden = true;
    box.innerHTML = '';
  }
}

/** Запустить рассылку сразу: один запрос вместо шага «создать». */
export async function launchCampaign() {
  if (!getToken()) {
    toast('Войдите, чтобы запустить рассылку', 'warn');
    return;
  }

  const baseId = document.getElementById('campaignBaseInput')?.value || '';
  if (!baseId) {
    toast('Выберите базу лидов', 'warn');
    return;
  }

  // Офферы не выбираются: обычный режим берёт все офферы владельца и чередует
  // их по лидам, ИИ-режим берёт сохранённый скелет. Здесь только понятные
  // проверки до запроса — сервер всё равно проверяет то же самое.
  if (aiMode && !aiTemplate) {
    toast('Скелет ИИ-оффера не настроен — откройте «Офферы» → «ИИ-оффер»', 'warn');
    return;
  }
  if (aiMode && !aiProviderReady) {
    toast('ИИ пока не настроен владельцем сервиса', 'warn');
    return;
  }
  if (!aiMode && offersCount === 0) {
    toast('Создайте хотя бы один оффер в разделе «Офферы»', 'warn');
    return;
  }

  const base = bases.find((b) => String(b.id) === String(baseId));
  const btn = document.getElementById('campaignLaunchBtn');
  const body = collectLaunchBody(baseId);

  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Отправляем…';
  }
  campaignLog(
    `Запуск по базе «${base ? base.name : baseId}» · ${aiMode ? 'ИИ-оффер' : `обычные офферы (${offersCount || 0}, по кругу)`}${!aiMode && spintaxOn ? ' · спинтакс' : ''}`,
    'info',
  );

  try {
    const res = await api('POST', '/api/campaigns/launch', body);
    campaignLog(res.note || `Отправлено ${res.sent}`, res.failed ? 'warning' : 'success');
    if (res.accounts_used && res.accounts_used.length) {
      campaignLog(`Аккаунты: ${res.accounts_used.join(', ')}`, 'info');
    }
    (res.problems || []).forEach((p) => campaignLog(p, 'error'));
    if (res.background) {
      // Отправка идёт в фоне на сервере: подтягиваем журнал кампании сюда,
      // чтобы человек видел ход отправки не переходя к списку кампаний.
      toast('Рассылка запущена — ход отправки в журнале', 'success');
      followCampaign(res.campaign_id);
    } else {
      toast(`Отправлено ${res.sent}, ошибок ${res.failed}`, res.failed ? 'warn' : 'success');
    }
    await loadCampaigns();
  } catch (e) {
    toast(`Ошибка: ${e.message}`, 'warn');
    campaignLog(`Запуск не удался: ${e.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Запустить';
    }
  }
}

// --- слежение за фоновой рассылкой ------------------------------------------
// WebSocket двигает проценты в списке кампаний, а серверный журнал (кто именно
// получил, какие ошибки) сюда доносит опрос раз в 2.5 секунды. Дешевле держать
// один механизм доставки строк журнала, чем дублировать его в сокете.

let followTimer = null;
let followSeenIds = new Set();

function stopFollowing() {
  if (followTimer) {
    clearInterval(followTimer);
    followTimer = null;
  }
  followSeenIds = new Set();
}

// После выхода журнал кампании и ожидание тестового ответа опрашивать нечем
// (токен снят) — глушим таймеры, чтобы не сыпать тихими 401.
window.addEventListener('talaria:logout', () => {
  stopFollowing();
  if (testReplyTimer) {
    window.clearInterval(testReplyTimer);
    testReplyTimer = null;
  }
});

function followCampaign(id) {
  stopFollowing();
  const startedAt = Date.now();
  followTimer = setInterval(async () => {
    try {
      const [logs, campaign] = await Promise.all([
        api('GET', `/api/campaigns/${id}/logs?limit=200`),
        api('GET', `/api/campaigns/${id}`),
      ]);
      // Сервер отдаёт новые сверху — разворачиваем и печатаем только свежие.
      const items = (logs.logs || logs.items || []).slice().reverse();
      for (const line of items) {
        if (followSeenIds.has(line.id)) continue;
        followSeenIds.add(line.id);
        const level = line.level === 'warn' ? 'warning' : line.level || 'info';
        campaignLog(line.message, level);
      }
      const done = campaign.status && campaign.status !== 'running';
      if (done) {
        stopFollowing();
        const failed = campaign.error_count || 0;
        toast(
          campaign.status === 'completed'
            ? `Готово: отправлено ${campaign.sent_count || 0}, ошибок ${failed}`
            : `Рассылка остановлена (${STATUS_TEXT[campaign.status] || campaign.status})`,
          campaign.status === 'completed' && !failed ? 'success' : 'warn',
        );
        await loadCampaigns();
      }
      // Страховка от вечного опроса, если вкладку забыли: 2 часа достаточно
      // любой партии в 200 сообщений даже с самыми большими паузами.
      if (Date.now() - startedAt > 2 * 60 * 60 * 1000) stopFollowing();
    } catch (_) {
      /* сеть мигнула — попробуем в следующий тик */
    }
  }, 2500);
}

export function clearCampaignForm() {
  const name = document.getElementById('campaignNameInput');
  const base = document.getElementById('campaignBaseInput');
  const score = document.getElementById('campaignMinScore');
  const skip = document.getElementById('campaignSkipContacted');
  if (name) name.value = '';
  if (base) base.selectedIndex = 0;
  if (score) score.value = '';
  // Галку возвращаем в «включено»: безопасное поведение — умолчание.
  if (skip) skip.checked = true;
  spintaxOn = false;
  paintModes();
}

/** Порог по баллу из формы (null — без ограничения). */
function scoreFilterValue() {
  const raw = (document.getElementById('campaignMinScore')?.value || '').trim();
  if (!raw) return null;
  const n = Number(raw);
  if (!Number.isFinite(n)) return null;
  return Math.max(0, Math.min(100, Math.round(n)));
}

/** Исключать ли тех, кому уже писали. */
function skipContactedValue() {
  const el = document.getElementById('campaignSkipContacted');
  return el ? !!el.checked : true;
}

/* ---------- СПИСОК ---------- */

/** Разметка строки кампании. Прогресс-бар рисуем только если что-то реально
 *  отправлено — иначе полоска на 4% создавала вид идущей рассылки. */
function campaignRowHtml(c) {
  const sent = c.sent_count || 0;
  const total = c.total_recipients || 0;
  const pct = total > 0 ? Math.min(100, Math.round((sent / total) * 100)) : 0;
  const when = c.created_at
    ? new Date(c.created_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
    : '';

  const progress =
    sent > 0
      ? `<div class="progress-line"><i class="camp-progress" style="width:${pct}%"></i></div>`
      : '';

  // Запланированное время — тоже факт о рассылке, а не только дело планировщика:
  // без него в списке нельзя отличить отложенную от ждущей ручного запуска.
  const sched = c.reply_count
    ? `<span data-tip="Ответили на рассылку">Ответили: <b>${group(c.reply_count)}</b></span>`
    : '';

  return `<div class="row-main">
      <div class="row-ic">${ENVELOPE_SVG}</div>
      <div style="min-width:0">
        <div class="row-title">${esc(c.name)}</div>
        <div class="row-sub">База: ${esc(c.database_name || '—')} · ${esc(c.channel || 'Telegram')}</div>
        ${progress}
        <div class="camp-meta">
          <span>Получателей: <b>${group(total)}</b></span>
          <span>Отправлено: <b>${group(sent)}</b></span>
          ${c.error_count ? `<span>Ошибок: <b>${group(c.error_count)}</b></span>` : ''}
          ${sched}
          ${when ? `<span>${when}</span>` : ''}
        </div>
      </div>
    </div>
    <div class="row-actions">
      <span class="badge ${STATUS_CLASS[c.status] || 'pending'}">${STATUS_TEXT[c.status] || esc(c.status || '')}</span>
      <button class="btn-mini" data-recipients-campaign="${c.id}" data-campaign-name="${esc(c.name)}" data-campaign-db="${esc(c.database_name || '')}">Список к отправке</button>
      ${
        c.status === 'running' || c.status === 'cancel_requested'
          ? `<button class="btn-mini" data-cancel-campaign="${c.id}"${c.status === 'cancel_requested' ? ' disabled' : ''}>Остановить</button>`
          : `<button class="mini-btn" data-tip="Удалить кампанию" data-del-campaign="${c.id}">${TRASH_SVG}</button>`
      }
    </div>`;
}

export async function loadCampaigns() {
  const container = document.getElementById('campaignList');
  if (!container || !getToken()) return;
  try {
    const response = await api('GET', '/api/campaigns');
    const campaigns = response.campaigns || [];
    const stopAll = document.getElementById('campaignStopAllBtn');
    if (stopAll)
      stopAll.hidden = !campaigns.some(
        (c) => c.status === 'running' || c.status === 'cancel_requested',
      );
    container.innerHTML = '';

    if (!campaigns.length) {
      container.innerHTML =
        '<div class="empty-state show">Рассылок пока нет — создайте первую выше</div>';
      return;
    }
    campaigns.forEach((c) => {
      const row = document.createElement('div');
      row.className = 'list-row';
      row.setAttribute('data-campaign-id', c.id);
      row.innerHTML = campaignRowHtml(c);
      container.appendChild(row);
    });
  } catch (e) {
    console.log('Не удалось загрузить кампании:', e.message);
  }
}

export async function stopAllCampaigns() {
  if (!(await confirmAction('Остановить все активные рассылки после текущей отправки?'))) return;
  const button = document.getElementById('campaignStopAllBtn');
  if (button) button.disabled = true;
  try {
    const result = await api('POST', '/api/campaigns/stop-all', {});
    toast(
      result.stopped ? `Останавливаем рассылки: ${result.stopped}` : 'Активных рассылок уже нет',
      'info',
    );
    campaignLog(`Аварийная остановка: ${result.stopped || 0} активных рассылок`, 'warn');
    await loadCampaigns();
  } catch (e) {
    toast(`Не удалось остановить рассылки: ${e.message}`, 'warn');
  } finally {
    if (button) button.disabled = false;
  }
}

/** Точечное живое обновление одной кампании (из WebSocket, live.js).
 *
 *  Двигаем прогресс-бар и меняем бейдж статуса прямо в нужной строке, не
 *  перерисовывая весь список. Если строки прогресса ещё нет (отправляли 0) или
 *  саму строку не нашли — перезагружаем список: так разметка гарантированно
 *  сойдётся с данными, а не «дорисуется» наполовину.
 */
export function applyCampaignLiveUpdate(id, progress, status) {
  const row = document.querySelector(`[data-campaign-id="${id}"]`);
  if (!row) return; // список не открыт или ещё не загружен — подхватится при загрузке

  if (progress != null) {
    const bar = row.querySelector('.camp-progress');
    if (bar) {
      bar.style.width = Math.min(100, Math.max(0, progress)) + '%';
    } else {
      // Прогресс-бара не было (sent был 0) — перерисуем список целиком.
      loadCampaigns();
      return;
    }
  }

  if (status) {
    const badge = row.querySelector('.badge');
    if (badge) {
      badge.className = `badge ${STATUS_CLASS[status] || 'pending'}`;
      badge.textContent = STATUS_TEXT[status] || status;
    }
  }
}

export async function removeCampaign(id) {
  if (!(await confirmAction('Удалить кампанию? Журнал кампании удалится вместе с ней.'))) return;
  try {
    await api('DELETE', `/api/campaigns/${id}`);
    await loadCampaigns();
    toast('Кампания удалена', 'info');
    campaignLog(`Удалена кампания #${id}`, 'warn');
  } catch (e) {
    toast(`Не удалось удалить: ${e.message}`, 'warn');
  }
}

export async function cancelCampaign(id) {
  if (!(await confirmAction('Остановить рассылку после текущей отправки?'))) return;
  try {
    await api('POST', `/api/campaigns/${id}/cancel`);
    toast('Остановка запрошена', 'info');
    await loadCampaigns();
  } catch (e) {
    toast(`Не удалось остановить: ${e.message}`, 'warn');
  }
}

/* ---------- ТЕСТОВАЯ ОТПРАВКА ---------- */

const TEST_REPLY_KEY = 'outly_test_reply_watch';
let pendingTestReply = null;
let testReplyTimer = null;

function testReplyUsername(value) {
  return String(value || '')
    .trim()
    .replace(/^@/, '')
    .toLowerCase();
}

function paintTestReplyWatch() {
  const box = document.getElementById('testReplyWatch');
  const title = document.getElementById('testReplyTitle');
  const text = document.getElementById('testReplyText');
  const timer = document.getElementById('testReplyTimer');
  if (!box || !title || !text || !timer || !pendingTestReply) return;
  const left = Math.max(0, Math.ceil((pendingTestReply.expiresAt - Date.now()) / 1000));
  box.hidden = false;
  box.className = 'test-reply-watch waiting';
  title.textContent = `Ждём ответ от @${pendingTestReply.username}`;
  text.textContent = 'Откройте сообщение на тестовом аккаунте и ответьте на него любым текстом.';
  timer.textContent = `${String(Math.floor(left / 60)).padStart(2, '0')}:${String(left % 60).padStart(2, '0')}`;
  if (left > 0) return;
  window.clearInterval(testReplyTimer);
  testReplyTimer = null;
  box.className = 'test-reply-watch timeout';
  title.textContent = 'Ответ пока не обнаружен';
  text.textContent =
    'Проверьте, что ответ отправлен именно подключённому аккаунту, и повторите тест.';
  timer.textContent = '—';
  pendingTestReply = null;
  sessionStorage.removeItem(TEST_REPLY_KEY);
}

function startTestReplyWatch(username, seconds = 300) {
  pendingTestReply = {
    username: testReplyUsername(username),
    expiresAt: Date.now() + Math.max(60, Math.min(600, Number(seconds) || 300)) * 1000,
  };
  sessionStorage.setItem(TEST_REPLY_KEY, JSON.stringify(pendingTestReply));
  window.clearInterval(testReplyTimer);
  paintTestReplyWatch();
  testReplyTimer = window.setInterval(paintTestReplyWatch, 1000);
}

function restoreTestReplyWatch() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(TEST_REPLY_KEY) || 'null');
    if (!saved || !saved.username || Number(saved.expiresAt) <= Date.now()) return;
    pendingTestReply = {
      username: testReplyUsername(saved.username),
      expiresAt: Number(saved.expiresAt),
    };
    paintTestReplyWatch();
    testReplyTimer = window.setInterval(paintTestReplyWatch, 1000);
  } catch (_) {
    sessionStorage.removeItem(TEST_REPLY_KEY);
  }
}

/** Живой WebSocket сообщил об ответе — закрыть именно активный тест. */
export function applyTestReplyUpdate(message) {
  if (!pendingTestReply || testReplyUsername(message?.username) !== pendingTestReply.username)
    return;
  window.clearInterval(testReplyTimer);
  testReplyTimer = null;
  sessionStorage.removeItem(TEST_REPLY_KEY);
  const box = document.getElementById('testReplyWatch');
  const title = document.getElementById('testReplyTitle');
  const text = document.getElementById('testReplyText');
  const timer = document.getElementById('testReplyTimer');
  if (box) {
    box.hidden = false;
    box.className = 'test-reply-watch success';
  }
  if (title) title.textContent = `Ответ от @${pendingTestReply.username} получен`;
  if (text)
    text.textContent = message?.text
      ? `Сообщение: ${message.text}`
      : 'Уведомление прошло через всю цепочку Talaria.';
  if (timer) timer.textContent = 'Готово';
  pendingTestReply = null;
}

/** Заполнить селект офферов для тестовой отправки.
 *
 *  Раньше список копировался из селекта офферов в форме рассылки. Этого селекта
 *  больше нет (офферы не выбираются поштучно), поэтому тянем список из API. */
export async function fillTestSendOffers() {
  const sel = document.getElementById('testSendOffer');
  if (!sel || !getToken()) return;
  try {
    const data = await api('GET', '/api/offers');
    setCampaignOffers(data.offers || []);
  } catch (e) {
    sel.innerHTML = `<option value="">${esc(e.message)}</option>`;
  }
}

/** Отправить одно тестовое сообщение через Telegram-gateway.
 *  Текст — выбранный оффер, либо произвольный. Пишет аккаунт пользователя. */
export async function sendTestMessage() {
  const usernameInput = document.getElementById('testSendUsername');
  const username = (usernameInput?.value || '').trim().replace(/^@/, '');
  if (!username) {
    toast('Укажите username получателя', 'warn');
    return;
  }
  if (!getToken()) {
    toast('Войдите, чтобы отправить тест', 'warn');
    return;
  }

  const offerId = document.getElementById('testSendOffer')?.value;
  const text = (document.getElementById('testSendText')?.value || '').trim();
  if (!text && !offerId) {
    toast('Выберите оффер или введите текст', 'warn');
    return;
  }

  const btn = document.getElementById('testSendBtn');
  const status = document.getElementById('testSendStatus');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Отправка…';
  }
  if (status) status.textContent = 'Отправляем через Telegram-аккаунт…';

  try {
    const body = { username };
    if (text) body.text = text;
    else body.offer_id = Number(offerId);
    const data = await api('POST', '/api/campaigns/test-send', body);
    if (status) status.textContent = `Отправлено на @${data.sent_to || username}`;
    toast(`Тест отправлен на @${data.sent_to || username}`, 'success');
    campaignLog(`Тест на @${data.sent_to || username} — сообщение ушло`, 'success');
    startTestReplyWatch(data.sent_to || username, data.reply_watch_seconds || 300);
    const receipt = document.getElementById('testSendReceipt');
    if (receipt) {
      const at = data.sent_at
        ? new Date(data.sent_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
        : '';
      receipt.hidden = false;
      receipt.innerHTML = `<div class="test-receipt-head"><span class="key-status ok">Доставлено в Telegram</span><span>${esc(data.account_label || '')} → @${esc(data.sent_to || username)} · ${esc(at)}</span></div><div class="test-receipt-bubble">${esc(data.rendered_text || '').replace(/\n/g, '<br>')}</div>`;
    }
  } catch (e) {
    if (status) status.textContent = e.message;
    toast(`Не отправлено: ${e.message}`, 'warn');
    campaignLog(`Тест на @${username} не прошёл: ${e.message}`, 'error');
    const receipt = document.getElementById('testSendReceipt');
    if (receipt) receipt.hidden = true;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Отправить и ждать ответ';
    }
  }
}

/* ---------- СПИСОК К ОТПРАВКЕ ---------- */
// История отправок приходит с backend и используется для защиты от повторной
// отправки тому же человеку в другой кампании.

const REC_PAGE = 100;
let recState = null; // { campaignId, name, databaseId, minScore, skipContacted, offset }
let recSelected = new Set();

/** Открыть панель со списком получателей кампании. */
export function openCampaignRecipients(campaignId, name, databaseId, opts = {}) {
  const panel = document.getElementById('campaignRecipientsPanel');
  if (!panel) return;
  if (!databaseId) {
    toast('Не нашёл базу этой рассылки — выберите её в форме выше', 'warn');
    return;
  }
  recState = {
    campaignId,
    name: name || '',
    databaseId: Number(databaseId),
    minScore: opts.minScore ?? scoreFilterValue(),
    skipContacted: opts.skipContacted ?? skipContactedValue(),
    offset: 0,
  };
  recSelected = new Set();
  panel.style.display = '';
  const forLabel = document.getElementById('campaignRecipientsFor');
  if (forLabel) forLabel.textContent = recState.name;
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  loadRecipients();
}

/** Закрыть панель списка. */
export function closeCampaignRecipients() {
  const panel = document.getElementById('campaignRecipientsPanel');
  if (panel) panel.style.display = 'none';
  recState = null;
  recSelected = new Set();
}

/** Загрузить страницу получателей (POST /api/campaigns/{id}/recipients). */
async function loadRecipients() {
  if (!recState) return;
  const list = document.getElementById('campaignRecipientsList');
  if (!list) return;
  list.innerHTML = '<div class="dd-empty">Считаем получателей…</div>';
  try {
    const body = {
      database_id: recState.databaseId,
      skip_contacted: recState.skipContacted,
      offset: recState.offset,
      limit: REC_PAGE,
    };
    if (recState.minScore != null) body.min_score = recState.minScore;
    const data = await api('POST', `/api/campaigns/${recState.campaignId}/recipients`, body);
    renderRecipients(data);
  } catch (e) {
    list.innerHTML = `<div class="dd-empty">Не удалось получить список: ${esc(e.message)}</div>`;
  }
}

function renderRecipients(data) {
  const list = document.getElementById('campaignRecipientsList');
  if (!list || !recState) return;
  const leads = data.leads || [];
  const total = data.total || 0;

  const summary = document.getElementById('campaignRecipientsSummary');
  if (summary) {
    const parts = [`К отправке: <b>${group(total)}</b>`];
    if (data.skipped_contacted)
      parts.push(`уже писали раньше и пропущены: <b>${group(data.skipped_contacted)}</b>`);
    if (recState.minScore != null) parts.push(`только балл от <b>${recState.minScore}</b>`);
    parts.push('отписавшиеся исключены всегда');
    summary.innerHTML = parts.join(' · ');
  }

  const pager = document.getElementById('campaignRecipientsPager');
  const count = document.getElementById('campaignRecipientsCount');
  if (pager) pager.style.display = total > REC_PAGE ? 'flex' : 'none';
  if (count)
    count.textContent = `Показаны ${leads.length ? recState.offset + 1 : 0}–${recState.offset + leads.length} из ${group(total)}`;
  const prev = document.getElementById('campaignRecipientsPrev');
  const next = document.getElementById('campaignRecipientsNext');
  if (prev) prev.disabled = recState.offset === 0;
  if (next) next.disabled = recState.offset + leads.length >= total;

  if (!leads.length) {
    list.innerHTML =
      '<div class="dd-empty">Отправлять некому: все подходящие лиды уже получили сообщение или отсеяны фильтрами.</div>';
    updateSendBar();
    return;
  }

  const rows = leads
    .map((l) => {
      const link = l.custom_url
        ? `<a href="https://www.youtube.com/${esc(l.custom_url)}" target="_blank" rel="noopener">${esc(l.title || l.custom_url)}</a>`
        : esc(l.title || 'без названия');
      const checked = recSelected.has(l.id) ? ' checked' : '';
      return `<tr>
      <td><input type="checkbox" data-rec-check="${l.id}"${checked}></td>
      <td>${link}</td>
      <td>${group(l.subscribers || 0)}</td>
      <td>${l.score == null ? '—' : l.score}</td>
    </tr>`;
    })
    .join('');

  list.innerHTML = `<div class="table-wrap"><table class="lead-table">
    <thead><tr><th><input type="checkbox" id="recCheckAll" data-tip="Выбрать всё на странице"></th><th>Канал</th><th>Подписчики</th><th>Балл</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
  updateSendBar();
}

function updateSendBar() {
  const bar = document.getElementById('campaignSendBar');
  const count = document.getElementById('campaignSendCount');
  const n = recSelected.size;
  if (bar) bar.style.display = n ? 'flex' : 'none';
  if (count) count.textContent = `Выбрано: ${group(n)}`;
  const all = document.getElementById('recCheckAll');
  if (all) {
    const boxes = document.querySelectorAll('[data-rec-check]');
    const checkedOnPage = document.querySelectorAll('[data-rec-check]:checked').length;
    all.checked = boxes.length > 0 && checkedOnPage === boxes.length;
    all.indeterminate = checkedOnPage > 0 && checkedOnPage < boxes.length;
  }
}

/** Отметить выбранных как отправленных или неудачных (POST .../sends). */
async function markSends(status) {
  if (!recState || !recSelected.size) return;
  const ids = [...recSelected];
  try {
    const res = await api('POST', `/api/campaigns/${recState.campaignId}/sends`, {
      lead_ids: ids,
      status,
    });
    const word = status === 'sent' ? 'отправленными' : 'неудачными';
    toast(
      `Отмечено ${word}: ${res.recorded}${res.duplicates ? `, уже было: ${res.duplicates}` : ''}`,
      'success',
    );
    campaignLog(`Отмечено ${word}: ${res.recorded} · повторов ${res.duplicates}`, 'info');
    recSelected = new Set();
    await loadCampaigns();
    await loadRecipients();
  } catch (e) {
    toast(`Не удалось отметить: ${e.message}`, 'warn');
  }
}

/** Перелистнуть список получателей. */
function recipientsPage(dir) {
  if (!recState) return;
  const next = recState.offset + dir * REC_PAGE;
  if (next < 0) return;
  recState.offset = next;
  loadRecipients();
}

export function initCampaigns() {
  // Обработчиков на кнопки здесь нет намеренно: у них в разметке есть
  // data-act="toggleCampaignAi" / "toggleCampaignSpintax", и делегированный
  // слушатель из actions.js уже их вызывает. Второй обработчик переключал
  // состояние дважды за один клик — переключатель выглядел неработающим.

  // Скелет ИИ-оффера сохранили на странице «Офферы» — форма рассылки должна
  // показывать актуальный текст, а не тот, что был при загрузке страницы.
  document.addEventListener('mf:aioffer-saved', (e) => {
    const s = e.detail || {};
    aiTemplate = s.template || '';
    aiAllowed = !!s.allowed;
    aiPlanNames = s.plan_names || '';
    aiProviderReady = !!s.provider_ready;
    paintModes();
  });
  paintModes();
  loadCampaignAccounts();
  refreshAiAvailability();
  restoreTestReplyWatch();

  // Делегирование: строки перерисовываются после каждой загрузки.
  document.getElementById('campaignList')?.addEventListener('click', (e) => {
    const cancel = e.target.closest('[data-cancel-campaign]');
    if (cancel) {
      cancelCampaign(Number(cancel.dataset.cancelCampaign));
      return;
    }
    const del = e.target.closest('[data-del-campaign]');
    if (del) {
      removeCampaign(Number(del.dataset.delCampaign));
      return;
    }

    const rec = e.target.closest('[data-recipients-campaign]');
    if (rec) {
      // В кампании хранится только имя базы, поэтому id ищем по имени.
      const dbName = rec.dataset.campaignDb || '';
      const base = bases.find((b) => b.name === dbName);
      openCampaignRecipients(
        Number(rec.dataset.recipientsCampaign),
        rec.dataset.campaignName || '',
        base ? base.id : null,
      );
    }
  });

  // Список к отправке: выделение и отметки.
  document.getElementById('campaignRecipientsList')?.addEventListener('change', (e) => {
    const all = e.target.closest('#recCheckAll');
    if (all) {
      document.querySelectorAll('[data-rec-check]').forEach((box) => {
        box.checked = all.checked;
        const id = Number(box.dataset.recCheck);
        if (all.checked) recSelected.add(id);
        else recSelected.delete(id);
      });
      updateSendBar();
      return;
    }
    const one = e.target.closest('[data-rec-check]');
    if (one) {
      const id = Number(one.dataset.recCheck);
      if (one.checked) recSelected.add(id);
      else recSelected.delete(id);
      updateSendBar();
    }
  });

  document.getElementById('campaignMarkSent')?.addEventListener('click', () => markSends('sent'));
  document
    .getElementById('campaignMarkFailed')
    ?.addEventListener('click', () => markSends('failed'));
  document.getElementById('campaignSelClear')?.addEventListener('click', () => {
    recSelected = new Set();
    document.querySelectorAll('[data-rec-check]').forEach((box) => {
      box.checked = false;
    });
    updateSendBar();
  });
  document
    .getElementById('campaignRecipientsPrev')
    ?.addEventListener('click', () => recipientsPage(-1));
  document
    .getElementById('campaignRecipientsNext')
    ?.addEventListener('click', () => recipientsPage(1));
  document
    .getElementById('campaignRecipientsClose')
    ?.addEventListener('click', closeCampaignRecipients);
}
