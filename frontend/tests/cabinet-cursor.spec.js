import { expect, test } from '@playwright/test';
import { mockApi } from './helpers/mock-api.js';

test.setTimeout(180_000);

// Регресс на курсор (правка Samat, v36): «кнопки которые нажимаются должны менять
// курсор». Скрипт нашёл 19 таких мест — карточки-ссылки, вкладки поиска, галочки,
// списки, «Очистить», меню набора фильтров. Тест держит правило для всего кабинета.
const SECTIONS = ['dashboard', 'search', 'databases', 'offer', 'mailing', 'settings', 'admin'];
const CLICKABLE = [
  'a[href]',
  'button:not(:disabled)',
  'summary',
  'select:not(:disabled)',
  'label.check-row',
  'input[type="checkbox"]',
  '[role="button"]',
  '.link',
  '.nav-item',
  '.theme-card',
  '.offer-tab',
  '.search-tab',
  '.stat-card.go',
].join(', ');

test('кабинет: всё нажимаемое меняет курсор на pointer', async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => localStorage.setItem('mf_token', 'visual-fixture-token'));
  await page.goto('/index.html');
  await page.locator('[data-telegram-login-trigger]').first().click();
  await expect(page.locator('#appWrapper')).toHaveClass(/\bactive\b/);
  await page.locator('#loginScreen').evaluate((element) => {
    element.classList.add('hidden');
    element.style.display = 'none';
  });
  await page.locator('#appWrapper').evaluate((element) => {
    element.style.display = 'block';
  });

  for (const id of SECTIONS) {
    await page.evaluate((activeId) => {
      for (const element of document.querySelectorAll('.section')) {
        const active = element.id === activeId;
        element.classList.toggle('active', active);
        element.hidden = !active;
        element.style.display = active ? 'block' : 'none';
      }
    }, id);
    const offenders = await page.evaluate((selector) => {
      const section = document.querySelector('.section.active');
      const found = [];
      for (const scope of [section, document.querySelector('.sidebar')].filter(Boolean)) {
        for (const element of scope.querySelectorAll(selector)) {
          const rect = element.getBoundingClientRect();
          if (rect.width < 3 || rect.height < 3) continue;
          if (getComputedStyle(element).cursor !== 'pointer') {
            found.push(`${element.tagName.toLowerCase()}#${element.id || '—'}`);
          }
        }
      }
      return found;
    }, CLICKABLE);
    expect(offenders, `нажимаемое без pointer в разделе ${id}`).toEqual([]);
  }
});
