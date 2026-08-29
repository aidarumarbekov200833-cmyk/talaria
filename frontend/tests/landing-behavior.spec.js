import { expect, test } from '@playwright/test';

const publicPlan = {
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

async function installTelegramScript(page) {
  await page.route('https://telegram.org/js/telegram-widget.js?22', (route) =>
    route.fulfill({
      body: `(() => {
        const script = document.currentScript;
        const frame = document.createElement('iframe');
        frame.title = 'Telegram Login Widget';
        frame.src = 'about:blank';
        script.parentNode.appendChild(frame);
      })();`,
      contentType: 'application/javascript',
    }),
  );
}

async function installApi(page, { configError = '', stats } = {}) {
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/public/stats') {
      return route.fulfill({
        body: JSON.stringify(
          stats ?? {
            audience: 4820000,
            countries: 7,
            databases: 28,
            delivered: 615,
            leads: 1250,
            messages_sent: 640,
            replies: 48,
          },
        ),
        contentType: 'application/json',
      });
    }
    if (url.pathname === '/api/config') {
      if (configError) {
        return route.fulfill({
          body: JSON.stringify({ detail: configError }),
          contentType: 'application/json',
          status: 503,
        });
      }
      return route.fulfill({
        body: JSON.stringify({ bot_username: 'zephyr_test_bot', demo_mode: false }),
        contentType: 'application/json',
      });
    }
    if (url.pathname === '/api/plans') {
      return route.fulfill({
        body: JSON.stringify({ admin_contact: 'zephyr_admin', plans: [publicPlan] }),
        contentType: 'application/json',
      });
    }
    if (url.pathname === '/api/plans/quote') {
      return route.fulfill({
        body: JSON.stringify({
          currency: 'RUB',
          period_months: 1,
          promo_valid: false,
          total_amount: 5000,
        }),
        contentType: 'application/json',
      });
    }
    return route.fulfill({ body: '{}', contentType: 'application/json' });
  });
}

function collectErrors(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  return errors;
}

test('stats, promo reveal, and both native dialogs preserve keyboard and focus behavior', async ({
  page,
}) => {
  const errors = collectErrors(page);
  await installTelegramScript(page);
  await installApi(page);
  await page.goto('/index.html');

  const loginDialog = page.locator('#telegramLoginDialog');
  await expect(loginDialog).not.toHaveAttribute('open', '');
  await expect(page.locator('#tg-widget-dialog-container iframe')).toHaveCount(1);
  await expect(page.locator('#tg-widget-dialog-container script[data-telegram-login]')).toHaveCount(
    1,
  );
  await expect(page.locator('#publicStatsStatus')).toHaveText(
    'фактические данные рабочего сервиса',
  );
  await expect(page.locator('[data-public-stat="leads"]').first()).toHaveText(/1[\s\u00a0]250/);

  const opener = page.locator('[data-telegram-login-trigger]').first();
  await opener.click();
  await expect(loginDialog).toHaveAttribute('open', '');
  await page.keyboard.press('Escape');
  await expect(loginDialog).not.toHaveAttribute('open', '');
  await expect(opener).toBeFocused();
  await opener.click();
  await loginDialog.evaluate((dialog) =>
    dialog.dispatchEvent(new MouseEvent('click', { bubbles: true })),
  );
  await expect(loginDialog).not.toHaveAttribute('open', '');
  await expect(opener).toBeFocused();

  // Промокод раскрывается на месте ссылки и НЕ двигает карточку (карточка не
  // должна «подрастать»): ссылка прячется, компактный ряд встаёт на её место,
  // а кнопка оплаты остаётся на прежней позиции.
  const promoReveal = page.locator('#landingTariffGrid [data-plan-promo-reveal]');
  await promoReveal.scrollIntoViewIfNeeded();
  const buy = page.locator('#landingTariffGrid [data-plan-buy]');
  await expect(buy).toBeEnabled();
  const buyBefore = await buy.boundingBox();
  await promoReveal.click();
  await expect(page.locator('#landingTariffGrid [data-plan-promo-controls]')).toBeVisible();
  await expect(promoReveal).toBeHidden();
  const buyAfter = await buy.boundingBox();
  expect(Math.abs(buyAfter.y - buyBefore.y)).toBeLessThanOrEqual(4);
  await page.locator('#landingTariffGrid [data-plan-promo-close]').click();
  await expect(promoReveal).toBeVisible();

  await buy.scrollIntoViewIfNeeded();
  await expect(buy).toBeEnabled();
  await buy.click();
  await expect(page.locator('#modalPlanTelegram')).toHaveAttribute('open', '');
  await page.keyboard.press('Escape');
  await expect(page.locator('#modalPlanTelegram')).not.toHaveAttribute('open', '');
  await expect(buy).toBeFocused();
  await buy.click();
  const planDialog = page.locator('#modalPlanTelegram');
  await expect(planDialog).toHaveAttribute('open', '');
  const bounds = await planDialog.boundingBox();
  expect(bounds).not.toBeNull();
  await page.mouse.click(Math.max(1, bounds.x - 8), Math.max(1, bounds.y + bounds.height / 2));
  await expect(planDialog).not.toHaveAttribute('open', '');
  await expect(buy).toBeFocused();
  expect(errors).toEqual([]);
});

test('invalid hostile stats and auth errors remain text and login never auto-opens', async ({
  page,
}) => {
  const errors = collectErrors(page);
  const hostile = '<img data-hostile src=x onerror=alert(1)>';
  await installApi(page, {
    configError: hostile,
    stats: {
      audience: 4820000,
      countries: 7,
      databases: 28,
      delivered: 615,
      leads: hostile,
      messages_sent: 640,
      replies: 48,
    },
  });
  await page.goto('/index.html');

  await expect(page.locator('#telegramLoginDialog')).not.toHaveAttribute('open', '');
  await expect(page.locator('[data-public-stat]')).toHaveText([
    '—',
    '—',
    '—',
    '—',
    '—',
    '—',
    '—',
    '—',
  ]);
  await expect(page.locator('#publicStatsStatus')).toHaveText('Данные временно недоступны');
  await expect(page.locator('#dialogLoginHint')).toContainText(hostile);
  await expect(page.locator('[data-hostile]')).toHaveCount(0);
  expect(errors.filter((message) => !message.includes('503 (Service Unavailable)'))).toEqual([]);
});

test('reduced motion exposes all reveal content and final metrics immediately', async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await installTelegramScript(page);
  await installApi(page);
  await page.goto('/index.html');

  await expect(page.locator('[data-public-stat="messages_sent"]')).toHaveText('640');
  const hidden = await page.locator('.zd-reveal').evaluateAll(
    (nodes) =>
      nodes.filter((node) => {
        const style = getComputedStyle(node);
        return style.opacity === '0' || style.visibility === 'hidden';
      }).length,
  );
  expect(hidden).toBe(0);
});
