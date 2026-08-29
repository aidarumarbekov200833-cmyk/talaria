import { expect, test } from '@playwright/test';

const plan = {
  ai: true,
  ai_offer: true,
  features: ['Поиск лидов'],
  max_databases: -1,
  max_offers: -1,
  max_per_launch: -1,
  max_warmup: -1,
  monthly_ai_offers: -1,
  monthly_leads: -1,
  monthly_messages: -1,
  name: 'Talaria',
  price_kzt: 25000,
  price_kzt_first: 25000,
  price_rub: 5000,
  price_rub_first: 5000,
  slug: 'zephyr',
  tagline: 'Все инструменты',
};

function quoteFor({ currency = 'RUB', period_months = 1, promo_code = '' }) {
  const promo = promo_code === 'valid-private-code';
  const total = {
    KZT: { 1: promo ? 17500 : 25000, 3: promo ? 56250 : 63750 },
    RUB: { 1: promo ? 3500 : 5000, 3: promo ? 11250 : 12750 },
  }[currency][period_months];
  return { currency, period_months, promo_valid: promo, total_amount: total };
}

async function installTariffApi(page) {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/plans') {
      return route.fulfill({
        body: JSON.stringify({ admin_contact: 'zephyr_admin', plans: [plan] }),
        contentType: 'application/json',
      });
    }
    if (url.pathname === '/api/plans/quote') {
      return route.fulfill({
        body: JSON.stringify(quoteFor(JSON.parse(route.request().postData() || '{}'))),
        contentType: 'application/json',
      });
    }
    return route.fulfill({ body: '{}', contentType: 'application/json' });
  });
}

test('tariff controls use server quotes and keep the promo value private', async ({ page }) => {
  const consoleErrors = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await installTariffApi(page);
  await page.goto('/index.html');

  const card = page.locator('#landingTariffGrid [data-plan-card]');
  await expect(card).toHaveAttribute('aria-busy', 'false');
  await expect(card.locator('[data-plan-price]')).toHaveText(/5[\s\u00a0]000 ₽/);
  await expect(card.locator('[data-plan-buy]')).toBeEnabled();
  await card.locator('[data-plan-currency="KZT"]').click();
  await expect(card.locator('[data-plan-checkout-price]')).toHaveText(/25[\s\u00a0]000 ₸/);
  await card.locator('[data-plan-period="3"]').click();
  await expect(card.locator('[data-plan-checkout-price]')).toHaveText(/63[\s\u00a0]750 ₸/);
  await expect(card.locator('[data-plan-promo-reveal]')).toBeVisible();
  await card.locator('[data-plan-promo-reveal]').click();
  await expect(card.locator('[data-plan-promo-controls]')).not.toHaveAttribute('hidden', '');
  const promoInput = card.locator('[data-plan-promo-input]');
  await expect(promoInput).toHaveAttribute('placeholder', 'Введите промокод');
  await promoInput.fill('not-a-private-code');
  await card.locator('[data-plan-promo]').click();
  await expect(card.locator('[data-plan-promo-status]')).toHaveText(
    'Промокод не подошёл. Проверьте и повторите.',
  );
  await expect(card.locator('[data-plan-promo-status]')).not.toContainText('not-a-private-code');
  await promoInput.fill('valid-private-code');
  await card.locator('[data-plan-promo]').click();
  await expect(card.locator('[data-plan-promo-status]')).toHaveText('Промокод применён');
  await expect(card.locator('[data-plan-checkout-price]')).toHaveText(/56[\s\u00a0]250 ₸/);
  await card.locator('[data-plan-promo-close]').click();
  await expect(card.locator('[data-plan-promo-controls]')).toHaveAttribute('hidden', '');
  await card.locator('[data-plan-buy]').click();
  await expect(page.locator('#modalPlanTelegram')).toHaveAttribute('open', '');
  const href = await page.locator('#planConfirmContinue').getAttribute('href');
  expect(decodeURIComponent(href)).not.toContain('valid-private-code');
  expect(decodeURIComponent(href)).toContain('Промокод применён: да');
  expect(consoleErrors).toEqual([]);
});

test('latest tariff quote owns the price and purchase availability', async ({ page }) => {
  const pending = [];
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/plans')
      return route.fulfill({
        body: JSON.stringify({ admin_contact: 'zephyr_admin', plans: [plan] }),
        contentType: 'application/json',
      });
    if (url.pathname === '/api/plans/quote') {
      pending.push(route);
      return undefined;
    }
    return route.fulfill({ body: '{}', contentType: 'application/json' });
  });
  await page.goto('/index.html');
  const card = page.locator('#landingTariffGrid [data-plan-card]');
  await expect.poll(() => pending.length).toBe(1);
  await card.locator('[data-plan-currency="KZT"]').click();
  await expect(card).toHaveAttribute('aria-busy', 'true');
  await expect(card.locator('[data-plan-buy]')).toBeDisabled();
  await expect.poll(() => pending.length).toBe(2);
  await pending[1].fulfill({
    body: JSON.stringify(quoteFor({ currency: 'KZT' })),
    contentType: 'application/json',
  });
  await expect(card.locator('[data-plan-checkout-price]')).toHaveText(/25[\s\u00a0]000 ₸/);
  await expect(card.locator('[data-plan-buy]')).toBeEnabled();
});

test('a failed latest quote clears busy state and leaves payment unavailable', async ({ page }) => {
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/plans')
      return route.fulfill({
        body: JSON.stringify({ admin_contact: 'zephyr_admin', plans: [plan] }),
        contentType: 'application/json',
      });
    if (url.pathname === '/api/plans/quote')
      return route.fulfill({ body: JSON.stringify({ detail: 'offline' }), status: 503 });
    return route.fulfill({ body: '{}', contentType: 'application/json' });
  });
  await page.goto('/index.html');
  const card = page.locator('#landingTariffGrid [data-plan-card]');
  await expect(card).toHaveAttribute('aria-busy', 'false');
  await expect(card.locator('[data-plan-buy]')).toBeDisabled();
});

test('a promo network failure uses fixed feedback and a retry restores the latest quote', async ({
  page,
}) => {
  let quoteCalls = 0;
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/plans')
      return route.fulfill({
        body: JSON.stringify({ admin_contact: 'zephyr_admin', plans: [plan] }),
        contentType: 'application/json',
      });
    if (url.pathname === '/api/plans/quote') {
      quoteCalls += 1;
      if (quoteCalls === 2)
        return route.fulfill({ body: JSON.stringify({ detail: 'server secret' }), status: 503 });
      return route.fulfill({
        body: JSON.stringify(quoteFor(JSON.parse(route.request().postData() || '{}'))),
        contentType: 'application/json',
      });
    }
    return route.fulfill({ body: '{}', contentType: 'application/json' });
  });
  await page.goto('/index.html');
  const card = page.locator('#landingTariffGrid [data-plan-card]');
  await expect(card.locator('[data-plan-buy]')).toBeEnabled();
  await card.locator('[data-plan-promo-reveal]').click();
  await card.locator('[data-plan-promo-input]').fill('valid-private-code');
  await card.locator('[data-plan-promo]').click();
  await expect(card.locator('[data-plan-promo-status]')).toHaveText(
    'Не удалось применить промокод. Повторите.',
  );
  await expect(card.locator('[data-plan-promo-status]')).not.toContainText('valid-private-code');
  await expect(card.locator('[data-plan-promo-status]')).not.toContainText('server secret');
  await expect(card.locator('[data-plan-buy]')).toBeDisabled();
  await card.locator('[data-plan-promo]').click();
  await expect(card.locator('[data-plan-promo-status]')).toHaveText('Промокод применён');
  await expect(card.locator('[data-plan-buy]')).toBeEnabled();
});
