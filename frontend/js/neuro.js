/** Нейрокомментинг: площадки, настройка, запуск, журнал.
 *
 *  Раздел сознательно отделён от рассылки: это разные действия с разной ценой
 *  ошибки. Общее у них одно — дневной лимит аккаунта, и он показывается здесь
 *  одним числом, потому что в Telegram репутация общая на аккаунт.
 *
 *  Вступление в чат — только по кнопке, по одной площадке за раз. Массовые
 *  вступления быстрее всего приводят к блокировке, поэтому «вступить во всё
 *  найденное» в интерфейсе нет намеренно.
 */

import { api } from './api.js';
import { escapeHtml } from './shared/dom.js';

const TIER_HINTS = {
  fresh: 'Аккаунт новый — темп минимальный, это нормально.',
  young: 'Аккаунту меньше месяца — работаем осторожно.',
  mature: 'Аккаунт зрелый — доступен рабочий темп.',
};

function el(id) {
  return document.getElementById(id);
}

function notify(message, kind) {
  if (typeof window.toast === 'function') window.toast(message, kind);
}

function statusLabel(target) {
  if (!target.joined) return '<span class="neuro-tag warn">не вступлен</span>';
  if (!target.can_comment) return '<span class="neuro-tag warn">нет обсуждений</span>';
  if (target.status === 'paused') return '<span class="neuro-tag">пауза</span>';
  if (target.status === 'approved') return '<span class="neuro-tag ok">работает</span>';
  return `<span class="neuro-tag">${escapeHtml(target.status)}</span>`;
}

function targetRow(target) {
  const members = target.members ? `${target.members.toLocaleString('ru-RU')} участников` : '';
  const slowmode = target.slowmode_seconds ? `слоумод ${target.slowmode_seconds} с` : '';
  const meta = [members, slowmode, `комментариев: ${target.comments_sent || 0}`]
    .filter(Boolean)
    .join(' · ');
  const joinBtn = target.joined
    ? ''
    : `<button class="btn ghost sm" data-act="joinNeuroTarget" data-args='[${target.id}]'>Вступить</button>`;
  const pauseBtn =
    target.status === 'paused'
      ? `<button class="btn ghost sm" data-act="setNeuroTargetStatus" data-args='[${target.id},"approved"]'>Включить</button>`
      : `<button class="btn ghost sm" data-act="setNeuroTargetStatus" data-args='[${target.id},"paused"]'>Пауза</button>`;
  return `
    <div class="neuro-row">
      <div class="neuro-row-main">
        <div class="neuro-row-title">@${escapeHtml(target.username)} ${statusLabel(target)}</div>
        <div class="neuro-row-sub">${escapeHtml(target.title || '')}</div>
        <div class="neuro-row-meta">${escapeHtml(meta)}</div>
        ${target.last_error ? `<div class="neuro-row-err">${escapeHtml(target.last_error)}</div>` : ''}
      </div>
      <div class="neuro-row-actions">
        ${joinBtn}
        ${pauseBtn}
        <button class="btn ghost sm" data-act="deleteNeuroTarget" data-args='[${target.id}]'>Удалить</button>
      </div>
    </div>`;
}

export async function loadNeuroTargets() {
  const box = el('neuroTargets');
  if (!box) return;
  try {
    const data = await api('GET', '/api/neuro/targets');
    const targets = data.targets || [];
    box.innerHTML = targets.length
      ? targets.map(targetRow).join('')
      : '<div class="neuro-empty">Площадок пока нет. Добавьте вручную или найдите по теме.</div>';
  } catch (e) {
    box.innerHTML = `<div class="neuro-empty">${escapeHtml(e.message)}</div>`;
  }
}

export async function loadNeuroBudget() {
  const box = el('neuroBudget');
  if (!box) return;
  try {
    const b = await api('GET', '/api/neuro/budget');
    if (!b.account_id) {
      box.innerHTML = `<div class="neuro-empty">${escapeHtml(b.reason || 'Аккаунт не подключён')}</div>`;
      return;
    }
    const hint = TIER_HINTS[b.tier] || '';
    const reason = b.allowed ? '' : `<div class="neuro-row-err">${escapeHtml(b.reason)}</div>`;
    box.innerHTML = `
      <div class="neuro-budget-main">
        <div>
          <div class="neuro-budget-num">${b.left_today}</div>
          <div class="neuro-budget-cap">действий осталось сегодня</div>
        </div>
        <div class="neuro-budget-note">
          <div><b>${escapeHtml(b.account_label || 'Аккаунт')}</b> · ${escapeHtml(b.tier_title || '')}</div>
          <div>Израсходовано сегодня: ${b.used_today}. Личные сообщения и комментарии
          считаются вместе — лимит в Telegram общий на аккаунт.</div>
          <div class="neuro-budget-hint">${escapeHtml(hint)}</div>
          ${reason}
        </div>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="neuro-empty">${escapeHtml(e.message)}</div>`;
  }
}

export async function loadNeuroRun() {
  try {
    const run = await api('GET', '/api/neuro/run');
    if (el('neuroPersona')) el('neuroPersona').value = run.persona || '';
    if (el('neuroLink')) el('neuroLink').value = run.link || '';
    if (el('neuroDailyLimit')) el('neuroDailyLimit').value = run.daily_limit || 8;
    const btn = el('neuroStartBtn');
    if (btn) {
      const running = run.status === 'running';
      btn.textContent = running ? 'Идёт прогон…' : 'Запустить';
      btn.disabled = running;
    }
    if (run.last_note) notifyOnce(run.last_note);
  } catch (_) {
    /* раздел открывается и без сохранённой настройки */
  }
}

let lastNote = '';
function notifyOnce(note) {
  if (note === lastNote) return;
  lastNote = note;
}

export async function loadNeuroLog() {
  const box = el('neuroLog');
  if (!box) return;
  try {
    const data = await api('GET', '/api/neuro/log?limit=50');
    const items = data.items || [];
    box.innerHTML = items.length
      ? items
          .map((item) => {
            const when = item.created_at ? new Date(item.created_at).toLocaleString('ru-RU') : '';
            const body = item.status === 'sent' ? item.text : item.error || item.text;
            return `
              <div class="neuro-row">
                <div class="neuro-row-main">
                  <div class="neuro-row-title">
                    <span class="neuro-tag ${item.status === 'sent' ? 'ok' : 'warn'}">${escapeHtml(item.status)}</span>
                    <span class="neuro-row-meta">${escapeHtml(when)}</span>
                  </div>
                  <div class="neuro-row-sub">${escapeHtml(body || '')}</div>
                </div>
              </div>`;
          })
          .join('')
      : '<div class="neuro-empty">Комментариев пока не было.</div>';
  } catch (e) {
    box.innerHTML = `<div class="neuro-empty">${escapeHtml(e.message)}</div>`;
  }
}

export async function addNeuroTarget() {
  const input = el('neuroTargetInput');
  const username = (input?.value || '').trim().replace(/^@/, '');
  if (!username) return notify('Укажите @имя канала или чата', 'error');
  try {
    await api('POST', '/api/neuro/targets', { username });
    input.value = '';
    notify('Площадка добавлена', 'success');
    await loadNeuroTargets();
  } catch (e) {
    notify(e.message, 'error');
  }
}

export async function searchNeuroTargets() {
  const input = el('neuroSearchInput');
  const box = el('neuroSearchResults');
  const query = (input?.value || '').trim();
  if (query.length < 2) return notify('Слишком короткий запрос', 'error');
  box.hidden = false;
  box.innerHTML = '<div class="neuro-empty">Ищем…</div>';
  try {
    const data = await api('POST', '/api/neuro/search', { query, limit: 20 });
    const results = data.results || [];
    box.innerHTML = results.length
      ? `<div class="neuro-note">${escapeHtml(data.note || '')}</div>` +
        results
          .map((item) => {
            const members = item.participants
              ? `${item.participants.toLocaleString('ru-RU')} участников`
              : '';
            const action = item.known
              ? '<span class="neuro-tag">уже добавлено</span>'
              : `<button class="btn ghost sm" data-act="addFoundNeuroTarget" data-args='["${escapeHtml(item.username)}"]'>Добавить</button>`;
            return `
              <div class="neuro-row">
                <div class="neuro-row-main">
                  <div class="neuro-row-title">@${escapeHtml(item.username)}</div>
                  <div class="neuro-row-sub">${escapeHtml(item.title || '')}</div>
                  <div class="neuro-row-meta">${escapeHtml([item.kind, members].filter(Boolean).join(' · '))}</div>
                </div>
                <div class="neuro-row-actions">${action}</div>
              </div>`;
          })
          .join('')
      : '<div class="neuro-empty">Ничего не нашлось.</div>';
  } catch (e) {
    box.innerHTML = `<div class="neuro-empty">${escapeHtml(e.message)}</div>`;
  }
}

export async function addFoundNeuroTarget(username) {
  try {
    await api('POST', '/api/neuro/targets', { username });
    notify(`@${username} добавлен`, 'success');
    await loadNeuroTargets();
    await searchNeuroTargets();
  } catch (e) {
    notify(e.message, 'error');
  }
}

export async function joinNeuroTarget(id) {
  try {
    await api('POST', `/api/neuro/targets/${id}/join`);
    notify('Вступили в чат', 'success');
    await loadNeuroTargets();
    await loadNeuroBudget();
  } catch (e) {
    notify(e.message, 'error');
  }
}

export async function setNeuroTargetStatus(id, status) {
  try {
    await api('POST', `/api/neuro/targets/${id}/status`, { status });
    await loadNeuroTargets();
  } catch (e) {
    notify(e.message, 'error');
  }
}

export async function deleteNeuroTarget(id) {
  try {
    await api('DELETE', `/api/neuro/targets/${id}`);
    await loadNeuroTargets();
  } catch (e) {
    notify(e.message, 'error');
  }
}

export async function saveNeuroRun() {
  const payload = {
    name: 'Нейрокомментинг',
    mode: 'targets',
    topic: '',
    persona: (el('neuroPersona')?.value || '').trim(),
    link: (el('neuroLink')?.value || '').trim(),
    daily_limit: Number(el('neuroDailyLimit')?.value || 8),
  };
  try {
    await api('PUT', '/api/neuro/run', payload);
    notify('Настройка сохранена', 'success');
    await loadNeuroBudget();
  } catch (e) {
    notify(e.message, 'error');
  }
}

export async function previewNeuroComment() {
  const box = el('neuroPreview');
  const username = (el('neuroTargetInput')?.value || '').trim().replace(/^@/, '');
  if (!username) return notify('Впишите @имя площадки в поле выше', 'error');
  box.hidden = false;
  box.innerHTML = 'Готовим комментарий…';
  try {
    const data = await api('POST', '/api/neuro/preview', { username });
    box.innerHTML = `
      <div class="neuro-preview-post"><b>Пост:</b> ${escapeHtml((data.post || '').slice(0, 400))}</div>
      <div class="neuro-preview-comment"><b>Комментарий:</b> ${escapeHtml(data.comment || '')}</div>`;
  } catch (e) {
    box.innerHTML = escapeHtml(e.message);
  }
}

export async function startNeuro() {
  try {
    await api('POST', '/api/neuro/start');
    notify('Прогон запущен', 'success');
    await loadNeuroRun();
    // Журнал наполняется постепенно: между комментариями стоят минуты пауз.
    setTimeout(() => {
      loadNeuroLog();
      loadNeuroBudget();
      loadNeuroRun();
    }, 15000);
  } catch (e) {
    notify(e.message, 'error');
  }
}

export async function reloadNeuro() {
  await Promise.all([loadNeuroBudget(), loadNeuroTargets(), loadNeuroRun(), loadNeuroLog()]);
}

let loaded = false;

export function initNeuro() {
  // Раздел тяжёлый (четыре запроса), поэтому грузится при первом открытии,
  // а не на старте приложения.
  window.addEventListener('mf:navigate', (event) => {
    if (event.detail?.target !== 'neuro') return;
    if (loaded) {
      loadNeuroBudget();
      return;
    }
    loaded = true;
    reloadNeuro();
  });
}
