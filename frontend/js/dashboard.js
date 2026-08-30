/** Главная: карточки, график и компактная лента событий.
 *
 *  Всё берётся из API: /api/stats (счётчики и лиды по дням) и /api/events
 *  (лента). Раньше здесь были зашитые числа (8214 отправлено, 64.2%, ₸1.2М) и
 *  график из констант [38,62,48,…] — красиво, но неправда. Метрики отправки и
 *  дохода убраны совсем: ни рассылки, ни оплат в системе нет.
 */
import { api, esc, getToken } from './api.js';
import { setQuickStep } from './onboarding.js?v=zephyr4';
import { isUnreadEvent, setNotifSnapshot } from './notifications.js?v=tal32';

/** Плавный счётчик от 0 до target. Вызывается после загрузки реальных чисел. */
function animateTo(el, target) {
  if (!el) return;
  const dur = 900,
    start = performance.now();
  function tick(now) {
    const p = Math.min((now - start) / dur, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(target * eased).toLocaleString('ru-RU');
    if (p < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

// Окно графика. Живёт в модуле, а не в DOM: при повторном заходе на «Главную»
// график должен открыться в том же режиме, что человек выбрал.
let chartDays = 14;
let dashboardClockTimer = null;
let offerStatsData = null;
let offerStatsFilter = 'all';

function startDashboardClock() {
  const clock = document.getElementById('dashLiveClock');
  if (!clock) return;
  const paint = () => {
    clock.textContent = new Intl.DateTimeFormat('ru-RU', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    }).format(new Date());
  };
  paint();
  if (!dashboardClockTimer) dashboardClockTimer = window.setInterval(paint, 1000);
}

/** Старое имя сохранено: его зовёт enterApp(). Теперь тянет реальные счётчики. */
export async function animateCounters() {
  startDashboardClock();
  loadOfferStats();
  if (!getToken()) {
    // Демо-превью намеренно не получает API-токен. Не оставляем на экране
    // бесконечные skeleton-блоки: показываем честное пустое состояние без
    // выдуманных метрик, пока пользователь не войдёт настоящим аккаунтом.
    buildChart([]);
    buildCompare({ chart_leads: 0, prev_chart_leads: 0, chart_days: chartDays });
    buildFunnel({});
    const feed = document.getElementById('activityFeed');
    if (feed)
      feed.innerHTML =
        '<div class="activity-empty">События появятся после входа и первого действия в Talaria.</div>';
    return;
  }
  try {
    const s = await api('GET', `/api/stats?days=${chartDays}`);
    animateTo(document.getElementById('statLeads'), s.leads || 0);
    animateTo(document.getElementById('statDatabases'), s.databases || 0);
    animateTo(document.getElementById('statOffers'), s.offers || 0);
    animateTo(document.getElementById('statCampaigns'), s.campaigns || 0);
    animateTo(document.getElementById('statVerifiedTelegram'), s.verified_telegram || 0);
    animateTo(document.getElementById('statPendingTelegram'), s.pending_telegram || 0);
    animateTo(document.getElementById('statSentMessages'), s.sent_messages || 0);
    animateTo(document.getElementById('statReplies'), s.replies || 0);
    const replyRate = document.getElementById('statReplyRate');
    if (replyRate) replyRate.textContent = `${Number(s.reply_rate || 0).toLocaleString('ru-RU')}%`;
    buildChart(s.leads_per_day || []);
    buildCompare(s);
    buildFunnel(s.leads_by_status || {});
    // Кубики быстрого старта отмечаем только по факту, без «галочек за красоту».
    setQuickStep('leads', (s.leads || 0) > 0);
    setQuickStep('offer', (s.offers || 0) > 0);
  } catch (e) {
    console.log('Не удалось загрузить статистику:', e.message);
  }
  loadActivity();
  loadMonthlyLimit();
}

/** Переключить окно графика (7/30 дней) и перерисовать его вместе со сравнением. */
export async function setChartDays(days) {
  chartDays = [7, 14, 30].includes(Number(days)) ? Number(days) : 14;
  document.querySelectorAll('[data-chart-days]').forEach((b) => {
    b.classList.toggle('active', Number(b.dataset.chartDays) === chartDays);
  });
  if (!getToken()) return;
  try {
    const s = await api('GET', `/api/stats?days=${chartDays}`);
    buildChart(s.leads_per_day || []);
    buildCompare(s);
  } catch (e) {
    console.log('Не удалось перестроить график:', e.message);
  }
}

/** Строка «+40% к прошлому периоду» под графиком. Сравнение считает сервер:
 *  у клиента данных за прошлое окно нет, а выдумывать их нельзя. */
function buildCompare(s) {
  const el = document.getElementById('chartCompare');
  if (!el) return;
  const cur = s.chart_leads || 0;
  const prev = s.prev_chart_leads || 0;
  const period =
    (s.chart_days || 14) === 30
      ? 'к прошлым 30 дням'
      : (s.chart_days || 14) === 14
        ? 'к прошлым 14 дням'
        : 'к прошлой неделе';
  if (!cur && !prev) {
    el.textContent = '';
    return;
  }
  if (!prev) {
    el.textContent = `За прошлый период лидов не было — сравнивать пока не с чем.`;
    return;
  }
  const diff = Math.round(((cur - prev) / prev) * 100);
  const sign = diff > 0 ? '+' : '';
  el.innerHTML = `<b class="${diff >= 0 ? 'up' : 'down'}">${sign}${diff}%</b> ${period} (${prev.toLocaleString('ru-RU')} → ${cur.toLocaleString('ru-RU')})`;
}

/* ---------- Лиды по статусам ---------- */

// Порядок и подписи те же, что у чипов-фильтров в базе лидов.
const FUNNEL_STEPS = [
  { key: 'new', label: 'Новые', cls: 'st-new' },
  { key: 'contacted', label: 'Написали', cls: 'st-contacted' },
  { key: 'replied', label: 'Ответили', cls: 'st-replied' },
  { key: 'interested', label: 'Заинтересованы', cls: 'st-interested' },
  { key: 'unsubscribed', label: 'Отписались', cls: 'st-unsub' },
];

/** Полоса-воронка из /api/stats.leads_by_status.
 *  Бэкенд эти цифры всегда считал — интерфейс их просто не показывал. */
export function buildFunnel(byStatus) {
  const box = document.getElementById('leadFunnel');
  if (!box) return;
  const total = FUNNEL_STEPS.reduce((sum, s) => sum + (byStatus[s.key] || 0), 0);
  const note = document.getElementById('funnelTotal');
  if (note) note.textContent = total ? `всего ${total.toLocaleString('ru-RU')}` : '';

  if (!total) {
    box.innerHTML =
      '<div class="dd-empty">Лидов пока нет — соберите первую базу в «Поиске лидов».</div>';
    return;
  }
  const seg = FUNNEL_STEPS.filter((s) => (byStatus[s.key] || 0) > 0)
    .map(
      (s) =>
        `<i class="${s.cls}" style="width:${((byStatus[s.key] / total) * 100).toFixed(2)}%"></i>`,
    )
    .join('');
  const legend = FUNNEL_STEPS.map((s) => {
    const n = byStatus[s.key] || 0;
    const pct = total ? Math.round((n / total) * 100) : 0;
    return `<div class="funnel-item"><span class="funnel-dot ${s.cls}"></span>${s.label}<b>${n.toLocaleString('ru-RU')}</b><span class="funnel-pct">${pct}%</span></div>`;
  }).join('');
  box.innerHTML = `<div class="funnel-bar">${seg}</div><div class="funnel-legend">${legend}</div>`;
}

/** Лимит рассылок за месяц — из /api/settings (ставит админ). */
export async function loadMonthlyLimit() {
  const wrap = document.getElementById('dashLimit');
  if (wrap) wrap.hidden = true;
}

const WEEKDAYS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

/** Столбики «лиды по дням». Высота относительно максимума недели; если за
 *  неделю ноль лидов — рисуем пустые столбики, а не делим на ноль.
 *
 *  Аргумент необязателен: enterApp() зовёт animateCounters(), которая передаёт
 *  сюда реальные данные. buildChart() без аргумента (из main.js по старой
 *  памяти) просто ничего не ломает — рисует пустую неделю.
 */
export function buildChart(perDay) {
  const wrap = document.getElementById('chartBars');
  if (!wrap) return;
  wrap.innerHTML = '';

  const data = Array.isArray(perDay) ? perDay : [];
  const total = data.reduce((sum, d) => sum + (d.leads || 0), 0);
  const max = Math.max(1, ...data.map((d) => d.leads || 0));

  const note = document.getElementById('chartTotal');
  if (note) note.textContent = total ? `всего ${total.toLocaleString('ru-RU')}` : 'пока пусто';

  if (!data.length) {
    wrap.innerHTML = '<div class="chart-empty">Нет данных за последнюю неделю</div>';
    return;
  }

  // На неделе подписи — дни недели; на месяце дни недели сливаются в кашу,
  // поэтому там даты «5.08» и только на каждом пятом столбике.
  const monthly = data.length > 7;
  data.forEach((d, i) => {
    const pct = Math.round(((d.leads || 0) / max) * 100);
    const date = new Date(d.day + 'T00:00:00');
    let label = WEEKDAYS[date.getDay()] || '';
    if (monthly) {
      label =
        i % 5 === 0 || i === data.length - 1
          ? `${date.getDate()}.${String(date.getMonth() + 1).padStart(2, '0')}`
          : '';
    }
    const col = document.createElement('div');
    col.className = 'chart-col' + (monthly ? ' dense' : '');
    col.innerHTML = `<div class="bar" style="height:0%" data-v="${d.leads || 0} лид."></div><div class="chart-day">${label}</div>`;
    wrap.appendChild(col);
    // Небольшая задержка на столбик — та же анимация «выезда», что и раньше.
    setTimeout(
      () => {
        col.querySelector('.bar').style.height = pct + '%';
      },
      (monthly ? 20 : 80) * i + 100,
    );
  });
}

const ACTIVITY_COLOR = {
  success: 'var(--success)',
  warn: 'var(--warning)',
  error: 'var(--danger)',
  info: 'var(--accent)',
};

/** Относительное время: «5 мин назад», «2 ч назад», «3 дн назад». */
function timeAgo(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  const sec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (sec < 60) return 'только что';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} мин назад`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} ч назад`;
  return `${Math.floor(hr / 24)} дн назад`;
}

/** Лента событий из /api/events. Пустую ленту так и оставляем пустой. */
export async function loadActivity() {
  const feed = document.getElementById('activityFeed');
  if (!feed || !getToken()) return;
  try {
    const data = await api('GET', '/api/events?limit=30');
    const events = (data.events || []).slice().reverse(); // свежие сверху
    feed.innerHTML = '';

    if (!events.length) {
      feed.innerHTML =
        '<div class="activity-empty">Событий пока нет. Начните с поиска лидов.</div>';
      return;
    }
    events.forEach((ev) => {
      const el = document.createElement('div');
      el.className = 'activity-item';
      const color = ACTIVITY_COLOR[ev.level] || ACTIVITY_COLOR.info;
      el.innerHTML = `<div class="dot" style="background:${color}"></div><div><p></p><span>${timeAgo(ev.created_at)}</span></div>`;
      el.querySelector('p').textContent = ev.message; // текст события — не через innerHTML
      feed.appendChild(el);
    });
  } catch (e) {
    console.log('Не удалось загрузить события:', e.message);
  }
}

/** Выпадающий список уведомлений (колокольчик). Тоже из /api/events —
 *  никаких выдуманных «рассылка запущена». Первым блоком идут подсказки
 *  настройки, дальше события; индикатор считает только непрочитанное. */
// Сколько событий показывает колокольчик. «Показать все» поднимает предел —
// просьба владельца: «где уведы посмотреть все уведы».
let notifLimit = 10;

export async function showAllNotifications() {
  notifLimit = 100;
  await loadNotifications();
}

export async function loadNotifications() {
  const list = document.getElementById('notifList');
  if (!list || !getToken()) return;
  try {
    const [data, keysData, accountsData] = await Promise.all([
      api('GET', `/api/events?limit=${notifLimit}`),
      api('GET', '/api/keys').catch(() => ({ keys: [] })),
      api('GET', '/api/warmup').catch(() => ({ accounts: [] })),
    ]);
    const events = (data.events || []).slice().reverse();
    const hasKeys = (keysData.keys || []).length > 0;
    const hasTelegram = (accountsData.accounts || []).some(
      (account) => account.connected && account.operational_status !== 'reauth',
    );
    setQuickStep('key', hasKeys);
    setQuickStep('telegram', hasTelegram);
    const setup = [];
    if (!hasKeys)
      setup.push({
        id: 'key',
        title: 'Добавьте YouTube API',
        text: 'Без ключа поиск лидов не запустится',
      });
    if (!hasTelegram) {
      setup.push({
        id: 'telegram',
        title: 'Подключите Telegram-аккаунт',
        text: 'Он нужен для проверки лидов и отправки',
      });
    }
    // Плитки поверх страницы убраны: подсказки живут первым блоком списка
    // уведомлений. Пустой контейнер в разметке оставлен намеренно — на него
    // ссылаются старые стили и он ещё пригодится под баннер тарифа.
    const setupBox = document.getElementById('notifSetup');
    if (setupBox) {
      setupBox.hidden = !setup.length;
      setupBox.innerHTML = setup.length
        ? `<div class="dd-title">Требует настройки</div>${setup
            .map(
              (item) =>
                `<button type="button" class="dd-setup-item" data-dd-close data-act="goTo" data-args='["settings"]'><span><b>${esc(item.title)}</b><small>${esc(item.text)}</small></span><i aria-hidden="true">→</i></button>`,
            )
            .join('')}`
        : '';
    }
    setNotifSnapshot(events, setup);

    if (!events.length) {
      list.innerHTML = setup.length
        ? ''
        : '<div class="dd-empty">Уведомлений нет. Здесь появятся ответы лидов и события рассылок.</div>';
      return;
    }
    list.innerHTML = '';
    events.forEach((ev) => {
      const color = ACTIVITY_COLOR[ev.level] || ACTIVITY_COLOR.info;
      const el = document.createElement('div');
      el.className = 'dd-item' + (isUnreadEvent(ev) ? ' is-unread' : '');
      el.innerHTML = `<div class="dd-dot" style="background:${color}"></div><div><p></p><span>${timeAgo(ev.created_at)}</span></div>`;
      el.querySelector('p').textContent = ev.message;
      list.appendChild(el);
    });
    const foot = document.createElement('div');
    foot.className = 'dd-foot';
    foot.innerHTML =
      notifLimit > 10
        ? `<span class="dd-foot-note">Показаны все за последнее время: ${events.length}</span>`
        : '<button type="button" class="btn ghost dd-foot-btn" data-act="showAllNotifications">Посмотреть все</button>';
    list.appendChild(foot);
  } catch (e) {
    console.log('Не удалось загрузить уведомления:', e.message);
  }
}

/* ---------- СТАТИСТИКА ПО ОФФЕРАМ ---------- */

/** Сколько отправлено и сколько ответили — по каждому офферу и по типу.
 *
 *  Всё берётся из /api/campaigns/offer-stats, где считаются реальные отправки и
 *  отметки об ответах. Своих процентов клиент не выдумывает.
 */
export async function loadOfferStats() {
  const table = document.getElementById('offerStatsTable');
  const cards = document.getElementById('offerKindCards');
  if (!table) return;
  if (!getToken()) {
    offerStatsData = {
      total_sent: 0,
      total_delivered: 0,
      total_read: 0,
      total_replied: 0,
      total_interested: 0,
      reply_rate: 0,
      by_kind: [],
      offers: [],
      reply_heatmap: [],
      best_send_times: [],
    };
    renderOfferStats();
    return;
  }

  try {
    const data = await api('GET', '/api/campaigns/offer-stats');
    offerStatsData = data;
    renderOfferStats();
  } catch (e) {
    table.innerHTML = `<div class="dd-empty">Статистика по офферам не загрузилась: ${esc(e.message)}</div>`;
  }
}

function renderOfferStats() {
  const data = offerStatsData;
  const table = document.getElementById('offerStatsTable');
  const cards = document.getElementById('offerKindCards');
  if (!data || !table) return;

  const kinds = new Map((data.by_kind || []).map((item) => [item.kind, item]));
  const cardRows = [
    kinds.get('regular') || {
      kind: 'regular',
      label: 'Обычные офферы',
      sent: 0,
      delivered: 0,
      read: 0,
      replied: 0,
      interested: 0,
      failed: 0,
      reply_rate: 0,
    },
    kinds.get('ai') || {
      kind: 'ai',
      label: 'ИИ-офферы',
      sent: 0,
      delivered: 0,
      read: 0,
      replied: 0,
      interested: 0,
      failed: 0,
      reply_rate: 0,
    },
  ];
  if (cards) {
    cards.innerHTML = cardRows
      .map(
        (k) => `
      <div class="offer-kind-card" data-offer-kind="${k.kind}">
        <span class="offer-kind-mark">${k.kind === 'ai' ? 'ИИ' : 'О'}</span>
        <div class="offer-kind-copy"><b>${esc(k.label)}</b><span>${Number(k.delivered || 0).toLocaleString('ru-RU')} доставлено · ${Number(k.read || 0).toLocaleString('ru-RU')} прочитано · ${Number(k.replied || 0).toLocaleString('ru-RU')} ответили · ${Number(k.interested || 0).toLocaleString('ru-RU')} заинтересовались${k.failed ? ` · ${Number(k.failed).toLocaleString('ru-RU')} ошибок` : ''}</span></div>
        <div class="offer-kind-rate"><b>${Number(k.reply_rate || 0).toLocaleString('ru-RU')}%</b><small>отклик</small></div>
      </div>`,
      )
      .join('');
  }

  const totalBox = document.getElementById('offerStatsTotal');
  if (totalBox) {
    totalBox.textContent = data.total_sent
      ? `Отправлено ${Number(data.total_sent).toLocaleString('ru-RU')} · доставлено ${Number(data.total_delivered).toLocaleString('ru-RU')} · прочитано ${Number(data.total_read).toLocaleString('ru-RU')} · ответили ${Number(data.total_replied).toLocaleString('ru-RU')} · заинтересовались ${Number(data.total_interested).toLocaleString('ru-RU')}`
      : 'Отправок пока не было — метрики появятся после первой рассылки.';
  }

  const rows = (data.offers || []).filter((offer) => {
    if (offerStatsFilter === 'all') return true;
    if (offerStatsFilter === 'manual') return offer.kind !== 'ai';
    return offer.kind === 'ai';
  });
  document.querySelectorAll('[data-dashboard-offer-filter]').forEach((button) => {
    const active = button.dataset.dashboardOfferFilter === offerStatsFilter;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });

  if (!rows.length) {
    table.innerHTML = '<div class="dd-empty">В этой категории отправок пока нет.</div>';
    renderReplyHeatmap(data);
    return;
  }

  table.innerHTML = `<div class="offer-performance">
    <div class="offer-performance-row head"><span>Оффер</span><span>Отправлено</span><span>Доставлено</span><span>Прочитано</span><span>Ответили</span><span>Интерес</span><span>Отклик</span></div>
    ${rows
      .map((o) => {
        const rate = Math.max(0, Math.min(100, Number(o.reply_rate || 0)));
        return `<div class="offer-performance-row" data-kind="${o.kind === 'ai' ? 'ai' : 'manual'}">
        <div class="offer-performance-name"><span>${o.kind === 'ai' ? 'ИИ' : 'О'}</span><div><b>${esc(o.name)}</b><small>${o.kind === 'ai' ? 'Персональный текст' : 'Обычный оффер'}${o.failed ? ` · ошибок: ${Number(o.failed).toLocaleString('ru-RU')}` : ''}</small></div></div>
        <div class="offer-performance-metric"><b>${Number(o.sent || 0).toLocaleString('ru-RU')}</b><small>сообщений</small></div>
        <div class="offer-performance-metric"><b>${Number(o.delivered || 0).toLocaleString('ru-RU')}</b><small>${Number(o.delivery_rate || 0).toLocaleString('ru-RU')}%</small></div>
        <div class="offer-performance-metric"><b>${Number(o.read || 0).toLocaleString('ru-RU')}</b><small>${Number(o.read_rate || 0).toLocaleString('ru-RU')}%</small></div>
        <div class="offer-performance-metric"><b>${Number(o.replied || 0).toLocaleString('ru-RU')}</b><small>ответов</small></div>
        <div class="offer-performance-metric"><b>${Number(o.interested || 0).toLocaleString('ru-RU')}</b><small>${Number(o.interest_rate || 0).toLocaleString('ru-RU')}%</small></div>
        <div class="offer-performance-reply"><span><b>${rate.toLocaleString('ru-RU')}%</b><small>отклик</small></span><span class="offer-performance-track"><i data-width="${rate}"></i></span></div>
      </div>`;
      })
      .join('')}
  </div>`;
  requestAnimationFrame(() =>
    table.querySelectorAll('[data-width]').forEach((bar) => {
      bar.style.width = `${bar.dataset.width}%`;
    }),
  );
  renderReplyHeatmap(data);
}

function renderReplyHeatmap(data) {
  const box = document.getElementById('replyHeatmap');
  const best = document.getElementById('bestSendTimes');
  if (!box) return;
  const weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const values = new Map(
    (data.reply_heatmap || []).map((cell) => [
      `${cell.weekday}:${cell.hour}`,
      Number(cell.replies || 0),
    ]),
  );
  const max = Math.max(1, ...values.values());
  const hours = Array.from(
    { length: 24 },
    (_, hour) => `<span>${String(hour).padStart(2, '0')}</span>`,
  ).join('');
  const rows = weekdays
    .map(
      (day, weekday) =>
        `<div class="reply-heat-row"><b>${day}</b>${Array.from({ length: 24 }, (_, hour) => {
          const count = values.get(`${weekday}:${hour}`) || 0;
          const level = count ? Math.max(0.16, count / max) : 0;
          return `<i style="--heat:${level}" data-tip="${day}, ${String(hour).padStart(2, '0')}:00 · ответов: ${count}"></i>`;
        }).join('')}</div>`,
    )
    .join('');
  box.innerHTML = `<div class="reply-heat-hours"><b></b>${hours}</div>${rows}`;
  if (best) {
    const slots = data.best_send_times || [];
    best.innerHTML = slots.length
      ? slots
          .map(
            (slot) =>
              `<span><b>${weekdays[slot.weekday]}, ${String(slot.hour).padStart(2, '0')}:00</b><small>${slot.replies} ответов после отправок в этот час</small></span>`,
          )
          .join('')
      : '<span><b>Пока недостаточно ответов</b><small>Карта заполнится после реальных диалогов.</small></span>';
  }
}

let offerStatsFiltersReady = false;
export function initDashboardDynamics() {
  startDashboardClock();
  if (offerStatsFiltersReady) return;
  offerStatsFiltersReady = true;
  document.getElementById('offerStatsPanel')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-dashboard-offer-filter]');
    if (!button) return;
    offerStatsFilter = button.dataset.dashboardOfferFilter || 'all';
    renderOfferStats();
  });
}
