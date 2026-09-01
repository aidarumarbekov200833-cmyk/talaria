/** Server-authoritative tariff quotes and manual Telegram payment. */
import { api, esc, getToken } from './api.js';
import { renderText } from './shared/dom.js';
import { attachDialogLifecycle } from './shared/dialogs.js';
import { createNumberTween, MOTION, prefersReducedMotion } from './shared/motion.js';
import { toast } from './ui.js?v=tal32';

const SYMBOL = { KZT: '₸', RUB: '₽' };
const states = new Map();
let adminContact = '';
let publicLoading = false;
let initialized = false;
let paymentDialog = null;

function money(value, currency = 'RUB') {
  return `${Number(value || 0).toLocaleString('ru-RU')} ${SYMBOL[currency] || currency}`;
}

function errorMessage(error) {
  return error instanceof Error && error.message ? error.message : 'Не удалось рассчитать цену';
}

export function createQuoteMachine({ requestQuote }) {
  const requests = new Map();
  return {
    cancel(key) {
      const current = requests.get(key);
      if (!current) return;
      current.controller.abort();
      requests.delete(key);
    },
    async quote(key, payload, onUpdate) {
      const previous = requests.get(key);
      previous?.controller.abort();
      const current = { controller: new AbortController(), id: (previous?.id || 0) + 1 };
      requests.set(key, current);
      onUpdate({ busy: true, error: null, quote: null });
      try {
        const quote = await requestQuote(payload, { signal: current.controller.signal });
        if (requests.get(key) !== current) return null;
        onUpdate({ busy: false, error: null, quote });
        return quote;
      } catch (error) {
        if (requests.get(key) !== current || error?.name === 'AbortError') return null;
        onUpdate({ busy: false, error: errorMessage(error), quote: null });
        return null;
      }
    },
  };
}

export function createPromoAttemptState() {
  let generation = 0;
  let open = false;
  return {
    close() {
      open = false;
      generation += 1;
    },
    isCurrent(value) {
      return open && generation === value;
    },
    open() {
      open = true;
      generation += 1;
      return generation;
    },
  };
}

const quoteMachine = createQuoteMachine({
  requestQuote: (payload, options) => api('POST', '/api/plans/quote', payload, options),
});

function periodText(quote) {
  return quote.period_months === 3 ? '3 месяца одним платежом' : '1 месяц';
}

export function buildPaymentUrl(contact, quote) {
  const username = String(contact || '').replace(/^@/, '');
  const text = [
    'Здравствуйте! Хочу оплатить тариф Talaria.',
    `Период: ${periodText(quote)}.`,
    `Валюта: ${quote.currency}.`,
    `Промокод применён: ${quote.promo_valid ? 'да' : 'нет'}.`,
    `Итоговая цена: ${money(quote.total_amount, quote.currency)}.`,
  ].join('\n');
  return `https://t.me/${encodeURIComponent(username)}?text=${encodeURIComponent(text)}`;
}

export function createTariffCardMarkup(plan, current, containerId) {
  const featureItems = (plan.features || [])
    .map(
      (item) =>
        `<li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>${esc(item)}</li>`,
    )
    .join('');
  const landing = containerId === 'landingTariffGrid';
  const act = 'data-act="handlePlanAction" data-args=\'["__EL__"]\'';
  return `<article class="tariff-card tariff-card-single${landing ? ' zp-access-pass' : ''}${current ? ' current' : ''}" data-plan-card data-container="${esc(containerId)}" data-plan-slug="${esc(plan.slug)}" aria-busy="true">
    <div class="tariff-body-grid"><div class="tariff-value-column">
      <div class="tariff-topline"><div><div class="tariff-name">${esc(plan.slug === 'zephyr' ? 'Talaria' : plan.name)}</div><div class="tariff-tagline">${esc(plan.tagline || '')}</div></div>${current ? '<span class="tariff-current">Ваш тариф</span>' : '<span class="tariff-one-badge">Полная версия</span>'}</div>
      <div class="tariff-price-live"><strong data-plan-price>—</strong><span data-plan-caption>за 1 месяц</span></div>
      <div class="plan-period" role="group" aria-label="Период оплаты" data-plan-period-control data-period="1"><i aria-hidden="true"></i><button type="button" class="active" ${act} data-plan-period="1" aria-pressed="true">1 месяц</button><button type="button" ${act} data-plan-period="3" aria-pressed="false">3 месяца <span>−15%</span></button></div>
      <ul class="tariff-feats">${featureItems}</ul>
    </div><div class="tariff-checkout">
      <div class="tariff-checkout-head"><span class="tariff-checkout-kicker">К оплате</span><div class="plan-currency" role="group" aria-label="Валюта оплаты"><button type="button" class="active" ${act} data-plan-currency="RUB" aria-pressed="true">₽ RUB</button><button type="button" ${act} data-plan-currency="KZT" aria-pressed="false">₸ KZT</button></div></div>
      <b data-plan-checkout-price>—</b><p>Без автосписаний. После нажатия откроется готовое сообщение администратору в Telegram.</p>
      <div class="promo-box"><button type="button" class="promo-ticket" ${act} data-plan-promo-reveal>У меня есть промокод</button><div data-plan-promo-controls hidden><label for="promo-${esc(containerId)}">Промокод</label><div><input id="promo-${esc(containerId)}" maxlength="40" autocomplete="off" placeholder="Введите промокод" data-enter-act="handlePlanAction" data-args='["__EL__"]' data-plan-promo-input><button type="button" class="btn ghost" ${act} data-plan-promo>Применить</button><button type="button" class="btn ghost" ${act} data-plan-promo-close>Закрыть</button></div></div><small data-plan-promo-status aria-live="polite"></small></div>
      <button type="button" class="btn tariff-pay" disabled ${act} data-plan-buy data-umami-event="tariff-pay">${current ? 'Продлить тариф' : 'Оплатить тариф'}</button>${landing ? TRIAL_NOTE : ''}
    </div></div></article>`;
}

// Бесплатный доступ на лендинге объясняется рядом с ценой, а не мелким шрифтом
// внизу: человек сравнивает «платить сейчас» и «попробовать», и оба варианта
// должны быть перед глазами вместе с честной границей пробного тарифа.
const TRIAL_NOTE = `<div class="tariff-trial">
  <b>Сначала бесплатно, 14 дней</b>
  <p>Поиск лидов на YouTube, сбор контактов и проверка в Telegram — без оплаты и без карты. Рассылка на пробном тарифе выключена: сообщения уходят с ваших аккаунтов в чужие личные, и за это мы отвечаем только на платном.</p>
  <button type="button" class="btn ghost" data-telegram-login-trigger data-umami-event="tariff-trial">Начать бесплатно</button>
</div>`;

function cardFor(containerId) {
  return document.querySelector(`[data-plan-card][data-container="${containerId}"]`);
}

function setSelected(card, selector, selected) {
  card.querySelectorAll(selector).forEach((button) => {
    const active = button === selected;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

function setPromoStatus(card, tone, text) {
  const status = card.querySelector('[data-plan-promo-status]');
  if (!status) return;
  status.className = tone ? `promo-${tone}` : '';
  renderText(status, text);
}

export function createPriceAnimator({
  tween = createNumberTween(),
  render,
  reducedMotion = prefersReducedMotion,
} = {}) {
  let displayed = null;
  let cancel = () => {};
  const animate = (total, currency) => {
    cancel();
    const from = Number.isFinite(displayed) ? displayed : total;
    const nextCancel = tween({
      from,
      to: total,
      duration: MOTION.price,
      reducedMotion: reducedMotion(),
      onUpdate: (value) => {
        displayed = value;
        render(money(Math.round(value), currency));
      },
    });
    cancel = typeof nextCancel === 'function' ? nextCancel : () => {};
  };
  animate.cancel = () => {
    cancel();
    cancel = () => {};
  };
  return animate;
}

function renderQuote(containerId, update, promoGeneration) {
  const state = states.get(containerId);
  const card = cardFor(containerId);
  if (!state || !card) return;
  const buy = card.querySelector('[data-plan-buy]');
  card.setAttribute('aria-busy', String(update.busy));
  if (buy) buy.disabled = update.busy || !update.quote;
  if (update.busy) return;
  /* Ответ пришёл — снимаем лёгкое размытие цены, включённое на время смены. */
  card.classList.remove('is-swapping');
  if (update.error) {
    if (state.promoAttempt.isCurrent(promoGeneration))
      setPromoStatus(card, 'error', 'Не удалось применить промокод. Повторите.');
    else if (promoGeneration === null) toast(`Не удалось рассчитать цену: ${update.error}`, 'warn');
    state.priceAnimator.cancel();
    state.checkoutAnimator.cancel();
    return;
  }
  if (!update.quote) return;
  state.quote = update.quote;
  state.priceAnimator(update.quote.total_amount, update.quote.currency);
  state.checkoutAnimator(update.quote.total_amount, update.quote.currency);
  renderText(
    card.querySelector('[data-plan-caption]'),
    update.quote.period_months === 3 ? 'одним платежом за 3 месяца' : 'за 1 месяц',
  );
  if (state.promoAttempt.isCurrent(promoGeneration))
    setPromoStatus(
      card,
      update.quote.promo_valid ? 'ok' : 'error',
      update.quote.promo_valid
        ? 'Промокод применён'
        : 'Промокод не подошёл. Проверьте и повторите.',
    );
}

function requestQuote(containerId, promoGeneration = null) {
  const state = states.get(containerId);
  if (!state) return Promise.resolve(null);
  state.quote = null;
  state.priceAnimator.cancel();
  state.checkoutAnimator.cancel();
  /* Пока считаем новую цену — цифра слегка размывается (без скачков вёрстки). */
  const swapCard = cardFor(containerId);
  if (swapCard) swapCard.classList.add('is-swapping');
  return quoteMachine.quote(
    containerId,
    { currency: state.currency, period_months: state.months, promo_code: state.promo },
    (update) => renderQuote(containerId, update, promoGeneration),
  );
}

function disposeState(containerId) {
  const state = states.get(containerId);
  quoteMachine.cancel(containerId);
  state?.priceAnimator.cancel();
  state?.checkoutAnimator.cancel();
  states.delete(containerId);
}

function render(plans, currentSlug, containerId) {
  const grid = document.getElementById(containerId);
  if (!grid) return;
  const plan = (plans || [])[0];
  disposeState(containerId);
  if (!plan) {
    grid.innerHTML = '<div class="dd-empty">Тариф пока недоступен</div>';
    return;
  }
  states.set(containerId, {
    checkoutAnimator: createPriceAnimator({
      render: (value) => {
        const checkout = cardFor(containerId)?.querySelector('[data-plan-checkout-price]');
        if (checkout) renderText(checkout, value);
      },
    }),
    currency: 'RUB',
    months: 1,
    priceAnimator: createPriceAnimator({
      render: (value) => {
        const price = cardFor(containerId)?.querySelector('[data-plan-price]');
        if (price) renderText(price, value);
      },
    }),
    promoAttempt: createPromoAttemptState(),
    promo: '',
    quote: null,
  });
  grid.classList.add('tariff-single-grid');
  grid.innerHTML = createTariffCardMarkup(plan, plan.slug === currentSlug, containerId);
  requestQuote(containerId);
}

function openPayment(containerId) {
  const quote = states.get(containerId)?.quote;
  if (!quote || !adminContact) return toast('Контакт для оплаты пока недоступен', 'warn');
  renderText(document.getElementById('planConfirmName'), `Talaria · ${periodText(quote)}`);
  renderText(
    document.getElementById('planConfirmContact'),
    `@${String(adminContact).replace(/^@/, '')}`,
  );
  renderText(
    document.getElementById('planConfirmPromo'),
    `Промокод применён: ${quote.promo_valid ? 'да' : 'нет'}. К оплате: ${money(quote.total_amount, quote.currency)}.`,
  );
  const link = document.getElementById('planConfirmContinue');
  if (link) link.href = buildPaymentUrl(adminContact, quote);
  paymentDialog?.open();
}

export function handlePlanAction(element) {
  if (element?.matches?.('[data-plan-confirm-continue]')) {
    document.getElementById('modalPlanTelegram')?.close();
    toast('Открываем подготовленное сообщение администратору', 'info');
    return;
  }
  const card = element?.closest?.('[data-plan-card]');
  const containerId = card?.dataset.container;
  const state = states.get(containerId);
  if (!card || !state) return;
  if (element.matches('[data-plan-period]')) {
    state.months = Number(element.dataset.planPeriod) === 3 ? 3 : 1;
    setSelected(card, '[data-plan-period]', element);
    card.querySelector('[data-plan-period-control]').dataset.period = String(state.months);
    requestQuote(containerId);
  } else if (element.matches('[data-plan-currency]')) {
    state.currency = element.dataset.planCurrency === 'KZT' ? 'KZT' : 'RUB';
    setSelected(card, '[data-plan-currency]', element);
    requestQuote(containerId);
  } else if (element.matches('[data-plan-promo-reveal]')) {
    card.querySelector('[data-plan-promo-controls]').hidden = false;
    state.promoAttempt.open();
    // preventScroll: поле встаёт на место ссылки, скроллить страницу не надо —
    // иначе браузер дёргает вьюпорт и карточка «прыгает» под курсором.
    card.querySelector('[data-plan-promo-input]').focus({ preventScroll: true });
  } else if (element.matches('[data-plan-promo-close]')) {
    state.promo = '';
    state.promoAttempt.close();
    card.querySelector('[data-plan-promo-input]').value = '';
    card.querySelector('[data-plan-promo-controls]').hidden = true;
    setPromoStatus(card, '', '');
  } else if (element.matches('[data-plan-promo], [data-plan-promo-input]')) {
    state.promo = card.querySelector('[data-plan-promo-input]').value.trim();
    requestQuote(containerId, state.promoAttempt.open());
  } else if (element.matches('[data-plan-buy]')) openPayment(containerId);
}

/** Замок на разделах, которые пробный тариф не даёт.
 *
 * Кнопки не прячем и не отключаем: скрытая функция выглядит как поломка, а
 * отключённая — как ошибка входа. Объясняем причину над разделом, а сервер всё
 * равно откажет со своим текстом, если нажать.
 */
function applySendingLock(data) {
  const locks = document.querySelectorAll('[data-plan-lock]');
  if (!locks.length) return;
  const blocked = data.is_trial || data.trial_expired || data.plan === 'none';
  locks.forEach((box) => {
    if (!blocked) {
      box.hidden = true;
      box.innerHTML = '';
      return;
    }
    const why = data.is_trial
      ? 'На пробном тарифе отправка сообщений выключена: сообщения уходят с ваших аккаунтов в чужие личные, и за это мы отвечаем только на платном тарифе.'
      : 'Тариф не активирован, поэтому отправка недоступна.';
    box.innerHTML = `<b>Раздел доступен на платном тарифе</b><span>${esc(why)} Поиск лидов, базы и офферы работают как обычно.</span><button type="button" class="btn" data-act="goTo" data-args='["tariffs"]'>Открыть тариф</button>`;
    box.hidden = false;
  });
}

/** Полоса пробного доступа над всем кабинетом.
 *
 * Три состояния, и у каждого свой текст: идёт триал, триал кончился, тарифа
 * никогда не было. Молчать в двух последних нельзя — человек уже внутри и
 * должен понимать, почему кнопки не работают.
 */
function renderTrialBar(data) {
  const bar = document.getElementById('trialBar');
  if (!bar) return;
  const pay = `<button type="button" class="btn" data-act="goTo" data-args='["tariffs"]'>Оплатить тариф</button>`;
  if (data.is_trial) {
    const left = Number(data.trial_days_left || 0);
    const days = left === 1 ? 'день' : left >= 2 && left <= 4 ? 'дня' : 'дней';
    bar.className = 'trial-bar on';
    bar.innerHTML = `<div><b>Пробный доступ · осталось ${left} ${days}</b><span>Поиск лидов, сбор контактов и проверка в Telegram работают полностью. Рассылка выключена: сообщения уходят с ваших аккаунтов в чужие личные, и это только на платном тарифе.</span></div>${pay}`;
    bar.hidden = false;
    return;
  }
  if (data.trial_expired) {
    bar.className = 'trial-bar warn';
    bar.innerHTML = `<div><b>Пробный доступ закончился</b><span>Найденные лиды и базы на месте — они никуда не делись. Чтобы продолжить искать и включить рассылку, оплатите тариф.</span></div>${pay}`;
    bar.hidden = false;
    return;
  }
  bar.hidden = true;
  bar.innerHTML = '';
}

function renderMyPlan(data) {
  const panel = document.getElementById('myPlanPanel');
  if (!panel) return;
  panel.style.display = 'block';
  const badge = document.getElementById('myPlanBadge');
  if (badge) {
    renderText(badge, data.blocked ? 'Доступ приостановлен' : data.plan_name);
    badge.className = `badge ${data.blocked ? 'err' : data.expired ? 'warn' : 'on'}`;
  }
  const notices = document.getElementById('myPlanNotices');
  if (notices)
    notices.innerHTML = data.blocked
      ? `<div class="notice err">Доступ приостановлен. Напишите @${esc(data.admin_contact || '')}.</div>`
      : data.expired
        ? '<div class="notice warn">Оплаченный период завершён. Продлите тариф ниже.</div>'
        : '';
  renderText(
    document.getElementById('myPlanUntil'),
    data.paid_until
      ? `Оплачен до ${new Date(data.paid_until).toLocaleDateString('ru-RU')}`
      : 'Тариф ещё не активирован',
  );
  const usage = document.getElementById('myPlanUsage');
  if (usage)
    usage.innerHTML = `<div class="plan-active-features"><b>В тариф входит</b><div>${(data.plans?.[0]?.features || []).map((item) => `<span>${esc(item)}</span>`).join('')}</div></div>`;
  const renew = document.getElementById('myPlanRenew');
  if (renew) renew.innerHTML = '';
}

export async function loadPublicPlans() {
  if (publicLoading) return;
  publicLoading = true;
  const grid = document.getElementById('landingTariffGrid');
  grid?.setAttribute('aria-busy', 'true');
  try {
    const data = await api('GET', '/api/plans');
    adminContact = data.admin_contact || '';
    render(data.plans, null, 'landingTariffGrid');
    renderText(
      document.getElementById('landingTariffNote'),
      'Условия оплаты рассчитываются перед оформлением.',
    );
    const foot = document.getElementById('landingTariffFoot');
    if (foot && adminContact)
      foot.innerHTML = `Вопросы по оплате: <a href="https://t.me/${encodeURIComponent(String(adminContact).replace(/^@/, ''))}" target="_blank" rel="noopener">@${esc(String(adminContact).replace(/^@/, ''))}</a>`;
  } catch (error) {
    if (grid)
      grid.innerHTML = `<div class="tariff-load-error"><b>Тариф временно не загрузился</b><span>${esc(errorMessage(error))}</span><button class="btn ghost" type="button" data-act="loadPublicPlans">Повторить</button></div>`;
  } finally {
    publicLoading = false;
    grid?.setAttribute('aria-busy', 'false');
  }
}

export async function loadSubscription() {
  if (!getToken()) return;
  try {
    const data = await api('GET', '/api/subscription');
    adminContact = data.admin_contact || '';
    render(data.plans, data.plan, 'tariffGrid');
    renderMyPlan(data);
    renderTrialBar(data);
    applySendingLock(data);
    return data;
  } catch (error) {
    const grid = document.getElementById('tariffGrid');
    if (grid)
      grid.innerHTML = `<div class="tariff-load-error"><b>Не удалось загрузить тариф</b><span>${esc(errorMessage(error))}</span><button class="btn ghost" type="button" data-act="loadSubscription">Повторить</button></div>`;
  }
}

export function initPlans() {
  if (initialized) return;
  initialized = true;
  const dialog = document.getElementById('modalPlanTelegram');
  paymentDialog = dialog ? attachDialogLifecycle(dialog) : null;
  const continueLink = document.getElementById('planConfirmContinue');
  if (continueLink) {
    continueLink.dataset.act = 'handlePlanAction';
    continueLink.dataset.args = '["__EL__"]';
    continueLink.dataset.planConfirmContinue = '';
  }
}
