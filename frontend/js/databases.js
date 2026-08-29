/** Базы лидов: список баз, поиск, просмотр лидов внутри базы, удаление.
 *
 *  Всё содержимое приходит из /api/leads — выдуманных баз и контактов здесь нет.
 *  Пустой список так и показываем пустым: раньше на странице лежали три
 *  придуманных человека, из-за которых казалось, что база уже собрана.
 *
 *  Два режима одной страницы: сетка баз и таблица лидов внутри базы.
 *  Поиск в сетке ищет по названию базы, поиск в таблице — по каналам.
 */
import { api, apiDownload, apiUpload, esc, getToken } from './api.js';
import { toast, showSkeleton, openModal, closeModal, confirmAction } from './ui.js?v=tal32';
import { loadCampaignBases } from './campaigns.js?v=zephyr3';

/* Состояние: какая база открыта и с какими фильтрами. */
let databases = [];
let openDbId = null; // null — показываем сетку баз
let openDbName = '';
let leadFilters = { q: '', status: '', sort: 'subs_desc', offset: 0, limit: 50 };
let leadTotal = 0;
let searchTimer = null;

/* Выбранные лиды для массовых действий.
 *
 *  Выбор живёт только в пределах одной страницы и сбрасывается при любой
 *  перезагрузке списка. Сохранять его между страницами опасно: человек видит
 *  пять галочек, а удаляет триста выбранных пять страниц назад.
 */
let selected = new Set();

const STATUS_LABEL = {
  new: 'Новый',
  contacted: 'Написали',
  replied: 'Ответил',
  interested: 'Заинтересован',
  unsubscribed: 'Отписан',
};
const STATUS_CLASS = {
  new: 'pending',
  contacted: 'on',
  replied: 'on',
  interested: 'on',
  unsubscribed: 'off',
};
const TG_STATUS = {
  verified: ['Telegram подтверждён', 'ok'],
  pending: ['ожидает проверки', 'wait'],
  channel: ['только канал', 'warn'],
  bot: ['бот', 'warn'],
  rejected: ['scam/fake', 'bad'],
  error: ['ошибка проверки', 'bad'],
  missing: ['Telegram не найден', 'muted'],
  unknown: ['контакт из старой базы', 'muted'],
};

const DB_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>';
const TRASH_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/></svg>';

/** 12400 -> «12 400». Формат как на бэкенде, чтобы числа выглядели одинаково. */
function group(n) {
  if (n === null || n === undefined) return '—';
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

function shortDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}

/** Ссылка на канал: по @handle, если он есть, иначе по channel_id.
 *  Логика повторяет channel_link() на бэкенде, чтобы ссылки совпадали. */
function channelLink(lead) {
  if (lead.custom_url) {
    const handle = lead.custom_url.startsWith('@') ? lead.custom_url : `@${lead.custom_url}`;
    return `https://www.youtube.com/${handle}`;
  }
  return `https://www.youtube.com/channel/${lead.channel_id}`;
}

/* ---------- СЕТКА БАЗ ---------- */

/** Загрузить и отрисовать базы. `q` приходит из поля поиска. */
export async function loadDatabases() {
  const grid = document.getElementById('databasesGrid');
  if (!grid || !getToken()) return;
  const q = (document.getElementById('dbSearch')?.value || '').trim();
  showSkeleton(grid, 6, 'card');
  try {
    const data = await api('GET', `/api/leads/databases?q=${encodeURIComponent(q)}`);
    databases = data.databases || [];
    renderDatabases(data.total_leads || 0, q);
  } catch (e) {
    grid.innerHTML = `<div class="table-empty">Не удалось загрузить базы: ${esc(e.message)}</div>`;
  }
}

function renderDatabases(totalLeads, q) {
  const grid = document.getElementById('databasesGrid');
  const totalBox = document.getElementById('dbTotals');
  grid.innerHTML = '';

  if (totalBox) {
    totalBox.textContent = databases.length
      ? `Баз: ${databases.length} · всего лидов: ${group(totalLeads)}`
      : '';
  }

  if (!databases.length) {
    // Разделяем «ничего не нашлось» и «баз ещё нет» — это разные ситуации.
    grid.innerHTML = q
      ? `<div class="table-empty">По запросу «${esc(q)}» баз не найдено</div>`
      : '<div class="table-empty">Баз пока нет. Соберите первую на странице «Поиск лидов».</div>';
    return;
  }

  databases.forEach((d) => {
    const card = document.createElement('div');
    card.className = 'db-card';
    card.innerHTML = `
      <div class="db-top">
        <div class="db-icon">${DB_SVG}</div>
        <button class="mini-btn" data-tip="Удалить базу" data-del-db="${d.id}">${TRASH_SVG}</button>
      </div>
      <div>
        <div class="db-title">${esc(d.name)}</div>
        <div class="db-sub">${esc(d.query || d.mode || '')}</div>
      </div>
      <div class="db-stats">
        <span>Лидов: ${group(d.leads_count)}</span>
        <span>${d.units_spent ? 'Единиц: ' + group(d.units_spent) : shortDate(d.created_at)}</span>
      </div>
      <button class="btn ghost" data-open-db="${d.id}" style="margin-top:12px;width:100%">Открыть</button>`;
    grid.appendChild(card);
  });
}

/** Поиск по базам с задержкой: без неё запрос уходит на каждую букву. */
export function searchDatabases() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadDatabases, 300);
}

export async function deleteDatabase(id) {
  const db = databases.find((x) => x.id === id);
  const label = db ? db.name : 'база';
  // Лиды удаляются каскадом — предупреждаем прямо, отменить это нельзя.
  if (
    !(await confirmAction(`Удалить базу «${label}» вместе со всеми лидами? Отменить не получится.`))
  )
    return;
  try {
    await api('DELETE', `/api/leads/databases/${id}`);
    toast(`База «${label}» удалена`, 'info');
    if (openDbId === id) backToDatabases();
    await loadDatabases();
    // Селект получателей в рассылке держал удалённую базу — обновляем.
    loadCampaignBases();
  } catch (e) {
    toast(`Не удалось удалить: ${e.message}`, 'warn');
  }
}

/* ---------- ВЫБОР СТРОК И МАССОВЫЕ ДЕЙСТВИЯ ---------- */

/** Цвет балла. Пороги те же, по которым балл читается глазами: 70+ — брать в работу. */
function scoreClass(v) {
  if (v === null || v === undefined) return 'cold';
  if (v >= 70) return 'hot';
  if (v >= 40) return 'warm';
  return 'cold';
}

/** Подсказка при наведении: не пересказ формулы, а что с этим лидом делать. */
function scoreTip(lead) {
  const v = lead?.score;
  if (v === null || v === undefined) return 'Балл ещё не посчитан';
  const level = v >= 70 ? 'Тёплый лид' : v >= 40 ? 'Средний лид' : 'Холодный лид';
  const reasons = Array.isArray(lead.score_reasons) ? lead.score_reasons.filter(Boolean) : [];
  return `${level} (${v}/100)${reasons.length ? ': ' + reasons.join(' · ') : ''}`;
}

/** Правильное слово для числа: 1 лид, 2 лида, 5 лидов. */
function plural(n, one, few, many) {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return many;
  const mod10 = n % 10;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

/** Перерисовать панель массовых действий под текущий выбор. */
function updateBulkBar() {
  const bar = document.getElementById('leadBulkBar');
  if (!bar) return;
  const n = selected.size;
  bar.hidden = n === 0;
  const label = document.getElementById('leadBulkCount');
  if (label) {
    label.textContent = `Выбрано ${n} ${plural(n, 'лид', 'лида', 'лидов')}`;
  }

  // Галочка «выбрать всё» отражает три состояния, а не два: частичный выбор
  // показывается промежуточным видом, иначе она врёт.
  const all = document.getElementById('leadCheckAll');
  if (all) {
    const boxes = document.querySelectorAll('[data-lead-check]');
    all.checked = boxes.length > 0 && n === boxes.length;
    all.indeterminate = n > 0 && n < boxes.length;
  }
}

/** Снять весь выбор. */
export function clearSelection() {
  selected.clear();
  document.querySelectorAll('[data-lead-check]').forEach((b) => {
    b.checked = false;
  });
  document.querySelectorAll('.lead-row.selected').forEach((r) => r.classList.remove('selected'));
  updateBulkBar();
}

function setLeadChecked(id, on) {
  if (on) selected.add(id);
  else selected.delete(id);
  const row = document.querySelector(`.lead-row[data-lead-id="${id}"]`);
  if (row) row.classList.toggle('selected', on);
}

/** Массовая смена статуса. */
export async function bulkSetStatus() {
  if (!selected.size) return;
  const status = document.getElementById('leadBulkStatus')?.value || 'new';
  try {
    const res = await api('POST', '/api/leads/bulk/status', {
      lead_ids: [...selected],
      status,
    });
    toast(
      `Статус обновлён у ${res.affected} ${plural(res.affected, 'лида', 'лидов', 'лидов')}`,
      'success',
    );
    await loadLeads();
  } catch (e) {
    toast(`Не удалось обновить: ${e.message}`, 'warn');
  }
}

/** Массовое удаление. */
export async function bulkDelete() {
  const n = selected.size;
  if (!n) return;
  if (
    !(await confirmAction(
      `Удалить ${n} ${plural(n, 'лид', 'лида', 'лидов')}? Отменить не получится.`,
    ))
  )
    return;
  try {
    const res = await api('POST', '/api/leads/bulk/delete', { lead_ids: [...selected] });
    toast(`Удалено ${res.affected}`, 'info');
    await loadLeads();
    loadDatabases(); // счётчики лидов в карточках баз устарели
  } catch (e) {
    toast(`Не удалось удалить: ${e.message}`, 'warn');
  }
}

/* ---------- ЛИДЫ ВНУТРИ БАЗЫ ---------- */

export function openDatabase(id) {
  const db = databases.find((x) => x.id === id);
  openDbId = id;
  openDbName = db ? db.name : '';
  leadFilters = { q: '', status: '', sort: 'subs_desc', offset: 0, limit: 50 };

  document.getElementById('dbGridView').style.display = 'none';
  document.getElementById('dbLeadsView').style.display = 'block';
  document.getElementById('openDbTitle').textContent = openDbName;
  const search = document.getElementById('leadSearch');
  if (search) search.value = '';
  document
    .querySelectorAll('#leadChips .chip')
    .forEach((c, i) => c.classList.toggle('active', i === 0));
  loadLeads();
}

export function backToDatabases() {
  openDbId = null;
  openDbName = '';
  // Сбрасываем фильтры: иначе «Экспорт CSV» из сетки баз выгрузил бы данные
  // по условиям, которых на экране уже не видно.
  leadFilters = { q: '', status: '', sort: 'subs_desc', offset: 0, limit: 50 };
  document.getElementById('dbGridView').style.display = 'block';
  document.getElementById('dbLeadsView').style.display = 'none';
  loadDatabases();
}

/** Загрузить лиды текущей базы. Без openDbId ищем по всем базам. */
export async function loadLeads() {
  const list = document.getElementById('leadsList');
  if (!list) return;
  // Строки сейчас сменятся — старые галочки больше ни к чему не относятся.
  clearSelection();
  showSkeleton(list, 8, 'row');
  const p = new URLSearchParams({
    q: leadFilters.q,
    sort: leadFilters.sort,
    offset: String(leadFilters.offset),
    limit: String(leadFilters.limit),
  });
  if (openDbId !== null) p.set('database_id', String(openDbId));
  if (leadFilters.status) p.set('status', leadFilters.status);

  try {
    const data = await api('GET', `/api/leads?${p.toString()}`);
    leadTotal = data.total || 0;
    loadedLeads = data.leads || [];
    renderLeads(loadedLeads);
  } catch (e) {
    list.innerHTML = `<div class="table-empty">Не удалось загрузить лидов: ${esc(e.message)}</div>`;
  }
}

// Лиды текущей страницы. Нужны заметке: она открывается по id из строки.
let loadedLeads = [];

function renderLeads(leads) {
  const list = document.getElementById('leadsList');
  const empty = document.getElementById('leadsEmpty');
  list.innerHTML = '';
  empty.classList.toggle('show', leads.length === 0);

  if (!leads.length) {
    empty.textContent =
      leadFilters.q || leadFilters.status
        ? 'По этим условиям лидов нет.'
        : 'В этой базе пока нет лидов.';
    renderLeadCount();
    return;
  }

  // Шапка таблицы — колонки те же, что в .lead-row из CSS.
  const head = document.createElement('div');
  head.className = 'lead-row head';
  // «?» у балла открывает объяснение: колонку с непонятным числом люди
  // просто игнорируют, а балл — главный ориентир, кому писать первым.
  head.innerHTML =
    '<div class="lead-check"><input type="checkbox" id="leadCheckAll" data-tip="Выбрать всё на странице"></div><div>Канал</div><div>Подписчики</div><div>Просмотры</div><div>Видео</div><div class="lead-score-head">Балл <button type="button" class="score-help" data-act="openModal" data-args=\'["modalScoreInfo"]\' aria-label="Что такое балл лида">?</button></div><div>Статус</div><div></div>';
  list.appendChild(head);

  leads.forEach((l) => {
    const row = document.createElement('div');
    row.className = 'lead-row';
    const link = channelLink(l);
    const tg = TG_STATUS[l.tg_status] || TG_STATUS.unknown;
    const tgTitle =
      l.tg_error ||
      (l.tg_username ? `@${l.tg_username}` : l.telegram_channel ? `@${l.telegram_channel}` : tg[0]);
    row.dataset.leadId = String(l.id);
    row.innerHTML = `
      <div class="lead-check"><input type="checkbox" data-lead-check="${l.id}"></div>
      <div class="lead-main">
        ${l.thumbnail ? `<img class="lead-ava" src="${esc(l.thumbnail)}" alt="">` : '<div class="lead-ava"></div>'}
        <div style="min-width:0">
          <div class="lead-title">${esc(l.title) || '—'}</div>
          <div class="lead-sub">${esc(l.matched_keyword || '')}${l.country ? ' · ' + esc(l.country) : ''} <span class="tg-state ${tg[1]}" data-tip="${esc(tgTitle)}">${esc(tg[0])}</span></div>
        </div>
      </div>
      <div class="lead-num">${group(l.subscribers)}</div>
      <div class="lead-num">${group(l.view_count)}</div>
      <div class="lead-num">${group(l.video_count)}</div>
      <div class="lead-score ${scoreClass(l.score)}" data-tip="${esc(scoreTip(l))}">${l.score === null || l.score === undefined ? '—' : l.score}</div>
      <div><span class="badge ${STATUS_CLASS[l.status] || 'pending'}">${STATUS_LABEL[l.status] || esc(l.status || '')}</span></div>
      <div class="lead-act">
        <button type="button" class="mini-btn${l.note ? ' has-note' : ''}" data-act="openLeadNote" data-args='[${l.id}]' data-tip="${l.note ? esc(l.note.slice(0, 120)) : 'Добавить заметку'}" aria-label="Заметка по лиду">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16v12l-4 4H4z"/><path d="M16 20v-4h4"/><path d="M8 9h8"/><path d="M8 13h5"/></svg>
        </button>
        <a class="mini-btn" href="${esc(link)}" target="_blank" rel="noopener noreferrer" data-tip="Открыть канал на YouTube">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><path d="M15 3h6v6"/><path d="M10 14L21 3"/></svg>
        </a>
      </div>`;
    list.appendChild(row);
  });

  renderLeadCount();
}

function renderLeadCount() {
  const box = document.getElementById('leadCount');
  if (!box) return;
  const shown = Math.min(leadFilters.offset + leadFilters.limit, leadTotal);
  box.textContent = leadTotal
    ? `Показано ${leadFilters.offset + 1}–${shown} из ${group(leadTotal)}`
    : '';
  const prev = document.getElementById('leadPrev');
  const next = document.getElementById('leadNext');
  if (prev) prev.disabled = leadFilters.offset === 0;
  if (next) next.disabled = leadFilters.offset + leadFilters.limit >= leadTotal;
}

export function leadPage(delta) {
  const at = leadFilters.offset + delta * leadFilters.limit;
  if (at < 0 || at >= leadTotal) return;
  leadFilters.offset = at;
  loadLeads();
}

export function sortLeads(value) {
  leadFilters.sort = value;
  leadFilters.offset = 0;
  loadLeads();
}

/** Поиск по лидам. Фильтрует на бэкенде, а не скрывает строки в DOM:
 *  раньше искалось только по загруженной странице, и результат был неполным. */
export function filterLeads() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    leadFilters.q = (document.getElementById('leadSearch')?.value || '').trim();
    leadFilters.offset = 0;
    loadLeads();
  }, 300);
}

/** Выгрузка лидов в CSV.
 *
 *  Файл собирает бэкенд (GET /api/leads/export). Раньше это делал браузер:
 *  тянул базу страницами по 500 строк и клеил CSV вручную. Две реализации
 *  одного формата неизбежно расходятся, и главное — выгруженный так файл
 *  нельзя было залить обратно через импорт: в нём нет channel_id. Теперь
 *  экспорт и импорт говорят на одном языке, а BOM и точку с запятой для Excel
 *  ставит сервер.
 */
export async function exportLeadsCSV() {
  const p = new URLSearchParams();
  if (openDbId !== null) p.set('database_id', String(openDbId));
  if (leadFilters.status) p.set('status', leadFilters.status);

  const query = p.toString();
  const fallback = `${(openDbName || 'leads').replace(/[^\wа-яА-ЯёЁ -]/g, '')}.csv`;
  try {
    const name = await apiDownload(`/api/leads/export${query ? '?' + query : ''}`, fallback);
    toast(`Файл выгружен: ${name}`, 'success');
  } catch (e) {
    toast(`Не удалось выгрузить: ${e.message}`, 'warn');
  }
}

/** Загрузка лидов из CSV.
 *
 *  Если открыта конкретная база — льём в неё. Из сетки баз спрашиваем имя
 *  новой: молча создавать «Импорт от 12.03» — верный способ через месяц
 *  получить десяток одинаковых баз.
 */
export async function importLeadsCSV(file) {
  if (!file) return;

  const form = new FormData();
  form.append('file', file);
  if (openDbId !== null) {
    form.append('database_id', String(openDbId));
  } else {
    const name = prompt('Название новой базы для импорта:', file.name.replace(/\.csv$/i, ''));
    if (name === null) return; // отмена — не ошибка, молча выходим
    form.append('database_name', name.trim());
  }

  try {
    const res = await apiUpload('/api/leads/import', form);
    // Сообщаем и про дубли, и про пропуски: «загружено 0» без причины
    // выглядит как поломка, хотя обычно это повторная заливка того же файла.
    const parts = [`Загружено: ${res.created}`];
    if (res.duplicates) parts.push(`уже были: ${res.duplicates}`);
    if (res.skipped) parts.push(`пропущено: ${res.skipped}`);
    toast(parts.join(' · '), res.created ? 'success' : 'info');
    if (res.errors && res.errors.length) {
      toast(`Проблемные строки: ${res.errors.slice(0, 3).join('; ')}`, 'warn');
    }

    await loadDatabases();
    loadCampaignBases(); // новая база должна появиться в селекте рассылки
    if (openDbId !== null) await loadLeads();
  } catch (e) {
    toast(`Не удалось загрузить: ${e.message}`, 'warn');
  }
}

/** Проверить Telegram-контакты лидов открытой базы.
 *
 *  Нужно плейсхолдерам {first_name}/{last_name} в офферах: YouTube отдаёт только
 *  название канала, а имя человека есть только в Telegram. За раз берём пачку:
 *  каждый запрос идёт с живого аккаунта, и сотни подряд — путь к блокировке.
 */
export async function resolveLeadNames() {
  if (!openDbId) {
    toast('Сначала откройте базу', 'warn');
    return;
  }
  toast('Проверяем контакты через подключённые Telegram-аккаунты…', 'info');
  try {
    const res = await api('POST', '/api/leads/resolve-telegram', {
      database_id: openDbId,
      limit: 30,
      only_missing: true,
    });
    if (!res.checked) {
      toast('Все найденные Telegram-контакты уже проверены', 'info');
      return;
    }
    toast(
      `Подтверждено личных аккаунтов: ${res.resolved} из ${res.checked}`,
      res.resolved ? 'success' : 'warn',
    );
    loadLeads();
  } catch (e) {
    toast(`Не удалось: ${e.message}`, 'warn');
  }
}

export function initDatabases() {
  const grid = document.getElementById('databasesGrid');
  if (!grid) return;

  // Делегирование: карточки перерисовываются, точечные слушатели терялись бы.
  grid.addEventListener('click', (e) => {
    const open = e.target.closest('[data-open-db]');
    if (open) {
      openDatabase(Number(open.dataset.openDb));
      return;
    }
    const del = e.target.closest('[data-del-db]');
    if (del) deleteDatabase(Number(del.dataset.delDb));
  });

  document.getElementById('dbSearch')?.addEventListener('input', searchDatabases);
  document.getElementById('leadSearch')?.addEventListener('input', filterLeads);
  document.getElementById('leadSort')?.addEventListener('change', (e) => sortLeads(e.target.value));

  /* Импорт и экспорт. Обработчики навешиваются здесь, а не через onclick в
     разметке: inline-обработчики требуют 'unsafe-inline' в CSP, а мы от него
     как раз уходим. */
  document.getElementById('leadExportBtn')?.addEventListener('click', exportLeadsCSV);

  const importInput = document.getElementById('leadImportFile');
  document.getElementById('leadImportBtn')?.addEventListener('click', () => importInput?.click());
  importInput?.addEventListener('change', async () => {
    const file = importInput.files && importInput.files[0];
    await importLeadsCSV(file);
    // Сброс значения: без него повторный выбор ТОГО ЖЕ файла не даёт
    // события change и кнопка выглядит сломанной.
    importInput.value = '';
  });

  /* Выбор строк — через делегирование: таблица перерисовывается целиком. */
  document.getElementById('leadsList')?.addEventListener('change', (e) => {
    const all = e.target.closest('#leadCheckAll');
    if (all) {
      document.querySelectorAll('[data-lead-check]').forEach((box) => {
        box.checked = all.checked;
        setLeadChecked(Number(box.dataset.leadCheck), all.checked);
      });
      updateBulkBar();
      return;
    }
    const one = e.target.closest('[data-lead-check]');
    if (!one) return;
    setLeadChecked(Number(one.dataset.leadCheck), one.checked);
    updateBulkBar();
  });

  document.getElementById('leadBulkApply')?.addEventListener('click', bulkSetStatus);
  document.getElementById('leadBulkDelete')?.addEventListener('click', bulkDelete);
  document.getElementById('leadBulkClear')?.addEventListener('click', clearSelection);

  document.querySelectorAll('#leadChips .chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('#leadChips .chip').forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      leadFilters.status = chip.dataset.status || '';
      leadFilters.offset = 0;
      loadLeads();
    });
  });

  // Данные не грузим: initDatabases() работает до входа, токена ещё нет и
  // запрос вернул бы 401. Загрузка идёт из enterApp() после авторизации.
}

/* ---------- ЗАМЕТКА ПО ЛИДУ ---------- */

// Какой лид сейчас открыт в модалке. Держим id, а не сам объект: список
// перерисовывается фоновым обновлением, и ссылка на старую строку протухает.
let noteLeadId = null;

/** Открыть заметку. Текст берём из уже загруженной строки — лишний запрос
 *  к серверу ради поля, которое уже есть в списке, тут не нужен. */
export function openLeadNote(id) {
  const lead = (loadedLeads || []).find((l) => l.id === Number(id));
  if (!lead) return;
  noteLeadId = lead.id;
  const title = document.getElementById('leadNoteTitle');
  const text = document.getElementById('leadNoteText');
  if (title) title.textContent = lead.title || 'Лид';
  if (text) text.value = lead.note || '';
  openModal('modalLeadNote');
  if (text) text.focus();
}

/** Сохранить заметку. Пустое поле стирает её — отдельной кнопки «удалить» нет. */
export async function saveLeadNote() {
  if (!noteLeadId) return;
  const text = document.getElementById('leadNoteText');
  const button = document.getElementById('leadNoteSave');
  const value = (text?.value || '').trim();
  if (button) button.disabled = true;
  try {
    const res = await api('PUT', `/api/leads/${noteLeadId}/note`, { note: value });
    if (res.ok === false) {
      toast(res.error || 'Не удалось сохранить заметку', 'error');
      return;
    }
    // Обновляем строку в памяти, чтобы иконка сразу стала «с заметкой»
    // и повторное открытие показало новый текст без перезагрузки списка.
    const lead = (loadedLeads || []).find((l) => l.id === noteLeadId);
    if (lead) lead.note = value;
    renderLeads(loadedLeads || []);
    closeModal('modalLeadNote');
    toast(value ? 'Заметка сохранена' : 'Заметка удалена', 'success');
  } finally {
    if (button) button.disabled = false;
  }
}
