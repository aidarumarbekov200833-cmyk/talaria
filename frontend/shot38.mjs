import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';

const OUT = '/work/temp/v38';
mkdirSync(OUT, { recursive: true });
const SECTIONS = ['dashboard', 'search', 'offer', 'mailing', 'messages', 'tariffs', 'settings'];

const browser = await chromium.launch({ channel: 'chromium' });
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
await page.addInitScript(() => localStorage.setItem('mf_token', 'visual-fixture-token'));
const { mockApi } = await import('./tests/helpers/mock-api.js');
await mockApi(page);
await page.goto('http://127.0.0.1:4173/index.html');
await page.click('[data-telegram-login-trigger]').catch(() => {});
await page.evaluate(() => {
  const l = document.getElementById('loginScreen');
  if (l) l.style.display = 'none';
  const w = document.getElementById('appWrapper');
  if (w) { w.style.display = 'block'; w.classList.add('active'); }
  const style = document.createElement('style');
  style.textContent = '.onb-overlay{display:none !important}';
  document.head.appendChild(style);
});
for (const theme of ['dark', 'light']) {
  await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
  for (const id of SECTIONS) {
    await page.evaluate((target) => {
      document.querySelectorAll('.section').forEach((s) => {
        s.classList.toggle('active', s.id === target);
      });
      window.scrollTo({ top: 0 });
    }, id);
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${id}-${theme}.png`, fullPage: id !== 'messages' });
  }
}
await browser.close();
console.log('shots ready');
