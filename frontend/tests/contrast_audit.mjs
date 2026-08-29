/* Аудит контраста: обходит каждый видимый текст, считает эффективный фон
   (первый непрозрачный предок) и отношение контраста по WCAG.
   Запуск: node tests/contrast_audit.mjs <базовый-url> [токен] */
import { chromium } from '@playwright/test';

const BASE = process.argv[2] || 'http://127.0.0.1:8903';
const TOKEN = process.argv[3] || '';

const AUDIT = () => {
  // Chrome отдаёт результат color-mix() как `color(srgb 0.97 0.93 0.97 / 0.96)`
  // — числа 0..1, а не 0..255. Если читать их как rgb(), почти белая панель
  // превращается в rgb(1, 1, 1), и весь светлый кабинет выглядит «чёрный текст
  // на чёрном». Формат надо различать, иначе аудит врёт.
  const parse = (c) => {
    if (!c) return null;
    const m = c.match(/[\d.]+(?:e[-+]?\d+)?/g);
    if (!m) return null;
    if (/^color\(/.test(c.trim())) {
      const n = m.slice(-4);
      const [r, g, b] = (n.length === 4 ? n : m.slice(-3)).slice(0, 3).map(Number);
      const a = m.length >= 4 ? +m[m.length - 1] : 1;
      return { r: r * 255, g: g * 255, b: b * 255, a: /\//.test(c) ? a : 1 };
    }
    return { r: +m[0], g: +m[1], b: +m[2], a: m.length > 3 ? +m[3] : 1 };
  };
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });
  const lum = (c) => {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
  };
  const path = (el) => {
    const bits = [];
    for (let n = el; n && n.tagName !== 'BODY'; n = n.parentElement) {
      const cls = (n.className || '').toString().trim().split(/\s+/).filter(Boolean).slice(0, 2);
      bits.unshift(n.tagName.toLowerCase() + (cls.length ? '.' + cls.join('.') : ''));
      if (bits.length >= 3) break;
    }
    return bits.join(' > ');
  };

  const out = [];
  for (const el of document.querySelectorAll('*')) {
    const own = [...el.childNodes]
      .filter((n) => n.nodeType === 3)
      .map((n) => n.textContent.trim())
      .join(' ')
      .trim();
    if (!own) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (cs.webkitTextFillColor === 'rgba(0, 0, 0, 0)') continue; // градиентный текст

    let op = 1;
    for (let n = el; n && n !== document.documentElement; n = n.parentElement)
      op *= parseFloat(getComputedStyle(n).opacity);
    if (op < 0.35) continue; // элемент в процессе появления

    // Эффективный фон: первый ПОКРАШЕННЫЙ предок.
    // `display: contents` пропускаем: элемент не рисуется, но computed style
    // всё равно отдаёт его фон — из-за этого белый текст на белой карточке
    // выглядел «текстом на тёмном» и терялся.
    let bg = parse(getComputedStyle(document.body).backgroundColor) || {
      r: 255,
      g: 255,
      b: 255,
      a: 1,
    };
    let gradient = false;
    for (let n = el; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.display === 'contents') continue;
      if (s.backgroundImage && s.backgroundImage !== 'none') {
        // Под текстом картинка или градиент — цвет фона не вычислить.
        gradient = true;
        break;
      }
      const c = parse(s.backgroundColor);
      if (c && c.a > 0.85) {
        bg = c;
        break;
      }
      if (c && c.a > 0) bg = over(c, bg);
    }
    const fg = parse(cs.color);
    if (!fg) continue;
    const eff = ratio(over(fg, bg), bg);
    const size = parseFloat(cs.fontSize);
    const bold = +cs.fontWeight >= 600;
    const large = size >= 24 || (size >= 18.66 && bold);
    const need = large ? 3.0 : 4.5;
    if (eff >= need) continue;
    out.push({
      text: own.slice(0, 58),
      path: path(el),
      color: cs.color,
      bg: `rgb(${Math.round(bg.r)}, ${Math.round(bg.g)}, ${Math.round(bg.b)})`,
      ratio: +eff.toFixed(2),
      need,
      size: +size.toFixed(1),
      gradient,
      y: Math.round(r.top + window.scrollY),
    });
  }
  return out;
};

async function audit(page, url, theme, label) {
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.evaluate((t) => {
    document.documentElement.setAttribute('data-theme', t);
    try {
      localStorage.setItem('mf_theme', t);
    } catch {}
  }, theme);
  await page.waitForTimeout(1200);
  // Проскроллить всю страницу, чтобы отработали появления по скроллу.
  await page.evaluate(async () => {
    const h = document.body.scrollHeight;
    for (let y = 0; y < h; y += 500) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 90));
    }
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(1400);
  const rows = await page.evaluate(AUDIT);
  const seen = new Set();
  const uniq = rows.filter((r) => {
    const k = r.path + '|' + r.color + '|' + r.bg;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  const hard = uniq.filter((r) => !r.gradient);
  const soft = uniq.filter((r) => r.gradient);
  const show = (title, list) => {
    if (!list.length) return;
    console.log(`\n  --- ${title}: ${list.length} ---`);
    for (const r of list.sort((a, b) => a.ratio - b.ratio))
      console.log(
        `  ${r.ratio.toFixed(2)} (нужно ${r.need})  ${r.color} на ${r.bg}` +
          `\n     ${r.path}\n     «${r.text}»`,
      );
  };
  console.log(
    `\n=== ${label} / ${theme} — точных: ${hard.length}, под градиентом: ${soft.length} ===`,
  );
  show('ТОЧНО ПЛОХО', hard);
  show('под картинкой или градиентом — глазами', soft);
  return uniq;
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
if (TOKEN)
  await ctx.addInitScript((t) => {
    try {
      localStorage.setItem('mf_token', t);
    } catch {}
  }, TOKEN);
const page = await ctx.newPage();
for (const theme of ['light', 'dark']) await audit(page, BASE + '/', theme, 'лендинг');
await browser.close();
