import { expect, test } from '@playwright/test';
import { mockApi } from './helpers/mock-api.js';

test.setTimeout(180_000);

// Регресс на «ровность» кабинета (правка Samat, v35). Раньше в одной строке
// стояли поля 38/39 px и кнопка 41 px, а шаг сетки в соседних блоках был 9, 14
// и 18 px — страница выглядела собранной на глаз. Тест держит две вещи: единую
// высоту органов управления и конечный набор шагов сетки.
const SECTIONS = ['dashboard', 'search', 'databases', 'offer', 'mailing', 'tariffs', 'settings'];
const CONTROL_HEIGHT = 38;
const LAYOUT_GAPS = new Set(['8px', '10px', '12px', '16px']);
const LAYOUT_BOXES =
  '.grid-2, .grid-3, .grid-4, .panel-row, .creator-with-log, .quick-start-grid, .lead-filter-grid, .theme-grid, .offer-side, .key-add, .pay-form';

test('кабинет: одна высота у полей и кнопок, конечный набор шагов сетки', async ({ page }) => {
  await mockApi(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem('mf_token', 'visual-fixture-token');
    // Тур первого входа всплывает через 400мс и перехватывает клики — в тестах он просмотрен.
    localStorage.setItem('mf_onboarded', '1');
  });
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
    const measured = await page.evaluate((boxes) => {
      const section = document.querySelector('.section.active');
      const visible = (element) => {
        const rect = element.getBoundingClientRect();
        return rect.width > 4 && rect.height > 4;
      };
      const heights = new Set();
      const gaps = new Set();
      const controls =
        'input[type="text"], input[type="number"], input[type="tel"], input[type="search"], select, .btn';
      for (const element of section.querySelectorAll(controls)) {
        if (visible(element)) heights.add(Math.round(element.getBoundingClientRect().height));
      }
      for (const element of section.querySelectorAll(boxes)) {
        if (!visible(element) || element.children.length < 2) continue;
        const style = getComputedStyle(element);
        gaps.add(style.rowGap);
        gaps.add(style.columnGap);
      }
      return { heights: [...heights], gaps: [...gaps] };
    }, LAYOUT_BOXES);

    expect(measured.heights, `высоты органов управления в разделе ${id}`).toEqual(
      measured.heights.length ? [CONTROL_HEIGHT] : [],
    );
    for (const gap of measured.gaps) {
      expect(LAYOUT_GAPS.has(gap), `шаг сетки ${gap} в разделе ${id}`).toBe(true);
    }
  }
});
