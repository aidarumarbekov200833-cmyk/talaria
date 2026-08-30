/** Админ-панель: сводка по всем пользователям.
 *
 *  Виден раздел только админу (window.__isAdmin, из /api/me → is_admin).
 *  Бэкенд всё равно проверяет права на каждом запросе — фронтовое скрытие
 *  это удобство, а не защита. Секретов бэкенд не отдаёт: только счётчики,
 *  профиль и факт наличия ключей.
 */
import { api, apiDownload, esc, getToken, setToken } from './api.js';
import { toast, confirmAction } from './ui.js?v=tal32';

const STATUS_LABEL = {
  new: 'новые',
  contacted: 'написали',
  replied: 'ответили',
  interested: 'заинтересованы',
  unsubscribed: 'отписались',
};
// Тариф в Talaria один, поэтому подпись бинарная: «Тариф» или «Без тарифа»
// (плюс пробный). Старые слаги (pro/max/ultra/zephyr) в базе остались от
// ранних версий — все читаются как «Тариф», чтобы админ не гадал, что за
// «Ультра» и чем она отличается.
const PLAN_LABEL = { none: 'Без тарифа', free: 'Без тарифа', trial: 'Пробный' };
const planLabel = (slug) => PLAN_LABEL[slug] || (slug ? 'Тариф' : '—');

// Причины отклонения входа из бэкенда (security/access) — в человеческий вид.
// Список должен совпадать с причинами из LoginAttempt на бэкенде. Раньше
// четырёх причин здесь не было, и админ видел в журнале сырой код вроде
// «clock_skew» — т.е. ровно в тех случаях, когда надо было разбираться.
const LOGIN_REASON = {
  ok: 'успешно',
  not_whitelisted: 'нет в белом списке',
  hash_mismatch: 'неверная подпись',
  expired: 'устаревшие данные',
  blocked: 'доступ заблокирован',
  no_bot: 'бот не настроен',
  clock_skew: 'расхождение часов',
  hash_missing: 'подпись не передана',
  auth_date_invalid: 'битая дата входа',
  bot_token_missing: 'нет токена бота',
  demo_mode: 'бот не настроен',
};
const loginReason = (r) => LOGIN_REASON[r] || r || '—';

function fmtDateTime(v) {
  return v
    ? new Date(v).toLocaleString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—';
}

let _q = ''; // текущий поисковый запрос
let _offset = 0; // сдвиг по списку пользователей
const _PAGE = 50; // размер страницы
let _scope = 'all'; // группа: all | trial | paid | expired | blocked | flagged

/** Показать/скрыть пункт меню и раздел в зависимости от прав. */
export function applyAdminVisibility(isAdmin) {
  window.__isAdmin = !!isAdmin;
  document.querySelectorAll('[data-admin-only]').forEach((el) => {
    el.style.display = isAdmin ? '' : 'none';
  });
}

/** Сводка админки (GET /api/admin/overview): кто онлайн, кто заходил, что делали. */
export async function loadAdminOverview() {
  const box = document.getElementById('adminOverview');
  if (!box || !getToken() || !window.__isAdmin) return;
  try {
    const d = await api('GET', '/api/admin/overview');
    box.innerHTML = [
      card('Сейчас на сайте', Number(d.online_now || 0), 'активность за 15 минут'),
      card('Входов сегодня', d.logins_today, `отклонено: ${d.failed_logins_today}`),
      card('Действий сегодня', d.events_today, 'событий в журнале'),
      card('Пользователей', d.users_total, `+${d.users_new_7d} за неделю`),
      card(
        'Платных подписок',
        d.active_subs,
        `пробных: ${d.trial_subs || 0} · заблокировано: ${d.blocked_users}`,
      ),
      card(
        'Лидов в базах',
        d.leads_total,
        `офферов: ${d.offers_total}, рассылок: ${d.campaigns_total}`,
      ),
      card(
        'Идёт рассылок',
        d.running_campaigns,
        `Telegram-аккаунтов онлайн: ${d.connected_accounts}`,
      ),
      card(
        'Лидов ждут проверки',
        d.pending_verifications,
        `Telegram-проверка контактов · ошибок сегодня: ${d.errors_today}`,
      ),
    ].join('');
    renderAdminHealth(d);
    renderRevenue(d.revenue || {});
    loadAdminChart();
    renderVisitors(d.recent_visitors || []);
    renderRecentActions(d.recent_actions || []);
    loadPayments();
    loadSupportThreads();
  } catch (e) {
    box.innerHTML = `<div class="dd-empty">Не удалось загрузить сводку: ${esc(e.message)}</div>`;
  }
}

export async function sendAdminNotification() {
  const target = document.getElementById('adminNoticeTarget')?.value || 'all';
  const userId = Number(document.getElementById('adminNoticeUserId')?.value || 0);
  const message = (document.getElementById('adminNoticeMessage')?.value || '').trim();
  if (!message) {
    toast('Введите сообщение', 'warn');
    return;
  }
  if (target === 'user' && !userId) {
    toast('Укажите ID пользователя', 'warn');
    return;
  }
  if (
    !(await confirmAction(
      `Отправить уведомление: ${target === 'all' ? 'всем пользователям' : target === 'active' ? 'активным пользователям' : `пользователю #${userId}`}?`,
    ))
  )
    return;
  const out = document.getElementById('adminNoticeResult');
  try {
    const data = await api('POST', '/api/admin/notifications', {
      target,
      user_id: target === 'user' ? userId : null,
      message,
      level: document.getElementById('adminNoticeLevel')?.value || 'info',
      telegram: !!document.getElementById('adminNoticeTelegram')?.checked,
    });
    out.textContent = `Внутри сайта: ${data.in_app}; Telegram: ${data.telegram_queued}`;
    document.getElementById('adminNoticeMessage').value = '';
    toast(`Уведомление отправлено: ${data.recipients}`, 'success');
    loadAdminOverview();
  } catch (e) {
    toast(e.message, 'warn');
  }
}

function card(title, value, sub) {
  // Значение бывает и числом (счётчики), и строкой («25 000 KZT · 5 000 RUB»
  // в «Деньгах»). Number() на строке давал «не число» прямо в интерфейсе.
  const shown = typeof value === 'number' ? value.toLocaleString('ru-RU') : String(value ?? '—');
  return `<div class="adm-card">
    <div class="adm-card-label">${esc(title)}</div>
    <div class="adm-card-value">${esc(shown)}</div>
    <div class="adm-card-sub">${esc(sub)}</div>
  </div>`;
}

function renderAdminHealth(d) {
  const put = (id, value) => {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
  };
  const n = (value) => Number(value || 0).toLocaleString('ru-RU');
  put(
    'adminHealthAudience',
    `Пользователи: ${n(d.users_total)} · лиды в базах: ${n(d.leads_total)}`,
  );
  put(
    'adminHealthDelivery',
    `Кампании: ${n(d.running_campaigns)} · аккаунты на связи: ${n(d.connected_accounts)}`,
  );
  put(
    'adminHealthQueue',
    `Лидов ждут Telegram-проверки: ${n(d.pending_verifications)} · ошибки сегодня: ${n(d.errors_today)}`,
  );
  put(
    'adminHealthAccess',
    `Активные подписки: ${n(d.active_subs)} · заблокировано: ${n(d.blocked_users)}`,
  );
}

/* Какой период графика выбран. Живёт в переменной, а не в DOM: обновление
   сводки перерисовывает график, и он должен остаться на выбранном периоде. */
let _chartPeriod = '14d';

/** Переключить период графика активности (кнопки над графиком). */
export function setChartPeriod(period) {
  _chartPeriod = period;
  document.querySelectorAll('[data-chart-period]').forEach((b) => {
    b.classList.toggle('is-active', b.dataset.chartPeriod === period);
  });
  loadAdminChart();
}

/** Загрузить точки графика под выбранный период (GET /api/admin/activity-series). */
async function loadAdminChart() {
  const box = document.getElementById('adminChart');
  if (!box) return;
  try {
    const data = await api('GET', `/api/admin/activity-series?period=${_chartPeriod}`);
    renderAdminChart(data.points || []);
  } catch (e) {
    box.innerHTML = `<div class="dd-empty">Не удалось загрузить график: ${esc(e.message)}</div>`;
  }
}

/** Столбики активности. Масштаб — по максимуму периода: абсолютные числа
 *  здесь менее важны, чем видная форма активности. Подробности — по наведению
 *  на столбик (data-tip). */
function renderAdminChart(points) {
  const box = document.getElementById('adminChart');
  if (!box) return;
  if (!points.length) {
    box.innerHTML = '<div class="dd-empty">Пока нет данных</div>';
    return;
  }
  const max = Math.max(1, ...points.map((d) => Math.max(d.events || 0, d.logins || 0)));
  // На плотных периодах (месяц — 30 столбиков, сутки — до 24) подписываем
  // каждый второй столбик: подписи внахлёст читать нельзя ни одной.
  const labelStep = points.length > 16 ? 2 : 1;
  box.classList.toggle('is-dense', points.length > 16);
  box.innerHTML =
    points
      .map((d, i) => {
        const eh = Math.round(((d.events || 0) / max) * 100);
        const lh = Math.round(((d.logins || 0) / max) * 100);
        const title = `${d.label}: входов ${d.logins || 0}, действий ${d.events || 0}`;
        const label = i % labelStep === 0 ? esc(d.label) : '';
        return `<div class="adm-bar-col" data-tip="${esc(title)}">
      <div class="adm-bars">
        <i class="adm-bar events" style="height:${eh}%"></i>
        <i class="adm-bar logins" style="height:${lh}%"></i>
      </div>
      <span class="adm-bar-label">${label}</span>
    </div>`;
      })
      .join('') +
    '<div class="adm-legend"><span><i class="dot events"></i>действия</span><span><i class="dot logins"></i>входы</span></div>';
}

function renderVisitors(rows) {
  const box = document.getElementById('adminVisitors');
  if (!box) return;
  if (!rows.length) {
    box.innerHTML = '<div class="dd-empty">Входов пока не было</div>';
    return;
  }
  box.innerHTML = rows
    .map((v) => {
      const who = v.name || (v.username ? '@' + v.username : 'неизвестный');
      const status = v.ok
        ? '<span class="key-status ok">вошёл</span>'
        : `<span class="key-status err">${esc(loginReason(v.reason))}</span>`;
      return `<div class="adm-row">
      <div class="adm-row-main">
        <span class="adm-row-name">${esc(who)}</span>
        ${status}
      </div>
      <div class="adm-row-sub">${esc(fmtDateTime(v.at))} · IP ${esc(v.ip || '—')}</div>
    </div>`;
    })
    .join('');
}

function renderRecentActions(rows) {
  const box = document.getElementById('adminRecentActions');
  if (!box) return;
  if (!rows.length) {
    box.innerHTML = '<div class="dd-empty">Действий пока нет</div>';
    return;
  }
  box.innerHTML = rows
    .map((a) => {
      const who = a.user_name || (a.username ? '@' + a.username : '—');
      return `<div class="adm-row">
      <div class="adm-row-main">
        <span class="adm-row-name">${esc(who)}</span>
        <span class="key-status ${LEVEL_CLASS[a.level] || 'muted'}">${esc(SCOPE_LABEL[a.scope] || a.scope || '—')}</span>
      </div>
      <div class="adm-row-sub">${esc(a.message)} · ${esc(fmtDateTime(a.created_at))}</div>
    </div>`;
    })
    .join('');
}

/** Загрузить список пользователей (GET /api/admin/users). */
export async function loadAdmin() {
  const wrap = document.getElementById('adminUsers');
  if (!wrap || !window.__isAdmin) return;
  if (!getToken()) {
    wrap.innerHTML =
      '<div class="dd-empty">Админ-данные доступны только после настоящего входа.</div>';
    return;
  }
  wrap.innerHTML = '<div class="dd-empty">Загрузка…</div>';
  // Промокоды грузим вместе со списком пользователей: это одна страница
  // админки, и отдельная кнопка «обновить» тут была бы лишним кликом.
  loadPromoCodes();
  const search = document.getElementById('adminSearch');
  if (search) _q = search.value.trim();
  try {
    const params = new URLSearchParams({
      q: _q,
      scope: _scope,
      offset: String(_offset),
      limit: String(_PAGE),
    });
    const data = await api('GET', `/api/admin/users?${params}`);
    renderUsers(data);
  } catch (e) {
    wrap.innerHTML = `<div class="dd-empty">Не удалось загрузить: ${esc(e.message)}</div>`;
  }
  // Белый список и журнал входов грузим вместе со списком — они на той же
  // странице. Ошибка одного не должна ронять остальные, поэтому по отдельности.
  loadAdminOverview();
  loadWhitelist();
  loadLoginAttempts();
  loadActivity();
}

/** Переключить страницу пользователей (кнопки «Назад»/«Вперёд»). */
export function adminPage(dir) {
  const next = _offset + dir * _PAGE;
  if (next < 0) return;
  _offset = next;
  loadAdmin();
}

const SCOPE_TITLES = {
  all: 'Все',
  trial: 'Пробные',
  paid: 'Платят',
  expired: 'Истёк срок',
  blocked: 'Заблокированы',
  flagged: 'Похоже на ботов',
};

/** Переключить группу пользователей. */
export function adminScope(name) {
  _scope = SCOPE_TITLES[name] ? name : 'all';
  _offset = 0;
  loadAdmin();
}

/** Открыть или закрыть самостоятельную регистрацию (POST /api/admin/registration). */
export async function adminToggleRegistration(open) {
  const wanted = open === true || open === 'true';
  const ok = await confirmAction(
    wanted
      ? 'Любой человек с аккаунтом Telegram сможет войти сам и получить пробный доступ. Отправка сообщений на пробном тарифе всё равно выключена.'
      : 'Новые люди перестанут входить сами. Те, кто уже зарегистрирован, продолжат пользоваться. Доступ можно будет выдавать вручную через белый список.',
    {
      title: wanted ? 'Открыть регистрацию?' : 'Закрыть регистрацию?',
      okLabel: wanted ? 'Открыть' : 'Закрыть',
    },
  );
  if (!ok) return;
  try {
    const data = await api('POST', '/api/admin/registration', { open: wanted });
    renderUsers(data);
    toast(wanted ? 'Регистрация открыта' : 'Регистрация закрыта');
  } catch (e) {
    toast(e.message, true);
  }
}

function renderScopes(data) {
  const box = document.getElementById('adminScopes');
  if (!box) return;
  const counts = data.counts || {};
  const buttons = Object.keys(SCOPE_TITLES)
    .map((name) => {
      const active = (data.scope || 'all') === name ? ' active' : '';
      const count = counts[name];
      const badge = count === undefined ? '' : `<i>${count}</i>`;
      return `<button type="button" class="adm-scope${active}" data-act="adminScope" data-args='["${name}"]'>${esc(SCOPE_TITLES[name])}${badge}</button>`;
    })
    .join('');
  const open = data.open_registration !== false;
  const toggle = `<div class="adm-reg ${open ? 'on' : 'off'}">
    <span><b>Регистрация ${open ? 'открыта' : 'закрыта'}</b><small>${
      open
        ? 'Любой аккаунт Telegram входит сам и получает пробный доступ без отправки сообщений.'
        : 'Войти могут только те, кто уже зарегистрирован или внесён в белый список.'
    }</small></span>
    <button type="button" class="btn ghost" data-act="adminToggleRegistration" data-args='[${open ? 'false' : 'true'}]'>${open ? 'Закрыть' : 'Открыть'}</button>
  </div>`;
  box.innerHTML = `<div class="adm-scope-row">${buttons}</div>${toggle}`;
}

function renderUsers(data) {
  const wrap = document.getElementById('adminUsers');
  if (!wrap) return;
  const users = data.users || [];

  renderScopes(data);

  const sum = document.getElementById('adminSummary');
  if (sum)
    sum.textContent = `Пользователей: ${Number(data.total_users || 0).toLocaleString('ru-RU')} · всего на этой странице лидов: ${(data.total_leads || 0).toLocaleString('ru-RU')}`;

  const pager = document.getElementById('adminPager');
  const count = document.getElementById('adminCount');
  if (pager) pager.style.display = (data.total_users || 0) > _PAGE ? 'flex' : 'none';
  if (count)
    count.textContent = `Страница ${Math.floor(_offset / _PAGE) + 1} · показано ${users.length} из ${data.total_users || 0}`;

  if (!users.length) {
    wrap.innerHTML = '<div class="dd-empty">Пользователей пока нет.</div>';
    return;
  }

  const rows = users
    .map((u) => {
      const uname = u.username ? '@' + esc(u.username) : '<span class="muted">без username</span>';
      const adminTag = u.is_admin ? '<span class="key-status ok">админ</span>' : '';
      const joined = u.created_at ? new Date(u.created_at).toLocaleDateString('ru-RU') : '—';
      const planCls = u.blocked ? 'err' : u.expired ? 'warn' : u.plan === 'trial' ? 'warn' : 'ok';
      const planTxt = u.blocked
        ? 'заблокирован'
        : u.expired
          ? `${planLabel(u.plan)} · истёк`
          : planLabel(u.plan);
      // «Когда заходил» важнее точного времени: админ ищет живых и брошенных.
      const seen = u.last_login_at ? fmtDateTime(u.last_login_at) : 'ни разу';
      const ip = u.last_ip ? `<div class="key-sub">${esc(u.last_ip)}</div>` : '';
      const flags = (u.flags || []).map((f) => `<span class="adm-flag">${esc(f)}</span>`).join('');
      return `<tr data-admin-user="${u.id}">
      <td>${u.id}</td>
      <td>${esc(u.name || '—')} ${adminTag}<div class="key-sub">${uname} · id ${u.telegram_id}</div>${flags ? `<div class="adm-flags">${flags}</div>` : ''}</td>
      <td><span class="key-status ${planCls}">${esc(planTxt)}</span></td>
      <td>${esc(seen)}${ip}</td>
      <td>${u.databases}</td>
      <td>${(u.leads || 0).toLocaleString('ru-RU')}</td>
      <td>${u.offers}</td>
      <td>${u.campaigns}</td>
      <td>${u.warmup_accounts}</td>
      <td>${u.api_keys}</td>
      <td>${joined}</td>
    </tr>`;
    })
    .join('');

  wrap.innerHTML = `<div class="table-wrap"><table class="lead-table admin-table">
    <thead><tr>
      <th>ID</th><th>Пользователь</th><th>Тариф</th><th>Последний вход</th><th>Базы</th><th>Лиды</th><th>Офферы</th>
      <th>Рассылки</th><th>Прогрев</th><th>Ключи</th><th>Регистрация</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

/** Открыть детали пользователя (GET /api/admin/users/{id}). */
async function openUser(id) {
  const modal = document.getElementById('adminUserModal');
  const body = document.getElementById('adminUserBody');
  if (!modal || !body) return;
  body.innerHTML = '<div class="dd-empty">Загрузка…</div>';
  modal.classList.add('show');
  try {
    const d = await api('GET', `/api/admin/users/${id}`);
    body.innerHTML = detailHtml(d);
  } catch (e) {
    body.innerHTML = `<div class="dd-empty">Не удалось загрузить: ${esc(e.message)}</div>`;
    toast(`Ошибка: ${e.message}`, 'warn');
  }
}

/** Кнопки управления подпиской: тариф есть или нет, без выбора «какой».
 *  Тариф в Talaria один; сумма и валюта записываются в «Деньги» вместе с
 *  активацией — отдельной формы «записать платёж» больше нет. */
function subActionsHtml(d) {
  const u = d.user || {};
  if (!u.id) return '';
  const hasPlan = !['none', 'free', ''].includes(d.stored_plan || 'none');
  return `<div class="ad-block admin-sub-actions">
    <h4>Тариф</h4>
    <div class="admin-plan-grid">
      <label>Месяцев
        <input type="number" id="adminPlanMonths" value="1" min="1" max="24">
      </label>
      <label>Сумма оплаты
        <input type="number" id="adminPlanAmount" min="0" step="0.01" placeholder="0 — без оплаты">
      </label>
      <label>Валюта
        <select id="adminPlanCurrency">
          <option value="RUB">RUB</option>
          <option value="KZT">KZT</option>
          <option value="USD">USD</option>
          <option value="EUR">EUR</option>
        </select>
      </label>
      <label class="admin-plan-note">Заметка
        <input type="text" id="adminPlanNote" placeholder="за что и когда">
      </label>
    </div>
    <div class="admin-sub-row">
      <button class="btn-mini" data-act="adminActivatePlan" data-args='[${u.id}]'>
        ${hasPlan ? 'Продлить тариф' : 'Выдать тариф'}
      </button>
      ${hasPlan ? `<button class="btn-mini danger" data-act="adminRevokePlan" data-args='[${u.id}]'>Снять тариф</button>` : ''}
    </div>
    <div class="admin-sub-row">
      <button class="btn-mini danger" data-act="adminToggleBlock" data-args='[${u.id}, ${u.blocked ? 'false' : 'true'}]'>
        ${u.blocked ? 'Разблокировать' : 'Заблокировать доступ'}
      </button>
      ${
        u.is_admin
          ? '<span class="key-sub">Себя заблокировать нельзя</span>'
          : `<button class="btn-mini" data-act="adminViewAs" data-args='[${u.id}]'>Смотреть его кабинет</button>`
      }
    </div>
  </div>`;
}

/** Ключ, под которым лежит собственный токен админа на время просмотра. */
const VIEW_BACKUP_KEY = 'mf_token_admin';

/**
 * Открыть кабинет пользователя глазами владельца — только чтение.
 *
 * Свой токен прячем отдельно, а не «запоминаем в переменной»: страница при
 * переключении перезагружается, и переменная не пережила бы этого. Без
 * сохранённого токена вернуться из просмотра было бы нечем — пришлось бы
 * логиниться заново.
 */
export async function adminViewAs(userId) {
  const ok = await confirmAction(
    'Открыть кабинет этого пользователя? Вы увидите его данные в режиме только для чтения, ' +
      'а он получит уведомление об этом.',
    { title: 'Смотреть глазами пользователя', okLabel: 'Открыть' },
  );
  if (!ok) return;
  try {
    const data = await api('POST', `/api/admin/users/${userId}/view-as`);
    const mine = getToken();
    if (mine) localStorage.setItem(VIEW_BACKUP_KEY, mine);
    setToken(data.access_token);
    location.reload();
  } catch (e) {
    toast(`Не удалось открыть: ${e.message}`, 'warn');
  }
}

/** Вернуться в свой аккаунт из режима просмотра. */
export function exitViewAs() {
  const mine = localStorage.getItem(VIEW_BACKUP_KEY);
  localStorage.removeItem(VIEW_BACKUP_KEY);
  if (mine) {
    setToken(mine);
    location.reload();
    return;
  }
  // Своего токена нет (например, вкладку открыли после очистки хранилища) —
  // честнее отправить на вход, чем оставить человека в чужом кабинете.
  location.reload();
}

/** Выдать/продлить тариф (POST /api/admin/users/{id}/subscription).
 *  Сумма, если указана, сразу попадает в «Деньги» — отдельной записи не нужно. */
export async function adminActivatePlan(userId) {
  const months = parseInt(document.getElementById('adminPlanMonths')?.value || '1', 10) || 1;
  const amount = Number(document.getElementById('adminPlanAmount')?.value || 0);
  const currency = document.getElementById('adminPlanCurrency')?.value || 'RUB';
  const note = (document.getElementById('adminPlanNote')?.value || '').trim();
  try {
    const d = await api('POST', `/api/admin/users/${userId}/subscription`, {
      plan: 'pro',
      months,
      note,
      amount,
      currency,
    });
    document.getElementById('adminUserBody').innerHTML = detailHtml(d);
    toast(
      'Тариф выдан',
      'success',
      amount ? `Оплата ${amount} ${currency} записана в «Деньги».` : 'Без записи оплаты.',
    );
    loadAdmin();
  } catch (e) {
    toast(`Не удалось активировать: ${e.message}`, 'warn');
  }
}

/** Снять тариф (plan: none) — доступ к платным функциям закрывается сразу. */
export async function adminRevokePlan(userId) {
  if (!(await confirmAction('Снять тариф у этого пользователя? Доступ закроется сразу.'))) return;
  try {
    const d = await api('POST', `/api/admin/users/${userId}/subscription`, {
      plan: 'none',
      months: 1,
      note: '',
    });
    document.getElementById('adminUserBody').innerHTML = detailHtml(d);
    toast('Тариф снят', 'info');
    loadAdmin();
  } catch (e) {
    toast(`Не удалось: ${e.message}`, 'warn');
  }
}

/** Заблокировать/разблокировать доступ (POST /api/admin/users/{id}/block). */
export async function adminToggleBlock(userId, blocked) {
  if (
    !(await confirmAction(
      blocked
        ? 'Заблокировать доступ этому пользователю?'
        : 'Разблокировать доступ этому пользователю?',
    ))
  )
    return;
  try {
    const d = await api('POST', `/api/admin/users/${userId}/block`, { blocked, note: '' });
    document.getElementById('adminUserBody').innerHTML = detailHtml(d);
    toast(blocked ? 'Доступ заблокирован' : 'Доступ разблокирован', 'info');
    loadAdmin();
  } catch (e) {
    toast(`Не удалось: ${e.message}`, 'warn');
  }
}

function detailHtml(d) {
  const u = d.user || {};
  const statuses =
    Object.entries(d.leads_by_status || {})
      .map(([k, v]) => `${STATUS_LABEL[k] || esc(k)}: ${v}`)
      .join(' · ') || 'нет лидов';

  const dbs =
    (d.databases_list || [])
      .map(
        (x) =>
          `<div class="key-row"><div class="key-main"><div class="key-top"><span class="key-label">${esc(x.name)}</span><span class="key-status muted">${esc(x.mode || '')}</span></div><div class="key-sub">${x.leads.toLocaleString('ru-RU')} лид. · источник ${esc(x.source || '—')} · ${x.created_at ? new Date(x.created_at).toLocaleDateString('ru-RU') : '—'}</div></div></div>`,
      )
      .join('') || '<div class="dd-empty">Баз нет</div>';

  const offers =
    (d.offers_list || []).map((o) => `<li>${esc(o)}</li>`).join('') || '<li class="muted">нет</li>';
  const camps =
    (d.campaigns_list || []).map((c) => `<li>${esc(c)}</li>`).join('') ||
    '<li class="muted">нет</li>';

  const events =
    (d.recent_events || [])
      .slice()
      .reverse()
      .map(
        (e) =>
          `<div class="key-sub">${e.created_at ? new Date(e.created_at).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''} · ${esc(e.message)}</div>`,
      )
      .join('') || '<div class="dd-empty">Событий нет</div>';

  const until = d.paid_until ? new Date(d.paid_until).toLocaleDateString('ru-RU') : '—';
  const subBlock = u.blocked
    ? `<span class="key-status err">заблокирован</span>`
    : d.expired
      ? `<span class="key-status warn">истёк</span>`
      : `<span class="key-status ok">${esc(planLabel(d.plan))}</span>`;

  // Доступ: есть ли в белом списке и действует ли скидка на первую покупку.
  const wl = d.whitelisted
    ? '<span class="key-status ok">в белом списке</span>'
    : '<span class="key-status err">не в белом списке</span>';
  const firstBuy = d.first_purchase
    ? '<span class="key-status ok">скидка на первую покупку доступна</span>'
    : '<span class="key-status muted">скидкой уже пользовался</span>';

  // Журнал входов этого пользователя (и по его telegram_id — ловим мультиаккаунты).
  const attempts =
    (d.login_attempts || [])
      .map((a) => {
        const cls = a.ok ? 'ok' : 'err';
        const who = a.username ? '@' + esc(a.username) : a.name ? esc(a.name) : 'без username';
        return `<div class="key-sub"><span class="key-status ${cls}">${a.ok ? 'вход' : 'отклонён'}</span>
      ${fmtDateTime(a.created_at)} · ${who} · id ${a.telegram_id ?? '—'} · ${esc(loginReason(a.reason))}
      · IP ${esc(a.ip || '—')}${a.user_agent ? ' · ' + esc(a.user_agent) : ''}</div>`;
      })
      .join('') || '<div class="dd-empty">Входов пока нет</div>';

  return `
    <div class="admin-detail">
      <div class="ad-head">
        <div class="ad-name">${esc(u.name || '—')} ${u.is_admin ? '<span class="key-status ok">админ</span>' : ''}</div>
        <div class="key-sub">${u.username ? '@' + esc(u.username) : 'без username'} · id ${u.telegram_id} · рег. ${u.created_at ? new Date(u.created_at).toLocaleDateString('ru-RU') : '—'}</div>
      </div>
      <div class="ad-grid">
        <div class="ad-stat"><b>${u.databases}</b><span>баз</span></div>
        <div class="ad-stat"><b>${(u.leads || 0).toLocaleString('ru-RU')}</b><span>лидов</span></div>
        <div class="ad-stat"><b>${u.offers}</b><span>офферов</span></div>
        <div class="ad-stat"><b>${u.campaigns}</b><span>рассылок</span></div>
        <div class="ad-stat"><b>${u.warmup_accounts}</b><span>прогрев</span></div>
        <div class="ad-stat"><b>${u.api_keys}</b><span>ключей</span></div>
      </div>
      <div class="ad-block"><h4>Подписка</h4>
        <div class="key-sub">${subBlock} · план в базе: ${esc(planLabel(d.stored_plan))} · оплачен до: ${until} · лидов в этом месяце: ${(d.leads_this_month || 0).toLocaleString('ru-RU')}</div>
        ${d.sub_note ? `<div class="key-sub">Заметка: ${esc(d.sub_note)}</div>` : ''}
      </div>
      ${subActionsHtml(d)}
      <div class="ad-block"><h4>Доступ</h4><div class="key-sub">${wl} · ${firstBuy}</div></div>
      <div class="ad-block"><h4>Лиды по статусам</h4><div class="key-sub">${statuses}</div></div>
      <div class="ad-block"><h4>Отправки за месяц</h4><div class="key-sub">Фактически отправлено: ${d.monthly_used.toLocaleString('ru-RU')}. В Talaria нет продуктовой месячной квоты; Telegram-безопасность управляет только темпом каждого аккаунта. ИИ-провайдер задаётся только переменными окружения сервера.</div></div>
      <div class="ad-block"><h4>Журнал входов</h4>${attempts}</div>
      <div class="ad-block"><h4>Базы лидов</h4>${dbs}</div>
      <div class="ad-cols">
        <div class="ad-block"><h4>Офферы</h4><ul class="ad-list">${offers}</ul></div>
        <div class="ad-block"><h4>Рассылки</h4><ul class="ad-list">${camps}</ul></div>
      </div>
      <div class="ad-block"><h4>Последние события</h4>${events}</div>
    </div>`;
}

/* --------------------------- белый список доступа ------------------------ */
// Без записи здесь войти нельзя: бесплатного входа нет. Пускаем по telegram_id
// и/или username (бэкенд требует хотя бы одно поле).

/** Загрузить белый список (GET /api/admin/whitelist). */
export async function loadWhitelist() {
  const wrap = document.getElementById('whitelistList');
  if (!wrap || !getToken() || !window.__isAdmin) return;
  try {
    const data = await api('GET', '/api/admin/whitelist');
    renderWhitelist(data.entries || []);
  } catch (e) {
    wrap.innerHTML = `<div class="dd-empty">Не удалось загрузить список: ${esc(e.message)}</div>`;
  }
}

function renderWhitelist(entries) {
  const wrap = document.getElementById('whitelistList');
  if (!wrap) return;
  if (!entries.length) {
    wrap.innerHTML =
      '<div class="dd-empty">Список пуст. Пока в нём никого — войти сможет только админ.</div>';
    return;
  }
  wrap.innerHTML = entries
    .map((w) => {
      const uname = w.username ? '@' + esc(w.username) : '<span class="muted">без username</span>';
      const id = w.telegram_id != null ? `id ${w.telegram_id}` : 'id неизвестен';
      const added = w.created_at ? new Date(w.created_at).toLocaleDateString('ru-RU') : '';
      return `<div class="key-row" data-wl-id="${w.id}">
      <div class="key-main">
        <div class="key-top"><span class="key-label">${w.name ? esc(w.name) : uname}</span></div>
        <div class="key-sub">${uname} · ${id}${added ? ' · добавлен ' + added : ''}</div>
        ${w.note ? `<div class="key-sub">${esc(w.note)}</div>` : ''}
      </div>
      <div class="key-actions"><button class="btn-mini ghost" data-wl-del="${w.id}">Убрать</button></div>
    </div>`;
    })
    .join('');
}

/** Добавить в белый список (POST /api/admin/whitelist). */
export async function addWhitelist() {
  if (!getToken()) {
    toast('Сначала войдите', 'warn');
    return;
  }
  const rawId = (document.getElementById('wlTelegramId')?.value || '').replace(/\D/g, '');
  const username = (document.getElementById('wlUsername')?.value || '').trim().replace(/^@/, '');
  const note = (document.getElementById('wlNote')?.value || '').trim();
  if (!rawId && !username) {
    toast('Укажите Telegram ID или username', 'warn');
    return;
  }
  const body = { note };
  if (rawId) body.telegram_id = Number(rawId);
  if (username) body.username = username;
  try {
    const data = await api('POST', '/api/admin/whitelist', body);
    ['wlTelegramId', 'wlUsername', 'wlNote'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    renderWhitelist(data.entries || []);
    toast('Добавлено в белый список', 'success');
  } catch (e) {
    toast(`Не удалось добавить: ${e.message}`, 'warn');
  }
}

/** Убрать из белого списка (DELETE /api/admin/whitelist/{id}). */
async function removeWhitelist(id) {
  if (
    !(await confirmAction(
      'Убрать этот аккаунт из белого списка? Его текущий вход перестанет работать.',
    ))
  )
    return;
  try {
    await api('DELETE', `/api/admin/whitelist/${id}`);
    toast('Убрано из белого списка', 'info');
    loadWhitelist();
  } catch (e) {
    toast(`Не удалось убрать: ${e.message}`, 'warn');
  }
}

/* ------------------------------ журнал входов ---------------------------- */
// Кто, откуда и с какого устройства пытался войти. Совпадение IP/устройства у
// разных аккаунтов — сигнал о мультиаккаунтах.

let _loginOffset = 0;
const _LOGIN_PAGE = 100;

/** Загрузить журнал входов (GET /api/admin/login-attempts).
 *
 *  Фильтры и постраничный просмотр считает бэкенд. Фильтровать на клиенте
 *  бессмысленно: мы видим только последнюю сотню записей, а искать надо по
 *  всему журналу — именно тот вход, который ищут, обычно уже ушёл вниз.
 *
 *  Ответ — простой список без total (контракт сохранён), поэтому «Вперёд»
 *  активна ровно тогда, когда страница пришла полной.
 */
export async function loadLoginAttempts() {
  const wrap = document.getElementById('loginAttemptsList');
  if (!wrap || !getToken() || !window.__isAdmin) return;

  const onlyFailed = !!document.getElementById('loginOnlyFailed')?.checked;
  const params = new URLSearchParams({
    only_failed: String(onlyFailed),
    offset: String(_loginOffset),
    limit: String(_LOGIN_PAGE),
  });
  const reason = document.getElementById('loginReasonFilter')?.value || '';
  const ip = (document.getElementById('loginIpFilter')?.value || '').trim();
  const q = (document.getElementById('loginQueryFilter')?.value || '').trim();
  if (reason) params.set('reason', reason);
  if (ip) params.set('ip', ip);
  if (q) params.set('q', q);

  const pager = document.getElementById('loginPager');
  const count = document.getElementById('loginCount');
  try {
    const rows = await api('GET', `/api/admin/login-attempts?${params}`);

    if (pager)
      pager.style.display = _loginOffset > 0 || rows.length === _LOGIN_PAGE ? 'flex' : 'none';
    if (count)
      count.textContent = rows.length
        ? `Записи ${_loginOffset + 1}–${_loginOffset + rows.length}`
        : 'Ничего не найдено';
    const prev = document.getElementById('loginPrev');
    const next = document.getElementById('loginNext');
    if (prev) prev.disabled = _loginOffset === 0;
    if (next) next.disabled = rows.length < _LOGIN_PAGE;

    if (!rows.length) {
      const filtered = onlyFailed || reason || ip || q;
      wrap.innerHTML = `<div class="dd-empty">${filtered ? 'Под эти условия ничего не подошло.' : 'Входов пока нет.'}</div>`;
      return;
    }
    wrap.innerHTML = `<div class="table-wrap"><table class="lead-table admin-table">
      <thead><tr><th>Когда</th><th>Итог</th><th>Пользователь</th><th>Telegram ID</th><th>Причина</th><th>IP</th><th>Устройство</th></tr></thead>
      <tbody>${rows.map(loginRowHtml).join('')}</tbody>
    </table></div>`;
  } catch (e) {
    if (pager) pager.style.display = 'none';
    wrap.innerHTML = `<div class="dd-empty">Не удалось загрузить журнал: ${esc(e.message)}</div>`;
  }
}

/** Перелистнуть журнал входов. */
export function loginPage(dir) {
  const next = _loginOffset + dir * _LOGIN_PAGE;
  if (next < 0) return;
  _loginOffset = next;
  loadLoginAttempts();
}

/** Скачать журнал входов CSV-файлом с теми же фильтрами, что и таблица на экране. */
export async function exportLoginAttempts() {
  if (!getToken() || !window.__isAdmin) return;
  const params = new URLSearchParams({
    only_failed: String(!!document.getElementById('loginOnlyFailed')?.checked),
  });
  const reason = document.getElementById('loginReasonFilter')?.value || '';
  const ip = (document.getElementById('loginIpFilter')?.value || '').trim();
  const q = (document.getElementById('loginQueryFilter')?.value || '').trim();
  if (reason) params.set('reason', reason);
  if (ip) params.set('ip', ip);
  if (q) params.set('q', q);
  try {
    await apiDownload(`/api/admin/login-attempts/export?${params}`, 'login-attempts.csv');
  } catch (e) {
    toast(`Не удалось скачать: ${e.message}`, 'warn');
  }
}

function loginRowHtml(a) {
  const who = a.username
    ? '@' + esc(a.username)
    : a.name
      ? esc(a.name)
      : '<span class="muted">—</span>';
  const cls = a.ok ? 'ok' : 'err';
  return `<tr>
    <td>${fmtDateTime(a.created_at)}</td>
    <td><span class="key-status ${cls}">${a.ok ? 'вход' : 'отклонён'}</span></td>
    <td>${who}</td>
    <td>${a.telegram_id ?? '—'}</td>
    <td>${esc(loginReason(a.reason))}</td>
    <td>${esc(a.ip || '—')}</td>
    <td class="ua-cell">${a.user_agent ? esc(a.user_agent) : '—'}</td>
  </tr>`;
}

/* ---------------------------- журнал действий --------------------------- */
// Отвечает на вопрос «что вообще происходит в системе». Раньше события были
// видны только внутри карточки конкретного человека и только последние 20 штук.

const SCOPE_LABEL = {
  search: 'Поиск',
  leads: 'Базы лидов',
  campaigns: 'Рассылки',
  offers: 'Офферы',
  warmup: 'Прогрев',
  ai: 'ИИ-помощник',
  settings: 'Настройки',
  notification: 'Уведомления',
  admin: 'Админка',
};
const LEVEL_CLASS = { error: 'err', warn: 'warn', success: 'ok', info: 'muted' };
const LEVEL_LABEL = { error: 'ошибка', warn: 'внимание', success: 'успех', info: 'инфо' };

let _actOffset = 0;
const _ACT_PAGE = 100;

/** Загрузить журнал действий (GET /api/admin/activity). */
export async function loadActivity() {
  const wrap = document.getElementById('adminActivityList');
  if (!wrap || !getToken() || !window.__isAdmin) return;

  const params = new URLSearchParams({ offset: String(_actOffset), limit: String(_ACT_PAGE) });
  const scope = document.getElementById('activityScopeFilter')?.value || '';
  const onlyErrors = !!document.getElementById('activityOnlyErrors')?.checked;
  const userId = (document.getElementById('activityUserFilter')?.value || '').replace(/\D/g, '');
  const q = (document.getElementById('activityQueryFilter')?.value || '').trim();
  if (scope) params.set('scope', scope);
  if (onlyErrors) params.set('level', 'error');
  if (userId) params.set('user_id', userId);
  if (q) params.set('q', q);

  try {
    const data = await api('GET', `/api/admin/activity?${params}`);
    renderActivity(data);
  } catch (e) {
    document.getElementById('activityPager')?.style.setProperty('display', 'none');
    wrap.innerHTML = `<div class="dd-empty">Не удалось загрузить журнал: ${esc(e.message)}</div>`;
  }
}

/** Скачать журнал действий CSV-файлом с теми же фильтрами, что и таблица на экране. */
export async function exportActivity() {
  if (!getToken() || !window.__isAdmin) return;
  const params = new URLSearchParams();
  const scope = document.getElementById('activityScopeFilter')?.value || '';
  const onlyErrors = !!document.getElementById('activityOnlyErrors')?.checked;
  const userId = (document.getElementById('activityUserFilter')?.value || '').replace(/\D/g, '');
  const q = (document.getElementById('activityQueryFilter')?.value || '').trim();
  if (scope) params.set('scope', scope);
  if (onlyErrors) params.set('level', 'error');
  if (userId) params.set('user_id', userId);
  if (q) params.set('q', q);
  try {
    await apiDownload(`/api/admin/activity/export?${params}`, 'activity.csv');
  } catch (e) {
    toast(`Не удалось скачать: ${e.message}`, 'warn');
  }
}

/** Пересчитать балл всех сохранённых лидов (POST /api/admin/leads/recompute-score). */
export async function recomputeLeadScores() {
  if (!getToken() || !window.__isAdmin) return;
  try {
    const res = await api('POST', '/api/admin/leads/recompute-score');
    toast(`Пересчитано: ${res.updated} из ${res.total} лидов обновлено`, 'success');
  } catch (e) {
    toast(`Не удалось пересчитать: ${e.message}`, 'warn');
  }
}

function renderActivity(data) {
  const wrap = document.getElementById('adminActivityList');
  if (!wrap) return;
  const events = data.events || [];
  const total = data.total || 0;

  const pager = document.getElementById('activityPager');
  const count = document.getElementById('activityCount');
  if (pager) pager.style.display = total > _ACT_PAGE ? 'flex' : 'none';
  if (count)
    count.textContent = `События ${events.length ? _actOffset + 1 : 0}–${_actOffset + events.length} из ${total.toLocaleString('ru-RU')}`;
  const prev = document.getElementById('activityPrev');
  const next = document.getElementById('activityNext');
  if (prev) prev.disabled = _actOffset === 0;
  if (next) next.disabled = _actOffset + events.length >= total;

  if (!events.length) {
    wrap.innerHTML = '<div class="dd-empty">Под эти условия событий нет.</div>';
    return;
  }

  const rows = events
    .map((e) => {
      // У событий удалённого пользователя имени уже нет — показываем хотя бы id.
      const who = e.username
        ? '@' + esc(e.username)
        : e.user_name
          ? esc(e.user_name)
          : `<span class="muted">ид ${e.owner_id ?? '—'}</span>`;
      const lvl = e.level || 'info';
      return `<tr>
      <td>${fmtDateTime(e.created_at)}</td>
      <td>${who}</td>
      <td>${esc(SCOPE_LABEL[e.scope] || e.scope || '—')}</td>
      <td><span class="key-status ${LEVEL_CLASS[lvl] || 'muted'} log-level">${esc(LEVEL_LABEL[lvl] || lvl)}</span></td>
      <td class="log-msg">${esc(e.message)}</td>
      <td class="mono">${e.ip ? esc(e.ip) : '<span class="muted">—</span>'}</td>
    </tr>`;
    })
    .join('');

  wrap.innerHTML = `<div class="table-wrap"><table class="lead-table admin-table">
    <thead><tr><th>Когда</th><th>Пользователь</th><th>Раздел</th><th>Уровень</th><th>Событие</th><th>IP</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

/** Перелистнуть журнал действий. */
export function activityPage(dir) {
  const next = _actOffset + dir * _ACT_PAGE;
  if (next < 0) return;
  _actOffset = next;
  loadActivity();
}

/** Повесить обработчик с задержкой: каждый символ — не повод для запроса. */
function onTypeDebounced(id, fn) {
  const el = document.getElementById(id);
  if (!el) return;
  let timer = null;
  el.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(fn, 350);
  });
}

export function initAdmin() {
  const wrap = document.getElementById('adminUsers');
  if (wrap) {
    wrap.addEventListener('click', (e) => {
      const row = e.target.closest('[data-admin-user]');
      if (row) openUser(Number(row.dataset.adminUser));
    });
  }
  document.getElementById('adminPrev')?.addEventListener('click', () => adminPage(-1));
  document.getElementById('adminNext')?.addEventListener('click', () => adminPage(1));

  // Периоды графика активности: сегодня / 14 дней / месяц / год.
  document.querySelectorAll('[data-chart-period]').forEach((b) => {
    b.addEventListener('click', () => setChartPeriod(b.dataset.chartPeriod));
  });

  // Белый список: удаление через делегирование (строки перерисовываются).
  const wl = document.getElementById('whitelistList');
  if (wl) {
    wl.addEventListener('click', (e) => {
      const del = e.target.closest('[data-wl-del]');
      if (del) removeWhitelist(Number(del.dataset.wlDel));
    });
  }

  // Поиск с задержкой: новый запрос всегда сбрасывает страницу на первую,
  // иначе поиск со 2–3 страницы показал бы пустоту.
  const search = document.getElementById('adminSearch');
  if (search) {
    let timer = null;
    search.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        _offset = 0;
        loadAdmin();
      }, 350);
    });
  }

  /* Журнал входов: любой фильтр возвращает на первую страницу, иначе поиск
     со второй страницы показывал бы пустоту. */
  const resetLogin = () => {
    _loginOffset = 0;
    loadLoginAttempts();
  };
  document.getElementById('loginOnlyFailed')?.addEventListener('change', resetLogin);
  document.getElementById('loginReasonFilter')?.addEventListener('change', resetLogin);
  onTypeDebounced('loginIpFilter', resetLogin);
  onTypeDebounced('loginQueryFilter', resetLogin);
  document.getElementById('loginPrev')?.addEventListener('click', () => loginPage(-1));
  document.getElementById('loginNext')?.addEventListener('click', () => loginPage(1));

  /* Журнал действий. */
  const resetAct = () => {
    _actOffset = 0;
    loadActivity();
  };
  document.getElementById('activityOnlyErrors')?.addEventListener('change', resetAct);
  document.getElementById('activityScopeFilter')?.addEventListener('change', resetAct);
  onTypeDebounced('activityUserFilter', resetAct);
  onTypeDebounced('activityQueryFilter', resetAct);
  document.getElementById('activityPrev')?.addEventListener('click', () => activityPage(-1));
  document.getElementById('activityNext')?.addEventListener('click', () => activityPage(1));
}

/* ---------------------------- промокоды ----------------------------------- */

/** Прочитать и нарисовать список промокодов. */
export async function loadPromoCodes() {
  const wrap = document.getElementById('promoList');
  if (!wrap) return;
  let rows = [];
  try {
    rows = await api('GET', '/api/admin/promo-codes');
  } catch (e) {
    wrap.innerHTML = `<div class="dd-empty">Не удалось загрузить: ${esc(e.message)}</div>`;
    return;
  }
  if (!rows.length) {
    wrap.innerHTML = '<div class="dd-empty">Промокодов пока нет.</div>';
    return;
  }
  const body = rows
    .map((r) => {
      // Негодный код показываем приглушённым, но не прячем: чаще всего надо
      // понять, почему клиент говорит «код не работает».
      const dead = r.usable ? '' : ' class="promo-dead"';
      const limit = r.max_uses ? `${r.used_count} / ${r.max_uses}` : `${r.used_count} / ∞`;
      const until = r.valid_until ? fmtDateTime(r.valid_until) : '<span class="muted">—</span>';
      return `<tr${dead}>
      <td class="mono">${esc(r.code)}</td>
      <td>−${r.discount_pct}%</td>
      <td>${limit}</td>
      <td>${until}</td>
      <td>${esc(r.note || '')}</td>
      <td>
        <button class="link-button" type="button" data-act="togglePromoCode" data-args='[${r.id}, ${r.active ? 'false' : 'true'}]'>
          ${r.active ? 'Выключить' : 'Включить'}
        </button>
        <button class="link-button danger" type="button" data-act="removePromoCode" data-args='[${r.id}]'>Удалить</button>
      </td>
    </tr>`;
    })
    .join('');
  wrap.innerHTML = `<div class="table-wrap"><table class="lead-table admin-table">
    <thead><tr><th>Код</th><th>Скидка</th><th>Применений</th><th>Действует до</th><th>Заметка</th><th></th></tr></thead>
    <tbody>${body}</tbody></table></div>`;
}

/** Создать промокод из формы. */
export async function createPromoCode() {
  const code = (document.getElementById('promoCode')?.value || '').trim();
  const pct = parseInt(document.getElementById('promoPct')?.value || '0', 10);
  const limit = parseInt(document.getElementById('promoLimit')?.value || '0', 10);
  const note = (document.getElementById('promoNote')?.value || '').trim();
  if (!code) {
    toast('Введите код', 'warn');
    return;
  }
  if (!(pct >= 1 && pct <= 100)) {
    toast('Скидка должна быть от 1 до 100', 'warn');
    return;
  }
  try {
    await api('POST', '/api/admin/promo-codes', {
      code,
      discount_pct: pct,
      max_uses: Number.isFinite(limit) && limit > 0 ? limit : 0,
      note,
    });
    const field = document.getElementById('promoCode');
    if (field) field.value = '';
    toast('Промокод создан', 'success', `${code.toUpperCase()} — скидка ${pct}%.`);
    await loadPromoCodes();
  } catch (e) {
    toast('Не удалось создать промокод', 'error', e.message);
  }
}

/** Включить или выключить промокод. */
export async function togglePromoCode(id, active) {
  try {
    await api('PATCH', `/api/admin/promo-codes/${id}`, { active });
    await loadPromoCodes();
  } catch (e) {
    toast('Не удалось изменить промокод', 'error', e.message);
  }
}

/** Удалить промокод. */
export async function removePromoCode(id) {
  if (!(await confirmAction('Удалить промокод? Клиенты с этим кодом скидку не получат.'))) return;
  try {
    await api('DELETE', `/api/admin/promo-codes/${id}`);
    toast('Промокод удалён', 'success');
    await loadPromoCodes();
  } catch (e) {
    toast('Не удалось удалить промокод', 'error', e.message);
  }
}

/* --------------------------- выдача дней ---------------------------------- */

/** Выдать (или снять) дни всем сразу. */
export async function grantDaysToAll() {
  const days = parseInt(document.getElementById('grantAllDays')?.value || '0', 10);
  const note = (document.getElementById('grantAllNote')?.value || '').trim();
  if (!Number.isFinite(days) || days === 0) {
    toast('Укажите число дней', 'warn', 'Ноль ничего не меняет.');
    return;
  }
  const verb = days > 0 ? `добавить ${days} дн.` : `снять ${Math.abs(days)} дн.`;
  if (
    !(await confirmAction(`Точно ${verb} всем клиентам с тарифом? Действие затронет всех сразу.`))
  )
    return;
  try {
    const result = await api('POST', '/api/admin/grant-days', { days, note });
    toast('Готово', 'success', `Затронуто пользователей: ${result.affected}.`);
    await loadAdmin();
  } catch (e) {
    toast('Не удалось выдать дни', 'error', e.message);
  }
}

/* ---------------------------- ДЕНЬГИ ------------------------------------ */

const MONEY = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });

/** Суммы по валютам. Не складываем разные валюты в одну цифру: курса на дату
 *  платежа мы не храним, а пересчёт по сегодняшнему менял бы прошлые месяцы. */
function money(bucket) {
  const entries = Object.entries(bucket || {});
  if (!entries.length) return '—';
  return entries.map(([code, value]) => `${MONEY.format(value)} ${esc(code)}`).join(' · ');
}

function renderRevenue(revenue) {
  const box = document.getElementById('adminRevenue');
  if (!box) return;
  box.innerHTML = [
    card(
      'За этот месяц',
      money(revenue.month),
      `плательщиков: ${revenue.paying_users_this_month || 0}`,
    ),
    card('За 30 дней', money(revenue.last_30_days), 'скользящее окно'),
    card('За всё время', money(revenue.all_time), `оплат: ${revenue.payments_total || 0}`),
  ].join('');
}

/** Список платежей. Отдельным запросом: сводка нужна всегда, а список —
 *  только когда админ до него долистал. */
async function loadPayments() {
  const box = document.getElementById('adminPayments');
  if (!box) return;
  try {
    const data = await api('GET', '/api/admin/payments?limit=20');
    if (!data.payments.length) {
      box.innerHTML =
        '<div class="dd-empty">Оплат пока нет. Они записываются при выдаче тарифа с суммой.</div>';
      return;
    }
    box.innerHTML = data.payments
      .map(
        (p) =>
          `<div class="pay-row"><div><b>${MONEY.format(p.amount)} ${esc(p.currency)}</b>` +
          `<span>${esc(p.user_label)} · ${esc(dateLabel(p.paid_at))}</span></div>` +
          `<div class="pay-note">${esc(p.note || '')}</div>` +
          `<button class="link-button" data-act="deletePayment" data-args='[${p.id}]'>Удалить</button></div>`,
      )
      .join('');
  } catch (e) {
    box.innerHTML = `<div class="dd-empty">Не удалось загрузить платежи: ${esc(e.message)}</div>`;
  }
}

function dateLabel(value) {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('ru-RU');
}

export async function deletePayment(id) {
  if (!(await confirmAction('Удалить эту запись о платеже? Выручка пересчитается.'))) return;
  try {
    await api('DELETE', `/api/admin/payments/${id}`);
    toast('Платёж удалён', 'info');
    loadAdminOverview();
  } catch (e) {
    toast('Не удалось удалить', 'error', e.message);
  }
}

/** «Показать все» в «Кто заходил»: полный журнал входов с фильтрами — модалкой. */
export function openLoginJournal() {
  document.getElementById('modalLoginJournal')?.classList.add('show');
  loadLoginAttempts();
}

/** «Показать все» в «Что делали»: полный журнал действий с фильтрами — модалкой. */
export function openActivityJournal() {
  document.getElementById('modalActivityJournal')?.classList.add('show');
  loadActivity();
}

/* ------------------------ обращения в поддержку --------------------------- */

// Какой диалог открыт. Ответ уходит именно ему, а не «последнему нажатому»:
// иначе достаточно случайно кликнуть другую строку, и ответ уйдёт чужому человеку.
let _supportUser = null;

function supportBubble(message) {
  const row = document.createElement('div');
  // Ответ владельца — «out» (справа): админ смотрит на переписку своими глазами,
  // поэтому справа его собственные слова, слева — клиента.
  row.className = `sup-msg ${message.author === 'admin' ? 'out' : 'in'}`;
  const text = document.createElement('div');
  text.className = 'sup-text';
  text.textContent = message.text;
  const meta = document.createElement('div');
  meta.className = 'sup-meta';
  const who = document.createElement('span');
  who.textContent = message.author === 'admin' ? 'Вы' : 'Клиент';
  const time = document.createElement('time');
  time.textContent = fmtDateTime(message.created_at);
  meta.append(who, time);
  row.append(text, meta);
  return row;
}

function renderSupportThreads(data) {
  const list = document.getElementById('adminSupportList');
  const badge = document.getElementById('adminSupportUnread');
  if (!list) return;
  const threads = data.threads || [];
  if (badge) {
    badge.hidden = !data.unread_total;
    badge.textContent = data.unread_total ? `${data.unread_total} без ответа` : '';
  }
  list.textContent = '';
  if (!threads.length) {
    const empty = document.createElement('div');
    empty.className = 'dd-empty';
    empty.textContent = 'Обращений пока нет.';
    list.append(empty);
    return;
  }
  threads.forEach((thread) => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = `admin-support-item${thread.unread ? ' is-unread' : ''}${
      thread.user_id === _supportUser ? ' active' : ''
    }`;
    row.dataset.supportUser = String(thread.user_id);
    const top = document.createElement('div');
    top.className = 'admin-support-item-top';
    const name = document.createElement('b');
    name.textContent = thread.name || `id ${thread.user_id}`;
    const when = document.createElement('time');
    when.textContent = fmtDateTime(thread.last_at);
    top.append(name, when);
    const sub = document.createElement('small');
    sub.textContent = thread.unread
      ? `${thread.unread} новых · всего ${thread.messages}`
      : `сообщений: ${thread.messages}`;
    row.append(top, sub);
    row.addEventListener('click', () => openSupportThread(thread.user_id));
    list.append(row);
  });
}

export async function loadSupportThreads() {
  if (!window.__isAdmin || !getToken()) return;
  try {
    renderSupportThreads(await api('GET', '/api/admin/support'));
  } catch (e) {
    const list = document.getElementById('adminSupportList');
    if (list)
      list.innerHTML = `<div class="dd-empty">Не удалось загрузить: ${esc(e.message)}</div>`;
  }
}

export async function openSupportThread(userId) {
  const box = document.getElementById('adminSupportThread');
  const field = document.getElementById('adminSupportReply');
  const button = document.getElementById('adminSupportSend');
  if (!box) return;
  _supportUser = Number(userId) || null;
  document.querySelectorAll('.admin-support-item').forEach((item) => {
    item.classList.toggle('active', Number(item.dataset.supportUser) === _supportUser);
  });
  box.textContent = '';
  try {
    const messages = await api('GET', `/api/admin/support/${_supportUser}`);
    if (!messages.length) {
      const empty = document.createElement('div');
      empty.className = 'sup-empty';
      empty.textContent = 'Диалог пуст.';
      box.append(empty);
    } else {
      messages.forEach((message) => box.append(supportBubble(message)));
      box.scrollTop = box.scrollHeight;
    }
    if (field) field.disabled = false;
    if (button) button.disabled = false;
    // Открытый диалог помечен прочитанным на сервере — обновляем счётчики.
    loadSupportThreads();
  } catch (e) {
    const error = document.createElement('div');
    error.className = 'sup-empty';
    error.textContent = `Не удалось открыть диалог: ${e.message}`;
    box.append(error);
  }
}

export async function replySupportThread() {
  const field = document.getElementById('adminSupportReply');
  const text = (field?.value || '').trim();
  if (!_supportUser) {
    toast('Выберите обращение', 'warn');
    return;
  }
  if (!text) {
    toast('Введите ответ', 'warn');
    field?.focus();
    return;
  }
  const button = document.getElementById('adminSupportSend');
  if (button) button.disabled = true;
  try {
    await api('POST', `/api/admin/support/${_supportUser}`, { text });
    if (field) field.value = '';
    toast('Ответ отправлен — клиент получит его в Telegram', 'success');
    await openSupportThread(_supportUser);
  } catch (e) {
    toast('Не удалось отправить ответ', 'error', e.message);
  } finally {
    if (button) button.disabled = false;
  }
}
