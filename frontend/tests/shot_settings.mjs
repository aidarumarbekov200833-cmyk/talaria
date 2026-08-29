/** Снимки новых панелей настроек и полосы режима просмотра.
 *
 *  Не тест: структуру и переполнение проверяют spec-файлы, а тут нужно увидеть
 *  цвета карточек риска, выделение текущего входа и то, что полоса просмотра
 *  не наезжает на шапку. Запуск: node tests/shot_settings.mjs
 */
import { chromium } from '@playwright/test';

const BASE = process.env.PREVIEW_URL || 'http://localhost:8903';
const OUT = process.env.SHOT_DIR || '/work/temp/shots';

async function openWorkspace(page) {
  await page.addInitScript(() => localStorage.setItem('mf_token', 'shot-fixture-token'));
  await page.goto(`${BASE}/index.html`);
  await page.locator('html[data-session="warm"]').waitFor({ timeout: 15000 });
  await page.locator('[data-telegram-login-trigger]').first().click();
  await page.locator('#appWrapper.active').waitFor({ timeout: 15000 });
  await page.locator('#loginScreen').evaluate((element) => {
    element.classList.add('hidden');
    element.style.display = 'none';
  });
  await page.locator('#appWrapper').evaluate((element) => {
    element.style.display = 'block';
  });
  await page
    .locator('.drawer, .modal-overlay, .onb-overlay, .overlay-soft, .toast-wrap')
    .evaluateAll((elements) => {
      for (const element of elements) {
        element.classList.remove('show');
        element.style.display = 'none';
      }
    });
  await page.evaluate(() => {
    for (const element of document.querySelectorAll('.section')) {
      const active = element.id === 'settings';
      element.classList.toggle('active', active);
      element.hidden = !active;
      element.style.display = active ? 'block' : 'none';
    }
  });
  await page.waitForTimeout(1500);
}

async function main() {
  const browser = await chromium.launch();
  for (const [name, width, height] of [
    ['desktop', 1440, 1000],
    ['mobile', 390, 844],
  ]) {
    const page = await browser.newPage({ viewport: { width, height } });
    await openWorkspace(page);

    // Панели рисуются lazy — прокручиваем к каждой и снимаем её отдельно.
    for (const id of ['accountRiskList', 'sessionsList']) {
      const panel = page.locator(`#${id}`).locator('xpath=ancestor::div[@class="panel"][1]');
      await panel.scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      await panel.screenshot({ path: `${OUT}/settings-${id}-${name}.png` });
    }

    // Полоса режима просмотра поверх уже отрисованного кабинета.
    await page.evaluate(() => {
      const bar = document.createElement('div');
      bar.className = 'view-only-bar';
      const text = document.createElement('span');
      text.append('Режим просмотра: кабинет ');
      const who = document.createElement('b');
      who.textContent = 'Анастасия Ковальчук';
      text.append(who, '. Изменения недоступны.');
      const back = document.createElement('button');
      back.className = 'btn-mini';
      back.textContent = 'Вернуться в свой аккаунт';
      bar.append(text, back);
      document.body.prepend(bar);
      window.scrollTo(0, 0);
    });
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${OUT}/view-only-${name}.png` });
    await page.close();
  }
  await browser.close();
  console.log('готово:', OUT);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
