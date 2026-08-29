import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  buildPaymentUrl,
  createPriceAnimator,
  createPromoAttemptState,
  createQuoteMachine,
  createTariffCardMarkup,
} from '../../js/tariffs.js';
import * as plansFacade from '../../js/plans.js';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, reject, resolve };
}

test('plans compatibility facade retains the public tariff entry points', () => {
  assert.equal(typeof plansFacade.loadPublicPlans, 'function');
  assert.equal(typeof plansFacade.loadSubscription, 'function');
  assert.equal(typeof plansFacade.initPlans, 'function');
  assert.equal(typeof plansFacade.handlePlanAction, 'function');
});

test('tariff initialization attaches the native payment lifecycle only once', async () => {
  const originalDocument = globalThis.document;
  const listeners = [];
  const dialog = {
    addEventListener(type, listener) {
      listeners.push({ listener, type });
    },
    open: false,
  };
  const link = { dataset: {} };
  globalThis.document = {
    getElementById(id) {
      return id === 'modalPlanTelegram' ? dialog : id === 'planConfirmContinue' ? link : null;
    },
  };

  try {
    const isolated = await import(`../../js/tariffs.js?init-test=${Date.now()}`);
    isolated.initPlans();
    isolated.initPlans();
  } finally {
    globalThis.document = originalDocument;
  }

  assert.deepEqual(
    listeners.map(({ type }) => type),
    ['close', 'click'],
  );
  assert.deepEqual(link.dataset, {
    act: 'handlePlanAction',
    args: '["__EL__"]',
    planConfirmContinue: '',
  });
});

test('quote machine keeps only the latest container request authoritative', async () => {
  const requests = [];
  const updates = [];
  const machine = createQuoteMachine({
    requestQuote(payload, options) {
      const request = deferred();
      requests.push({ ...request, options, payload });
      return request.promise;
    },
  });
  const render = (update) => updates.push(update);

  const first = machine.quote('landingTariffGrid', { currency: 'RUB', period_months: 1 }, render);
  const second = machine.quote('landingTariffGrid', { currency: 'KZT', period_months: 1 }, render);

  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(updates.at(-1).busy, true);
  requests[0].resolve({ total_amount: 5000, currency: 'RUB' });
  requests[1].resolve({ total_amount: 25000, currency: 'KZT' });
  await Promise.all([first, second]);

  assert.deepEqual(updates.at(-1), {
    busy: false,
    error: null,
    quote: { currency: 'KZT', total_amount: 25000 },
  });
  assert.equal(
    updates.some((update) => update.quote?.currency === 'RUB'),
    false,
  );
});

test('quote machine ignores a late rejection and exposes the latest error only', async () => {
  const requests = [];
  const updates = [];
  const machine = createQuoteMachine({
    requestQuote(_payload, options) {
      const request = deferred();
      requests.push({ ...request, options });
      return request.promise;
    },
  });

  const first = machine.quote('tariffGrid', {}, (update) => updates.push(update));
  const second = machine.quote('tariffGrid', {}, (update) => updates.push(update));
  requests[0].reject(new Error('stale failure'));
  requests[1].reject(new Error('latest failure'));
  await Promise.all([first, second]);

  assert.deepEqual(updates.at(-1), { busy: false, error: 'latest failure', quote: null });
  assert.equal(
    updates.some((update) => update.error === 'stale failure'),
    false,
  );
});

test('quote machine cancellation makes a destroyed container inert', async () => {
  const request = deferred();
  const updates = [];
  const machine = createQuoteMachine({ requestQuote: () => request.promise });
  const quoted = machine.quote('landingTariffGrid', {}, (update) => updates.push(update));

  machine.cancel('landingTariffGrid');
  request.resolve({ currency: 'RUB', total_amount: 5000 });
  await quoted;

  assert.equal(updates.length, 1);
  assert.equal(updates[0].busy, true);
});

test('payment URL describes the final quote without leaking the entered promo code', () => {
  const url = buildPaymentUrl('administrator', {
    currency: 'RUB',
    period_months: 3,
    promo_code: 'secret-promo',
    promo_valid: true,
    total_amount: 11250,
  });

  const decoded = decodeURIComponent(url);
  assert.match(decoded, /Talaria/);
  assert.doesNotMatch(decoded, /Zephyr/);
  assert.match(decoded, /3 месяца/);
  assert.match(decoded, /RUB/);
  assert.match(decoded, /Промокод применён: да/);
  assert.match(decoded, /11[\s\u00a0]?250/);
  assert.doesNotMatch(decoded, /secret-promo/);
});

test('price animator uses the shared 400ms tween, including intermediate and reduced-motion final values', () => {
  const queued = [];
  const rendered = [];
  const tween = ({ from, onUpdate, reducedMotion, to }) => {
    if (reducedMotion) {
      onUpdate(to);
      return;
    }
    queued.push(() => onUpdate((from + to) / 2));
    queued.push(() => onUpdate(to));
  };
  const animate = createPriceAnimator({
    reducedMotion: () => false,
    render: (value) => rendered.push(value),
    tween,
  });

  animate(5000, 'RUB');
  queued.shift()();
  queued.shift()();
  animate(11250, 'RUB');
  queued.shift()();
  queued.shift()();
  assert.deepEqual(rendered, ['5 000 ₽', '5 000 ₽', '8 125 ₽', '11 250 ₽']);

  const immediate = [];
  createPriceAnimator({
    reducedMotion: () => true,
    render: (value) => immediate.push(value),
    tween,
  })(56250, 'KZT');
  assert.deepEqual(immediate, ['56 250 ₸']);
});

test('a replacement price tween starts from the visible intermediate value', () => {
  const calls = [];
  const animate = createPriceAnimator({
    render() {},
    tween: (options) => calls.push(options),
  });

  animate(5000, 'RUB');
  calls[0].onUpdate(2200);
  animate(11250, 'RUB');

  assert.equal(calls[0].duration, 400);
  assert.equal(calls[1].from, 2200);
  assert.equal(calls[1].to, 11250);
});

test('price animator cancels scheduled stale frames before a pending replacement or disposal', () => {
  const queued = [];
  const cancelled = [];
  const values = [];
  const tween = createPriceAnimator({
    render: (value) => values.push(value),
    tween: ({ onUpdate, to }) => {
      const frame = { cancelled: false, onUpdate, to };
      queued.push(frame);
      return () => {
        frame.cancelled = true;
        cancelled.push(frame);
      };
    },
  });

  tween(5000, 'RUB');
  queued[0].onUpdate(2200);
  tween.cancel();
  if (!queued[0].cancelled) queued[0].onUpdate(5000);

  assert.equal(cancelled.length, 1);
  assert.deepEqual(values, ['2 200 ₽']);
});

test('promo close invalidates only feedback from an old promo attempt', () => {
  const promo = createPromoAttemptState();
  const generation = promo.open();
  promo.close();

  assert.equal(promo.isCurrent(generation), false);
  assert.equal(promo.isCurrent(promo.open()), true);
});

test('final CSS cascade keeps the promo trigger visible and runtime markup private', () => {
  const appCss = readFileSync(new URL('../../css/app.css', import.meta.url), 'utf8');
  const tariffSource = readFileSync(new URL('../../js/tariffs.js', import.meta.url), 'utf8');

  assert.doesNotMatch(appCss, /\.promo-ticket\s*\{\s*display:\s*none/);
  assert.match(tariffSource, />У меня есть промокод</);
  assert.doesNotMatch(tariffSource, /ZEPHYR30|−30%|Например,/);
});

test('generated promo trigger has no secret or promo promise in visible or control attributes', () => {
  const markup = createTariffCardMarkup(
    { features: [], name: 'Talaria', price_rub: 5000, slug: 'zephyr', tagline: '' },
    false,
    'landingTariffGrid',
  );
  const trigger =
    markup.match(/<button[^>]*data-plan-promo-reveal[^>]*>[^<]+<\/button>/)?.[0] || '';
  const controlMarkup = markup.slice(0, markup.indexOf('data-plan-promo-input'));

  assert.equal(trigger.includes('У меня есть промокод'), true);
  assert.match(markup, /Talaria/);
  assert.doesNotMatch(markup, />Zephyr</);
  assert.doesNotMatch(controlMarkup, /ZEPHYR30|−30%|Например,|title=/);
  assert.doesNotMatch(controlMarkup, /aria-label="[^"]*(?:код|скидк|процент)/i);
});
