import { expect, test } from '@playwright/test';

test('landing exposes the Telegram login route', async ({ page }) => {
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.route('**/api/**', (route) =>
    route.fulfill({ contentType: 'application/json', status: 200, body: '{}' }),
  );
  await page.goto('/index.html');

  await expect(page).toHaveTitle(/Talaria/);
  await expect(page.locator('#loginScreen')).toBeVisible();
  await expect(page.locator('[data-telegram-login-trigger]').first()).toBeVisible();
  const unresolved = await page
    .locator('[data-act], [data-change-act], [data-input-act], [data-enter-act]')
    .evaluateAll((elements) => {
      const names = new Set();
      for (const element of elements) {
        for (const attribute of [
          'data-act',
          'data-change-act',
          'data-input-act',
          'data-enter-act',
        ]) {
          const name = element.getAttribute(attribute);
          if (name) names.add(name);
        }
      }
      return [...names].filter((name) => typeof window[name] !== 'function');
    });
  expect(unresolved).toEqual([]);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
