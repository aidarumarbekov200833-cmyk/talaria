/** Снимки раздела «Чат» для глазной проверки вёрстки.
 *
 *  Не тест: тесты проверяют структуру и переполнение, а тут нужно увидеть
 *  пузыри, аватары и то, как ведёт себя длинное имя. Запуск:
 *    node tests/shot_chat.mjs
 */
import { chromium } from '@playwright/test';

const BASE = process.env.PREVIEW_URL || 'http://localhost:8903';
const OUT = process.env.SHOT_DIR || '/work/temp/shots';

const VIEWS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
];

/** Показать кабинет без входа: лендинг и экран логина здесь не нужны.
 *
 *  Порядок повторяет visual-structure.spec.js — токен в localStorage, клик по
 *  «Войти», затем скрытие экрана логина. Своими руками показывать #appWrapper
 *  бесполезно: класс active вешает сам код входа.
 */
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
      const active = element.id === 'messages';
      element.classList.toggle('active', active);
      element.hidden = !active;
      element.style.display = active ? 'block' : 'none';
    }
    window.initChat?.();
  });
  await page.waitForTimeout(1500);
}

async function main() {
  const browser = await chromium.launch();
  for (const view of VIEWS) {
    for (const theme of ['light', 'dark']) {
      const page = await browser.newPage({ viewport: { width: view.width, height: view.height } });
      await openWorkspace(page);
      await page.evaluate((value) => {
        document.documentElement.setAttribute('data-theme', value);
      }, theme);
      await page.waitForTimeout(300);
      await page.screenshot({ path: `${OUT}/chat-${view.name}-${theme}-list.png` });

      // Открытый диалог: на телефоне это отдельный экран, его тоже надо видеть.
      await page.evaluate(() => window.openChatDialog?.('5001'));
      await page.waitForTimeout(900);
      await page.screenshot({ path: `${OUT}/chat-${view.name}-${theme}-room.png` });
      await page.close();
    }
  }
  await browser.close();
  console.log('готово:', OUT);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
