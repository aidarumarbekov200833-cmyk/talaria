/* Аудит контраста кабинета и админки: заходит настоящим токеном, обходит
   разделы и меряет каждый видимый текст. Запуск:
   node tests/contrast_cabinet.mjs <url> <токен> [разделы через запятую] */
import { chromium } from '@playwright/test';
import { readFileSync } from 'node:fs';

const BASE = process.argv[2];
const TOKEN = process.argv[3];
const SECTIONS = (process.argv[4] || '').split(',').filter(Boolean);
const AUDIT = new Function(
  'return ' +
    readFileSync(new URL('./contrast_audit.mjs', import.meta.url), 'utf8')
      .split('const AUDIT = ')[1]
      .split('\n};')[0] +
    '\n}',
)();

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
await ctx.addInitScript((t) => {
  try {
    localStorage.setItem('mf_token', t);
  } catch {}
}, TOKEN);
const page = await ctx.newPage();
const problems = [];

for (const theme of ['light', 'dark']) {
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.evaluate((t) => {
    document.documentElement.setAttribute('data-theme', t);
    try {
      localStorage.setItem('mf_theme', t);
    } catch {}
  }, theme);
  await page.click('#navLoginCta').catch(() => {});
  await page.waitForTimeout(2500);
  for (const sec of SECTIONS) {
    const opened = await page.evaluate((s) => {
      // Раздел тарифа открывается своей кнопкой, а не пунктом меню.
      const item = document.querySelector(
        `.nav-item[data-target="${s}"], .tariff-btn[data-target="${s}"]`,
      );
      if (item) {
        item.click();
        return true;
      }
      // Выпадающие панели (ИИ-помощник, колокольчик) открываются кнопкой.
      const opener = document.querySelector(`[data-act="${s}"]`);
      if (opener) {
        opener.click();
        return true;
      }
      return false;
    }, sec);
    if (!opened) {
      console.log(`  (раздел ${sec} недоступен этому пользователю)`);
      continue;
    }
    await page.waitForTimeout(2000);
    const rows = (await page.evaluate(AUDIT)).filter((r) => !r.gradient);
    const seen = new Set();
    const uniq = rows.filter((r) => {
      const k = r.path + '|' + r.color + '|' + r.bg;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
    console.log(`\n=== ${sec} / ${theme} — точных проблем: ${uniq.length} ===`);
    for (const r of uniq.sort((a, b) => a.ratio - b.ratio))
      console.log(
        `  ${r.ratio.toFixed(2)} (нужно ${r.need})  ${r.color} на ${r.bg}` +
          `\n     ${r.path}\n     «${r.text}»`,
      );
    problems.push(...uniq.map((r) => ({ ...r, sec, theme })));
    await page.screenshot({ path: `/tmp/cab-${theme}-${sec}.png`, fullPage: false });
  }
}
console.log('\nИТОГО:', problems.length);
await browser.close();
